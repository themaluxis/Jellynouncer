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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List


@dataclass(slots=True)
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
    Basic descriptive information about the media:

    Attributes:
        year (Optional[int]): Release year for movies, air year for TV episodes
        series_name (Optional[str]): TV series name (for episodes and seasons)
        season_number (Optional[int]): Season number (for TV episodes)
        episode_number (Optional[int]): Episode number within season
        overview (Optional[str]): Content description, plot summary, or synopsis

    **Video Technical Specifications:**
    Video stream properties for quality detection and upgrade notifications:

    Attributes:
        video_height (Optional[int]): Video resolution height in pixels (720, 1080, 2160, etc.)
        video_width (Optional[int]): Video resolution width in pixels (1280, 1920, 3840, etc.)
        video_codec (Optional[str]): Video codec (h264, hevc, av1, mpeg2, etc.)
        video_profile (Optional[str]): Codec profile (High, Main, Main10, etc.)
        video_range (Optional[str]): Video range (SDR, HDR10, HDR10+, Dolby Vision)
        video_framerate (Optional[float]): Frames per second (23.976, 24, 25, 29.97, 30, 50, 60)
        aspect_ratio (Optional[str]): Display aspect ratio (16:9, 4:3, 2.35:1, etc.)
        video_title (Optional[str]): Video stream title/name from container
        video_type (Optional[str]): Stream type identifier
        video_language (Optional[str]): Video stream language code (eng, spa, fra, etc.)
        video_level (Optional[str]): Codec level specification (3.1, 4.0, 5.1, etc.)
        video_interlaced (Optional[bool]): Whether video uses interlaced scanning
        video_bitrate (Optional[int]): Video bitrate in bits per second
        video_bitdepth (Optional[int]): Color bit depth (8, 10, 12)
        video_colorspace (Optional[str]): Color space specification (bt709, bt2020nc, etc.)
        video_colortransfer (Optional[str]): Color transfer characteristics (bt709, smpte2084, etc.)
        video_colorprimaries (Optional[str]): Color primaries specification (bt709, bt2020, etc.)
        video_pixelformat (Optional[str]): Pixel format (yuv420p, yuv420p10le, etc.)
        video_refframes (Optional[int]): Number of reference frames used by codec

    **Audio Technical Specifications:**
    Audio stream properties for quality detection:

    Attributes:
        audio_codec (Optional[str]): Audio codec (aac, ac3, dts, flac, mp3, etc.)
        audio_channels (Optional[int]): Number of audio channels (2, 6, 8 for stereo, 5.1, 7.1)
        audio_language (Optional[str]): Audio language code (eng, spa, fra, etc.)
        audio_bitrate (Optional[int]): Audio bitrate in bits per second
        audio_title (Optional[str]): Audio stream title/name from container
        audio_type (Optional[str]): Stream type identifier
        audio_samplerate (Optional[int]): Sample rate in Hz (48000, 44100, 96000, etc.)
        audio_default (Optional[bool]): Whether this is the default audio track

    **Subtitle Information:**
    Subtitle/caption tracks available:

    Attributes:
        subtitle_title (Optional[str]): Subtitle stream title/name
        subtitle_type (Optional[str]): Subtitle stream type identifier
        subtitle_language (Optional[str]): Subtitle language code (eng, spa, fra, etc.)
        subtitle_codec (Optional[str]): Subtitle format (srt, ass, pgs, vtt, etc.)
        subtitle_default (Optional[bool]): Whether this is the default subtitle track
        subtitle_forced (Optional[bool]): Whether subtitle is forced display
        subtitle_external (Optional[bool]): Whether subtitle is external file vs embedded

    **External References:**
    External provider IDs for linking to movie/TV databases:

    Attributes:
        imdb_id (Optional[str]): Internet Movie Database identifier (tt1234567)
        tmdb_id (Optional[str]): The Movie Database identifier
        tvdb_id (Optional[str]): The TV Database identifier
        tvdb_slug (Optional[str]): TVDB URL slug identifier

    **Server Information:**
    Context about the Jellyfin server:

    Attributes:
        server_id (Optional[str]): Jellyfin server unique identifier
        server_name (Optional[str]): Human-readable server name
        server_version (Optional[str]): Jellyfin server version string
        server_url (Optional[str]): Public URL of the Jellyfin server
        notification_type (Optional[str]): Type of notification event (ItemAdded, etc.)

    **File System Information:**
    File system and library organization:

    Attributes:
        file_path (Optional[str]): File system path to the media file
        library_name (Optional[str]): Name of the Jellyfin library containing this item
        file_size (Optional[int]): File size in bytes

    **TV Series Data:**
    Detailed TV series organization and numbering:

    Attributes:
        series_id (Optional[str]): Parent series unique identifier
        series_premiere_date (Optional[str]): Series premiere date
        season_id (Optional[str]): Parent season unique identifier
        season_number_padded (Optional[str]): Zero-padded season number (01, 02, etc.)
        season_number_padded_3 (Optional[str]): Three-digit padded season number (001, 002, etc.)
        episode_number_padded (Optional[str]): Zero-padded episode number (05, 10, etc.)
        episode_number_padded_3 (Optional[str]): Three-digit padded episode number (005, 010, etc.)
        air_time (Optional[str]): Episode air time

    **Extended Metadata from API:**
    These fields come from Jellyfin API calls (not available in webhook):

    Attributes:
        date_created (Optional[str]): When item was added to Jellyfin
        date_modified (Optional[str]): When item was last modified in Jellyfin
        runtime_ticks (Optional[int]): Duration in Jellyfin's tick format (10,000 ticks = 1ms)
        runtime_formatted (Optional[str]): Human-readable duration string (2h 15m)
        official_rating (Optional[str]): MPAA rating (G, PG, R), TV rating (TV-MA), etc.
        tagline (Optional[str]): Marketing tagline or promotional text
        genres (List[str]): List of genre names (Action, Comedy, Drama, etc.)
        studios (List[str]): Production companies/studios
        tags (List[str]): User-defined or imported tags

    **Music-Specific Metadata:**
    Fields specific to audio content:

    Attributes:
        album (Optional[str]): Album name (for music tracks)
        artists (List[str]): List of artist names
        album_artist (Optional[str]): Primary album artist

    **Photo-Specific Metadata:**
    Fields specific to image content:

    Attributes:
        width (Optional[int]): Image width in pixels
        height (Optional[int]): Image height in pixels

    **Internal Tracking Fields:**
    Fields used for service operations and change detection:

    Attributes:
        content_hash (str): MD5 hash for change detection (auto-generated)
        timestamp_created (str): When this object was created (auto-generated)
        timestamp (Optional[str]): Local timestamp with timezone from webhook
        utc_timestamp (Optional[str]): UTC timestamp from webhook
        premiere_date (Optional[str]): Original release/air date

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
        content_hash and timestamp_created. This ensures consistent object state
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

    # Additional video properties from webhook for template customization
    video_title: Optional[str] = None
    video_type: Optional[str] = None
    video_language: Optional[str] = None
    video_level: Optional[str] = None
    video_interlaced: Optional[bool] = None
    video_bitrate: Optional[int] = None
    video_bitdepth: Optional[int] = None
    video_colorspace: Optional[str] = None
    video_colortransfer: Optional[str] = None
    video_colorprimaries: Optional[str] = None
    video_pixelformat: Optional[str] = None
    video_refframes: Optional[int] = None

    # ==================== AUDIO TECHNICAL SPECIFICATIONS ====================
    # Audio stream properties for quality detection
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None
    audio_language: Optional[str] = None
    audio_bitrate: Optional[int] = None

    # Additional audio properties from webhook for template customization
    audio_title: Optional[str] = None
    audio_type: Optional[str] = None
    audio_samplerate: Optional[int] = None
    audio_default: Optional[bool] = None

    # ==================== SUBTITLE INFORMATION ====================
    # Subtitle properties from webhook for template customization
    subtitle_title: Optional[str] = None
    subtitle_type: Optional[str] = None
    subtitle_language: Optional[str] = None
    subtitle_codec: Optional[str] = None
    subtitle_default: Optional[bool] = None
    subtitle_forced: Optional[bool] = None
    subtitle_external: Optional[bool] = None

    # ==================== EXTERNAL REFERENCES ====================
    # External provider IDs for linking to movie/TV databases
    imdb_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None

    # Additional provider ID from webhook
    tvdb_slug: Optional[str] = None

    # ==================== IMAGE/THUMBNAIL METADATA ====================
    # Image tags from Jellyfin for thumbnail generation
    # These tags are provided by Jellyfin webhooks and are required for the
    # ThumbnailManager to construct valid thumbnail URLs with proper authentication
    primary_image_tag: Optional[str] = None  # Main poster/cover art tag
    backdrop_image_tag: Optional[str] = None  # Background/backdrop image tag
    logo_image_tag: Optional[str] = None  # Logo/branding image tag
    thumb_image_tag: Optional[str] = None  # Thumbnail image tag (alternative)
    banner_image_tag: Optional[str] = None  # Banner image tag (for collections)

    # Image item IDs for cross-references (e.g., series image for episodes)
    series_primary_image_tag: Optional[str] = None  # For episodes to use series poster
    parent_backdrop_image_tag: Optional[str] = None  # Parent item backdrop (for episodes/seasons)
    parent_logo_image_tag: Optional[str] = None  # Parent item logo (for episodes/seasons)

    # ==================== SERVER INFORMATION ====================
    # Server context from webhook for template customization
    server_id: Optional[str] = None
    server_name: Optional[str] = None
    server_version: Optional[str] = None
    server_url: Optional[str] = None
    notification_type: Optional[str] = None

    # ==================== FILE SYSTEM INFORMATION ====================
    # File system data from webhook for template customization
    file_path: Optional[str] = None
    library_name: Optional[str] = None

    # ==================== TV SERIES DATA ====================
    # TV series fields
    series_id: Optional[str] = None  # Parent series ID (for episodes and seasons)
    series_premiere_date: Optional[str] = None
    season_id: Optional[str] = None
    season_number_padded: Optional[str] = None  # SeasonNumber00
    season_number_padded_3: Optional[str] = None  # SeasonNumber000
    episode_number_padded: Optional[str] = None  # EpisodeNumber00
    episode_number_padded_3: Optional[str] = None  # EpisodeNumber000
    air_time: Optional[str] = None

    # ==================== TIMESTAMP INFORMATION ====================
    # Timestamp data
    timestamp: Optional[str] = None  # Local timestamp
    utc_timestamp: Optional[str] = None  # UTC timestamp
    premiere_date: Optional[str] = None

    # ==================== EXTENDED METADATA FROM API ====================
    # These fields come from Jellyfin API calls (not available in webhook)
    date_created: Optional[str] = None
    date_modified: Optional[str] = None
    runtime_ticks: Optional[int] = None  # Jellyfin uses "ticks" for duration (10,000 ticks = 1ms)
    runtime_formatted: Optional[str] = None  # Human-readable runtime
    official_rating: Optional[str] = None  # MPAA rating (G, PG, R), TV rating (TV-MA), etc.
    tagline: Optional[str] = None
    genres: List[str] = field(default_factory=list)  # List of genre names
    studios: List[str] = field(default_factory=list)  # Production companies/studios
    tags: List[str] = field(default_factory=list)  # User-defined or imported tags

    # ==================== MUSIC-SPECIFIC METADATA ====================
    # Fields specific to audio content
    album: Optional[str] = None
    artists: List[str] = field(default_factory=list)  # List of artist names
    album_artist: Optional[str] = None  # Primary album artist

    # ==================== PHOTO-SPECIFIC METADATA ====================
    # Fields specific to image content
    width: Optional[int] = None  # Image width in pixels
    height: Optional[int] = None  # Image height in pixels

    # ==================== INTERNAL TRACKING ====================
    # Fields used for service operations and change detection
    timestamp_created: str = field(default="", init=False)  # Object creation timestamp (auto-generated)
    file_size: Optional[int] = None
    _content_hash: Optional[str] = field(default=None, init=False, repr=False)  # Cached hash storage  # File size in bytes

    def __post_init__(self) -> None:
        """
        Initialize only the timestamp after dataclass construction.
        
        Content hash generation is now lazy via cached_property for better performance.
        """
        # Set creation timestamp if not already set
        if not self.timestamp_created:
            self.timestamp_created = datetime.now(timezone.utc).isoformat()
    
    @property
    def content_hash(self) -> str:
        """
        Generate content hash lazily using Blake2b for better performance.
        
        Blake2b is faster than MD5 and cryptographically secure.
        This hash is generated only when first accessed, improving batch sync performance.
        
        **Fields included in hash:**
        - Video specifications: height, width, codec, profile, range
        - Audio specifications: codec, channels, language
        - File size (for detecting complete file replacements)
        
        Returns:
            str: Blake2b hash of technical specifications
        """
        # Return cached value if available
        if self._content_hash is not None:
            return self._content_hash
        
        # Generate hash data with only technical specifications
        hash_data = {
            # Core identification
            "item_id": self.item_id,
            "name": self.name,
            "item_type": self.item_type,

            # Video specifications (all technical properties for change detection)
            "video_height": self.video_height,
            "video_width": self.video_width,
            "video_codec": self.video_codec,
            "video_profile": self.video_profile,
            "video_range": self.video_range,
            "video_framerate": self.video_framerate,
            "video_bitrate": self.video_bitrate,
            "video_bitdepth": self.video_bitdepth,

            # Audio specifications (all technical properties for change detection)
            "audio_codec": self.audio_codec,
            "audio_channels": self.audio_channels,
            "audio_bitrate": self.audio_bitrate,
            "audio_samplerate": self.audio_samplerate,

            # File path for detecting file replacements
            "file_path": self.file_path,
        }

        # Use Blake2b for faster hashing (2-3x faster than MD5)
        hash_string = json.dumps(hash_data, sort_keys=True, default=str)
        self._content_hash = hashlib.blake2b(
            hash_string.encode('utf-8'), 
            digest_size=32  # 256-bit hash
        ).hexdigest()
        
        return self._content_hash