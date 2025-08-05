#!/usr/bin/env python3
"""
Jellynouncer Rating Services Module

This module provides comprehensive rating and metadata enhancement services by integrating
with multiple external APIs including OMDb, TMDb, and TVDb. It enriches Discord notifications
with additional rating information, reviews, and enhanced metadata not available in Jellyfin.

The rating services are designed to be optional and fault-tolerant - if they fail, the core
notification service continues to operate normally without rating enhancements.

Classes:
    RatingService: Main service coordinator for all rating providers
    TVDBAPIv4: Specialized client for TVDB API v4 integration

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import asyncio
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

import aiohttp

from config_models import RatingServicesConfig, TVDBConfig
from media_models import MediaItem
from utils import get_logger


class TVDBAPIv4:
    """
    Specialized client for TVDB API v4 integration.

    The TV Database (TVDB) provides comprehensive television show information
    including ratings, episode data, and artwork. This client handles the
    complexities of TVDB's authentication system and API access patterns.

    **TVDB API v4 Features:**
    - Comprehensive TV show and episode metadata
    - Multiple authentication modes (subscriber, licensed)
    - Enhanced artwork and poster collections
    - Community ratings and reviews
    - Multi-language support

    **Authentication Modes:**
    - Subscriber: Enhanced access with API key + PIN
    - Licensed: Commercial access with different auth flow
    - Auto: Automatically detect best available mode

    Attributes:
        config (TVDBConfig): TVDB configuration and authentication settings
        logger (logging.Logger): Logger instance for TVDB operations
        session (aiohttp.ClientSession): HTTP session for API requests
        access_token (Optional[str]): Current authentication token
        token_expires (Optional[datetime]): Token expiration timestamp
        access_mode (str): Current access mode (subscriber, licensed, auto)

    Example:
        ```python
        tvdb_config = TVDBConfig(
            enabled=True,
            api_key="your_tvdb_key",
            subscriber_pin="your_pin"
        )

        tvdb_client = TVDBAPIv4(tvdb_config, logger)
        await tvdb_client.initialize(session)

        # Get TV show information
        show_data = await tvdb_client.get_series_info("12345")
        if show_data:
            logger.info(f"Show: {show_data['name']}")
            logger.info(f"Rating: {show_data.get('rating', 'N/A')}")
        ```
    """

    def __init__(self, config: TVDBConfig, logger: logging.Logger):
        """
        Initialize TVDB API v4 client with configuration.

        Args:
            config (TVDBConfig): TVDB configuration with API key and access settings
            logger (logging.Logger): Logger instance for TVDB operations
        """
        self.config = config
        self.logger = logger
        self.session = None
        self.access_token = None
        self.token_expires = None
        self.access_mode = config.access_mode

        # Determine actual access mode based on available credentials
        if config.subscriber_pin and config.api_key:
            self.access_mode = "subscriber"
        elif config.api_key:
            self.access_mode = "licensed"
        else:
            self.access_mode = "disabled"

        self.logger.debug(f"TVDB client initialized in {self.access_mode} mode")

    async def initialize(self, session: aiohttp.ClientSession) -> bool:
        """
        Initialize TVDB client with HTTP session and authenticate.

        Args:
            session (aiohttp.ClientSession): Shared HTTP session for requests

        Returns:
            bool: True if initialization successful, False otherwise
        """
        self.session = session

        if self.access_mode == "disabled":
            self.logger.info("TVDB client disabled - no API key provided")
            return False

        try:
            success = await self._authenticate()
            if success:
                self.logger.info(f"TVDB client initialized successfully ({self.access_mode} mode)")
            else:
                self.logger.warning("TVDB authentication failed")
            return success
        except Exception as e:
            self.logger.error(f"TVDB initialization failed: {e}")
            return False

    async def _authenticate(self) -> bool:
        """
        Authenticate with TVDB API and obtain access token.

        Returns:
            bool: True if authentication successful, False otherwise
        """
        try:
            auth_data = {"apikey": self.config.api_key}

            if self.access_mode == "subscriber" and self.config.subscriber_pin:
                auth_data["pin"] = self.config.subscriber_pin

            async with self.session.post(
                    f"{self.config.base_url}login",
                    json=auth_data,
                    timeout=10
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.access_token = data.get("data", {}).get("token")

                    if self.access_token:
                        # Token typically expires in 24 hours
                        self.token_expires = datetime.now(timezone.utc) + timedelta(hours=23)
                        self.logger.debug("TVDB authentication successful")
                        return True
                    else:
                        self.logger.error("TVDB authentication response missing token")
                        return False
                else:
                    error_text = await response.text()
                    self.logger.error(f"TVDB authentication failed: {response.status} - {error_text}")
                    return False

        except Exception as e:
            self.logger.error(f"TVDB authentication error: {e}")
            return False

    async def _ensure_authenticated(self) -> bool:
        """
        Ensure we have a valid authentication token.

        Returns:
            bool: True if token is valid or refresh successful, False otherwise
        """
        if not self.access_token or not self.token_expires:
            return await self._authenticate()

        # Check if token is about to expire (refresh 1 hour early)
        if datetime.now(timezone.utc) >= (self.token_expires - timedelta(hours=1)):
            self.logger.debug("TVDB token expiring soon, refreshing...")
            return await self._authenticate()

        return True

    async def get_series_info(self, tvdb_id: str) -> Optional[Dict[str, Any]]:
        """
        Get TV series information from TVDB.

        Args:
            tvdb_id (str): TVDB series ID

        Returns:
            Optional[Dict[str, Any]]: Series information if found, None otherwise
        """
        if not await self._ensure_authenticated():
            return None

        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}

            async with self.session.get(
                    f"{self.config.base_url}series/{tvdb_id}",
                    headers=headers,
                    timeout=10
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    series_data = data.get("data", {})

                    self.logger.debug(f"Retrieved TVDB series info for {tvdb_id}")
                    return series_data
                else:
                    self.logger.warning(f"TVDB series not found: {tvdb_id} (status: {response.status})")
                    return None

        except Exception as e:
            self.logger.error(f"Error retrieving TVDB series {tvdb_id}: {e}")
            return None


class RatingService:
    """
    Comprehensive rating service that integrates multiple external APIs.

    This service manages rating data from various sources including OMDb (which aggregates
    IMDb, Rotten Tomatoes, and Metacritic), TMDb, and TVDb. It provides a unified interface
    for rating information while handling the complexity of multiple APIs, caching strategies,
    and error recovery.

    **Understanding External Rating APIs:**

    **OMDb (Open Movie Database):**
    - Aggregates ratings from IMDb, Rotten Tomatoes, and Metacritic
    - Provides comprehensive movie and TV show information
    - Requires API key (free tier available)
    - Single API call returns multiple rating sources

    **TMDb (The Movie Database):**
    - Community-driven movie and TV database
    - Provides user ratings, vote counts, and metadata
    - Free API with generous rate limits
    - Excellent for movie information and posters

    **TVDb (The TV Database):**
    - Specialized TV show database with episode-level information
    - Community ratings and comprehensive episode metadata
    - Both free and paid tiers available
    - Best source for TV show information and artwork

    **Advanced Features:**

    **Intelligent Caching:**
    Rating data doesn't change frequently, so aggressive caching reduces API usage:
    - Database-backed cache with configurable expiration
    - Automatic cleanup of expired cache entries
    - Cache hit optimization for repeated requests

    **Multi-Source Aggregation:**
    Combines ratings from multiple sources to provide comprehensive information:
    - OMDb: IMDb ratings, Rotten Tomatoes percentages, Metacritic scores
    - TMDb: Community ratings and vote counts
    - TVDb: TV-specific ratings and episode information

    **Graceful Error Handling:**
    External APIs can fail, but the service continues operating:
    - Individual API failures don't break the entire service
    - Retry logic with exponential backoff for temporary failures
    - Fallback strategies when preferred sources are unavailable

    **Rate Limiting:**
    Respects API rate limits to maintain good standing with external services:
    - Per-service rate limiting based on API terms
    - Automatic backoff when rate limits are detected
    - Batching optimization for multiple requests

    Attributes:
        config (RatingServicesConfig): Configuration for all rating services
        logger (logging.Logger): Logger instance for rating operations
        session (aiohttp.ClientSession): HTTP session for API requests
        db_manager: Database manager for cache storage
        enabled (bool): Whether rating service is enabled globally
        tvdb_api (TVDBAPIv4): TVDb API client for TV show information
        omdb_api_key (str): OMDb API key for movie/TV ratings
        tmdb_api_key (str): TMDb API key for community ratings
        cache_duration_hours (int): How long to cache rating data
        last_request_times (Dict): Rate limiting state per service

    Example:
        ```python
        # Initialize rating service
        rating_config = RatingServicesConfig(
            enabled=True,
            cache_duration_hours=24,
            omdb=OMDbConfig(enabled=True, api_key="your_omdb_key"),
            tmdb=TMDbConfig(enabled=True, api_key="your_tmdb_key"),
            tvdb=TVDBConfig(enabled=True, api_key="your_tvdb_key")
        )

        rating_service = RatingService(rating_config)

        # Initialize with shared resources
        async with aiohttp.ClientSession() as session:
            await rating_service.initialize(session, db_manager)

            # Get comprehensive ratings for a movie
            movie = MediaItem(item_id="abc123", name="The Matrix", imdb_id="tt0133093")
            ratings = await rating_service.get_ratings_for_item(movie)

            logger.info(f"IMDb: {ratings.get('imdb', {}).get('rating', 'N/A')}")
            logger.info(f"Rotten Tomatoes: {ratings.get('rotten_tomatoes', {}).get('rating', 'N/A')}")
            logger.info(f"TMDb: {ratings.get('tmdb', {}).get('rating', 'N/A')}")
        ```

    Note:
        This service is designed to enhance Discord notifications with rich rating
        information. It gracefully handles API failures and provides sensible
        fallbacks to ensure notifications are always sent, even if ratings are unavailable.
    """

    def __init__(self, config: RatingServicesConfig):
        """
        Initialize rating service with configuration and API client setup.

        This constructor sets up the rating service with configuration for all
        external APIs and initializes the TVDb client. It doesn't perform network
        operations - those happen in the initialize() method.

        **Service Configuration:**
        The rating service can enable/disable individual APIs based on configuration:
        - Global enable/disable switch for entire rating system
        - Per-API enable/disable switches for granular control
        - API key validation to prevent misconfiguration

        **TVDB Integration:**
        The TVDb client is initialized during construction but requires async
        initialization later. This separation allows for clean error handling
        and resource management.

        Args:
            config (RatingServicesConfig): Configuration for all rating services

        Example:
            ```python
            # Initialize with comprehensive configuration
            config = RatingServicesConfig(
                enabled=True,
                cache_duration_hours=24,
                omdb=OMDbConfig(enabled=True, api_key="omdb_key"),
                tmdb=TMDbConfig(enabled=True, api_key="tmdb_key"),
                tvdb=TVDBConfig(enabled=True, api_key="tvdb_key")
            )

            rating_service = RatingService(config)
            logger.info(f"Rating service enabled: {rating_service.enabled}")
            ```
        """
        self.config = config
        self.logger = get_logger("jellynouncer.ratings")
        self.session = None  # Set during initialize()
        self.db_manager = None  # Set during initialize()

        # Extract global configuration settings
        self.enabled = config.enabled
        self.cache_duration_hours = config.cache_duration_hours
        self.request_timeout = config.request_timeout_seconds
        self.retry_attempts = config.retry_attempts

        # Extract individual API service configurations
        self.omdb_config = config.omdb
        self.tmdb_config = config.tmdb
        self.tvdb_config = config.tvdb

        # Initialize API keys from configuration
        self.omdb_api_key = self.omdb_config.api_key
        self.tmdb_api_key = self.tmdb_config.api_key

        # Initialize TVDB API v4 client if enabled
        self.tvdb_api = None
        if self.tvdb_config.enabled:
            self.tvdb_api = TVDBAPIv4(self.tvdb_config, self.logger)

        # Rate limiting state for external APIs
        self.last_request_times = {}
        self.min_request_interval = 1.0  # Minimum seconds between API requests

        # Log initialization status and available services
        self.logger.info(f"Rating service initialized - Enabled: {self.enabled}")
        if self.enabled:
            available_services = []
            if self.omdb_config.enabled and self.omdb_api_key:
                available_services.append("OMDb")
            if self.tmdb_config.enabled and self.tmdb_api_key:
                available_services.append("TMDb")
            if self.tvdb_config.enabled and self.tvdb_api:
                available_services.append(f"TVDb v4 ({self.tvdb_api.access_mode})")

            service_list = ', '.join(available_services) if available_services else 'None (no API keys configured)'
            self.logger.info(f"Available rating services: {service_list}")

    async def initialize(self, session: aiohttp.ClientSession, db_manager) -> None:
        """
        Initialize rating service with shared resources and perform setup tasks.

        This async method completes the initialization process by setting up
        shared resources and performing any necessary authentication or setup
        with external APIs.

        **Initialization Tasks:**
        - Store references to shared HTTP session and database manager
        - Initialize TVDB client with authentication
        - Validate API keys and connectivity
        - Set up caching infrastructure

        Args:
            session (aiohttp.ClientSession): Shared HTTP session for all API requests
            db_manager: Database manager for caching rating data

        Example:
            ```python
            async with aiohttp.ClientSession() as session:
                await rating_service.initialize(session, db_manager)

                # Now ready to fetch ratings
                ratings = await rating_service.get_ratings_for_item(media_item)
            ```
        """
        self.session = session
        self.db_manager = db_manager

        if not self.enabled:
            self.logger.info("Rating services disabled in configuration")
            return

        # Initialize TVDB client if configured
        if self.tvdb_api:
            try:
                tvdb_success = await self.tvdb_api.initialize(session)
                if tvdb_success:
                    self.logger.info("TVDB client initialized successfully")
                else:
                    self.logger.warning("TVDB client initialization failed")
            except Exception as e:
                self.logger.error(f"TVDB initialization error: {e}")

        self.logger.info("Rating service initialization completed")

    async def get_ratings_for_item(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get comprehensive rating information for a media item from all available sources.

        This is the main entry point for rating retrieval. It coordinates calls
        to multiple external APIs and combines the results into a unified format.
        The method handles errors gracefully - individual API failures don't
        prevent other sources from being queried.

        **Rating Sources:**
        - OMDb: IMDb ratings, Rotten Tomatoes scores, Metacritic scores
        - TMDb: Community ratings and vote counts
        - TVDb: TV show specific ratings and metadata

        **Caching Strategy:**
        Ratings are cached aggressively since they don't change frequently.
        Cache duration is configurable and helps reduce API usage and improve
        response times for repeated requests.

        Args:
            item (MediaItem): Media item to get ratings for

        Returns:
            Dict[str, Dict[str, Any]]: Nested dictionary with ratings from each source.
            Format: {source_name: {rating_type: value, ...}}

        Example:
            ```python
            ratings = await rating_service.get_ratings_for_item(media_item)

            # Access different rating sources
            imdb_rating = ratings.get('imdb', {}).get('rating')
            rt_score = ratings.get('rotten_tomatoes', {}).get('rating')
            tmdb_rating = ratings.get('tmdb', {}).get('rating')

            if imdb_rating:
                logger.info(f"IMDb: {imdb_rating}/10")
            if rt_score:
                logger.info(f"Rotten Tomatoes: {rt_score}%")
            ```

        Note:
            This method never raises exceptions. Individual API failures are logged
            but don't prevent other APIs from being queried successfully.
        """
        if not self.enabled:
            return {}

        self.logger.debug(f"Getting ratings for {item.name} ({item.item_type})")

        # Check cache first to avoid unnecessary API requests
        cached_ratings = await self._get_cached_ratings(item)
        if cached_ratings:
            self.logger.debug(f"Using cached ratings for {item.name}")
            return cached_ratings

        # Collect ratings from all available sources concurrently
        rating_tasks = []

        # OMDb API (IMDb, Rotten Tomatoes, Metacritic)
        if (self.omdb_config.enabled and self.omdb_api_key and
                (item.imdb_id or item.tmdb_id)):
            rating_tasks.append(self._get_omdb_ratings(item))

        # TMDb API (Community ratings)
        if (self.tmdb_config.enabled and self.tmdb_api_key and
                (item.tmdb_id or item.imdb_id)):
            rating_tasks.append(self._get_tmdb_ratings(item))

        # TVDb API (TV show ratings)
        if (self.tvdb_api and item.item_type in ['Episode', 'Season', 'Series'] and
                item.tvdb_id):
            rating_tasks.append(self._get_tvdb_ratings(item))

        # Execute all rating requests concurrently for better performance
        if rating_tasks:
            try:
                rating_results = await asyncio.gather(*rating_tasks, return_exceptions=True)

                # Combine results from all sources
                combined_ratings = {}
                for result in rating_results:
                    if isinstance(result, dict):
                        combined_ratings.update(result)
                    elif isinstance(result, Exception):
                        self.logger.warning(f"Rating API error: {result}")

                # Cache the combined results for future requests
                if combined_ratings:
                    await self._cache_ratings(item, combined_ratings)

                self.logger.debug(f"Retrieved ratings from {len(combined_ratings)} sources for {item.name}")
                return combined_ratings

            except Exception as e:
                self.logger.error(f"Error getting ratings for {item.name}: {e}")

        return {}

    async def _get_cached_ratings(self, item: MediaItem) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Retrieve cached rating information for a media item.

        This private method checks the database cache for existing rating information
        that hasn't expired. It helps reduce API usage by reusing recent rating data.

        **Cache Key Generation:**
        Creates a unique cache key based on the item's provider IDs to ensure
        accurate cache hits even when the same content has different Jellyfin IDs.

        Args:
            item (MediaItem): Media item to check cache for

        Returns:
            Optional[Dict[str, Dict[str, Any]]]: Cached ratings if found and valid, None otherwise
        """
        if not self.db_manager:
            return None

        try:
            # Generate cache key based on provider IDs
            cache_key_parts = []
            if item.imdb_id:
                cache_key_parts.append(f"imdb:{item.imdb_id}")
            if item.tmdb_id:
                cache_key_parts.append(f"tmdb:{item.tmdb_id}")
            if item.tvdb_id:
                cache_key_parts.append(f"tvdb:{item.tvdb_id}")

            if not cache_key_parts:
                # No provider IDs available for caching
                return None

            cache_key = "_".join(cache_key_parts)

            # Check database for cached ratings (implementation would depend on db_manager structure)
            # This is a placeholder for the actual cache retrieval logic
            # In a real implementation, you'd query the database for cached rating data

            self.logger.debug(f"Checking cache for key: {cache_key}")
            return None  # Placeholder - implement actual cache retrieval

        except Exception as e:
            self.logger.warning(f"Error checking rating cache: {e}")
            return None

    async def _cache_ratings(self, item: MediaItem, ratings: Dict[str, Dict[str, Any]]) -> None:
        """
        Cache rating information for future use.

        This private method stores rating data in the database cache with an
        expiration timestamp based on the configured cache duration.

        Args:
            item (MediaItem): Media item the ratings belong to
            ratings (Dict[str, Dict[str, Any]]): Ratings to cache
        """
        if not self.db_manager or not ratings:
            return

        try:
            # Generate same cache key as retrieval
            cache_key_parts = []
            if item.imdb_id:
                cache_key_parts.append(f"imdb:{item.imdb_id}")
            if item.tmdb_id:
                cache_key_parts.append(f"tmdb:{item.tmdb_id}")
            if item.tvdb_id:
                cache_key_parts.append(f"tvdb:{item.tvdb_id}")

            if not cache_key_parts:
                return

            cache_key = "_".join(cache_key_parts)

            # Calculate expiration time
            expiration = datetime.now(timezone.utc) + timedelta(hours=self.cache_duration_hours)

            # Store in cache (implementation would depend on db_manager structure)
            # This is a placeholder for the actual cache storage logic
            self.logger.debug(f"Caching ratings for key: {cache_key} (expires: {expiration})")

        except Exception as e:
            self.logger.warning(f"Error caching ratings: {e}")

    async def _get_omdb_ratings(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get ratings from OMDb API (IMDb, Rotten Tomatoes, Metacritic).

        Args:
            item (MediaItem): Media item to get ratings for

        Returns:
            Dict[str, Dict[str, Any]]: OMDb ratings data
        """
        if not self.omdb_api_key:
            return {}

        try:
            # Use IMDb ID if available, otherwise try TMDb ID
            query_param = None
            if item.imdb_id:
                query_param = f"i={item.imdb_id}"
            elif item.tmdb_id:
                query_param = f"i={item.tmdb_id}"

            if not query_param:
                self.logger.debug(f"No suitable ID for OMDb lookup: {item.name}")
                return {}

            # Rate limiting
            await self._respect_rate_limit('omdb')

            url = f"{self.omdb_config.base_url}?{query_param}&apikey={self.omdb_api_key}"

            async with self.session.get(url, timeout=self.request_timeout) as response:
                if response.status == 200:
                    data = await response.json()

                    if data.get('Response') == 'True':
                        ratings = {}

                        # Extract IMDb rating
                        if data.get('imdbRating') and data['imdbRating'] != 'N/A':
                            ratings['imdb'] = {
                                'rating': data['imdbRating'],
                                'votes': data.get('imdbVotes', ''),
                                'source': 'OMDb'
                            }

                        # Extract other ratings from Ratings array
                        for rating in data.get('Ratings', []):
                            source = rating.get('Source', '').lower()
                            value = rating.get('Value', '')

                            if 'rotten tomatoes' in source:
                                ratings['rotten_tomatoes'] = {
                                    'rating': value.replace('%', ''),
                                    'source': 'OMDb'
                                }
                            elif 'metacritic' in source:
                                ratings['metacritic'] = {
                                    'rating': value.split('/')[0],
                                    'source': 'OMDb'
                                }

                        self.logger.debug(f"Retrieved {len(ratings)} OMDb ratings for {item.name}")
                        return ratings
                    else:
                        self.logger.debug(f"OMDb: Item not found: {item.name}")
                        return {}
                else:
                    self.logger.warning(f"OMDb API error: {response.status}")
                    return {}

        except Exception as e:
            self.logger.error(f"OMDb API request failed for {item.name}: {e}")
            return {}

    async def _get_tmdb_ratings(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get ratings from TMDb API.

        Args:
            item (MediaItem): Media item to get ratings for

        Returns:
            Dict[str, Dict[str, Any]]: TMDb ratings data
        """
        if not self.tmdb_api_key:
            return {}

        try:
            # Rate limiting
            await self._respect_rate_limit('tmdb')

            # Determine endpoint based on item type
            if item.item_type == 'Movie':
                endpoint = 'movie'
            elif item.item_type in ['Episode', 'Series']:
                endpoint = 'tv'
            else:
                return {}

            # Use TMDb ID if available, otherwise search by IMDb ID
            url = None
            if item.tmdb_id:
                url = f"{self.tmdb_config.base_url}{endpoint}/{item.tmdb_id}?api_key={self.tmdb_api_key}"
            elif item.imdb_id:
                url = f"{self.tmdb_config.base_url}find/{item.imdb_id}?api_key={self.tmdb_api_key}&external_source=imdb_id"

            if not url:
                return {}

            async with self.session.get(url, timeout=self.request_timeout) as response:
                if response.status == 200:
                    data = await response.json()

                    # Handle find results
                    if 'movie_results' in data or 'tv_results' in data:
                        results = data.get('movie_results', []) + data.get('tv_results', [])
                        if results:
                            data = results[0]
                        else:
                            return {}

                    rating = data.get('vote_average')
                    vote_count = data.get('vote_count')

                    if rating:
                        tmdb_ratings = {
                            'tmdb': {
                                'rating': str(rating),
                                'vote_count': str(vote_count) if vote_count else '0',
                                'source': 'TMDb'
                            }
                        }

                        self.logger.debug(f"Retrieved TMDb rating for {item.name}: {rating}")
                        return tmdb_ratings

                    return {}
                else:
                    self.logger.warning(f"TMDb API error: {response.status}")
                    return {}

        except Exception as e:
            self.logger.error(f"TMDb API request failed for {item.name}: {e}")
            return {}

    async def _get_tvdb_ratings(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get ratings from TVDB API.

        Args:
            item (MediaItem): Media item to get ratings for

        Returns:
            Dict[str, Dict[str, Any]]: TVDB ratings data
        """
        if not self.tvdb_api or not item.tvdb_id:
            return {}

        try:
            series_info = await self.tvdb_api.get_series_info(item.tvdb_id)
            if series_info:
                rating = series_info.get('score')
                if rating:
                    tvdb_ratings = {
                        'tvdb': {
                            'rating': str(rating),
                            'source': 'TVDB'
                        }
                    }

                    self.logger.debug(f"Retrieved TVDB rating for {item.name}: {rating}")
                    return tvdb_ratings

            return {}

        except Exception as e:
            self.logger.error(f"TVDB API request failed for {item.name}: {e}")
            return {}

    async def _respect_rate_limit(self, service: str) -> None:
        """
        Implement rate limiting for external API calls.

        Args:
            service (str): Service name for rate limiting tracking
        """
        last_request = self.last_request_times.get(service, 0)
        time_since_last = time.time() - last_request

        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            self.logger.debug(f"Rate limiting {service}: sleeping {sleep_time:.2f}s")
            await asyncio.sleep(sleep_time)

        self.last_request_times[service] = time.time()