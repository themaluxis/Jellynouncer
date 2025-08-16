#!/usr/bin/env python3
"""
Jellynouncer TMDb API Module

This module provides integration with The Movie Database (TMDb) API for fetching
comprehensive movie and TV metadata including ratings, cast, crew, images, and more.
It uses the tmdbv3api library for reliable API interactions.

TMDb is a community-driven movie and TV database that provides extensive metadata,
images, and user ratings. This module handles all TMDb API interactions with proper
error handling, retry logic, and data sanitization.

Classes:
    TMDbAPI: Main class for TMDb API interactions
    TMDbMetadata: Dataclass for complete TMDb response data

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import os
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict

from tmdbv3api import TMDb, Movie, TV, Season, Episode, Search, Configuration
from tmdbv3api.exceptions import TMDbException

from media_models import MediaItem
from utils import get_logger


@dataclass
class TMDbMetadata:
    """
    Complete TMDb API response data with all available fields.

    This dataclass contains all possible fields returned by TMDb API for movies,
    TV series, seasons, and episodes. Fields are Optional as availability varies
    by media type and data completeness in TMDb.

    All fields are sanitized and formatted for direct use in Jinja2 templates.
    """
    # Core identification
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    media_type: Optional[str] = None  # movie, tv, season, episode

    # Basic info
    title: Optional[str] = None  # or name for TV shows
    original_title: Optional[str] = None
    tagline: Optional[str] = None
    overview: Optional[str] = None
    status: Optional[str] = None  # Released, In Production, etc.

    # Dates
    release_date: Optional[str] = None  # For movies
    first_air_date: Optional[str] = None  # For TV shows
    last_air_date: Optional[str] = None  # For TV shows

    # Ratings and popularity
    vote_average: Optional[float] = None  # 0-10 scale
    vote_count: Optional[int] = None
    popularity: Optional[float] = None

    # Media details
    runtime: Optional[int] = None  # Minutes for movies
    episode_runtime: Optional[List[int]] = field(default_factory=list)  # For TV shows
    budget: Optional[int] = None
    revenue: Optional[int] = None

    # Languages and regions
    original_language: Optional[str] = None
    spoken_languages: List[Dict[str, str]] = field(default_factory=list)
    production_countries: List[Dict[str, str]] = field(default_factory=list)

    # Content
    genres: List[Dict[str, Any]] = field(default_factory=list)
    keywords: List[Dict[str, Any]] = field(default_factory=list)

    # Production
    production_companies: List[Dict[str, Any]] = field(default_factory=list)
    networks: List[Dict[str, Any]] = field(default_factory=list)  # For TV shows

    # Cast and crew (limited)
    cast: List[Dict[str, Any]] = field(default_factory=list)
    crew: List[Dict[str, Any]] = field(default_factory=list)
    created_by: List[Dict[str, Any]] = field(default_factory=list)  # For TV shows
    guest_stars: List[Dict[str, Any]] = field(default_factory=list)  # For episodes

    # Images
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    logo_path: Optional[str] = None
    still_path: Optional[str] = None  # For episodes

    # Full image URLs (constructed)
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    logo_url: Optional[str] = None

    # TV-specific fields
    number_of_seasons: Optional[int] = None
    number_of_episodes: Optional[int] = None
    seasons: List[Dict[str, Any]] = field(default_factory=list)
    in_production: Optional[bool] = None
    type: Optional[str] = None  # Scripted, Reality, etc.

    # Episode-specific fields
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    air_date: Optional[str] = None

    # External IDs
    external_ids: Dict[str, Optional[str]] = field(default_factory=dict)

    # Videos (trailers, teasers, etc.)
    videos: List[Dict[str, Any]] = field(default_factory=list)

    # Processed fields for templates
    genres_list: List[str] = field(default_factory=list)
    spoken_languages_list: List[str] = field(default_factory=list)
    production_countries_list: List[str] = field(default_factory=list)
    production_companies_list: List[str] = field(default_factory=list)
    networks_list: List[str] = field(default_factory=list)

    # API response metadata
    success: bool = True
    error: Optional[str] = None

    def __post_init__(self):
        """Post-process and sanitize data for template use."""
        self._extract_lists()
        self._construct_image_urls()

    def _extract_lists(self):
        """Extract simple lists from complex dictionaries for easier template use."""
        if self.genres:
            self.genres_list = [g.get('name', '') for g in self.genres if g.get('name')]

        if self.spoken_languages:
            self.spoken_languages_list = [l.get('english_name', l.get('name', ''))
                                          for l in self.spoken_languages if l.get('name')]

        if self.production_countries:
            self.production_countries_list = [c.get('name', '')
                                              for c in self.production_countries if c.get('name')]

        if self.production_companies:
            self.production_companies_list = [c.get('name', '')
                                              for c in self.production_companies if c.get('name')]

        if self.networks:
            self.networks_list = [n.get('name', '') for n in self.networks if n.get('name')]

    def _construct_image_urls(self):
        """Construct full image URLs from paths."""
        base_url = "https://image.tmdb.org/t/p/"

        if self.poster_path:
            self.poster_url = f"{base_url}w500{self.poster_path}"

        if self.backdrop_path:
            self.backdrop_url = f"{base_url}w1280{self.backdrop_path}"

        if self.logo_path:
            self.logo_url = f"{base_url}w500{self.logo_path}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for template context."""
        return asdict(self)


class TMDbAPI:
    """
    TMDb API client for fetching movie and TV metadata.

    This class provides methods to fetch metadata from TMDb for movies, TV series,
    seasons, and episodes. It handles API key management, error handling, retry logic,
    and data sanitization.

    The class uses the tmdbv3api library for reliable API interactions while adding
    additional features like retry logic, comprehensive error handling, and data
    sanitization for Jellynouncer's needs.

    Attributes:
        api_key (str): TMDb API key from environment or config
        logger (logging.Logger): Logger instance for API operations
        enabled (bool): Whether TMDb integration is enabled
        tmdb (TMDb): TMDb library instance
        movie_client (Movie): TMDb Movie API client
        tv_client (TV): TMDb TV API client
        season_client (Season): TMDb Season API client
        episode_client (Episode): TMDb Episode API client
        search_client (Search): TMDb Search API client
    """

    def __init__(self, api_key: Optional[str] = None, enabled: bool = True):
        """
        Initialize TMDb API client.

        Args:
            api_key (Optional[str]): TMDb API key. If not provided, checks
                environment variable TMDB_API_KEY
            enabled (bool): Whether TMDb integration is enabled
        """
        self.logger = get_logger("jellynouncer.tmdb")
        self.enabled = enabled

        # Get API key from parameter or environment
        self.api_key = api_key or os.getenv("TMDB_API_KEY")

        if not self.api_key:
            self.logger.warning("TMDb API key not provided. TMDb integration disabled.")
            self.enabled = False
            self.tmdb = None
            return

        try:
            # Initialize TMDb client
            self.tmdb = TMDb()
            self.tmdb.api_key = self.api_key
            self.tmdb.language = 'en-US'  # Default language

            # Initialize API clients
            self.movie_client = Movie()
            self.tv_client = TV()
            self.season_client = Season()
            self.episode_client = Episode()
            self.search_client = Search()
            self.config_client = Configuration()

            # Get configuration for image URLs
            # Note: The newer version uses api_configuration() method
            try:
                self._config = self.config_client.api_configuration()
            except AttributeError:
                self.logger.warning("Could not fetch TMDb configuration")

            self.logger.info("TMDb API initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize TMDb client: {e}")
            self.enabled = False

    def _make_request(self, client_method, *args, **kwargs) -> Optional[Any]:
        """
        Make TMDb API request with error handling and retry logic.

        Args:
            client_method: The API client method to call
            *args: Positional arguments for the method
            **kwargs: Keyword arguments for the method

        Returns:
            Optional[Any]: API response or None if failed
        """
        if not self.enabled:
            return None

        try:
            # First attempt
            response = client_method(*args, **kwargs)
            return response

        except TMDbException as e:
            self.logger.warning(f"TMDb API error: {e}")

            # Retry once on failure
            try:
                self.logger.info("Retrying TMDb API request...")
                response = client_method(*args, **kwargs)
                return response

            except Exception as retry_error:
                self.logger.error(f"TMDb API retry failed: {retry_error}")

        except Exception as e:
            self.logger.error(f"Unexpected TMDb API error: {e}")

        return None

    def _parse_movie_response(self, movie_data: Any) -> TMDbMetadata:
        """
        Parse TMDb movie response into TMDbMetadata dataclass.

        Args:
            movie_data: Raw movie data from TMDb API

        Returns:
            TMDbMetadata: Parsed and sanitized metadata
        """
        try:
            # Convert object attributes to dictionary if needed
            if hasattr(movie_data, '__dict__'):
                data = movie_data.__dict__
            else:
                data = movie_data

            metadata = TMDbMetadata(
                tmdb_id=data.get('id'),
                imdb_id=data.get('imdb_id'),
                media_type='movie',
                title=data.get('title'),
                original_title=data.get('original_title'),
                tagline=data.get('tagline'),
                overview=data.get('overview'),
                status=data.get('status'),
                release_date=data.get('release_date'),
                vote_average=data.get('vote_average'),
                vote_count=data.get('vote_count'),
                popularity=data.get('popularity'),
                runtime=data.get('runtime'),
                budget=data.get('budget'),
                revenue=data.get('revenue'),
                original_language=data.get('original_language'),
                spoken_languages=data.get('spoken_languages', []),
                production_countries=data.get('production_countries', []),
                genres=data.get('genres', []),
                production_companies=data.get('production_companies', []),
                poster_path=data.get('poster_path'),
                backdrop_path=data.get('backdrop_path'),
                videos=data.get('videos', {}).get('results', []) if data.get('videos') else [],
                success=True
            )

            return metadata

        except Exception as e:
            self.logger.error(f"Error parsing movie response: {e}")
            return TMDbMetadata(success=False, error=str(e))

    def _parse_tv_response(self, tv_data: Any) -> TMDbMetadata:
        """
        Parse TMDb TV show response into TMDbMetadata dataclass.

        Args:
            tv_data: Raw TV show data from TMDb API

        Returns:
            TMDbMetadata: Parsed and sanitized metadata
        """
        try:
            # Convert object attributes to dictionary if needed
            if hasattr(tv_data, '__dict__'):
                data = tv_data.__dict__
            else:
                data = tv_data

            metadata = TMDbMetadata(
                tmdb_id=data.get('id'),
                media_type='tv',
                title=data.get('name'),  # TV shows use 'name' instead of 'title'
                original_title=data.get('original_name'),
                tagline=data.get('tagline'),
                overview=data.get('overview'),
                status=data.get('status'),
                first_air_date=data.get('first_air_date'),
                last_air_date=data.get('last_air_date'),
                vote_average=data.get('vote_average'),
                vote_count=data.get('vote_count'),
                popularity=data.get('popularity'),
                episode_runtime=data.get('episode_run_time', []),
                original_language=data.get('original_language'),
                spoken_languages=data.get('spoken_languages', []),
                production_countries=data.get('production_countries', []),
                genres=data.get('genres', []),
                production_companies=data.get('production_companies', []),
                networks=data.get('networks', []),
                created_by=data.get('created_by', []),
                poster_path=data.get('poster_path'),
                backdrop_path=data.get('backdrop_path'),
                number_of_seasons=data.get('number_of_seasons'),
                number_of_episodes=data.get('number_of_episodes'),
                seasons=data.get('seasons', []),
                in_production=data.get('in_production'),
                type=data.get('type'),
                success=True
            )

            return metadata

        except Exception as e:
            self.logger.error(f"Error parsing TV response: {e}")
            return TMDbMetadata(success=False, error=str(e))

    def _parse_episode_response(self, episode_data: Any, series_id: Optional[int] = None) -> TMDbMetadata:
        """
        Parse TMDb episode response into TMDbMetadata dataclass.

        Args:
            episode_data: Raw episode data from TMDb API
            series_id: Optional series TMDb ID

        Returns:
            TMDbMetadata: Parsed and sanitized metadata
        """
        try:
            # Convert object attributes to dictionary if needed
            if hasattr(episode_data, '__dict__'):
                data = episode_data.__dict__
            else:
                data = episode_data

            metadata = TMDbMetadata(
                tmdb_id=data.get('id'),
                media_type='episode',
                title=data.get('name'),
                overview=data.get('overview'),
                air_date=data.get('air_date'),
                season_number=data.get('season_number'),
                episode_number=data.get('episode_number'),
                vote_average=data.get('vote_average'),
                vote_count=data.get('vote_count'),
                runtime=data.get('runtime'),
                crew=data.get('crew', []),
                guest_stars=data.get('guest_stars', []),
                still_path=data.get('still_path'),
                success=True
            )

            # Use still_path as poster for episodes
            if data.get('still_path'):
                metadata.poster_path = data.get('still_path')

            return metadata

        except Exception as e:
            self.logger.error(f"Error parsing episode response: {e}")
            return TMDbMetadata(success=False, error=str(e))

    async def get_metadata_for_item(self, item: MediaItem) -> Optional[TMDbMetadata]:
        """
        Get TMDb metadata for a media item.

        This method determines the appropriate TMDb query based on the media item's
        type and available identifiers. It prioritizes TMDb ID for accuracy but can
        fall back to title-based searches.

        Args:
            item (MediaItem): Media item to fetch metadata for

        Returns:
            Optional[TMDbMetadata]: Complete TMDb metadata or None if failed
        """
        if not self.enabled:
            return None

        try:
            metadata = None

            # Determine media type and fetch accordingly
            if item.item_type == "Movie":
                metadata = await self._get_movie_metadata(item)

            elif item.item_type == "Series":
                metadata = await self._get_tv_metadata(item)

            elif item.item_type == "Episode":
                metadata = await self._get_episode_metadata(item)

            elif item.item_type == "Season":
                metadata = await self._get_season_metadata(item)

            if metadata and metadata.success:
                self.logger.info(
                    f"Successfully fetched TMDb metadata for: {item.name} "
                    f"(Rating: {metadata.vote_average}/10 from {metadata.vote_count} votes)"
                )
            else:
                self.logger.warning(f"No TMDb data found for: {item.name}")

            return metadata

        except Exception as e:
            self.logger.error(f"Error fetching TMDb metadata for {item.name}: {e}")
            return None

    async def _get_movie_metadata(self, item: MediaItem) -> Optional[TMDbMetadata]:
        """
        Get movie metadata from TMDb.

        Args:
            item (MediaItem): Movie item

        Returns:
            Optional[TMDbMetadata]: Movie metadata
        """
        movie_data = None

        # Try by TMDb ID first
        if item.tmdb_id:
            self.logger.debug(f"Fetching TMDb movie by ID: {item.tmdb_id}")
            movie_data = self._make_request(self.movie_client.details, item.tmdb_id)

        # Fall back to search by title using Search().movies()
        if not movie_data and item.name:
            self.logger.debug(f"Searching TMDb for movie: {item.name}")
            # Use Search().movies() as recommended in deprecation warning
            search_results = self._make_request(
                self.search_client.movies,
                term=item.name,
                page=1
            )

            # The search results should be a list
            if search_results and len(search_results) > 0:
                first_result = search_results[0]
                movie_id = first_result.id if hasattr(first_result, 'id') else first_result.get('id')
                if movie_id:
                    movie_data = self._make_request(self.movie_client.details, movie_id)

        if movie_data:
            return self._parse_movie_response(movie_data)

        return None

    async def _get_tv_metadata(self, item: MediaItem) -> Optional[TMDbMetadata]:
        """
        Get TV series metadata from TMDb.

        Args:
            item (MediaItem): TV series item

        Returns:
            Optional[TMDbMetadata]: TV series metadata
        """
        tv_data = None

        # Try by TMDb ID first
        if item.tmdb_id:
            self.logger.debug(f"Fetching TMDb TV series by ID: {item.tmdb_id}")
            tv_data = self._make_request(self.tv_client.details, item.tmdb_id)

        # Fall back to search by title using Search().tv_shows()
        if not tv_data and item.name:
            self.logger.debug(f"Searching TMDb for TV series: {item.name}")
            # Use Search().tv_shows() as recommended in deprecation warning
            search_results = self._make_request(
                self.search_client.tv_shows,
                term=item.name,
                page=1
            )

            # The search results should be a list
            if search_results and len(search_results) > 0:
                first_result = search_results[0]
                tv_id = first_result.id if hasattr(first_result, 'id') else first_result.get('id')
                if tv_id:
                    tv_data = self._make_request(self.tv_client.details, tv_id)

        if tv_data:
            return self._parse_tv_response(tv_data)

        return None

    async def _get_episode_metadata(self, item: MediaItem) -> Optional[TMDbMetadata]:
        """
        Get episode metadata from TMDb.

        Args:
            item (MediaItem): Episode item

        Returns:
            Optional[TMDbMetadata]: Episode metadata
        """
        # Need series TMDb ID to fetch episode
        series_tmdb_id = None

        if item.tmdb_id:
            # If we have a TMDb ID, assume it's the series ID
            series_tmdb_id = item.tmdb_id
        elif item.series_name:
            # Search for the series first using Search().tv_shows()
            search_results = self._make_request(
                self.search_client.tv_shows,
                term=item.series_name,
                page=1
            )

            if search_results and len(search_results) > 0:
                first_result = search_results[0]
                series_tmdb_id = first_result.id if hasattr(first_result, 'id') else first_result.get('id')

        if series_tmdb_id and item.season_number and item.episode_number:
            self.logger.debug(
                f"Fetching TMDb episode: Series {series_tmdb_id} "
                f"S{item.season_number}E{item.episode_number}"
            )

            episode_data = self._make_request(
                self.episode_client.details,
                series_tmdb_id,
                item.season_number,
                item.episode_number
            )

            if episode_data:
                return self._parse_episode_response(episode_data, series_tmdb_id)

        return None

    async def _get_season_metadata(self, item: MediaItem) -> Optional[TMDbMetadata]:
        """
        Get season metadata from TMDb.

        Args:
            item (MediaItem): Season item

        Returns:
            Optional[TMDbMetadata]: Season metadata
        """
        # Need series TMDb ID to fetch season
        series_tmdb_id = None

        if item.tmdb_id:
            series_tmdb_id = item.tmdb_id
        elif item.series_name:
            # Search for the series first using Search().tv_shows()
            search_results = self._make_request(
                self.search_client.tv_shows,
                term=item.series_name,
                page=1
            )

            if search_results and len(search_results) > 0:
                first_result = search_results[0]
                series_tmdb_id = first_result.id if hasattr(first_result, 'id') else first_result.get('id')

        if series_tmdb_id and item.season_number:
            self.logger.debug(
                f"Fetching TMDb season: Series {series_tmdb_id} Season {item.season_number}"
            )

            season_data = self._make_request(
                self.season_client.details,
                series_tmdb_id,
                item.season_number
            )

            if season_data:
                # Parse season data similar to TV show
                return self._parse_tv_response(season_data)

        return None