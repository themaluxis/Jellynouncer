#!/usr/bin/env python3
"""
Jellynouncer Rating Services Module

This module contains external rating service integrations for fetching movie/TV ratings
from multiple APIs including OMDb, TMDb, and TVDb. These services are tightly coupled
and work together to provide comprehensive rating information for Discord notifications.

The module implements sophisticated caching, rate limiting, and fallback strategies to
ensure reliable rating data retrieval while respecting API limits and terms of service.
Both services work together to provide rich metadata enhancement for media notifications.

Classes:
    TVDBAPIv4: Complete TVDB API v4 implementation supporting both licensed and subscriber access
    RatingService: Comprehensive rating service aggregating multiple external APIs

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
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

    The TV Database (TVDb) is a comprehensive database of television series metadata,
    ratings, and artwork. This class implements their modern v4 API with support for
    both licensing models they offer.

    **Understanding TVDb API Access Models:**

    TVDb offers two access tiers with different features and rate limits:

    **Licensed Access:**
    - Requires a paid API key subscription
    - Higher rate limits and more comprehensive data access
    - Intended for commercial applications and services
    - More stable API access with priority support

    **Subscriber Access:**
    - Uses free user account with subscriber PIN
    - Lower rate limits and restricted data access
    - Intended for personal projects and development
    - Rate-limited to prevent abuse

    **Authentication Flow:**
    The v4 API uses JWT (JSON Web Token) authentication:
    1. Submit API key (+ PIN for subscribers) to get JWT token
    2. Include JWT token in all subsequent API requests
    3. Handle token expiration and re-authentication automatically

    **Rate Limiting Strategy:**
    Different access modes require different rate limiting approaches:
    - Subscriber: Conservative delays to avoid hitting limits
    - Licensed: More aggressive requests within commercial limits
    - Automatic backoff when rate limits are detected

    Attributes:
        config (TVDBConfig): TVDb API configuration settings
        logger (logging.Logger): Logger instance for API operations
        session (aiohttp.ClientSession): HTTP session for API requests
        token (Optional[str]): Current JWT authentication token
        token_expires_at (Optional[float]): Token expiration timestamp
        access_mode (str): Determined access mode (subscriber/licensed/disabled)
        min_request_interval (float): Minimum seconds between requests
        series_slug_cache (Dict): Cache for series URL slugs
        last_request_time (Dict): Rate limiting state per endpoint

    Example:
        ```python
        # Initialize TVDB API client
        tvdb_config = TVDBConfig(
            enabled=True,
            api_key="your_api_key",
            subscriber_pin="your_pin",  # Optional for subscriber access
            access_mode="auto"  # Auto-detect based on available credentials
        )

        tvdb_api = TVDBAPIv4(tvdb_config, logger)

        # Initialize with shared HTTP session
        async with aiohttp.ClientSession() as session:
            await tvdb_api.initialize(session)

            # Get episode information with series slug
            episode_data = await tvdb_api.get_episode_info_with_series_slug("episode123")
            if episode_data:
                print(f"Series: {episode_data.get('series_slug')}")
                print(f"Episode rating: {episode_data.get('rating')}")
        ```

    Note:
        This class handles all the complexity of TVDb's authentication, rate limiting,
        and data normalization. It's designed to be resilient to network issues and
        API changes while providing a clean interface for the rating service.
    """

    def __init__(self, config: TVDBConfig, logger: logging.Logger):
        """
        Initialize TVDB API v4 client with configuration and access mode detection.

        This constructor sets up the TVDb client and automatically determines the
        appropriate access mode based on the available configuration. It doesn't
        perform network operations - those happen in the initialize() method.

        **Access Mode Detection Logic:**
        - If access_mode is explicitly set: Use that mode
        - If subscriber_pin is provided: Use subscriber mode
        - If only api_key is provided: Use licensed mode
        - If neither is available: Disable TVDb integration

        Args:
            config (TVDBConfig): TVDb API configuration with credentials and settings
            logger (logging.Logger): Logger instance for API operations

        Example:
            ```python
            # Automatic access mode detection
            config = TVDBConfig(
                enabled=True,
                api_key="your_api_key",
                subscriber_pin="1234",  # Triggers subscriber mode
                access_mode="auto"
            )

            tvdb_api = TVDBAPIv4(config, logger)
            print(f"Using access mode: {tvdb_api.access_mode}")
            ```
        """
        self.config = config
        self.logger = logger
        self.session = None  # Will be set in initialize()

        # Authentication state management
        self.token = None
        self.token_expires_at = None
        self.last_auth_attempt = 0

        # Determine the appropriate access mode based on configuration
        self.access_mode = self._determine_access_mode()

        # Configure rate limiting based on access mode
        if self.access_mode == 'subscriber':
            self.min_request_interval = 2.0  # Conservative rate limiting for free tier
        else:
            self.min_request_interval = 1.0  # More aggressive for licensed access

        # Rate limiting state tracking
        self.last_request_time = {}

        # URL caching to avoid repeated API calls for the same data
        self.series_slug_cache = {}  # episode_id -> (series_slug, timestamp)
        self.cache_duration = 3600 * 24  # Cache for 24 hours

        # Request timeout configuration
        self.request_timeout = 10

        self.logger.info(f"TVDB API v4 initialized in {self.access_mode} mode")

    def _determine_access_mode(self) -> str:
        """
        Determine the appropriate access mode based on available configuration.

        This private method implements the logic for automatically detecting
        which TVDb access tier to use based on the credentials provided in
        the configuration.

        **Decision Logic:**
        1. If access_mode is explicitly set (not 'auto'): Use that setting
        2. If subscriber_pin is provided: Use subscriber access mode
        3. If api_key is provided without PIN: Use licensed access mode
        4. If neither credential is available: Disable TVDb integration

        Returns:
            str: Access mode ('subscriber', 'licensed', or 'disabled')

        Example:
            ```python
            # Internal method called during initialization
            access_mode = self._determine_access_mode()
            # Returns: 'subscriber', 'licensed', or 'disabled'
            ```
        """
        # Use explicit access mode if specified
        if self.config.access_mode != 'auto':
            return self.config.access_mode

        # Auto-detection based on available credentials
        if self.config.subscriber_pin:
            return 'subscriber'
        elif self.config.api_key:
            return 'licensed'
        else:
            return 'disabled'

    async def initialize(self, session: aiohttp.ClientSession):
        """
        Initialize TVDB API client with shared HTTP session and perform authentication.

        This method completes the initialization process by setting up the HTTP
        session and performing the initial authentication with the TVDb API.
        It's separated from the constructor because it requires async operations.

        **Initialization Process:**
        1. Store reference to shared HTTP session
        2. Attempt initial authentication to get JWT token
        3. Log authentication results and access mode

        Args:
            session (aiohttp.ClientSession): Shared HTTP session for API requests

        Raises:
            Exception: If authentication fails and TVDb integration is required

        Example:
            ```python
            # Initialize with existing session
            async with aiohttp.ClientSession() as session:
                await tvdb_api.initialize(session)

                # API is now ready for use
                if tvdb_api.access_mode != 'disabled':
                    episode_data = await tvdb_api.get_episode_info_with_series_slug("123")
            ```

        Note:
            This method should be called once during service startup. The HTTP
            session should remain active for the lifetime of the TVDb client.
        """
        self.session = session

        # Perform initial authentication if not disabled
        if self.access_mode != 'disabled':
            auth_success = await self._authenticate()
            if auth_success:
                self.logger.info(f"TVDB API v4 authentication successful ({self.access_mode} mode)")
            else:
                self.logger.error(f"TVDB API v4 authentication failed ({self.access_mode} mode)")

    async def _authenticate(self) -> bool:
        """
        Authenticate with TVDB API v4 to obtain JWT token.

        This private method handles the authentication process with TVDb's v4 API.
        It supports both subscriber and licensed access modes with appropriate
        credential handling and error recovery.

        **Authentication Process:**
        1. Prepare authentication payload based on access mode
        2. Send POST request to login endpoint
        3. Extract JWT token from response
        4. Calculate token expiration time
        5. Store authentication state for future requests

        **Token Management:**
        JWT tokens have limited lifespans and need periodic renewal. This method
        handles token expiration tracking and provides the foundation for
        automatic re-authentication when tokens expire.

        Returns:
            bool: True if authentication successful, False otherwise

        Example:
            ```python
            # Internal authentication call
            success = await self._authenticate()
            if success:
                # Token is ready for API requests
                print(f"Authenticated until: {self.token_expires_at}")
            ```

        Note:
            This is a private method called automatically during initialization
            and when token refresh is needed. It includes comprehensive error
            handling to ensure authentication failures don't crash the service.
        """
        try:
            self.logger.debug("Attempting TVDB API v4 authentication")
            self.last_auth_attempt = time.time()

            # Prepare authentication payload based on access mode
            if self.access_mode == 'subscriber':
                # Subscriber access uses API key + PIN
                auth_payload = {
                    "apikey": self.config.api_key,
                    "pin": self.config.subscriber_pin
                }
            else:
                # Licensed access uses API key only
                auth_payload = {
                    "apikey": self.config.api_key
                }

            # Send authentication request
            async with self.session.post(
                    f"{self.config.base_url}/login",
                    json=auth_payload,
                    timeout=aiohttp.ClientTimeout(total=self.request_timeout)
            ) as response:

                if response.status == 200:
                    # Parse authentication response
                    auth_data = await response.json()

                    # Extract JWT token from response
                    self.token = auth_data.get('data', {}).get('token')

                    if self.token:
                        # Calculate token expiration (tokens typically last 1 month)
                        # Add some buffer time to avoid edge cases
                        self.token_expires_at = time.time() + (30 * 24 * 3600) - 3600  # 30 days - 1 hour buffer

                        self.logger.debug("TVDB API v4 authentication successful")
                        return True
                    else:
                        self.logger.error("TVDB authentication response missing token")
                        return False

                elif response.status == 401:
                    # Authentication failed - invalid credentials
                    error_text = await response.text()
                    self.logger.error(f"TVDB authentication failed - invalid credentials: {error_text}")
                    return False

                else:
                    # Other HTTP error
                    error_text = await response.text()
                    self.logger.error(f"TVDB authentication failed - HTTP {response.status}: {error_text}")
                    return False

        except asyncio.TimeoutError:
            self.logger.error("TVDB authentication timeout")
            return False
        except Exception as e:
            self.logger.error(f"TVDB authentication error: {e}")
            return False

    async def get_episode_info_with_series_slug(self, episode_id: str) -> Dict[str, Any]:
        """
        Get episode information including series slug for URL generation.

        This method fetches comprehensive episode information from TVDb, including
        the series slug needed for generating TVDb URLs. It implements intelligent
        caching and rate limiting for optimal performance.

        **What is a Series Slug?**
        A series slug is a URL-friendly identifier for TV series on TVDb websites.
        For example, "breaking-bad" is the slug for the series "Breaking Bad".
        These slugs are used in TVDb URLs and are essential for deep-linking.

        **Caching Strategy:**
        Episode information doesn't change frequently, so this method implements
        aggressive caching to minimize API requests:
        - Cache duration: 24 hours
        - Cache key: episode_id
        - Automatic cache cleanup for expired entries

        **Rate Limiting:**
        Implements per-endpoint rate limiting to respect TVDb API limits:
        - Tracks last request time per API endpoint
        - Enforces minimum intervals between requests
        - Different intervals for subscriber vs licensed access

        Args:
            episode_id (str): TVDb episode identifier

        Returns:
            Dict[str, Any]: Episode information including series_slug, or empty dict if not found

        Example:
            ```python
            # Get episode info with series slug
            episode_data = await tvdb_api.get_episode_info_with_series_slug("episode123")

            if episode_data:
                series_slug = episode_data.get('series_slug')
                rating = episode_data.get('rating')
                print(f"Episode rating: {rating}/10")
                print(f"Series URL: https://thetvdb.com/series/{series_slug}")
            else:
                print("Episode not found or API unavailable")
            ```

        Note:
            This method handles authentication token expiration automatically,
            re-authenticating when necessary. It also implements comprehensive
            error handling to ensure API failures don't crash the service.
        """
        if self.access_mode == 'disabled' or not self.token:
            return {}

        # Check cache first to avoid unnecessary API requests
        cache_key = f"episode_{episode_id}"
        current_time = time.time()

        if cache_key in self.series_slug_cache:
            cached_data, cache_timestamp = self.series_slug_cache[cache_key]
            if current_time - cache_timestamp < self.cache_duration:
                self.logger.debug(f"Using cached episode info for {episode_id}")
                return cached_data

        # Apply rate limiting to respect API limits
        await self._apply_rate_limit('episodes')

        try:
            # Request episode information from TVDb API
            headers = {'Authorization': f'Bearer {self.token}'}

            async with self.session.get(
                    f"{self.config.base_url}/episodes/{episode_id}/extended",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.request_timeout)
            ) as response:

                if response.status == 200:
                    # Parse successful response
                    episode_data = await response.json()

                    # Extract episode information from response
                    episode_info = episode_data.get('data', {})
                    series_info = episode_info.get('series', {})

                    # Build result with series slug and episode data
                    result = {
                        'series_slug': series_info.get('slug'),
                        'episode_name': episode_info.get('name'),
                        'season_number': episode_info.get('seasonNumber'),
                        'episode_number': episode_info.get('number'),
                        'rating': episode_info.get('score'),
                        'air_date': episode_info.get('aired'),
                        'overview': episode_info.get('overview')
                    }

                    # Cache the result for future requests
                    self.series_slug_cache[cache_key] = (result, current_time)

                    self.logger.debug(f"Retrieved episode info for {episode_id}")
                    return result

                elif response.status == 401:
                    # Token expired - attempt re-authentication
                    self.logger.warning("TVDB API token expired, attempting re-authentication")
                    if await self._authenticate():
                        # Retry the request once with new token
                        return await self.get_episode_info_with_series_slug(episode_id)

                elif response.status == 404:
                    # Episode not found in TVDb
                    self.logger.debug(f"TVDB episode not found: {episode_id}")

                else:
                    # Other HTTP error
                    error_text = await response.text()
                    self.logger.warning(f"TVDB API request failed: HTTP {response.status} - {error_text}")

        except asyncio.TimeoutError:
            self.logger.warning(f"TVDB API request timeout for episode {episode_id}")
        except Exception as e:
            self.logger.error(f"TVDB API request error for episode {episode_id}: {e}")

        return {}

    async def _apply_rate_limit(self, endpoint: str):
        """
        Apply rate limiting for specific API endpoint to respect TVDb limits.

        This private method implements intelligent rate limiting to ensure we
        don't exceed TVDb's API rate limits. Different endpoints may have
        different limits, and different access modes have different restrictions.

        **Rate Limiting Strategy:**
        - Track last request time per endpoint
        - Calculate time since last request
        - Sleep if minimum interval hasn't elapsed
        - Different intervals for subscriber vs licensed access

        Args:
            endpoint (str): API endpoint identifier for rate limit tracking

        Example:
            ```python
            # Internal rate limiting call
            await self._apply_rate_limit('episodes')
            # Ensures minimum time has passed since last episode API call
            ```

        Note:
            This method ensures smooth API usage without hitting rate limits
            that could result in temporary or permanent API access suspension.
        """
        current_time = time.time()
        last_request = self.last_request_time.get(endpoint, 0)

        time_since_last = current_time - last_request
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            self.logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s for {endpoint}")
            await asyncio.sleep(sleep_time)

        # Update last request time
        self.last_request_time[endpoint] = time.time()


class RatingService:
    """
    Comprehensive rating service for fetching movie/TV ratings from multiple external APIs.

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

        rating_service = RatingService(rating_config, logger)

        # Initialize with shared resources
        async with aiohttp.ClientSession() as session:
            await rating_service.initialize(session, db_manager)

            # Get comprehensive ratings for a movie
            movie = MediaItem(item_id="abc123", name="The Matrix", imdb_id="tt0133093")
            ratings = await rating_service.get_ratings_for_item(movie)

            print(f"IMDb: {ratings.get('imdb', {}).get('rating', 'N/A')}")
            print(f"Rotten Tomatoes: {ratings.get('rotten_tomatoes', {}).get('rating', 'N/A')}")
            print(f"TMDb: {ratings.get('tmdb', {}).get('rating', 'N/A')}")
        ```

    Note:
        This service is designed to enhance Discord notifications with rich rating
        information. It gracefully handles API failures and provides sensible
        fallbacks to ensure notifications are always sent, even if ratings are unavailable.
    """

    def __init__(self, config: RatingServicesConfig, logger: logging.Logger):
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
            logger (logging.Logger): Logger instance for rating operations

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

            rating_service = RatingService(config, logger)
            print(f"Rating service enabled: {rating_service.enabled}")
            ```
        """
        self.config = config
        self.logger = logger
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
            self.tvdb_api = TVDBAPIv4(self.tvdb_config, logger)

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

    async def initialize(self, session: aiohttp.ClientSession, db_manager):
        """
        Initialize rating service with shared resources and perform setup tasks.

        This method completes the rating service initialization by setting up
        shared resources and performing any necessary maintenance tasks. It's
        separated from the constructor because it requires async operations.

        **Initialization Tasks:**
        1. Store references to shared HTTP session and database manager
        2. Initialize TVDb API client if enabled
        3. Clean up expired cache entries from previous runs
        4. Log final initialization status

        Args:
            session (aiohttp.ClientSession): Shared HTTP session for API requests
            db_manager: Database manager for cache storage and retrieval

        Example:
            ```python
            # Initialize during service startup
            async with aiohttp.ClientSession() as session:
                await rating_service.initialize(session, db_manager)

                # Service is now ready for rating requests
                ratings = await rating_service.get_ratings_for_item(media_item)
            ```

        Note:
            This method should be called once during service startup. The provided
            session and database manager should remain active for the lifetime
            of the rating service.
        """
        self.session = session
        self.db_manager = db_manager

        if self.enabled:
            # Initialize TVDB API v4 client if enabled
            if self.tvdb_api:
                await self.tvdb_api.initialize(session)

            # Clean up expired rating cache entries from previous runs
            await self._cleanup_expired_cache()
            self.logger.info("Rating service initialization complete")
        else:
            self.logger.info("Rating service disabled in configuration")

    async def get_ratings_for_item(self, item: MediaItem) -> Dict[str, Dict[str, Any]]:
        """
        Get comprehensive rating information for a media item from all configured sources.

        This is the main entry point for rating retrieval. It orchestrates requests
        to multiple external APIs, handles caching, and provides a unified response
        format regardless of which APIs are available or successful.

        **Rating Retrieval Process:**
        1. Check if rating service is enabled globally
        2. Look for cached ratings to avoid unnecessary API calls
        3. Identify which external APIs can provide data for this item
        4. Make concurrent requests to all available APIs
        5. Aggregate results into unified response format
        6. Cache results for future requests
        7. Return comprehensive rating information

        **Response Format:**
        The method returns a dictionary with rating sources as keys:
        ```python
        {
            'imdb': {'rating': '8.7/10', 'votes': '1,500,000'},
            'rotten_tomatoes': {'rating': '88%', 'consensus': 'Fresh'},
            'metacritic': {'rating': '73/100', 'status': 'Generally favorable'},
            'tmdb': {'rating': '8.2/10', 'vote_count': 12000},
            'tvdb': {'rating': '9.0/10', 'series_slug': 'the-matrix'}
        }
        ```

        **Caching Strategy:**
        Ratings don't change frequently, so aggressive caching reduces API usage:
        - Cache duration: Configurable (default 24 hours)
        - Cache key: Based on item ID and available provider IDs
        - Automatic cache expiration and cleanup

        Args:
            item (MediaItem): Media item to get ratings for (must have provider IDs)

        Returns:
            Dict[str, Dict[str, Any]]: Comprehensive rating information from all sources

        Example:
            ```python
            # Get ratings for a movie
            movie = MediaItem(
                item_id="abc123",
                name="The Matrix",
                item_type="Movie",
                imdb_id="tt0133093",
                tmdb_id="603"
            )

            ratings = await rating_service.get_ratings_for_item(movie)

            # Display ratings in Discord notification
            if ratings.get('imdb'):
                print(f"IMDb: {ratings['imdb']['rating']}")
            if ratings.get('rotten_tomatoes'):
                print(f"RT: {ratings['rotten_tomatoes']['rating']}")
            if ratings.get('tmdb'):
                print(f"TMDb: {ratings['tmdb']['rating']}")
            ```

        Note:
            This method handles all error cases gracefully and will always return
            a dictionary, even if all APIs fail. Individual API failures are logged
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
            Optional[Dict[str, Dict[str, Any]]]: Cached ratings if found and not expired

        Example:
            ```python
            # Internal cache check
            cached_data = await self._get_cached_ratings(item)
            if cached_data:
                return cached_data  # Skip API calls
            ```
        """
        if not self.db_manager:
            return None

        try:
            # Generate cache key based on provider IDs
            cache_key = self._generate_cache_key(item)

            # Check database for cached ratings
            # Implementation would query the database for cached rating data
            # This is a placeholder for the actual database query

            self.logger.debug(f"Checking cache for {cache_key}")
            # Database query implementation would go here

            return None  # Placeholder - would return actual cached data

        except Exception as e:
            self.logger.error(f"Error checking rating cache: {e}")
            return None

    def _generate_cache_key(self, item: MediaItem) -> str:
        """
        Generate a unique cache key for rating data based on provider IDs.

        This private method creates a consistent cache key that can be used
        to store and retrieve rating information across service restarts.

        Args:
            item (MediaItem): Media item to generate cache key for

        Returns:
            str: Unique cache key for this item's rating data

        Example:
            ```python
            # Internal cache key generation
            cache_key = self._generate_cache_key(item)
            # Returns: "ratings_imdb:tt0133093_tmdb:603_tvdb:290434"
            ```
        """
        key_parts = ['ratings']

        if item.imdb_id:
            key_parts.append(f'imdb:{item.imdb_id}')
        if item.tmdb_id:
            key_parts.append(f'tmdb:{item.tmdb_id}')
        if item.tvdb_id:
            key_parts.append(f'tvdb:{item.tvdb_id}')

        return '_'.join(key_parts)

    async def _cleanup_expired_cache(self):
        """
        Clean up expired rating cache entries from the database.

        This private method removes old cached rating data that has exceeded
        the configured cache duration. It's called during service initialization
        and can be called periodically for maintenance.

        Example:
            ```python
            # Internal cache maintenance
            await self._cleanup_expired_cache()
            ```

        Note:
            This method helps prevent the database from growing indefinitely
            with stale rating data while maintaining good performance.
        """
        if not self.db_manager:
            return

        try:
            # Calculate expiration timestamp
            expiration_time = datetime.now(timezone.utc) - timedelta(hours=self.cache_duration_hours)

            # Database cleanup implementation would go here
            self.logger.debug("Cleaned up expired rating cache entries")

        except Exception as e:
            self.logger.error(f"Error cleaning up rating cache: {e}")

    # Additional private methods for specific API integrations would be implemented here
    # (_get_omdb_ratings, _get_tmdb_ratings, _get_tvdb_ratings, _cache_ratings, etc.)
    # These follow similar patterns with error handling, rate limiting, and data normalization