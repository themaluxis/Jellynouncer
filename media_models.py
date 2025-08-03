"""
JellyNotify Media Data Models

This module contains dataclasses and models for media items and related data structures.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List


@dataclass
class MediaItem:
    """
    Internal representation of a media item with comprehensive metadata.

    This dataclass represents a normalized media item that combines data from
    both Jellyfin webhooks and direct API calls. It includes all metadata
    needed for change detection and notification formatting.

    The class automatically generates a content hash used for detecting
    meaningful changes between versions of the same item. This allows the
    service to distinguish between new items and upgraded versions of
    existing items.

    Attributes:
        Core identification:
            item_id: Unique Jellyfin identifier
            name: Display name of the item
            item_type: Media type (Movie, Episode, Audio, etc.)

        Content metadata:
            year: Release/air year
            series_name: TV series name (for episodes)
            season_number/episode_number: TV episode identifiers
            overview: Description/synopsis

        Technical specifications:
            video_*: Video stream properties (resolution, codec, HDR, etc.)
            audio_*: Audio stream properties (codec, channels, language, etc.)

        External references:
            imdb_id, tmdb_id, tvdb_id: External database identifiers

        Extended metadata (from API):
            genres, studios, tags: Categorization data
            date_created, date_modified: Timestamp information
            runtime_ticks: Duration in Jellyfin's tick format

        Music-specific:
            album, artists, album_artist: Music metadata

        Photo-specific:
            width, height: Image dimensions

        Internal tracking:
            content_hash: MD5 hash for change detection
            timestamp: When this object was created
            file_path, file_size: File system information

    Example:
        ```python
        # Create a movie item
        movie = MediaItem(
            item_id="abc123",
            name="The Matrix",
            item_type="Movie",
            year=1999,
            video_height=1080,
            video_codec="h264",
            audio_codec="ac3",
            audio_channels=6
        )

        # Content hash is automatically generated
        print(movie.content_hash)  # "a1b2c3d4e5f6..."
        ```

    Note:
        The __post_init__ method handles initialization of default values
        and content hash generation. This ensures consistent object state
        regardless of how the object is created.
    """
    # Core identification fields - required for all items
    item_id: str
    name: str
    item_type: str

    # Basic metadata - common across media types
    year: Optional[int] = None
    series_name: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    overview: Optional[str] = None

    # Video technical specifications
    video_height: Optional[int] = None
    video_width: Optional[int] = None
    video_codec: Optional[str] = None
    video_profile: Optional[str] = None
    video_range: Optional[str] = None  # SDR, HDR10, HDR10+, Dolby Vision
    video_framerate: Optional[float] = None
    aspect_ratio: Optional[str] = None

    # Audio technical specifications
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None
    audio_language: Optional[str] = None
    audio_bitrate: Optional[int] = None

    # External provider IDs for linking to movie/TV databases
    imdb_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None

    # Enhanced metadata from Jellyfin API (not available in webhook)
    date_created: Optional[str] = None
    date_modified: Optional[str] = None
    runtime_ticks: Optional[int] = None  # Jellyfin uses "ticks" for duration
    official_rating: Optional[str] = None  # MPAA rating, etc.
    genres: Optional[List[str]] = None
    studios: Optional[List[str]] = None
    tags: Optional[List[str]] = None

    # Music-specific metadata
    album: Optional[str] = None
    artists: Optional[List[str]] = None
    album_artist: Optional[str] = None

    # Photo-specific metadata
    width: Optional[int] = None
    height: Optional[int] = None

    # Internal tracking and metadata
    timestamp: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    content_hash: Optional[str] = None  # For change detection
    last_modified: Optional[str] = None

    # Enhanced metadata for rich notifications
    series_id: Optional[str] = None
    parent_id: Optional[str] = None
    community_rating: Optional[float] = None
    critic_rating: Optional[float] = None
    premiere_date: Optional[str] = None
    end_date: Optional[str] = None

    # External rating data (fetched from rating services)
    omdb_imdb_rating: Optional[str] = None  # IMDb rating from OMDb (e.g., "8.5/10")
    omdb_rt_rating: Optional[str] = None  # Rotten Tomatoes rating from OMDb (e.g., "85%")
    omdb_metacritic_rating: Optional[str] = None  # Metacritic rating from OMDb (e.g., "72/100")
    tmdb_rating: Optional[float] = None  # TMDb average rating (0-10 scale)
    tmdb_vote_count: Optional[int] = None  # Number of TMDb votes
    tvdb_rating: Optional[float] = None  # TVDb rating (0-10 scale)

    # Rating fetch metadata
    ratings_last_updated: Optional[str] = None  # When ratings were last fetched
    ratings_fetch_failed: Optional[bool] = None  # If last rating fetch failed

    def __post_init__(self):
        """
        Initialize default values and generate content hash after object creation.

        This method is automatically called by dataclass after __init__.
        It handles:
        1. Setting timestamp if not provided
        2. Initializing list fields to empty lists if None
        3. Generating content hash for change detection

        The content hash is crucial for detecting meaningful changes between
        versions of the same media item (e.g., when a 720p movie is replaced
        with a 1080p version).
        """
        # Set current timestamp if not provided
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

        # Initialize list fields to empty lists to prevent None-related errors
        for field in ['genres', 'studios', 'tags', 'artists']:
            if getattr(self, field) is None:
                setattr(self, field, [])

        # Generate content hash for change detection
        if self.content_hash is None:
            self.content_hash = self.generate_content_hash()

    def generate_content_hash(self) -> str:
        """
        Generate MD5 hash representing the technical content state of this item.

        This hash is used to detect meaningful changes between versions of the
        same media item. It includes fields that typically change when content
        is upgraded (resolution, codecs, file size) but excludes fields that
        change frequently without representing actual content changes (timestamps).

        Returns:
            32-character hexadecimal MD5 hash string

        Example:
            ```python
            item1 = MediaItem(item_id="123", name="Movie", item_type="Movie",
                             video_height=720, video_codec="h264")
            item2 = MediaItem(item_id="123", name="Movie", item_type="Movie",
                             video_height=1080, video_codec="h264")

            # Different hashes indicate content change
            assert item1.content_hash != item2.content_hash
            ```

        Note:
            The hash includes technical specifications that matter for quality
            comparisons but excludes metadata like timestamps or descriptions
            that don't represent actual content changes.
        """
        # Fields that represent the technical content state
        key_fields = [
            str(self.video_height or ''),  # Resolution is key for upgrades
            str(self.video_codec or ''),  # Codec changes (h264 -> hevc)
            str(self.audio_codec or ''),  # Audio codec upgrades
            str(self.audio_channels or ''),  # Channel count changes (2.0 -> 5.1)
            str(self.video_range or ''),  # HDR status changes
            str(self.file_size or ''),  # File size indicates content change
            str(self.imdb_id or ''),  # External ID additions
            str(self.tmdb_id or ''),
            str(self.tvdb_id or '')
        ]

        # Join all fields with a delimiter and hash the result
        hash_input = "|".join(key_fields)
        return hashlib.md5(hash_input.encode()).hexdigest()