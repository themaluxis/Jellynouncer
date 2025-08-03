#!/usr/bin/env python3
"""
Rating Services Module

This module contains external rating service integrations for fetching movie/TV ratings
from multiple APIs including OMDb, TMDb, and TVDb. These services are tightly coupled
and work together to provide comprehensive rating information.

Classes:
    TVDBAPIv4: Complete TVDB API v4 implementation supporting both licensed and subscriber access
    RatingService: Comprehensive rating service aggregating multiple external APIs
"""

import asyncio
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

import aiohttp
import aiosqlite

from config_models import RatingServicesConfig, TVDBConfig
from media_models import MediaItem


class TVDBAPIv4:
    """
    Complete TVDB API v4 implementation supporting both licensed and subscriber access modes.
    """

    def __init__(self, config: TVDBConfig, logger: logging.Logger):
        """Initialize TVDB API v4 client."""
        self.config = config
        self.logger = logger
        self.session = None

        # Authentication state
        self.token = None
        self.token_expires_at = None
        self.last_auth_attempt = 0

        # Determine access mode
        self.access_mode = self._determine_access_mode()

        # Rate limiting based on access mode
        if self.access_mode == 'subscriber':
            self.min_request_interval = 2.0  # Conservative for subscriber access
        else:
            self.min_request_interval = 1.0  # More relaxed for licensed access

        self.last_request_time = {}

        # URL caching to avoid repeated API calls
        self.series_slug_cache = {}  # episode_id -> series_slug
        self.cache_duration = 3600 * 24  # 24 hours

        # Request timeout
        self.request_timeout = 10

        self.logger.info(f"TVDB API v4 initialized in {self.access_mode} mode")

    def _determine_access_mode(self) -> str:
        """Determine the access mode based on configuration."""
        if self.config.access_mode != 'auto':
            return self.config.access_mode

        # Auto-detection logic
        if self.config.subscriber_pin:
            return 'subscriber'
        elif self.config.api_key:
            return 'licensed'
        else:
            return 'disabled'

    async def initialize(self, session: aiohttp.ClientSession):
        """Initialize with shared HTTP session."""
        self.session = session

        if self.access_mode != 'disabled':
            await self._authenticate()

    async def _authenticate(self) -> bool:
        """
        Authenticate with TVDB API v4 to get JWT token.

        Returns:
            True if authentication successful, False otherwise
        """
        if not self.config.api_key:
            self.logger.error("TVDB API key not configured")
            return False

        # Avoid rapid re-authentication attempts
        current_time = time.time()
        if current_time - self.last_auth_attempt < 30:
            return False

        self.last_auth_attempt = current_time

        try:
            # Prepare authentication payload
            auth_payload = {
                "apikey": self.config.api_key
            }

            # Add PIN for subscriber mode
            if self.access_mode == 'subscriber' and self.config.subscriber_pin:
                auth_payload["pin"] = self.config.subscriber_pin

            self.logger.debug(f"Authenticating with TVDB API v4 in {self.access_mode} mode")

            async with self.session.post(
                    f"{self.config.base_url}/login",
                    json=auth_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.request_timeout
            ) as response:

                if response.status == 200:
                    data = await response.json()

                    if "data" in data and "token" in data["data"]:
                        self.token = data["data"]["token"]
                        # JWT tokens are valid for 1 month
                        self.token_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
                        self.logger.info(f"TVDB API v4 authentication successful ({self.access_mode} mode)")
                        return True
                    else:
                        self.logger.error(f"TVDB API v4 auth failed: No token in response - {data}")
                        return False

                elif response.status == 401:
                    error_text = await response.text()
                    if self.access_mode == 'subscriber':
                        self.logger.error(f"TVDB API v4 auth failed: Invalid API key or subscriber PIN - {error_text}")
                    else:
                        self.logger.error(f"TVDB API v4 auth failed: Invalid API key - {error_text}")
                    return False

                else:
                    error_text = await response.text()
                    self.logger.error(f"TVDB API v4 auth failed: HTTP {response.status} - {error_text}")
                    return False

        except asyncio.TimeoutError:
            self.logger.error("TVDB API v4 authentication timeout")
            return False
        except Exception as e:
            self.logger.error(f"TVDB API v4 authentication error: {e}")
            return False

    async def _ensure_authenticated(self) -> bool:
        """Ensure we have a valid authentication token."""
        # Check if token exists and is not expired (refresh 1 day before expiry)
        if (self.token and self.token_expires_at and
                datetime.now(timezone.utc) < self.token_expires_at - timedelta(days=1)):
            return True

        # Re-authenticate
        return await self._authenticate()

    async def _rate_limit_check(self) -> None:
        """Implement rate limiting based on access mode."""
        current_time = time.time()
        service_key = f"tvdb_{self.access_mode}"

        last_request = self.last_request_time.get(service_key, 0)
        time_since_last = current_time - last_request

        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            await asyncio.sleep(sleep_time)

        self.last_request_time[service_key] = time.time()

    async def get_series_rating(self, tvdb_id: str) -> Dict[str, Any]:
        """
        Get series rating from TVDB API v4.
        This method is modified to handle both series IDs and episode IDs.

        Args:
            tvdb_id: TVDB series ID or episode ID

        Returns:
            Dictionary with rating data and attribution info
        """
        if self.access_mode == 'disabled':
            return {}

        if not await self._ensure_authenticated():
            self.logger.error("TVDB API v4 authentication failed, skipping rating fetch")
            return {}

        try:
            await self._rate_limit_check()

            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            # First, try to get it as a series ID
            async with self.session.get(
                    f"{self.config.base_url}/series/{tvdb_id}",
                    headers=headers,
                    timeout=self.request_timeout
            ) as response:

                if response.status == 200:
                    data = await response.json()
                    if "data" in data:
                        series_data = data["data"]
                        score = series_data.get("score")
                        if score is not None:
                            return {
                                'tvdb': {
                                    'value': round(score, 1),
                                    'scale': '10',
                                    'source': 'TVDb',
                                    'access_mode': self.access_mode,
                                    'attribution_required': True,
                                    'attribution_text': 'Metadata provided by TheTVDB',
                                    'attribution_url': 'https://thetvdb.com'
                                }
                            }

                elif response.status == 404:
                    # If not found as series, try as episode and get series info
                    self.logger.debug(f"TVDB ID {tvdb_id} not found as series, trying as episode")
                    episode_info = await self.get_episode_info_with_series_slug(tvdb_id)
                    if episode_info.get('series_id'):
                        # Recursively try to get rating for the series
                        return await self.get_series_rating(episode_info['series_id'])

                elif response.status == 401:
                    self.logger.warning(f"TVDB API v4 token expired, attempting re-authentication")
                    if await self._authenticate():
                        # Retry once with new token
                        return await self.get_series_rating(tvdb_id)

                else:
                    error_text = await response.text()
                    self.logger.warning(f"TVDB API v4 request failed: HTTP {response.status} - {error_text}")

        except asyncio.TimeoutError:
            self.logger.warning(f"TVDB API v4 request timeout for series {tvdb_id}")
        except Exception as e:
            self.logger.error(f"TVDB API v4 request error for series {tvdb_id}: {e}")

        return {}

    async def get_episode_info_with_series_slug(self, episode_id: str) -> Dict[str, Any]:
        """
        Get episode information including series slug for proper URL generation.

        Args:
            episode_id: TVDB episode ID

        Returns:
            Dictionary with episode info and series slug for URL generation
        """
        if self.access_mode == 'disabled':
            return {}

        # Check cache first
        cache_key = f"episode_{episode_id}"
        if cache_key in self.series_slug_cache:
            cached_data, timestamp = self.series_slug_cache[cache_key]
            if time.time() - timestamp < self.cache_duration:
                self.logger.debug(f"Using cached series slug for episode {episode_id}")
                return cached_data

        if not await self._ensure_authenticated():
            self.logger.error("TVDB API v4 authentication failed, cannot get episode info")
            return {}

        try:
            await self._rate_limit_check()

            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }

            # Get episode information
            async with self.session.get(
                    f"{self.config.base_url}/episodes/{episode_id}/extended",
                    headers=headers,
                    timeout=self.request_timeout
            ) as response:

                if response.status == 200:
                    data = await response.json()

                    if "data" in data:
                        episode_data = data["data"]
                        series_info = episode_data.get("series", {})

                        # Extract series information
                        series_id = series_info.get("id")
                        series_slug = series_info.get("slug")
                        series_name = series_info.get("name")

                        if series_slug and series_id:
                            result = {
                                'episode_id': episode_id,
                                'episode_name': episode_data.get('name'),
                                'series_id': series_id,
                                'series_slug': series_slug,
                                'series_name': series_name,
                                'season_number': episode_data.get('seasonNumber'),
                                'episode_number': episode_data.get('number'),
                                'proper_url': f"https://thetvdb.com/series/{series_slug}/episodes/{episode_id}",
                                'attribution_required': True,
                                'attribution_text': 'Metadata provided by TheTVDB',
                                'attribution_url': 'https://thetvdb.com'
                            }

                            # Cache the result
                            self.series_slug_cache[cache_key] = (result, time.time())

                            self.logger.debug(f"Retrieved series slug '{series_slug}' for episode {episode_id}")
                            return result
                        else:
                            self.logger.warning(f"No series slug found for episode {episode_id}")

                elif response.status == 401:
                    self.logger.warning(f"TVDB API v4 token expired, attempting re-authentication")
                    if await self._authenticate():
                        # Retry once with new token
                        return await self.get_episode_info_with_series_slug(episode_id)

                elif response.status == 404:
                    self.logger.debug(f"TVDB episode not found: {episode_id}")

                else:
                    error_text = await response.text()
                    self.logger.warning(f"TVDB API v4 request failed: HTTP {response.status} - {error_text}")

        except asyncio.TimeoutError:
            self.logger.warning(f"TVDB API v4 request timeout for episode {episode_id}")
        except Exception as e:
            self.logger.error(f"TVDB API v4 request error for episode {episode_id}: {e}")

        return {}


class RatingService:
    """
    Comprehensive rating service for fetching movie/TV ratings from multiple external APIs.

    This service manages rating data from various sources including OMDb (which aggregates
    IMDb, Rotten Tomatoes, and Metacritic), TMDb, and TVDb. It includes intelligent caching,
    retry logic, and graceful fallback handling.

    Features:
    - Multi-source rating aggregation (OMDb, TMDb, TVDb)
    - Intelligent caching with configurable expiration
    - Rate limiting and retry logic
    - Graceful error handling and fallback
    - Batch processing for library sync operations
    """

    def __init__(self, config: RatingServicesConfig, logger: logging.Logger):
        """Initialize rating service with configuration and logging."""
        self.config = config
        self.logger = logger
        self.session = None
        self.db_manager = None

        # Extract API configuration and keys
        self.enabled = config.enabled
        self.cache_duration_hours = config.cache_duration_hours
        self.request_timeout = config.request_timeout_seconds
        self.retry_attempts = config.retry_attempts

        # API service configurations
        self.omdb_config = config.omdb
        self.tmdb_config = config.tmdb
        self.tvdb_config = config.tvdb

        # Initialize API keys from configuration
        self.omdb_api_key = self.omdb_config.api_key
        self.tmdb_api_key = self.tmdb_config.api_key

        # Initialize TVDB API v4 client
        self.tvdb_api = None
        if self.tvdb_config.enabled:
            self.tvdb_api = TVDBAPIv4(self.tvdb_config, logger)

        # Rate limiting state
        self.last_request_times = {}
        self.min_request_interval = 1.0

        self.logger.info(f"Rating service initialized - Enabled: {self.enabled}")
        if self.enabled:
            services = []
            if self.omdb_config.enabled and self.omdb_api_key:
                services.append("OMDb")
            if self.tmdb_config.enabled and self.tmdb_api_key:
                services.append("TMDb")
            if self.tvdb_config.enabled and self.tvdb_api:
                services.append(f"TVDb v4 ({self.tvdb_api.access_mode})")

            self.logger.info(
                f"Available rating services: {', '.join(services) if services else 'None (no API keys configured)'}"
            )

    async def initialize(self, session: aiohttp.ClientSession, db_manager):
        """Initialize with shared HTTP session and database manager."""
        self.session = session
        self.db_manager = db_manager

        if self.enabled:
            # Initialize TVDB API v4 if enabled
            if self.tvdb_api:
                await self.tvdb_api.initialize(session)

            # Clean up expired rating cache entries
            await self._cleanup_expired_cache()
            self.logger.info("Rating service initialization complete")
        else:
            self.logger.info("Rating service disabled in configuration")

    async def get_ratings_for_item(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get comprehensive rating information for a media item.

        This method attempts to fetch ratings from all configured services,
        using cached data when available and fresh data when cache is expired.

        Args:
            item: MediaItem to fetch ratings for

        Returns:
            Dictionary containing rating data from all available sources
        """
        if not self.enabled or not self.session:
            return {}

        # Check if we have any external IDs to work with
        if not any([item.imdb_id, item.tmdb_id, item.tvdb_id]):
            self.logger.debug(f"No external IDs available for item {item.item_id}")
            return {}

        try:
            # Check cache first
            cached_ratings = await self._get_cached_ratings(item.imdb_id, item.tmdb_id, item.tvdb_id)
            if cached_ratings:
                self.logger.debug(f"Using cached ratings for item {item.item_id}")
                return cached_ratings

            # Fetch fresh ratings from all available services
            ratings = {}

            # Fetch from OMDb (includes IMDb, RT, Metacritic)
            if self.omdb_api_key and item.imdb_id:
                omdb_ratings = await self._fetch_omdb_ratings(item.imdb_id)
                ratings.update(omdb_ratings)

            # Fetch from TMDb
            if self.tmdb_api_key and item.tmdb_id:
                tmdb_ratings = await self._fetch_tmdb_ratings(item.tmdb_id, item.item_type)
                ratings.update(tmdb_ratings)

            # Fetch from TVDb (for TV content only) with proper URL generation
            if self.tvdb_api and item.tvdb_id and item.item_type in ['Episode', 'Season', 'Series']:
                tvdb_data = await self._fetch_tvdb_data_with_urls(item.tvdb_id, item.item_type)
                if tvdb_data:
                    ratings.update(tvdb_data)

            # Cache the results for future use
            if ratings:
                await self._cache_ratings(item.imdb_id, item.tmdb_id, item.tvdb_id, ratings)
                self.logger.debug(f"Cached {len(ratings)} ratings for item {item.item_id}")

            return ratings

        except Exception as e:
            self.logger.error(f"Error fetching ratings for item {item.item_id}: {e}")
            return {}

    async def _fetch_omdb_ratings(self, imdb_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Fetch ratings from OMDb API (includes IMDb, Rotten Tomatoes, Metacritic).

        Args:
            imdb_id: IMDb identifier (e.g., "tt0133093")

        Returns:
            Dictionary with rating data from OMDb sources
        """
        if not self.omdb_config.enabled or not self.omdb_api_key or not imdb_id:
            return {}

        try:
            await self._rate_limit_check('omdb')

            url = self.omdb_config.base_url
            params = {
                'apikey': self.omdb_api_key,
                'i': imdb_id,
                'plot': 'short',
                'r': 'json'
            }

            async with self.session.get(url, params=params, timeout=self.request_timeout) as response:
                if response.status == 200:
                    data = await response.json()

                    if data.get('Response') == 'True':
                        ratings = {}

                        # Parse the Ratings array from OMDb
                        for rating in data.get('Ratings', []):
                            source = rating.get('Source', '').lower()
                            value = rating.get('Value', '')

                            if 'imdb' in source and value:
                                ratings['imdb'] = {
                                    'value': value.split('/')[0],  # Extract just the rating part
                                    'scale': '10',
                                    'source': 'IMDb',
                                    'full_value': value
                                }
                            elif 'rotten tomatoes' in source and value:
                                ratings['rotten_tomatoes'] = {
                                    'value': value.rstrip('%'),
                                    'scale': '100%',
                                    'source': 'Rotten Tomatoes',
                                    'full_value': value
                                }
                            elif 'metacritic' in source and value:
                                ratings['metacritic'] = {
                                    'value': value.split('/')[0],
                                    'scale': '100',
                                    'source': 'Metacritic',
                                    'full_value': value
                                }

                        self.logger.debug(f"OMDb API returned {len(ratings)} ratings for {imdb_id}")
                        return ratings
                    else:
                        self.logger.debug(f"OMDb API: No data found for {imdb_id}")
                else:
                    self.logger.warning(f"OMDb API request failed with status {response.status}")

        except asyncio.TimeoutError:
            self.logger.warning(f"OMDb API request timeout for {imdb_id}")
        except Exception as e:
            self.logger.error(f"OMDb API request failed for {imdb_id}: {e}")

        return {}

    async def _fetch_tmdb_ratings(self, tmdb_id: str, item_type: str) -> Dict[str, Dict[str, Any]]:
        """
        Fetch ratings from TMDb API.

        Args:
            tmdb_id: TMDb identifier
            item_type: Type of content (Movie, Episode, etc.)

        Returns:
            Dictionary with TMDb rating data
        """
        if not self.tmdb_config.enabled or not self.tmdb_api_key or not tmdb_id:
            return {}

        try:
            await self._rate_limit_check('tmdb')

            # Determine API endpoint based on content type
            if item_type == 'Movie':
                endpoint = f"movie/{tmdb_id}"
            elif item_type in ['Episode', 'Season', 'Series']:
                endpoint = f"tv/{tmdb_id}"
            else:
                return {}  # Unsupported content type for TMDb

            url = f"{self.tmdb_config.base_url}{endpoint}"
            params = {
                'api_key': self.tmdb_api_key
            }

            async with self.session.get(url, params=params, timeout=self.request_timeout) as response:
                if response.status == 200:
                    data = await response.json()

                    vote_average = data.get('vote_average')
                    vote_count = data.get('vote_count')

                    if vote_average is not None and vote_count and vote_count > 0:
                        return {
                            'tmdb': {
                                'value': round(vote_average, 1),
                                'scale': '10',
                                'source': 'TMDb',
                                'vote_count': vote_count,
                                'popularity': data.get('popularity')
                            }
                        }
                else:
                    self.logger.warning(f"TMDb API request failed with status {response.status}")

        except asyncio.TimeoutError:
            self.logger.warning(f"TMDb API request timeout for {tmdb_id}")
        except Exception as e:
            self.logger.error(f"TMDb API request failed for {tmdb_id}: {e}")

        return {}

    async def _fetch_tvdb_data_with_urls(self, tvdb_id: str, item_type: str) -> Dict[str, Dict[str, Any]]:
        """
        Fetch TVDB data including proper URLs for episodes.

        Args:
            tvdb_id: TVDB identifier
            item_type: Type of content (Episode, Series, etc.)

        Returns:
            Dictionary with TVDB data including proper URLs
        """
        if not self.tvdb_api or not tvdb_id:
            return {}

        try:
            ratings = {}

            # Get rating data
            rating_data = await self.tvdb_api.get_series_rating(tvdb_id)
            if rating_data:
                ratings.update(rating_data)

            # For episodes, get the proper URL information
            if item_type == 'Episode':
                episode_info = await self.tvdb_api.get_episode_info_with_series_slug(tvdb_id)
                if episode_info.get('proper_url'):
                    # Add URL information to the existing rating data
                    if 'tvdb' in ratings:
                        ratings['tvdb']['proper_url'] = episode_info['proper_url']
                        ratings['tvdb']['series_slug'] = episode_info.get('series_slug')
                        ratings['tvdb']['episode_id'] = episode_info.get('episode_id')
                    else:
                        # Even if no rating, provide URL info
                        ratings['tvdb'] = {
                            'proper_url': episode_info['proper_url'],
                            'series_slug': episode_info.get('series_slug'),
                            'episode_id': episode_info.get('episode_id'),
                            'source': 'TVDb',
                            'attribution_required': True,
                            'attribution_text': 'Metadata provided by TheTVDB',
                            'attribution_url': 'https://thetvdb.com'
                        }

                    self.logger.debug(f"Added proper TVDB URL for episode {tvdb_id}: {episode_info['proper_url']}")

            return ratings

        except Exception as e:
            self.logger.error(f"Error fetching TVDB data with URLs for {tvdb_id}: {e}")
            return {}

    async def _rate_limit_check(self, service: str):
        """
        Simple rate limiting to avoid overwhelming external APIs.

        Args:
            service: Name of the service to rate limit ('omdb', 'tmdb', 'tvdb')
        """
        current_time = time.time()
        last_request = self.last_request_times.get(service, 0)

        time_since_last = current_time - last_request
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            await asyncio.sleep(sleep_time)

        self.last_request_times[service] = time.time()

    async def _get_cached_ratings(self, imdb_id: str, tmdb_id: str, tvdb_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Retrieve cached rating data if available and not expired.

        Args:
            imdb_id: IMDb identifier
            tmdb_id: TMDb identifier
            tvdb_id: TVDb identifier

        Returns:
            Cached rating data or empty dict if no valid cache found
        """
        if not self.db_manager:
            return {}

        try:
            async with aiosqlite.connect(self.db_manager.db_path) as db:
                # Look for cached ratings that match any of the provided IDs
                conditions = []
                params = []

                if imdb_id:
                    conditions.append("imdb_id = ?")
                    params.append(imdb_id)
                if tmdb_id:
                    conditions.append("tmdb_id = ?")
                    params.append(tmdb_id)
                if tvdb_id:
                    conditions.append("tvdb_id = ?")
                    params.append(tvdb_id)

                if not conditions:
                    return {}

                query = f"""
                    SELECT * FROM ratings_cache 
                    WHERE ({' OR '.join(conditions)}) 
                    AND (expires_at IS NULL OR expires_at > datetime('now'))
                    ORDER BY updated_at DESC 
                    LIMIT 1
                """

                cursor = await db.execute(query, params)
                row = await cursor.fetchone()

                if row:
                    # Convert database row to rating format
                    ratings = {}

                    if row[3]:  # omdb_imdb_rating
                        ratings['imdb'] = {
                            'value': row[3].split('/')[0] if '/' in row[3] else row[3],
                            'scale': '10',
                            'source': 'IMDb',
                            'full_value': row[3]
                        }

                    if row[4]:  # omdb_rt_rating
                        ratings['rotten_tomatoes'] = {
                            'value': row[4].rstrip('%'),
                            'scale': '100%',
                            'source': 'Rotten Tomatoes',
                            'full_value': row[4]
                        }

                    if row[5]:  # omdb_metacritic_rating
                        ratings['metacritic'] = {
                            'value': row[5].split('/')[0] if '/' in row[5] else row[5],
                            'scale': '100',
                            'source': 'Metacritic',
                            'full_value': row[5]
                        }

                    if row[8]:  # tmdb_rating
                        ratings['tmdb'] = {
                            'value': row[8],
                            'scale': '10',
                            'source': 'TMDb',
                            'vote_count': row[9]  # tmdb_vote_count
                        }

                    if row[11]:  # tvdb_rating
                        ratings['tvdb'] = {
                            'value': row[11],
                            'scale': '10',
                            'source': 'TVDb'
                        }

                    return ratings

        except Exception as e:
            self.logger.error(f"Error retrieving cached ratings: {e}")

        return {}

    async def _cache_ratings(self, imdb_id: str, tmdb_id: str, tvdb_id: str, ratings: Dict[str, Dict[str, Any]]):
        """
        Cache rating data for future use.

        Args:
            imdb_id: IMDb identifier
            tmdb_id: TMDb identifier
            tvdb_id: TVDb identifier
            ratings: Rating data to cache
        """
        if not self.db_manager or not ratings:
            return

        try:
            # Calculate expiration time
            expires_at = datetime.now(timezone.utc) + timedelta(hours=self.cache_duration_hours)

            # Extract rating values for database storage
            omdb_imdb = ratings.get('imdb', {}).get('full_value')
            omdb_rt = ratings.get('rotten_tomatoes', {}).get('full_value')
            omdb_metacritic = ratings.get('metacritic', {}).get('full_value')
            tmdb_rating = ratings.get('tmdb', {}).get('value')
            tmdb_vote_count = ratings.get('tmdb', {}).get('vote_count')
            tvdb_rating = ratings.get('tvdb', {}).get('value')

            async with aiosqlite.connect(self.db_manager.db_path) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO ratings_cache 
                    (imdb_id, tmdb_id, tvdb_id, omdb_imdb_rating, omdb_rt_rating, 
                     omdb_metacritic_rating, tmdb_rating, tmdb_vote_count, tvdb_rating, 
                     expires_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    imdb_id, tmdb_id, tvdb_id, omdb_imdb, omdb_rt, omdb_metacritic,
                    tmdb_rating, tmdb_vote_count, tvdb_rating,
                    expires_at.isoformat(), datetime.now(timezone.utc).isoformat()
                ))
                await db.commit()

        except Exception as e:
            self.logger.error(f"Error caching ratings: {e}")

    async def _cleanup_expired_cache(self):
        """Remove expired rating cache entries to keep database size manageable."""
        if not self.db_manager:
            return

        try:
            async with aiosqlite.connect(self.db_manager.db_path) as db:
                cursor = await db.execute("""
                                          DELETE
                                          FROM ratings_cache
                                          WHERE expires_at IS NOT NULL
                                            AND expires_at < datetime('now')
                                          """)
                deleted_count = cursor.rowcount
                await db.commit()

                if deleted_count > 0:
                    self.logger.info(f"Cleaned up {deleted_count} expired rating cache entries")

        except Exception as e:
            self.logger.error(f"Error cleaning up rating cache: {e}")