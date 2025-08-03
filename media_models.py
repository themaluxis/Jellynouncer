#!/usr/bin/env python3
"""
Jellynouncer Media Data Models

This module contains dataclasses and models for media items and related data structures.
The primary purpose is to provide a normalized, internal representation of media items
that combines data from both Jellyfin webhooks and direct API calls.

The MediaItem dataclass serves as the central data structure for the entire application,
providing consistent access to media metadata regardless of the original data source.
It includes automatic content hash generation for change detection capabilities.

Classes:
    MediaItem: Internal representation of media with comprehensive metadata

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
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

    **Understanding Python Dataclasses for Beginners:**

    A dataclass is a special type of class that automatically generates common
    methods like __init__, __repr__, and __eq__ based on the field definitions.
    This eliminates boilerplate code and ensures consistent behavior.

    The @dataclass decorator automatically creates:
    - Constructor that accepts all fields as parameters
    - String representation for debugging
    - Equality comparison between instances
    - Hash function (when frozen=True)

    **Key Features:**
    - Automatic content hash generation for change detection
    - Comprehensive metadata from multiple sources
    - Type hints for better code documentation and IDE support
    - Default values for optional fields
    - Post-initialization processing for derived fields

    **Content Hash for Change Detection:**

    The class automatically generates a content hash used for detecting
    meaningful changes between versions of the same item. This allows the
    service to distinguish between new items and upgraded versions of
    existing items.

    The hash includes technical specifications (video/audio codecs, resolution,
    channels) but excludes metadata that changes frequently (timestamps, file paths).
    This ensures that only significant quality upgrades trigger notifications.

    **Field Categories:**

    **Core Identification:**
    These fields uniquely identify the media item:

    Attributes:
        item_id (str): Unique Jellyfin identifier - primary key for database storage
        name (str): Display name of the item (movie title, episode name, etc.)
        item_type (str): Media type (Movie, Episode, Series, Audio, Album, Book, etc.)

    **Content Metadata:**
    These fields describe the media content:

    Attributes:
        year (Optional[int]): Release year for movies, air year for TV episodes
        series_name (Optional[str]): TV series name (for episodes only)
        season_number (Optional[int]): Season number within series (for episodes)
        episode_number (Optional[int]): Episode number within season (for episodes)
        overview (Optional[str]): Plot summary, description, or synopsis

    **Video Technical Specifications:**
    These fields describe video stream properties used for upgrade detection:

    Attributes:
        video_height (Optional[int]): Resolution height in pixels (720, 1080, 2160, etc.)
        video_width (Optional[int]): Resolution width in pixels (1280, 1920, 3840, etc.)
        video_codec (Optional[str]): Video codec (h264, hevc, av1, vp9, etc.)
        video_profile (Optional[str]): Codec profile (High, Main, etc.)
        video_range (Optional[str]): Dynamic range (SDR, HDR10, HDR10+, Dolby Vision)
        video_framerate (Optional[float]): Frames per second (23.976, 25, 29.97, 60, etc.)
        aspect_ratio (Optional[str]): Display aspect ratio (16:9, 21:9, 4:3, etc.)

    **Audio Technical Specifications:**
    These fields describe audio stream properties:

    Attributes:
        audio_codec (Optional[str]): Audio codec (aac, ac3, dts, flac, opus, etc.)
        audio_channels (Optional[int]): Channel count (2, 6, 8 for stereo, 5.1, 7.1)
        audio_language (Optional[str]): Primary audio language code (eng, spa, fra, etc.)
        audio_bitrate (Optional[int]): Audio bitrate in bits per second

    **External References:**
    These fields link to external movie/TV databases:

    Attributes:
        imdb_id (Optional[str]): Internet Movie Database identifier
        tmdb_id (Optional[str]): The Movie Database identifier
        tvdb_id (Optional[str]): The TV Database identifier

    **Extended Metadata from API:**
    These fields are populated from Jellyfin API calls (not available in webhooks):

    Attributes:
        date_created (Optional[str]): When item was added to Jellyfin (ISO format)
        date_modified (Optional[str]): When item was last modified (ISO format)
        runtime_ticks (Optional[int]): Duration in Jellyfin's tick format (10,000 ticks = 1ms)
        official_rating (Optional[str]): Content rating (G, PG, PG-13, R, TV-MA, etc.)
        genres (Optional[List[str]]): List of genre names
        studios (Optional[List[str]]): Production studios/companies
        tags (Optional[List[str]]): User-defined or imported tags
        community_rating (Optional[float]): User/community rating score
        critic_rating (Optional[float]): Professional critic rating score
        premiere_date (Optional[str]): Original air/release date

    **Music-Specific Metadata:**
    These fields apply to audio content:

    Attributes:
        album (Optional[str]): Album name (for music tracks)
        artists (Optional[List[str]]): List of artist names
        album_artist (Optional[str]): Primary album artist

    **Photo-Specific Metadata:**
    These fields apply to image content:

    Attributes:
        width (Optional[int]): Image width in pixels
        height (Optional[int]): Image height in pixels

    **Internal Tracking Fields:**
    These fields are used for service operations:

    Attributes:
        content_hash (Optional[str]): MD5 hash for change detection (auto-generated)
        timestamp (Optional[str]): When this object was created (auto-generated)
        file_path (Optional[str]): File system path to media file
        file_size (Optional[int]): File size in bytes
        last_modified (Optional[str]): File modification timestamp

    **Relationships:**
    These fields establish parent/child relationships:

    Attributes:
        series_id (Optional[str]): Parent series ID (for episodes and seasons)
        parent_id (Optional[str]): Direct parent ID (season for episodes, series for seasons)

    Example:
        ```python
        # Create a movie item
        movie = MediaItem(
            item_id="abc123def456",
            name="The Matrix",
            item_type="Movie",
            year=1999,
            video_height=1080,
            video_width=1920,
            video_codec="h264",
            audio_codec="ac3",
            audio_channels=6,
            imdb_id="tt0133093"
        )

        # Content hash is automatically generated in __post_init__
        print(f"Content hash: {movie.content_hash}")

        # Create a TV episode
        episode = MediaItem(
            item_id="def456ghi789",
            name="Pilot",
            item_type="Episode",
            series_name="Breaking Bad",
            season_number=1,
            episode_number=1,
            year=2008,
            video_height=1080,
            video_codec="hevc"
        )
        ```

    Note:
        The __post_init__ method handles initialization of derived fields like
        content_hash and timestamp. This ensures consistent object state
        regardless of how the object is created.

        The content hash is crucial for change detection - it allows the service
        to determine if an item has been upgraded (better quality) versus just
        being re-added with the same specifications.
    """

    # ==================== CORE IDENTIFICATION ====================
    # Required fields for all items
    item_id: str
    name: str
    item_type: str

    # ==================== CONTENT METADATA ====================
    # Basic metadata common across media types
    year: Optional[int] = None
    series_name: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    overview: Optional[str] = None

    # ==================== VIDEO TECHNICAL SPECIFICATIONS ====================
    # Video stream properties for quality detection and upgrade notifications
    video_height: Optional[int] = None
    video_width: Optional[int] = None
    video_codec: Optional[str] = None
    video_profile: Optional[str] = None
    video_range: Optional[str] = None  # SDR, HDR10, HDR10+, Dolby Vision
    video_framerate: Optional[float] = None
    aspect_ratio: Optional[str] = None

    # ==================== AUDIO TECHNICAL SPECIFICATIONS ====================
    # Audio stream properties for quality detection
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None
    audio_language: Optional[str] = None
    audio_bitrate: Optional[int] = None

    # ==================== EXTERNAL REFERENCES ====================
    # External provider IDs for linking to movie/TV databases
    imdb_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None

    # ==================== EXTENDED METADATA FROM API ====================
    # These fields come from Jellyfin API calls (not available in webhook)
    date_created: Optional[str] = None
    date_modified: Optional[str] = None
    runtime_ticks: Optional[int] = None  # Jellyfin uses "ticks" for duration (10,000 ticks = 1ms)
    official_rating: Optional[str] = None  # MPAA rating (G, PG, R), TV rating (TV-MA), etc.
    genres: Optional[List[str]] = None  # List of genre names
    studios: Optional[List[str]] = None  # Production companies/studios
    tags: Optional[List[str]] = None  # User-defined or imported tags
    community_rating: Optional[float] = None  # User community rating
    critic_rating: Optional[float] = None  # Professional critic rating
    premiere_date: Optional[str] = None  # Original air/release date

    # ==================== MUSIC-SPECIFIC METADATA ====================
    # Fields specific to audio content
    album: Optional[str] = None
    artists: Optional[List[str]] = None  # List of artist names
    album_artist: Optional[str] = None  # Primary album artist

    # ==================== PHOTO-SPECIFIC METADATA ====================
    # Fields specific to image content
    width: Optional[int] = None  # Image width in pixels
    height: Optional[int] = None  # Image height in pixels

    # ==================== INTERNAL TRACKING ====================
    # Fields used for service operations and change detection
    content_hash: Optional[str] = None  # MD5 hash for change detection (auto-generated)
    timestamp: Optional[str] = None  # Object creation timestamp (auto-generated)
    file_path: Optional[str] = None  # File system path
    file_size: Optional[int] = None  # File size in bytes
    last_modified: Optional[str] = None  # File modification timestamp

    # ==================== RELATIONSHIPS ====================
    # Parent/child relationships for complex media structures
    series_id: Optional[str] = None  # Parent series ID (for episodes and seasons)
    parent_id: Optional[str] = None  # Direct parent ID (season for episodes, etc.)

    def __post_init__(self):
        """
        Initialize derived fields after dataclass construction.

        This method is automatically called after the dataclass __init__ method
        completes. It handles the generation of derived fields that depend on
        the values of other fields.

        **Content Hash Generation:**
        The content hash is an MD5 digest of key technical specifications that
        affect media quality. This hash is used to detect when the same item
        has been upgraded with better quality (higher resolution, better codec, etc.).

        **Fields included in hash:**
        - Video specifications: height, width, codec, profile, range
        - Audio specifications: codec, channels, language
        - File size (for detecting complete file replacements)

        **Fields excluded from hash:**
        - Timestamps (change frequently without quality impact)
        - File paths (can change during library reorganization)
        - Metadata (descriptions, ratings - don't affect quality)

        This ensures that only meaningful quality changes trigger upgrade notifications.
        """
        # Generate timestamp if not provided
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

        # Generate content hash for change detection
        if self.content_hash is None:
            self.content_hash = self._generate_content_hash()

    def _generate_content_hash(self) -> str:
        """
        Generate MD5 hash of key content specifications for change detection.

        This private method creates a hash that represents the "quality signature"
        of the media item. The hash includes technical specifications that affect
        media quality but excludes metadata that changes frequently.

        **Hash Algorithm:**
        1. Create dictionary of relevant technical specifications
        2. Convert to JSON string with sorted keys for consistency
        3. Generate MD5 hash of the JSON string
        4. Return hexadecimal digest

        Returns:
            str: MD5 hexadecimal digest of content specifications

        Example:
            Two MediaItem instances with the same technical specs will have
            identical content hashes, even if they have different timestamps
            or file paths.
        """
        # Build dictionary of fields that affect content quality
        hash_fields = {
            # Video specifications that indicate quality level
            'video_height': self.video_height,
            'video_width': self.video_width,
            'video_codec': self.video_codec,
            'video_profile': self.video_profile,
            'video_range': self.video_range,  # HDR vs SDR is significant

            # Audio specifications that indicate quality level
            'audio_codec': self.audio_codec,
            'audio_channels': self.audio_channels,
            'audio_language': self.audio_language,

            # File size can indicate quality differences
            'file_size': self.file_size,

            # Provider IDs can change when metadata is enhanced
            'imdb_id': self.imdb_id,
            'tmdb_id': self.tmdb_id,
            'tvdb_id': self.tvdb_id
        }

        # Convert to JSON string with sorted keys for consistent hashing
        hash_string = json.dumps(hash_fields, sort_keys=True, default=str)

        # Generate MD5 hash of the JSON string
        return hashlib.md5(hash_string.encode('utf-8')).hexdigest()