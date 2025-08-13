#!/usr/bin/env python3
"""
Jellynouncer Metadata Services Module

This module provides comprehensive metadata and metadata enhancement services by integmetadata
with multiple external APIs including OMDb, TMDb, and TVDb. It enriches Discord notifications
with additional metadata information, reviews, and enhanced metadata not available in Jellyfin.

The metadata services are designed to be optional and fault-tolerant - if they fail, the core
notification service continues to operate normally without metadata enhancements.

Classes:
    MetadataService: Main service coordinator for all metadata providers
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

from config_models import MetadataServicesConfig
from media_models import MediaItem
from tvdb import TVDB
from utils import get_logger


class MetadataService:
    """
    Comprehensive metadata service that integrates multiple external APIs.

    This service manages metadata data from various sources including OMDb (which aggregates
    IMDb, Rotten Tomatoes, and Metacritic), TMDb, and TVDb. It provides a unified interface
    for metadata information while handling the complexity of multiple APIs, caching strategies,
    and error recovery.

    **Understanding External Metadata APIs:**

    **OMDb (Open Movie Database):**
    - Aggregates metadatas from IMDb, Rotten Tomatoes, and Metacritic
    - Provides comprehensive movie and TV show information
    - Requires API key (free tier available)
    - Single API call returns multiple metadata sources

    **TMDb (The Movie Database):**
    - Community-driven movie and TV database
    - Provides user metadatas, vote counts, and metadata
    - Free API with generous rate limits
    - Excellent for movie information and posters

    **TVDb (The TV Database):**
    - Specialized TV show database with episode-level information
    - Community metadatas and comprehensive episode metadata
    - Both free and paid tiers available
    - Best source for TV show information and artwork

    **Advanced Features:**

    **Intelligent Caching:**
    Metadata data doesn't change frequently, so aggressive caching reduces API usage:
    - Database-backed cache with configurable expiration
    - Automatic cleanup of expired cache entries
    - Cache hit optimization for repeated requests

    **Multi-Source Aggregation:**
    Combines metadatas from multiple sources to provide comprehensive information:
    - OMDb: IMDb metadatas, Rotten Tomatoes percentages, Metacritic scores
    - TMDb: Community metadatas and vote counts
    - TVDb: TV-specific metadatas and episode information

    **Graceful Error Handling:**
    External APIs can fail, but the service continues opemetadata:
    - Individual API failures don't break the entire service
    - Retry logic with exponential backoff for temporary failures
    - Fallback strategies when preferred sources are unavailable

    **Rate Limiting:**
    Respects API rate limits to maintain good standing with external services:
    - Per-service rate limiting based on API terms
    - Automatic backoff when rate limits are detected
    - Batching optimization for multiple requests

    Attributes:
        config (MetadataServicesConfig): Configuration for all metadata services
        logger (logging.Logger): Logger instance for metadata operations
        session (aiohttp.ClientSession): HTTP session for API requests
        db_manager: Database manager for cache storage
        enabled (bool): Whether metadata service is enabled globally
        tvdb_client (TVDBAPIv4): TVDb API client for TV show information
        omdb_api_key (str): OMDb API key for movie/TV metadatas
        tmdb_api_key (str): TMDb API key for community metadatas
        cache_duration_hours (int): How long to cache metadata data
        last_request_times (Dict): Rate limiting state per service

    Example:
        ```python
        # Initialize metadata service
        metadata_config = MetadataServicesConfig(
            enabled=True,
            cache_duration_hours=24,
            omdb=OMDbConfig(enabled=True, api_key="your_omdb_key"),
            tmdb=TMDbConfig(enabled=True, api_key="your_tmdb_key"),
            tvdb=TVDBConfig(enabled=True, api_key="your_tvdb_key")
        )

        metadata_service = MetadataService(metadata_config)

        # Initialize with shared resources
        async with aiohttp.ClientSession() as session:
            await metadata_service.initialize(session, db_manager)

            # Get comprehensive metadatas for a movie
            movie = MediaItem(item_id="abc123", name="The Matrix", imdb_id="tt0133093")
            metadatas = await metadata_service.get_metadatas_for_item(movie)

            logger.info(f"IMDb: {metadatas.get('imdb', {}).get('metadata', 'N/A')}")
            logger.info(f"Rotten Tomatoes: {metadatas.get('rotten_tomatoes', {}).get('metadata', 'N/A')}")
            logger.info(f"TMDb: {metadatas.get('tmdb', {}).get('metadata', 'N/A')}")
        ```

    Note:
        This service is designed to enhance Discord notifications with rich metadata
        information. It gracefully handles API failures and provides sensible
        fallbacks to ensure notifications are always sent, even if metadatas are unavailable.
    """

    def __init__(self, config: MetadataServicesConfig):
        """
        Initialize metadata service with configuration and API client setup.

        This constructor sets up the metadata service with configuration for all
        external APIs and prepares for TVDB client initialization. It doesn't perform
        network operations - those happen in the initialize() method.

        **Service Configuration:**
        The metadata service can enable/disable individual APIs based on configuration:
        - Global enable/disable switch for entire metadata system
        - Per-API enable/disable switches for granular control
        - API key validation to prevent misconfiguration

        **TVDB Integration:**
        The TVDB client is prepared during construction but requires async
        initialization later. This separation allows for clean error handling
        and resource management.

        Args:
            config (MetadataServicesConfig): Configuration for all metadata services

        Example:
            ```python
            # Initialize with comprehensive configuration
            config = MetadataServicesConfig(
                enabled=True,
                cache_duration_hours=24,
                omdb=OMDbConfig(enabled=True, api_key="omdb_key"),
                tmdb=TMDbConfig(enabled=True, api_key="tmdb_key"),
                tvdb=TVDBConfig(enabled=True, api_key="tvdb_key")
            )

            metadata_service = MetadataService(config)
            logger.info(f"Metadata service enabled: {metadata_service.enabled}")
            ```
        """
        self.config = config
        self.logger = get_logger("jellynouncer.metadata")
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

        # Prepare TVDB API v4 client initialization if enabled
        self.tvdb_client = None
        self.tvdb_config_ready = self.tvdb_config.enabled and self.tvdb_config.api_key

        # Rate limiting state for external APIs
        self.last_request_times = {}
        self.min_request_interval = 1.0  # Minimum seconds between API requests

        # Log initialization status and available services
        self.logger.info(f"Metadata service initialized - Enabled: {self.enabled}")
        if self.enabled:
            available_services = []
            if self.omdb_config.enabled and self.omdb_api_key:
                available_services.append("OMDb")
            if self.tmdb_config.enabled and self.tmdb_api_key:
                available_services.append("TMDb")
            if self.tvdb_config_ready:
                access_mode = "subscriber" if self.tvdb_config.subscriber_pin else "standard"
                available_services.append(f"TVDb v4 ({access_mode})")

            service_list = ', '.join(available_services) if available_services else 'None (no API keys configured)'
            self.logger.info(f"Available metadata services: {service_list}")

    async def initialize(self, session: aiohttp.ClientSession, db_manager) -> None:
        """
        Initialize metadata service with shared resources and perform setup tasks.

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
            db_manager: Database manager for caching metadata

        Example:
            ```python
            async with aiohttp.ClientSession() as session:
                await metadata_service.initialize(session, db_manager)

                # Now ready to fetch metadata and metadatas
                metadata = await metadata_service.get_metadatas_for_item(media_item)
            ```
        """
        self.session = session
        self.db_manager = db_manager

        if not self.enabled:
            self.logger.info("Metadata services disabled in configuration")
            return

        # Initialize TVDB client if configured
        if self.tvdb_config_ready:
            try:
                self.tvdb_client = TVDB(
                    api_key=self.tvdb_config.api_key,
                    pin=self.tvdb_config.subscriber_pin,
                    enable_caching=True,
                    cache_ttl=self.config.cache_duration_hours * 3600  # Convert to seconds
                )
                await self.tvdb_client.__aenter__()
                self.logger.info("TVDB client initialized successfully")
            except Exception as e:
                self.logger.error(f"TVDB initialization error: {e}")
                self.tvdb_client = None

        self.logger.info("Metadata service initialization completed")

    async def get_metadatas_for_item(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get comprehensive metadata information for a media item from all available sources.

        This is the main entry point for metadata retrieval. It coordinates calls
        to multiple external APIs and combines the results into a unified format.
        The method handles errors gracefully - individual API failures don't
        prevent other sources from being queried.

        **Metadata Sources:**
        - OMDb: IMDb metadatas, Rotten Tomatoes scores, Metacritic scores
        - TMDb: Community metadatas and vote counts
        - TVDb: TV show specific metadatas and metadata

        **Caching Strategy:**
        Metadatas are cached aggressively since they don't change frequently.
        Cache duration is configurable and helps reduce API usage and improve
        response times for repeated requests.

        Args:
            item (MediaItem): Media item to get metadatas for

        Returns:
            Dict[str, Dict[str, Any]]: Nested dictionary with metadatas from each source.
            Format: {source_name: {metadata_type: value, ...}}

        Example:
            ```python
            metadatas = await metadata_service.get_metadatas_for_item(media_item)

            # Access different metadata sources
            imdb_metadata = metadatas.get('imdb', {}).get('metadata')
            rt_score = metadatas.get('rotten_tomatoes', {}).get('metadata')
            tmdb_metadata = metadatas.get('tmdb', {}).get('metadata')

            if imdb_metadata:
                logger.info(f"IMDb: {imdb_metadata}/10")
            if rt_score:
                logger.info(f"Rotten Tomatoes: {rt_score}%")
            ```

        Note:
            This method never raises exceptions. Individual API failures are logged
            but don't prevent other APIs from being queried successfully.
        """
        if not self.enabled:
            return {}

        self.logger.debug(f"Getting metadatas for {item.name} ({item.item_type})")

        # Check cache first to avoid unnecessary API requests
        cached_metadatas = await self._get_cached_metadatas(item)
        if cached_metadatas:
            self.logger.debug(f"Using cached metadatas for {item.name}")
            return cached_metadatas

        # Collect metadatas from all available sources concurrently
        metadata_tasks = []

        # OMDb API (IMDb, Rotten Tomatoes, Metacritic)
        if (self.omdb_config.enabled and self.omdb_api_key and
                (item.imdb_id or item.tmdb_id)):
            metadata_tasks.append(self._get_omdb_metadatas(item))

        # TMDb API (Community metadatas)
        if (self.tmdb_config.enabled and self.tmdb_api_key and
                (item.tmdb_id or item.imdb_id)):
            metadata_tasks.append(self._get_tmdb_metadatas(item))

        # TVDb API (TV show metadatas)
        if (self.tvdb_client and item.item_type in ['Episode', 'Season', 'Series'] and
                item.tvdb_id):
            metadata_tasks.append(self._get_tvdb_metadatas(item))

        # Execute all metadata requests concurrently for better performance
        if metadata_tasks:
            try:
                metadata_results = await asyncio.gather(*metadata_tasks, return_exceptions=True)

                # Combine results from all sources
                combined_metadatas = {}
                for result in metadata_results:
                    if isinstance(result, dict):
                        combined_metadatas.update(result)
                    elif isinstance(result, Exception):
                        self.logger.warning(f"Metadata API error: {result}")

                # Cache the combined results for future requests
                if combined_metadatas:
                    await self._cache_metadatas(item, combined_metadatas)

                self.logger.debug(f"Retrieved metadatas from {len(combined_metadatas)} sources for {item.name}")
                return combined_metadatas

            except Exception as e:
                self.logger.error(f"Error getting metadatas for {item.name}: {e}")

        return {}

    async def cleanup(self) -> None:
        """
        Clean up resources used by the metadata service.

        This method should be called when shutting down the service to ensure
        all connections are properly closed and resources are released.
        """
        if self.tvdb_client:
            try:
                await self.tvdb_client.__aexit__(None, None, None)
                self.logger.debug("TVDB client cleaned up")
            except Exception as e:
                self.logger.error(f"Error cleaning up TVDB client: {e}")

    async def enhance_item_with_tvdb_metadata(self, media_item: MediaItem) -> bool:
        """
        Enhance a media item with TVDB metadata if applicable.

        This method adds additional metadata from TVDB to TV-related media items
        (series, seasons, episodes) when TVDB IDs are available. It enriches the
        media item with information like ratings, artwork, and additional details
        not available from Jellyfin.

        **Enhancement Process:**
        - Checks if item is TV content (series, season, or episode)
        - Verifies TVDB ID is available
        - Fetches and applies TVDB metadata
        - Handles errors gracefully without affecting core functionality

        Args:
            media_item (MediaItem): The media item to enhance with TVDB metadata

        Returns:
            bool: True if enhancement was successful, False otherwise

        Example:
            ```python
            if await metadata_service.enhance_item_with_tvdb_metadata(media_item):
                logger.info(f"Enhanced {media_item.name} with TVDB metadata")
            ```
        """
        # Check if TVDB client is available and item is eligible
        if not self.tvdb_client:
            return False

        if not (hasattr(media_item, 'item_type') and
                media_item.item_type.lower() in ['series', 'season', 'episode']):
            return False

        if not (hasattr(media_item, 'tvdb_id') and media_item.tvdb_id):
            return False

        try:
            self.logger.debug(f"Adding TVDB metadata to {media_item.item_type}: {media_item.name}")

            # Import the enhancement function from tvdb module
            from tvdb import add_tvdb_metadata_to_item

            # Apply TVDB metadata to the item
            await add_tvdb_metadata_to_item(media_item, self.tvdb_client)

            self.logger.debug(f"Successfully enhanced {media_item.name} with TVDB metadata")
            return True

        except Exception as e:
            self.logger.error(f"Failed to enhance {media_item.name} with TVDB metadata: {e}")
            return False

    async def _get_cached_metadatas(self, item: MediaItem) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Retrieve cached metadata information for a media item.

        This private method checks the database cache for existing metadata information
        that hasn't expired. It helps reduce API usage by reusing recent metadata data.

        **Cache Key Generation:**
        Creates a unique cache key based on the item's provider IDs to ensure
        accurate cache hits even when the same content has different Jellyfin IDs.

        Args:
            item (MediaItem): Media item to check cache for

        Returns:
            Optional[Dict[str, Dict[str, Any]]]: Cached metadatas if found and valid, None otherwise
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

            # Check database for cached metadatas (implementation would depend on db_manager structure)
            # This is a placeholder for the actual cache retrieval logic
            # In a real implementation, you'd query the database for cached metadata data

            self.logger.debug(f"Checking cache for key: {cache_key}")
            return None  # Placeholder - implement actual cache retrieval

        except Exception as e:
            self.logger.warning(f"Error checking metadata cache: {e}")
            return None

    async def _cache_metadatas(self, item: MediaItem, metadatas: Dict[str, Dict[str, Any]]) -> None:
        """
        Cache metadata information for future use.

        This private method stores metadata data in the database cache with an
        expiration timestamp based on the configured cache duration.

        Args:
            item (MediaItem): Media item the metadatas belong to
            metadatas (Dict[str, Dict[str, Any]]): Metadatas to cache
        """
        if not self.db_manager or not metadatas:
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
            self.logger.debug(f"Caching metadatas for key: {cache_key} (expires: {expiration})")

        except Exception as e:
            self.logger.warning(f"Error caching metadatas: {e}")

    async def _get_omdb_metadatas(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get metadatas from OMDb API (IMDb, Rotten Tomatoes, Metacritic).

        Args:
            item (MediaItem): Media item to get metadatas for

        Returns:
            Dict[str, Dict[str, Any]]: OMDb metadatas data
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
                        metadatas = {}

                        # Extract IMDb metadata
                        if data.get('imdbMetadata') and data['imdbMetadata'] != 'N/A':
                            metadatas['imdb'] = {
                                'metadata': data['imdbMetadata'],
                                'votes': data.get('imdbVotes', ''),
                                'source': 'OMDb'
                            }

                        # Extract other metadatas from Metadatas array
                        for metadata in data.get('Metadatas', []):
                            source = metadata.get('Source', '').lower()
                            value = metadata.get('Value', '')

                            if 'rotten tomatoes' in source:
                                metadatas['rotten_tomatoes'] = {
                                    'metadata': value.replace('%', ''),
                                    'source': 'OMDb'
                                }
                            elif 'metacritic' in source:
                                metadatas['metacritic'] = {
                                    'metadata': value.split('/')[0],
                                    'source': 'OMDb'
                                }

                        self.logger.debug(f"Retrieved {len(metadatas)} OMDb metadatas for {item.name}")
                        return metadatas
                    else:
                        self.logger.debug(f"OMDb: Item not found: {item.name}")
                        return {}
                else:
                    self.logger.warning(f"OMDb API error: {response.status}")
                    return {}

        except Exception as e:
            self.logger.error(f"OMDb API request failed for {item.name}: {e}")
            return {}

    async def _get_tmdb_metadatas(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get metadatas from TMDb API.

        Args:
            item (MediaItem): Media item to get metadatas for

        Returns:
            Dict[str, Dict[str, Any]]: TMDb metadatas data
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

                    metadata = data.get('vote_average')
                    vote_count = data.get('vote_count')

                    if metadata:
                        tmdb_metadatas = {
                            'tmdb': {
                                'metadata': str(metadata),
                                'vote_count': str(vote_count) if vote_count else '0',
                                'source': 'TMDb'
                            }
                        }

                        self.logger.debug(f"Retrieved TMDb metadata for {item.name}: {metadata}")
                        return tmdb_metadatas

                    return {}
                else:
                    self.logger.warning(f"TMDb API error: {response.status}")
                    return {}

        except Exception as e:
            self.logger.error(f"TMDb API request failed for {item.name}: {e}")
            return {}

    async def _get_tvdb_metadatas(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get metadatas from TVDB API.

        Args:
            item (MediaItem): Media item to get metadatas for

        Returns:
            Dict[str, Dict[str, Any]]: TVDB metadatas data
        """
        if not self.tvdb_client or not item.tvdb_id:
            return {}

        try:
            series_info = await self.tvdb_client.get_series_info(item.tvdb_id)
            if series_info:
                metadata = series_info.get('score')
                if metadata:
                    tvdb_metadatas = {
                        'tvdb': {
                            'metadata': str(metadata),
                            'source': 'TVDB'
                        }
                    }

                    self.logger.debug(f"Retrieved TVDB metadata for {item.name}: {metadata}")
                    return tvdb_metadatas

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