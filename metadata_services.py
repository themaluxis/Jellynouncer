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

        # Log initialization status and available services
        self.logger.info(f"Metadata service initialized - Enabled: {self.enabled}")
        if self.enabled:
            available_services = []
            if self.omdb_client:
                available_services.append("OMDb")
            if self.tmdb_client:
                available_services.append("TMDb")
            if self.tvdb_config_ready:
                access_mode = "subscriber" if self.tvdb_config.subscriber_pin else "standard"
                available_services.append(f"TVDb v4 ({access_mode})")

            service_list = ', '.join(available_services) if available_services else 'None (no API keys configured)'
            self.logger.info(f"Available metadata services: {service_list}")

    async def initialize(self, session: aiohttp.ClientSession, db_manager) -> None:
        """
        Initialize metadata service with shared resources and perform setup tasks.

        Args:
            session (aiohttp.ClientSession): Shared HTTP session for all API requests
            db_manager: Database manager for caching metadata
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
                    cache_ttl=self.config.tvdb_cache_ttl_hours * 3600  # Convert to seconds
                )
                await self.tvdb_client.__aenter__()
                self.logger.info("TVDB client initialized successfully")
            except Exception as e:
                self.logger.error(f"TVDB initialization error: {e}")
                self.tvdb_client = None

        self.logger.info("Metadata service initialization completed")

    async def enrich_media_item(self, item: MediaItem) -> MediaItem:
        """
        Enrich a media item with metadata from all available sources.

        This is the main entry point for adding metadata to a media item before
        template rendering. It adds data from OMDb, TMDb, and TVDb as nested
        objects that can be accessed in templates.

        The enriched item will have the following additional attributes:
        - item.omdb: OMDb metadata including all ratings
        - item.tvdb: TVDb metadata for TV shows
        - item.tmdb: TMDb community ratings (future)
        - item.ratings: Simplified ratings dictionary for easy access

        Args:
            item (MediaItem): Media item to enrich with metadata

        Returns:
            MediaItem: The same item with added metadata attributes
        """
        if not self.enabled:
            return item

            # Initialize metadata containers using setattr for dynamic attributes
        setattr(item, 'omdb', None)
        setattr(item, 'tvdb', None)
        setattr(item, 'tmdb', None)
        setattr(item, 'ratings', {})

        # Fetch metadata from all available sources concurrently
        tasks = []

        if self.omdb_client:
            tasks.append(self._fetch_omdb_metadata(item))

        if self.tvdb_client and item.item_type in ["Series", "Season", "Episode"]:
            tasks.append(self._fetch_tvdb_metadata(item))

        if self.tmdb_client:
            tasks.append(self._fetch_tmdb_metadata(item))

        # Execute all API calls concurrently
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for result in results:
                if isinstance(result, Exception):
                    self.logger.warning(f"Metadata fetch error: {result}")

        # Create simplified ratings dictionary for easy template access
        self._create_ratings_summary(item)

        # Count available metadata sources
        metadata_sources = []
        if getattr(item, 'omdb', None):
            metadata_sources.append('OMDb')
        if getattr(item, 'tvdb', None):
            metadata_sources.append('TVDb')
        if getattr(item, 'tmdb', None):
            metadata_sources.append('TMDb')

        self.logger.info(
            f"Enriched {item.item_type} '{item.name}' with metadata from "
            f"{len(metadata_sources)} sources: {', '.join(metadata_sources) if metadata_sources else 'none'}"
        )

        return item

    async def _fetch_omdb_metadata(self, item: MediaItem) -> None:
        """
        Fetch and attach OMDb metadata to the media item.

        Args:
            item (MediaItem): Media item to fetch OMDb data for
        """
        try:
            omdb_data = await self.omdb_client.get_metadata_for_item(item)
            if omdb_data:
                # Use setattr to add the dynamic attribute
                setattr(item, 'omdb', omdb_data)

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
                self.logger.debug(
                    f"  - Plot: {(getattr(omdb_data, 'plot', 'N/A')[:100] + '...') if getattr(omdb_data, 'plot', None) and len(getattr(omdb_data, 'plot', '')) > 100 else getattr(omdb_data, 'plot', 'N/A')}")
                self.logger.debug(f"  - Language: {getattr(omdb_data, 'language', 'N/A')}")
                self.logger.debug(f"  - Country: {getattr(omdb_data, 'country', 'N/A')}")
                self.logger.debug(f"  - Awards: {getattr(omdb_data, 'awards', 'N/A')}")
                self.logger.debug(f"  - Poster URL: {getattr(omdb_data, 'poster', 'N/A')}")
                self.logger.debug(f"  - Metascore: {getattr(omdb_data, 'metascore', 'N/A')}")
                self.logger.debug(f"  - IMDb Rating: {getattr(omdb_data, 'imdb_rating', 'N/A')}")
                self.logger.debug(f"  - IMDb Votes: {getattr(omdb_data, 'imdb_votes', 'N/A')}")
                self.logger.debug(f"  - Box Office: {getattr(omdb_data, 'box_office', 'N/A')}")
                self.logger.debug(f"  - Production: {getattr(omdb_data, 'production', 'N/A')}")

                # Log ratings if available
                if hasattr(omdb_data, 'ratings') and omdb_data.ratings:
                    self.logger.debug(f"  - Ratings count: {len(omdb_data.ratings)}")
                    for rating in omdb_data.ratings:
                        self.logger.debug(f"    • {rating.source}: {rating.value}")

                # Log ratings dictionary if available
                if hasattr(omdb_data, 'ratings_dict') and omdb_data.ratings_dict:
                    self.logger.debug(f"  - Ratings dict keys: {list(omdb_data.ratings_dict.keys())}")
                    for source, rating_info in omdb_data.ratings_dict.items():
                        if hasattr(rating_info, 'value'):
                            self.logger.debug(f"    • {source}: {rating_info.value}")

                self.logger.debug("=" * 60)
            else:
                self.logger.debug(f"No OMDb metadata returned for {item.name}")
        except Exception as e:
            self.logger.error(f"Error fetching OMDb metadata: {e}")

    async def _fetch_tvdb_metadata(self, item: MediaItem) -> None:
        """
        Fetch and attach TVDb metadata to the media item.

        Args:
            item (MediaItem): Media item to fetch TVDB data for
        """
        try:
            if not self.tvdb_client or not item.tvdb_id:
                return

            tvdb_data = None

            if item.item_type == "Series":
                tvdb_data = await self.tvdb_client.get_series_metadata(int(item.tvdb_id))
            elif item.item_type == "Episode":
                series_data = await self.tvdb_client.get_series_metadata(int(item.tvdb_id))
                if series_data:
                    tvdb_data = series_data

            if tvdb_data:
                # Use setattr to add the dynamic attribute
                setattr(item, 'tvdb', tvdb_data)

                # Detailed debug logging
                self.logger.debug("=" * 60)
                self.logger.debug(f"📺 TVDb METADATA RECEIVED for: {item.name}")
                self.logger.debug("=" * 60)

                # Log raw data structure first
                self.logger.debug("📦 RAW TVDb DATA STRUCTURE:")
                if hasattr(tvdb_data, '__dict__'):
                    # If it's an object with __dict__
                    import json
                    try:
                        # Try to serialize the object's dict representation
                        raw_dict = tvdb_data.__dict__ if hasattr(tvdb_data, '__dict__') else tvdb_data
                        self.logger.debug(json.dumps(raw_dict, indent=2, default=str))
                    except Exception as e:
                        # Fallback to repr if JSON serialization fails
                        self.logger.debug(f"  {repr(tvdb_data)}")
                else:
                    # If it's already a dict or other type
                    self.logger.debug(f"  {tvdb_data}")

                self.logger.debug(f"  - TVDb ID: {getattr(tvdb_data, 'tvdb_id', 'N/A')}")
                self.logger.debug(f"  - Name: {getattr(tvdb_data, 'name', 'N/A')}")
                self.logger.debug(f"  - Status: {getattr(tvdb_data, 'status', 'N/A')}")
                self.logger.debug(f"  - First Aired: {getattr(tvdb_data, 'first_aired', 'N/A')}")
                self.logger.debug(f"  - Network: {getattr(tvdb_data, 'network', 'N/A')}")
                self.logger.debug(f"  - Runtime: {getattr(tvdb_data, 'runtime', 'N/A')} min")
                self.logger.debug(f"  - Average Runtime: {getattr(tvdb_data, 'average_runtime', 'N/A')} min")
                self.logger.debug(f"  - Rating: {getattr(tvdb_data, 'rating', 'N/A')}")
                self.logger.debug(f"  - Rating Count: {getattr(tvdb_data, 'rating_count', 'N/A')}")
                self.logger.debug(
                    f"  - Overview: {(getattr(tvdb_data, 'overview', 'N/A')[:100] + '...') if getattr(tvdb_data, 'overview', None) and len(getattr(tvdb_data, 'overview', '')) > 100 else getattr(tvdb_data, 'overview', 'N/A')}")

                # Log genres if available
                if hasattr(tvdb_data, 'genres') and tvdb_data.genres:
                    self.logger.debug(f"  - Genres: {', '.join(tvdb_data.genres)}")

                # Log image URLs if available
                self.logger.debug(f"  - Poster URL: {getattr(tvdb_data, 'poster_url', 'N/A')}")
                self.logger.debug(f"  - Banner URL: {getattr(tvdb_data, 'banner_url', 'N/A')}")
                self.logger.debug(f"  - Fanart URL: {getattr(tvdb_data, 'fanart_url', 'N/A')}")

                self.logger.debug("=" * 60)
            else:
                self.logger.debug(f"No TVDb metadata returned for {item.name}")

        except Exception as e:
            self.logger.error(f"Error fetching TVDb metadata: {e}")

    async def _fetch_tmdb_metadata(self, item: MediaItem) -> None:
        """
        Fetch and attach TMDb metadata to the media item.

        Args:
            item (MediaItem): Media item to fetch TMDb data for
        """
        try:
            tmdb_data = await self.tmdb_client.get_metadata_for_item(item)
            if tmdb_data:
                # Attach the TMDb metadata object to the item
                setattr(item, 'tmdb', tmdb_data)

                # Detailed debug logging
                self.logger.debug("=" * 60)
                self.logger.debug(f"🎬 TMDb METADATA RECEIVED for: {item.name}")
                self.logger.debug("=" * 60)

                # Log raw data structure first
                self.logger.debug("📦 RAW TMDb DATA STRUCTURE:")
                if hasattr(tmdb_data, '__dict__'):
                    # If it's an object with __dict__
                    import json
                    try:
                        # Try to serialize the object's dict representation
                        raw_dict = tmdb_data.__dict__ if hasattr(tmdb_data, '__dict__') else tmdb_data
                        self.logger.debug(json.dumps(raw_dict, indent=2, default=str))
                    except Exception as e:
                        # Fallback to repr if JSON serialization fails
                        self.logger.debug(f"  {repr(tmdb_data)}")
                else:
                    # If it's already a dict or other type
                    self.logger.debug(f"  {tmdb_data}")

                # Log all available attributes
                self.logger.debug("\n🔍 ALL AVAILABLE ATTRIBUTES:")
                if hasattr(tmdb_data, '__dict__'):
                    for attr_name in dir(tmdb_data):
                        if not attr_name.startswith('_'):
                            try:
                                attr_value = getattr(tmdb_data, attr_name)
                                if not callable(attr_value):
                                    self.logger.debug(f"  - {attr_name}: {attr_value}")
                            except:
                                pass

                self.logger.debug(f"  - TMDb ID: {getattr(tmdb_data, 'tmdb_id', 'N/A')}")
                self.logger.debug(f"  - Media Type: {getattr(tmdb_data, 'media_type', 'N/A')}")
                self.logger.debug(f"  - Title: {getattr(tmdb_data, 'title', 'N/A')}")
                self.logger.debug(f"  - Original Title: {getattr(tmdb_data, 'original_title', 'N/A')}")
                self.logger.debug(f"  - Release Date: {getattr(tmdb_data, 'release_date', 'N/A')}")
                self.logger.debug(f"  - Vote Average: {getattr(tmdb_data, 'vote_average', 'N/A')}/10")
                self.logger.debug(f"  - Vote Count: {getattr(tmdb_data, 'vote_count', 'N/A')}")
                self.logger.debug(f"  - Popularity: {getattr(tmdb_data, 'popularity', 'N/A')}")
                self.logger.debug(
                    f"  - Overview: {(getattr(tmdb_data, 'overview', 'N/A')[:100] + '...') if getattr(tmdb_data, 'overview', None) and len(getattr(tmdb_data, 'overview', '')) > 100 else getattr(tmdb_data, 'overview', 'N/A')}")
                self.logger.debug(f"  - Tagline: {getattr(tmdb_data, 'tagline', 'N/A')}")
                self.logger.debug(f"  - Status: {getattr(tmdb_data, 'status', 'N/A')}")
                self.logger.debug(f"  - Runtime: {getattr(tmdb_data, 'runtime', 'N/A')} min")
                self.logger.debug(f"  - Budget: ${getattr(tmdb_data, 'budget', 'N/A')}")
                self.logger.debug(f"  - Revenue: ${getattr(tmdb_data, 'revenue', 'N/A')}")
                self.logger.debug(f"  - Homepage: {getattr(tmdb_data, 'homepage', 'N/A')}")

                # Log genres if available - FIXED VERSION
                if hasattr(tmdb_data, 'genres') and tmdb_data.genres:
                    self.logger.debug(f"  - Genres: {tmdb_data.genres}")

                # Log production companies if available
                if hasattr(tmdb_data, 'production_companies') and tmdb_data.production_companies:
                    self.logger.debug(f"  - Production Companies: {tmdb_data.production_companies[:3]}")

                # Log image paths
                self.logger.debug(f"  - Poster Path: {getattr(tmdb_data, 'poster_path', 'N/A')}")
                self.logger.debug(f"  - Backdrop Path: {getattr(tmdb_data, 'backdrop_path', 'N/A')}")

                # Log TV-specific fields if present
                if item.item_type in ["Series", "Episode", "Season"]:
                    self.logger.debug(f"  - First Air Date: {getattr(tmdb_data, 'first_air_date', 'N/A')}")
                    self.logger.debug(f"  - Last Air Date: {getattr(tmdb_data, 'last_air_date', 'N/A')}")
                    self.logger.debug(f"  - Number of Seasons: {getattr(tmdb_data, 'number_of_seasons', 'N/A')}")
                    self.logger.debug(f"  - Number of Episodes: {getattr(tmdb_data, 'number_of_episodes', 'N/A')}")
                    self.logger.debug(f"  - Episode Runtime: {getattr(tmdb_data, 'episode_run_time', 'N/A')}")

                self.logger.debug("=" * 60)
            else:
                self.logger.debug(f"No TMDb metadata returned for {item.name}")
        except Exception as e:
            self.logger.error(f"Error fetching TMDb metadata: {e}")

    def _create_ratings_summary(self, item: MediaItem) -> None:
        """
        Create a simplified ratings summary for easy template access.

        This creates a flat dictionary of all ratings from various sources
        that can be easily accessed in templates as item.ratings.imdb,
        item.ratings.rotten_tomatoes, etc.

        Args:
            item (MediaItem): Media item with attached metadata
        """
        # Create ratings dictionary
        ratings = {}

        # Extract OMDb ratings
        omdb_data = getattr(item, 'omdb', None)
        if omdb_data and hasattr(omdb_data, 'ratings_dict'):
            for source, rating in omdb_data.ratings_dict.items():
                ratings[source] = {
                    "value": rating.value,
                    "normalized": rating.normalized_value,
                    "source": rating.source
                }

        # Add IMDb rating as a direct field if available
        if omdb_data and hasattr(omdb_data, 'imdb_rating'):
            ratings["imdb_score"] = omdb_data.imdb_rating
            if hasattr(omdb_data, 'imdb_votes'):
                ratings["imdb_votes"] = omdb_data.imdb_votes

        # Add Metascore if available
        if omdb_data and hasattr(omdb_data, 'metascore'):
            ratings["metascore"] = omdb_data.metascore

        # Add TVDb rating if available
        tvdb_data = getattr(item, 'tvdb', None)
        if tvdb_data and hasattr(tvdb_data, 'rating'):
            ratings["tvdb"] = {
                "value": tvdb_data.rating,
                "count": getattr(tvdb_data, 'rating_count', None)
            }

        # Add TMDb rating if available
        tmdb_data = getattr(item, 'tmdb', None)
        if tmdb_data and hasattr(tmdb_data, 'vote_average'):
            ratings["tmdb"] = {
                "value": f"{tmdb_data.vote_average}/10",
                "normalized": tmdb_data.vote_average,
                "count": getattr(tmdb_data, 'vote_count', None)
            }

        # Set the ratings attribute on the item
        setattr(item, 'ratings', ratings)

        # Debug logging for ratings summary
        if ratings:
            self.logger.debug("=" * 60)
            self.logger.debug(f"⭐ RATINGS SUMMARY for: {item.name}")
            self.logger.debug("=" * 60)
            for key, value in ratings.items():
                if isinstance(value, dict):
                    self.logger.debug(f"  - {key}: {value.get('value', 'N/A')} (count: {value.get('count', 'N/A')})")
                else:
                    self.logger.debug(f"  - {key}: {value}")
            self.logger.debug("=" * 60)
        else:
            self.logger.debug(f"No ratings data available for: {item.name}")

    async def cleanup(self) -> None:
        """
        Clean up resources when shutting down the metadata service.
        """
        if self.tvdb_client:
            try:
                await self.tvdb_client.__aexit__(None, None, None)
                self.logger.info("TVDB client cleaned up")
            except Exception as e:
                self.logger.error(f"Error cleaning up TVDB client: {e}")