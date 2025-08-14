#!/usr/bin/env python3
"""
Jellynouncer OMDb API Module

This module provides integration with the Open Movie Database (OMDb) API for fetching
comprehensive movie and TV metadata including ratings from IMDb, Rotten Tomatoes, and
Metacritic. It uses the omdb library by dgilland for reliable API interactions.

The OMDb API aggregates rating data from multiple sources and provides detailed metadata
about movies, TV series, seasons, and individual episodes. This module handles all OMDb
API interactions with proper error handling, retry logic, and data sanitization.

Classes:
    OMDbAPI: Main class for OMDb API interactions
    OMDbRating: Dataclass for individual rating sources
    OMDbMetadata: Dataclass for complete OMDb response data

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import os
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict

import omdb

from media_models import MediaItem
from utils import get_logger


@dataclass
class OMDbRating:
    """
    Individual rating from a specific source.

    Attributes:
        source (str): Rating source name (e.g., "Internet Movie Database")
        value (str): Rating value in source's format (e.g., "8.5/10", "85%")
        normalized_value (Optional[float]): Normalized value (0-10 scale)
    """
    source: str
    value: str
    normalized_value: Optional[float] = None

    def __post_init__(self):
        """Normalize rating values to 0-10 scale for consistency."""
        if self.normalized_value is None:
            self.normalized_value = self._normalize_rating()

    def _normalize_rating(self) -> Optional[float]:
        """
        Convert various rating formats to normalized 0-10 scale.

        Returns:
            Optional[float]: Normalized rating or None if unable to parse
        """
        try:
            if "/" in self.value:
                # Format: "8.5/10" or similar
                numerator, denominator = self.value.split("/")
                return (float(numerator) / float(denominator)) * 10
            elif "%" in self.value:
                # Format: "85%"
                return float(self.value.rstrip("%")) / 10
            else:
                # Assume it's already a number
                return float(self.value)
        except (ValueError, ZeroDivisionError):
            return None


@dataclass
class OMDbMetadata:
    """
    Complete OMDb API response data with all available fields.

    This dataclass contains all possible fields returned by OMDb API for movies,
    series, seasons, and episodes. Fields are Optional as availability varies by
    media type and data completeness in OMDb.

    All fields are sanitized and formatted for direct use in Jinja2 templates.
    """
    # Core identification
    imdb_id: Optional[str] = None
    type: Optional[str] = None  # movie, series, episode
    title: Optional[str] = None
    year: Optional[str] = None

    # Content details
    rated: Optional[str] = None  # MPAA rating
    released: Optional[str] = None  # Release date
    runtime: Optional[str] = None  # Duration in minutes
    genre: Optional[str] = None  # Comma-separated genres
    director: Optional[str] = None
    writer: Optional[str] = None
    actors: Optional[str] = None  # Comma-separated cast
    plot: Optional[str] = None  # Synopsis
    language: Optional[str] = None
    country: Optional[str] = None
    awards: Optional[str] = None

    # Media details
    poster: Optional[str] = None  # Poster URL
    metascore: Optional[str] = None  # Metacritic score
    imdb_rating: Optional[str] = None  # IMDb rating
    imdb_votes: Optional[str] = None  # Number of IMDb votes

    # Ratings from multiple sources
    ratings: List[OMDbRating] = field(default_factory=list)
    ratings_dict: Dict[str, OMDbRating] = field(default_factory=dict)

    # Additional movie-specific fields
    dvd: Optional[str] = None  # DVD release date
    box_office: Optional[str] = None  # Box office earnings
    production: Optional[str] = None  # Production company
    website: Optional[str] = None  # Official website

    # Series-specific fields
    total_seasons: Optional[str] = None

    # Episode-specific fields
    season: Optional[str] = None
    episode: Optional[str] = None
    series_id: Optional[str] = None  # Parent series IMDb ID

    # API response metadata
    response: bool = False  # Whether API call was successful
    error: Optional[str] = None  # Error message if failed

    # Processed/sanitized fields for templates
    runtime_minutes: Optional[int] = None
    genres_list: List[str] = field(default_factory=list)
    actors_list: List[str] = field(default_factory=list)
    languages_list: List[str] = field(default_factory=list)
    countries_list: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Post-process and sanitize data for template use."""
        self._sanitize_fields()
        self._parse_lists()
        self._process_ratings()
        self._convert_runtime()

    def _sanitize_fields(self):
        """Convert 'N/A' strings to None for cleaner template handling."""
        from dataclasses import fields

        for field in fields(self):
            field_name = field.name
            value = getattr(self, field_name)
            if value == "N/A":
                setattr(self, field_name, None)

    def _parse_lists(self):
        """Parse comma-separated strings into lists for easier template iteration."""
        if self.genre:
            self.genres_list = [g.strip() for g in self.genre.split(",")]
        if self.actors:
            self.actors_list = [a.strip() for a in self.actors.split(",")]
        if self.language:
            self.languages_list = [l.strip() for l in self.language.split(",")]
        if self.country:
            self.countries_list = [c.strip() for c in self.country.split(",")]

    def _process_ratings(self):
        """Create a dictionary of ratings by source for easy template access."""
        self.ratings_dict = {}
        for rating in self.ratings:
            # Create simplified keys for template access
            if "Internet Movie Database" in rating.source:
                self.ratings_dict["imdb"] = rating
            elif "Rotten Tomatoes" in rating.source:
                self.ratings_dict["rotten_tomatoes"] = rating
            elif "Metacritic" in rating.source:
                self.ratings_dict["metacritic"] = rating
            else:
                # Use source name as-is for unknown sources
                self.ratings_dict[rating.source.lower().replace(" ", "_")] = rating

    def _convert_runtime(self):
        """Convert runtime string to integer minutes."""
        if self.runtime and self.runtime != "N/A":
            try:
                # Remove " min" suffix and convert to int
                self.runtime_minutes = int(self.runtime.replace(" min", "").strip())
            except (ValueError, AttributeError):
                self.runtime_minutes = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for template context."""
        return asdict(self)


class OMDbAPI:
    """
    OMDb API client for fetching movie and TV metadata.

    This class provides methods to fetch metadata from OMDb for movies, TV series,
    seasons, and episodes. It handles API key management, error handling, retry logic,
    and data sanitization.

    The class uses the omdb library by dgilland for reliable API interactions while
    adding additional features like retry logic, comprehensive error handling, and
    data sanitization for Jellynouncer's needs.

    Attributes:
        api_key (str): OMDb API key from environment or config
        logger (logging.Logger): Logger instance for API operations
        enabled (bool): Whether OMDb integration is enabled
        client (omdb.OMDBClient): OMDb library client instance
    """

    def __init__(self, api_key: Optional[str] = None, enabled: bool = True):
        """
        Initialize OMDb API client.

        Args:
            api_key (Optional[str]): OMDb API key. If not provided, checks
                environment variable OMDB_API_KEY
            enabled (bool): Whether OMDb integration is enabled
        """
        self.logger = get_logger("jellynouncer.omdb")
        self.enabled = enabled

        # Get API key from parameter or environment
        self.api_key = api_key or os.getenv("OMDB_API_KEY")

        if not self.api_key:
            self.logger.warning("OMDb API key not provided. OMDb integration disabled.")
            self.enabled = False
            self.client = None
        else:
            # Initialize OMDb client with API key
            omdb.set_default("apikey", self.api_key)
            self.client = omdb.OMDBClient(apikey=self.api_key)
            self.logger.info("OMDb API client initialized successfully")

    def _make_request(self, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Make OMDb API request with error handling and retry logic.

        Args:
            **kwargs: Parameters to pass to OMDb API

        Returns:
            Optional[Dict[str, Any]]: API response or None if failed
        """
        if not self.enabled or not self.client:
            return None

        # Add plot=full by default for comprehensive data
        if "plot" not in kwargs:
            kwargs["plot"] = "full"

        # Always request Rotten Tomatoes data
        kwargs["tomatoes"] = True

        try:
            # First attempt
            response = self.client.get(**kwargs)

            if response and response.get("response") == "True":
                return response
            elif response and response.get("error"):
                self.logger.warning(f"OMDb API error: {response.get('error')}")
                return None

        except Exception as e:
            self.logger.warning(f"OMDb API request failed: {e}")

            # Retry once on failure
            try:
                self.logger.info("Retrying OMDb API request...")
                response = self.client.get(**kwargs)

                if response and response.get("response") == "True":
                    return response

            except Exception as retry_error:
                self.logger.error(f"OMDb API retry failed: {retry_error}")

        return None

    def _parse_response(self, response: Dict[str, Any]) -> OMDbMetadata:
        """
        Parse OMDb API response into OMDbMetadata dataclass.

        Args:
            response (Dict[str, Any]): Raw API response

        Returns:
            OMDbMetadata: Parsed and sanitized metadata
        """
        # Parse ratings list
        ratings = []
        for rating_data in response.get("ratings", []):
            ratings.append(OMDbRating(
                source=rating_data.get("source", "Unknown"),
                value=rating_data.get("value", "N/A")
            ))

        # Create metadata object with all available fields
        metadata = OMDbMetadata(
            imdb_id=response.get("imdb_id"),
            type=response.get("type"),
            title=response.get("title"),
            year=response.get("year"),
            rated=response.get("rated"),
            released=response.get("released"),
            runtime=response.get("runtime"),
            genre=response.get("genre"),
            director=response.get("director"),
            writer=response.get("writer"),
            actors=response.get("actors"),
            plot=response.get("plot"),
            language=response.get("language"),
            country=response.get("country"),
            awards=response.get("awards"),
            poster=response.get("poster"),
            metascore=response.get("metascore"),
            imdb_rating=response.get("imdb_rating"),
            imdb_votes=response.get("imdb_votes"),
            ratings=ratings,
            dvd=response.get("dvd"),
            box_office=response.get("box_office"),
            production=response.get("production"),
            website=response.get("website"),
            total_seasons=response.get("total_seasons"),
            season=response.get("season"),
            episode=response.get("episode"),
            series_id=response.get("series_id"),
            response=True,
            error=None
        )

        return metadata

    async def get_metadata_for_item(self, item: MediaItem) -> Optional[OMDbMetadata]:
        """
        Get OMDb metadata for a media item.

        This method determines the appropriate OMDb query based on the media item's
        type and available identifiers. It prioritizes IMDb ID for accuracy but can
        fall back to title-based searches.

        Args:
            item (MediaItem): Media item to fetch metadata for

        Returns:
            Optional[OMDbMetadata]: Complete OMDb metadata or None if failed
        """
        if not self.enabled:
            return None

        try:
            response = None

            # Determine query parameters based on media type and available data
            if item.imdb_id:
                # Prefer IMDb ID for most accurate results
                self.logger.debug(f"Fetching OMDb data by IMDb ID: {item.imdb_id}")
                response = self._make_request(imdbid=item.imdb_id)

            elif item.item_type == "Episode" and item.series_name:
                # For episodes, try to get specific episode data
                self.logger.debug(
                    f"Fetching OMDb episode data: {item.series_name} "
                    f"S{item.season_number}E{item.episode_number}"
                )
                response = self._make_request(
                    title=item.series_name,
                    season=item.season_number,
                    episode=item.episode_number,
                    type="episode"
                )

            elif item.item_type == "Season" and item.series_name and item.season_number:
                # For seasons, get season-level data
                self.logger.debug(
                    f"Fetching OMDb season data: {item.series_name} "
                    f"Season {item.season_number}"
                )
                response = self._make_request(
                    title=item.series_name,
                    season=item.season_number,
                    type="series"
                )

            elif item.item_type == "Series" and item.name:
                # For series, get series-level data
                self.logger.debug(f"Fetching OMDb series data: {item.name}")
                response = self._make_request(
                    title=item.name,
                    type="series",
                    year=item.year if item.year else None
                )

            elif item.name:
                # Fallback to title search
                self.logger.debug(f"Fetching OMDb data by title: {item.name}")
                media_type = "movie" if item.item_type == "Movie" else None
                response = self._make_request(
                    title=item.name,
                    year=item.year if item.year else None,
                    type=media_type
                )

            if response:
                metadata = self._parse_response(response)
                self.logger.info(
                    f"Successfully fetched OMDb metadata for: {item.name} "
                    f"(IMDb: {metadata.imdb_rating}, RT: "
                    f"{metadata.ratings_dict.get('rotten_tomatoes', {}).get('value', 'N/A')})"
                )
                return metadata
            else:
                self.logger.warning(f"No OMDb data found for: {item.name}")
                return None

        except Exception as e:
            self.logger.error(f"Error fetching OMDb metadata for {item.name}: {e}")
            return None

    async def get_movie_metadata(self, imdb_id: Optional[str] = None,
                                 title: Optional[str] = None,
                                 year: Optional[int] = None) -> Optional[OMDbMetadata]:
        """
        Get metadata for a movie.

        Args:
            imdb_id (Optional[str]): IMDb ID of the movie
            title (Optional[str]): Title of the movie
            year (Optional[int]): Year of release

        Returns:
            Optional[OMDbMetadata]: Movie metadata or None if not found
        """
        if not self.enabled:
            return None

        if imdb_id:
            response = self._make_request(imdbid=imdb_id)
        elif title:
            response = self._make_request(title=title, year=year, type="movie")
        else:
            self.logger.error("Either imdb_id or title must be provided")
            return None

        return self._parse_response(response) if response else None

    async def get_series_metadata(self, imdb_id: Optional[str] = None,
                                  title: Optional[str] = None,
                                  year: Optional[int] = None) -> Optional[OMDbMetadata]:
        """
        Get metadata for a TV series.

        Args:
            imdb_id (Optional[str]): IMDb ID of the series
            title (Optional[str]): Title of the series
            year (Optional[int]): Year of first air

        Returns:
            Optional[OMDbMetadata]: Series metadata or None if not found
        """
        if not self.enabled:
            return None

        if imdb_id:
            response = self._make_request(imdbid=imdb_id)
        elif title:
            response = self._make_request(title=title, year=year, type="series")
        else:
            self.logger.error("Either imdb_id or title must be provided")
            return None

        return self._parse_response(response) if response else None

    async def get_episode_metadata(self, series_title: str,
                                   season: int,
                                   episode: int) -> Optional[OMDbMetadata]:
        """
        Get metadata for a specific TV episode.

        Args:
            series_title (str): Title of the TV series
            season (int): Season number
            episode (int): Episode number

        Returns:
            Optional[OMDbMetadata]: Episode metadata or None if not found
        """
        if not self.enabled:
            return None

        response = self._make_request(
            title=series_title,
            season=season,
            episode=episode,
            type="episode"
        )

        return self._parse_response(response) if response else None

    async def get_season_metadata(self, series_title: str,
                                  season: int) -> Optional[Dict[str, Any]]:
        """
        Get metadata for all episodes in a season.

        Args:
            series_title (str): Title of the TV series
            season (int): Season number

        Returns:
            Optional[Dict[str, Any]]: Season data with episode list or None
        """
        if not self.enabled:
            return None

        response = self._make_request(title=series_title, season=season)

        if response:
            # Parse each episode in the season
            episodes = []
            for ep_data in response.get("episodes", []):
                episodes.append({
                    "title": ep_data.get("title"),
                    "episode": ep_data.get("episode"),
                    "imdb_id": ep_data.get("imdb_id"),
                    "imdb_rating": ep_data.get("imdb_rating"),
                    "released": ep_data.get("released")
                })

            return {
                "title": response.get("title"),
                "season": response.get("season"),
                "total_seasons": response.get("total_seasons"),
                "episodes": episodes
            }

        return None