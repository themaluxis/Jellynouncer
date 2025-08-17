#!/usr/bin/env python3
"""
Jellynouncer Metadata Services Module

This module provides comprehensive metadata and rating enhancement services by integrating
with multiple external APIs including OMDb, TMDb, and TVDb. It enriches Discord notifications
with additional rating information, reviews, and enhanced metadata not available in Jellyfin.

The metadata services are designed to be optional and fault-tolerant - if they fail, the core
notification service continues to operate normally without rating enhancements.

Classes:
    MetadataService: Main service coordinator for all metadata providers

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import asyncio
import logging
from functools import lru_cache
from typing import Optional, Dict, Any
import hashlib
import json

import aiohttp

from config_models import MetadataServicesConfig
from media_models import MediaItem
from metadata_tvdb import TVDB
from metadata_omdb import OMDbAPI
from metadata_tmdb import TMDbAPI
from utils import get_logger


class MetadataService:
    """
    Comprehensive metadata service that integrates multiple external APIs.

    This service manages rating data from various sources including OMDb (which aggregates
    IMDb, Rotten Tomatoes, and Metacritic), TMDb, and TVDb. It provides a unified interface
    for rating information while handling the complexity of multiple APIs, caching strategies,
    and error recovery.

    **Understanding External Metadata APIs:**

    **OMDb (Open Movie Database):**
    - Aggregates ratings from IMDb, Rotten Tomatoes, and Metacritic
    - Provides comprehensive movie and TV show information
    - Requires API key (free tier available with 1000 requests/day)
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

    Attributes:
        config (MetadataServicesConfig): Configuration for all metadata services
        logger (logging.Logger): Logger instance for metadata operations
        session (aiohttp.ClientSession): HTTP session for API requests
        db_manager: Database manager for cache storage
        enabled (bool): Whether metadata service is enabled globally
        tvdb_client (TVDB): TVDb API client for TV show information
        omdb_client (OMDbAPI): OMDb API client for ratings and metadata
        tmdb_client (TMDbAPI): TMDb API client for ratings and metadata
    """

    def __init__(self, config: MetadataServicesConfig):
        """
        Initialize metadata service with configuration and API client setup.

        Args:
            config (MetadataServicesConfig): Configuration for all metadata services
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

        # Initialize OMDb client if configured
        self.omdb_client = None
        if self.omdb_config.enabled and self.omdb_config.api_key:
            self.omdb_client = OMDbAPI(
                api_key=self.omdb_config.api_key,
                enabled=True
            )
            self.logger.info("OMDb client initialized")

        # Initialize TMDb client if configured
        self.tmdb_client = None
        if self.tmdb_config.enabled and self.tmdb_config.api_key:
            self.tmdb_client = TMDbAPI(
                api_key=self.tmdb_config.api_key,
                enabled=True
            )
            self.logger.info("TMDb client initialized")

        # Prepare TVDB API v4 client initialization if enabled
        self.tvdb_client = None
        self.tvdb_config_ready = self.tvdb_config.enabled and self.tvdb_config.api_key

        # Rate limiting state for external APIs
        self.last_request_times = {}
        self.min_request_interval = 1.0  # Minimum seconds between API requests
        
        # LRU cache for metadata results
        self._metadata_cache = {}
        self._cache_max_size = 1000
        
        # Semaphores for API rate limiting
        self._api_semaphore = None  # Will be created during initialize
        self._api_semaphore_limit = 3  # Max concurrent API requests

    async def initialize(self, session: aiohttp.ClientSession, db_manager) -> None:
        """
        Initialize metadata service with shared resources and perform setup tasks.

        Args:
            session (aiohttp.ClientSession): Shared HTTP session for all API requests
            db_manager: Database manager for caching metadata
        """
        self.session = session
        self.db_manager = db_manager
        
        # Initialize semaphore for API rate limiting
        self._api_semaphore = asyncio.Semaphore(self._api_semaphore_limit)

        if not self.enabled:
            self.logger.info("Metadata services disabled in configuration")
            return

        # Initialize TVDB client if configured
        if self.tvdb_config_ready:
            try:
                self.tvdb_client = TVDB(
                    api_key=self.tvdb_config.api_key,
                    cache_duration_hours=self.cache_duration_hours
                )
                # TVDB needs its own session initialization
                await self.tvdb_client.initialize(session)
                self.logger.info("TVDb client initialized and authenticated")
            except Exception as e:
                self.logger.error(f"Failed to initialize TVDb client: {e}")
                self.tvdb_client = None

        # Pass session to OMDb and TMDb clients
        if self.omdb_client:
            self.omdb_client.session = session
            self.logger.debug("OMDb client assigned shared session")

        if self.tmdb_client:
            self.tmdb_client.session = session
            self.logger.debug("TMDb client assigned shared session")

        # Report available services
        available_services = []
        if self.omdb_client:
            available_services.append("OMDb")
        if self.tmdb_client:
            available_services.append("TMDb")
        if self.tvdb_client:
            available_services.append("TVDb")

            service_list = ', '.join(available_services) if available_services else 'None (no API keys configured)'
            self.logger.info(f"Available metadata services: {service_list}")

    async def enrich_media_item(self, item: MediaItem) -> Dict[str, Any]:
        """
        Enrich a media item with metadata from all available sources.

        This is the main entry point for adding metadata to a media item before
        template rendering. It returns metadata from OMDb, TMDb, and TVDb as a
        dictionary that can be passed to the Discord notification service.

        The returned dictionary contains:
        - omdb: OMDb metadata including all ratings
        - tvdb: TVDb metadata for TV shows
        - tmdb: TMDb community ratings (future)
        - ratings: Simplified ratings dictionary for easy access

        Args:
            item (MediaItem): Media item to enrich with metadata

        Returns:
            Dict[str, Any]: Dictionary containing metadata from all sources
        """
        if not self.enabled:
            return {}

        # Initialize metadata containers
        metadata = {
            'omdb': None,
            'tvdb': None,
            'tmdb': None,
            'ratings': {}
        }

        # Fetch metadata from all available sources concurrently with semaphore control
        tasks = []

        async def rate_limited_fetch(fetch_func, item):
            """Wrap fetch function with semaphore for rate limiting."""
            async with self._api_semaphore:
                return await fetch_func(item)

        if self.omdb_client:
            tasks.append(('omdb', rate_limited_fetch(self._fetch_omdb_metadata, item)))

        if self.tvdb_client and item.item_type in ["Series", "Season", "Episode"]:
            tasks.append(('tvdb', rate_limited_fetch(self._fetch_tvdb_metadata, item)))

        if self.tmdb_client:
            tasks.append(('tmdb', rate_limited_fetch(self._fetch_tmdb_metadata, item)))

        # Execute all API calls concurrently with controlled concurrency
        if tasks:
            task_names = [name for name, _ in tasks]
            task_futures = [future for _, future in tasks]
            results = await asyncio.gather(*task_futures, return_exceptions=True)

            # Process results and store in metadata dict
            for name, result in zip(task_names, results):
                if isinstance(result, Exception):
                    self.logger.warning(f"Metadata fetch error for {name}: {result}")
                else:
                    metadata[name] = result

        # Create simplified ratings dictionary for easy template access
        metadata['ratings'] = self._create_ratings_summary(metadata)

        # Count available metadata sources
        metadata_sources = []
        if metadata.get('omdb'):
            metadata_sources.append('OMDb')
        if metadata.get('tvdb'):
            metadata_sources.append('TVDb')
        if metadata.get('tmdb'):
            metadata_sources.append('TMDb')

        self.logger.info(
            f"Enriched {item.item_type} '{item.name}' with metadata from "
            f"{len(metadata_sources)} sources: {', '.join(metadata_sources) if metadata_sources else 'none'}"
        )

        return metadata

    def _get_cache_key(self, provider: str, item: MediaItem) -> str:
        """
        Generate a cache key for metadata lookups.
        
        Args:
            provider: Name of the metadata provider (omdb, tmdb, tvdb)
            item: MediaItem to generate key for
            
        Returns:
            str: Cache key for the item and provider
        """
        # Use relevant IDs based on provider
        if provider == "omdb" and item.imdb_id:
            return f"omdb:{item.imdb_id}"
        elif provider == "tmdb" and item.tmdb_id:
            return f"tmdb:{item.tmdb_id}"
        elif provider == "tvdb" and item.tvdb_id:
            return f"tvdb:{item.tvdb_id}"
        else:
            # Fallback to item ID and name
            return f"{provider}:{item.item_id}:{item.name}"
    
    @lru_cache(maxsize=1000)
    def _get_cached_metadata(self, cache_key: str) -> Optional[Any]:
        """
        Get cached metadata if available.
        
        This uses LRU cache to store the most recently used metadata,
        reducing API calls for frequently accessed items.
        
        Args:
            cache_key: Cache key for the metadata
            
        Returns:
            Optional[Any]: Cached metadata or None
        """
        return self._metadata_cache.get(cache_key)
    
    def _set_cached_metadata(self, cache_key: str, metadata: Any) -> None:
        """
        Store metadata in cache.
        
        Args:
            cache_key: Cache key for the metadata
            metadata: Metadata to cache
        """
        # Implement simple size limit
        if len(self._metadata_cache) >= self._cache_max_size:
            # Remove oldest items (simple FIFO for now)
            oldest_key = next(iter(self._metadata_cache))
            del self._metadata_cache[oldest_key]
        
        self._metadata_cache[cache_key] = metadata
        # Clear the LRU cache to force refresh
        self._get_cached_metadata.cache_clear()

    async def _fetch_omdb_metadata(self, item: MediaItem) -> Optional[Any]:
        """
        Fetch and return OMDb metadata for the media item with caching.

        Args:
            item (MediaItem): Media item to fetch OMDb data for
            
        Returns:
            Optional[Any]: OMDb metadata object or None
        """
        try:
            # Check cache first
            cache_key = self._get_cache_key("omdb", item)
            cached_data = self._get_cached_metadata(cache_key)
            
            if cached_data is not None:
                self.logger.debug(f"Using cached OMDb metadata for {item.name}")
                return cached_data
            
            # Fetch from API if not cached
            omdb_data = await self.omdb_client.get_metadata_for_item(item)
            if omdb_data:
                # Cache the fetched data
                self._set_cached_metadata(cache_key, omdb_data)
                
                # Detailed debug logging
                self.logger.debug("=" * 60)
                self.logger.debug(f"📊 OMDb METADATA RECEIVED for: {item.name}")
                self.logger.debug("=" * 60)

                # Log raw data structure first
                self.logger.debug("📦 RAW OMDb DATA STRUCTURE:")
                if hasattr(omdb_data, '__dict__'):
                    # If it's an object with __dict__
                    import json
                    try:
                        # Try to serialize the object's dict representation
                        raw_dict = omdb_data.__dict__ if hasattr(omdb_data, '__dict__') else omdb_data
                        self.logger.debug(json.dumps(raw_dict, indent=2, default=str))
                    except Exception as e:
                        # Fallback to repr if JSON serialization fails
                        self.logger.debug(f"  {repr(omdb_data)}")
                else:
                    # If it's already a dict or other type
                    self.logger.debug(f"  {omdb_data}")

                self.logger.debug(f"  - IMDb ID: {getattr(omdb_data, 'imdb_id', 'N/A')}")
                self.logger.debug(f"  - Title: {getattr(omdb_data, 'title', 'N/A')}")
                self.logger.debug(f"  - Year: {getattr(omdb_data, 'year', 'N/A')}")
                self.logger.debug(f"  - Rated: {getattr(omdb_data, 'rated', 'N/A')}")
                self.logger.debug(f"  - Released: {getattr(omdb_data, 'released', 'N/A')}")
                self.logger.debug(f"  - Runtime: {getattr(omdb_data, 'runtime', 'N/A')}")
                self.logger.debug(f"  - Genre: {getattr(omdb_data, 'genre', 'N/A')}")
                self.logger.debug(f"  - Director: {getattr(omdb_data, 'director', 'N/A')}")
                self.logger.debug(f"  - Writer: {getattr(omdb_data, 'writer', 'N/A')}")
                self.logger.debug(f"  - Actors: {getattr(omdb_data, 'actors', 'N/A')}")
                self.logger.debug(f"  - Plot: {getattr(omdb_data, 'plot', 'N/A')[:100]}...")
                self.logger.debug(f"  - Language: {getattr(omdb_data, 'language', 'N/A')}")
                self.logger.debug(f"  - Country: {getattr(omdb_data, 'country', 'N/A')}")
                self.logger.debug(f"  - Awards: {getattr(omdb_data, 'awards', 'N/A')}")
                self.logger.debug(f"  - Poster URL: {getattr(omdb_data, 'poster', 'N/A')}")
                self.logger.debug(f"  - IMDb Rating: {getattr(omdb_data, 'imdb_rating', 'N/A')}")
                self.logger.debug(f"  - IMDb Votes: {getattr(omdb_data, 'imdb_votes', 'N/A')}")
                self.logger.debug(f"  - Metascore: {getattr(omdb_data, 'metascore', 'N/A')}")
                self.logger.debug(f"  - Type: {getattr(omdb_data, 'type', 'N/A')}")
                self.logger.debug(f"  - Box Office: {getattr(omdb_data, 'box_office', 'N/A')}")

                # Log ratings information if available
                if hasattr(omdb_data, 'ratings'):
                    self.logger.debug("  - Ratings (raw list):")
                    for rating in getattr(omdb_data, 'ratings', []):
                        self.logger.debug(f"    - {rating}")

                # Log the processed ratings_dict if available
                if hasattr(omdb_data, 'ratings_dict'):
                    self.logger.debug("  - Ratings Dictionary (processed):")
                    ratings_dict = getattr(omdb_data, 'ratings_dict', {})
                    for source, rating_obj in ratings_dict.items():
                        if hasattr(rating_obj, 'value'):
                            self.logger.debug(f"    - {source}: {rating_obj.value}")
                        else:
                            self.logger.debug(f"    - {source}: {rating_obj}")

                self.logger.debug("=" * 60)
                return omdb_data
            
            return None
        except Exception as e:
            self.logger.error(f"Error fetching OMDb metadata for {item.name}: {e}")
            return None

    async def _fetch_tvdb_metadata(self, item: MediaItem) -> Optional[Any]:
        """
        Fetch and return TVDb metadata for the media item with caching.

        Args:
            item (MediaItem): Media item to fetch TVDB data for
            
        Returns:
            Optional[Any]: TVDb metadata object or None
        """
        try:
            if not self.tvdb_client or not item.tvdb_id:
                return None
            
            # Check cache first
            cache_key = self._get_cache_key("tvdb", item)
            cached_data = self._get_cached_metadata(cache_key)
            
            if cached_data is not None:
                self.logger.debug(f"Using cached TVDb metadata for {item.name}")
                return cached_data
            
            # Determine which TVDb API method to use based on item type
            tvdb_data = None
            if item.item_type == "Series":
                tvdb_data = await self.tvdb_client.get_series_metadata(item.tvdb_id)
            elif item.item_type == "Episode" and item.series_id:
                # For episodes, we need the series TVDB ID, not the episode ID
                tvdb_data = await self.tvdb_client.get_episode_metadata(
                    series_tvdb_id=item.tvdb_id,
                    season_number=item.season_number,
                    episode_number=item.episode_number
                )
            
            if tvdb_data:
                # Cache the fetched data
                self._set_cached_metadata(cache_key, tvdb_data)
                
                self.logger.debug("=" * 60)
                self.logger.debug(f"📺 TVDb METADATA RECEIVED for: {item.name}")
                self.logger.debug("=" * 60)
                self.logger.debug(f"  - TVDb ID: {getattr(tvdb_data, 'tvdb_id', 'N/A')}")
                self.logger.debug(f"  - Series Name: {getattr(tvdb_data, 'series_name', 'N/A')}")
                self.logger.debug(f"  - Season: {getattr(tvdb_data, 'season_number', 'N/A')}")
                self.logger.debug(f"  - Episode: {getattr(tvdb_data, 'episode_number', 'N/A')}")
                self.logger.debug(f"  - Episode Name: {getattr(tvdb_data, 'episode_name', 'N/A')}")
                self.logger.debug(f"  - Overview: {getattr(tvdb_data, 'overview', 'N/A')[:100]}...")
                self.logger.debug(f"  - First Aired: {getattr(tvdb_data, 'first_aired', 'N/A')}")
                self.logger.debug(f"  - Runtime: {getattr(tvdb_data, 'runtime', 'N/A')}")
                self.logger.debug(f"  - Rating: {getattr(tvdb_data, 'rating', 'N/A')}")
                self.logger.debug(f"  - Genres: {getattr(tvdb_data, 'genres', [])}")
                self.logger.debug(f"  - Network: {getattr(tvdb_data, 'network', 'N/A')}")
                self.logger.debug(f"  - Status: {getattr(tvdb_data, 'status', 'N/A')}")
                self.logger.debug(f"  - Poster URL: {getattr(tvdb_data, 'poster_url', 'N/A')}")
                self.logger.debug(f"  - Background URL: {getattr(tvdb_data, 'background_url', 'N/A')}")
                self.logger.debug("=" * 60)
                
                return tvdb_data
            
            return None
        except Exception as e:
            self.logger.error(f"Error fetching TVDb metadata for {item.name}: {e}")
            return None

    async def _fetch_tmdb_metadata(self, item: MediaItem) -> Optional[Any]:
        """
        Fetch and return TMDb metadata for the media item with caching.

        Args:
            item (MediaItem): Media item to fetch TMDb data for
            
        Returns:
            Optional[Any]: TMDb metadata object or None
        """
        try:
            if not self.tmdb_client:
                return None
            
            # Check cache first
            cache_key = self._get_cache_key("tmdb", item)
            cached_data = self._get_cached_metadata(cache_key)
            
            if cached_data is not None:
                self.logger.debug(f"Using cached TMDb metadata for {item.name}")
                return cached_data
            
            # Fetch from API based on item type
            tmdb_data = await self.tmdb_client.get_metadata_for_item(item)
            
            if tmdb_data:
                # Cache the fetched data
                self._set_cached_metadata(cache_key, tmdb_data)
                
                self.logger.debug("=" * 60)
                self.logger.debug(f"🎬 TMDb METADATA RECEIVED for: {item.name}")
                self.logger.debug("=" * 60)
                self.logger.debug(f"  - TMDb ID: {getattr(tmdb_data, 'tmdb_id', 'N/A')}")
                self.logger.debug(f"  - Title: {getattr(tmdb_data, 'title', 'N/A')}")
                self.logger.debug(f"  - Original Title: {getattr(tmdb_data, 'original_title', 'N/A')}")
                self.logger.debug(f"  - Overview: {getattr(tmdb_data, 'overview', 'N/A')[:100]}...")
                self.logger.debug(f"  - Release Date: {getattr(tmdb_data, 'release_date', 'N/A')}")
                self.logger.debug(f"  - Popularity: {getattr(tmdb_data, 'popularity', 'N/A')}")
                self.logger.debug(f"  - Vote Average: {getattr(tmdb_data, 'vote_average', 'N/A')}")
                self.logger.debug(f"  - Vote Count: {getattr(tmdb_data, 'vote_count', 'N/A')}")
                self.logger.debug(f"  - Poster Path: {getattr(tmdb_data, 'poster_path', 'N/A')}")
                self.logger.debug(f"  - Backdrop Path: {getattr(tmdb_data, 'backdrop_path', 'N/A')}")
                self.logger.debug(f"  - Adult: {getattr(tmdb_data, 'adult', 'N/A')}")
                self.logger.debug(f"  - Genres: {getattr(tmdb_data, 'genre_ids', [])}")
                self.logger.debug("=" * 60)
                
                return tmdb_data
            
            return None
        except Exception as e:
            self.logger.error(f"Error fetching TMDb metadata for {item.name}: {e}")
            return None

    def _create_ratings_summary(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a simplified ratings dictionary from all metadata sources.

        This combines ratings from OMDb, TMDb, and TVDb into a single dictionary
        for easy access in templates.

        Args:
            metadata: Dictionary containing metadata from all sources

        Returns:
            Dict[str, Any]: Simplified ratings dictionary
        """
        ratings = {}

        # Extract OMDb ratings
        omdb_data = metadata.get('omdb')
        if omdb_data:
            # IMDb rating
            if hasattr(omdb_data, 'imdb_rating') and omdb_data.imdb_rating != 'N/A':
                ratings['imdb'] = {
                    'value': omdb_data.imdb_rating,
                    'normalized': self._normalize_rating(omdb_data.imdb_rating, '/10'),
                    'source': 'Internet Movie Database'
                }
                ratings['imdb_score'] = omdb_data.imdb_rating
                if hasattr(omdb_data, 'imdb_votes'):
                    ratings['imdb_votes'] = omdb_data.imdb_votes

            # Metascore
            if hasattr(omdb_data, 'metascore') and omdb_data.metascore != 'N/A':
                ratings['metascore'] = {
                    'value': omdb_data.metascore,
                    'normalized': self._normalize_rating(omdb_data.metascore, '/100'),
                    'source': 'Metacritic'
                }

            # Rotten Tomatoes
            if hasattr(omdb_data, 'ratings_dict'):
                rt_rating = omdb_data.ratings_dict.get('rotten_tomatoes')
                if rt_rating:
                    ratings['rotten_tomatoes'] = {
                        'value': rt_rating.value if hasattr(rt_rating, 'value') else rt_rating,
                        'normalized': self._normalize_rating(
                            rt_rating.value if hasattr(rt_rating, 'value') else rt_rating, '%'
                        ),
                        'source': 'Rotten Tomatoes'
                    }

        # Extract TMDb ratings
        tmdb_data = metadata.get('tmdb')
        if tmdb_data:
            if hasattr(tmdb_data, 'vote_average') and tmdb_data.vote_average:
                ratings['tmdb'] = {
                    'value': f"{tmdb_data.vote_average}/10",
                    'normalized': tmdb_data.vote_average,
                    'source': 'The Movie Database'
                }
                if hasattr(tmdb_data, 'vote_count'):
                    ratings['tmdb_votes'] = tmdb_data.vote_count

        # Extract TVDb ratings
        tvdb_data = metadata.get('tvdb')
        if tvdb_data:
            if hasattr(tvdb_data, 'rating') and tvdb_data.rating:
                ratings['tvdb'] = {
                    'value': f"{tvdb_data.rating}/10",
                    'normalized': tvdb_data.rating,
                    'source': 'The TV Database'
                }

        return ratings

    def _normalize_rating(self, rating_value: str, rating_format: str) -> float:
        """
        Normalize various rating formats to a 0-10 scale.

        Args:
            rating_value: Raw rating value string
            rating_format: Format hint ('/10', '%', '/100')

        Returns:
            float: Normalized rating on 0-10 scale
        """
        try:
            if rating_format == '/10':
                # Extract number before /10
                parts = rating_value.split('/')
                return float(parts[0])
            elif rating_format == '%':
                # Convert percentage to 0-10 scale
                value = rating_value.replace('%', '').strip()
                return float(value) / 10.0
            elif rating_format == '/100':
                # Convert 0-100 to 0-10 scale
                return float(rating_value) / 10.0
            else:
                # Try to parse as float directly
                return float(rating_value)
        except (ValueError, AttributeError, IndexError):
            return 0.0

    async def cleanup(self) -> None:
        """
        Clean up metadata service resources.

        This should be called during application shutdown to properly close
        connections and clean up resources.
        """
        if self.tvdb_client:
            await self.tvdb_client.cleanup()
            self.logger.debug("TVDb client cleaned up")

        # Clear caches
        self._metadata_cache.clear()
        self._get_cached_metadata.cache_clear()
        self.logger.debug("Metadata caches cleared")