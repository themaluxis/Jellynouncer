#!/usr/bin/env python3
"""
JellyNotify Discord Webhook Service

This module provides a comprehensive intermediate webhook service that sits between Jellyfin
media server and Discord, enabling intelligent notifications for new media additions and
quality upgrades.

Architecture Overview:
    The service follows a modular, async-first design with clear separation of concerns:

    1. Configuration Layer (Pydantic Models):
       - Type-safe configuration with validation
       - Environment variable overrides
       - Nested configuration structures

    2. Data Layer (DatabaseManager):
       - SQLite with WAL mode for concurrent access
       - Content hashing for change detection
       - Batch operations for performance

    3. Integration Layer (JellyfinAPI, DiscordNotifier):
       - Jellyfin API client with retry logic
       - Discord webhook with rate limiting
       - Template-based message formatting

    4. Business Logic Layer (ChangeDetector, WebhookService):
       - Intelligent change detection (resolution, codec, audio, HDR)
       - Webhook routing based on content type
       - Background sync and maintenance tasks

    5. API Layer (FastAPI):
       - RESTful endpoints for webhook processing
       - Health checks and diagnostics
       - Debug endpoints for troubleshooting

Key Features:
    - Smart change detection (new vs. upgraded content)
    - Multi-webhook routing (movies, TV, music)
    - Rate limiting and error recovery
    - Background library synchronization
    - Template-based Discord embeds
    - Comprehensive logging and monitoring

Example Usage:
    This service is designed to run as a Docker container with environment variables
    for configuration:

    ```bash
    # Set required environment variables
    export JELLYFIN_SERVER_URL="http://jellyfin:8096"
    export JELLYFIN_API_KEY="your_api_key_here"
    export JELLYFIN_USER_ID="your_user_id_here"
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

    # Run the service
    python main.py
    ```

Dependencies:
    - FastAPI: Modern web framework for the API layer
    - aiohttp: Async HTTP client for Discord webhooks
    - aiosqlite: Async SQLite database operations
    - Pydantic: Data validation and configuration management
    - Jinja2: Template engine for Discord embed formatting
    - jellyfin-apiclient-python: Official Jellyfin API client

Author: JellyNotify Development Team
Version: 2.0.0
License: MIT
"""

import os
import json
import asyncio
import logging
import logging.handlers
import time
import hashlib
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import aiosqlite
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from jellyfin_apiclient_python import JellyfinClient
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError
from pydantic import BaseModel, Field, ConfigDict, ValidationError, field_validator
import uvicorn


# ==================== CONFIGURATION MODELS ====================

class JellyfinConfig(BaseModel):
    """
    Configuration model for Jellyfin server connection settings.

    This class uses Pydantic for type validation and automatic parsing from
    configuration files or environment variables. The validation ensures that
    URLs are properly formatted and required fields are not empty.

    Attributes:
        server_url: The base URL of the Jellyfin server (e.g., "http://jellyfin:8096")
        api_key: API key for authenticating with Jellyfin server
        user_id: Jellyfin user ID to use for API requests
        client_name: Identifier for this client in Jellyfin logs
        client_version: Version string for this client
        device_name: Device name shown in Jellyfin dashboard
        device_id: Unique device identifier

    Example:
        ```python
        config = JellyfinConfig(
            server_url="http://localhost:8096",
            api_key="your_api_key_here",
            user_id="user123456789"
        )
        ```
    """
    # ConfigDict controls Pydantic behavior - forbid extra fields and strip whitespace
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    server_url: str = Field(..., description="Jellyfin server URL")
    api_key: str = Field(..., description="Jellyfin API key")
    user_id: str = Field(..., description="Jellyfin user ID")
    client_name: str = Field(default="JellyNotify-Discord-Webhook")
    client_version: str = Field(default="2.0.0")
    device_name: str = Field(default="jellynotify-webhook-service")
    device_id: str = Field(default="jellynotify-discord-webhook-001")

    @field_validator('server_url')
    @classmethod
    def validate_server_url(cls, v: str) -> str:
        """
        Validate and normalize Jellyfin server URL.

        This validator ensures the URL is properly formatted with a scheme
        (http/https) and removes trailing slashes that could cause API issues.

        Args:
            v: The server URL string to validate

        Returns:
            Normalized URL string without trailing slash

        Raises:
            ValueError: If URL format is invalid or missing required components

        Example:
            ```python
            # These are all valid and will be normalized:
            "http://jellyfin:8096/"  -> "http://jellyfin:8096"
            "https://jellyfin.example.com" -> "https://jellyfin.example.com"
            ```
        """
        if not v:
            raise ValueError("Jellyfin server URL cannot be empty")

        # Parse URL to validate structure
        parsed = urlparse(v)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid Jellyfin server URL format: {v}")

        if parsed.scheme not in ['http', 'https']:
            raise ValueError(f"Jellyfin server URL must use http or https: {v}")

        # Remove trailing slash to prevent double slashes in API calls
        return v.rstrip('/')

    @field_validator('api_key', 'user_id')
    @classmethod
    def validate_required_strings(cls, v: str) -> str:
        """
        Validate that required string fields are not empty or just whitespace.

        Args:
            v: String value to validate

        Returns:
            Stripped string value

        Raises:
            ValueError: If field is empty or contains only whitespace
        """
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class WebhookConfig(BaseModel):
    """
    Configuration for individual Discord webhooks.

    This class represents a single Discord webhook configuration, including
    the webhook URL, display name, and grouping settings. Multiple webhook
    configs can be used to route different types of content to different
    Discord channels.

    Attributes:
        url: Discord webhook URL (None if not configured)
        name: Human-readable name for this webhook
        enabled: Whether this webhook should receive notifications
        grouping: Configuration for notification grouping behavior

    Example:
        ```python
        webhook = WebhookConfig(
            url="https://discord.com/api/webhooks/123/abc",
            name="Movies Channel",
            enabled=True,
            grouping={"mode": "type", "delay_minutes": 5}
        )
        ```
    """
    model_config = ConfigDict(extra='forbid')

    url: Optional[str] = Field(default=None, description="Discord webhook URL")
    name: str = Field(..., description="Webhook display name")
    enabled: bool = Field(default=False, description="Whether webhook is enabled")
    grouping: Dict[str, Any] = Field(default_factory=dict, description="Grouping configuration")

    @field_validator('url')
    @classmethod
    def validate_webhook_url(cls, v: Optional[str]) -> Optional[str]:
        """
        Validate Discord webhook URL format.

        Discord webhooks must follow a specific URL pattern. This validator
        ensures the URL is correctly formatted to prevent runtime errors.

        Args:
            v: Webhook URL to validate (can be None)

        Returns:
            Validated webhook URL or None

        Raises:
            ValueError: If URL doesn't match Discord webhook pattern
        """
        if v is None:
            return None

        if not v.strip():
            return None

        v = v.strip()
        # Discord webhooks always follow this URL pattern
        if not v.startswith('https://discord.com/api/webhooks/'):
            raise ValueError(f"Discord webhook URL must start with 'https://discord.com/api/webhooks/': {v}")

        return v


class DiscordConfig(BaseModel):
    """
    Overall Discord integration configuration.

    This class manages all Discord-related settings including multiple webhooks,
    routing rules, and rate limiting parameters.

    Attributes:
        webhooks: Dictionary of webhook configurations by name
        routing: Rules for routing content to different webhooks
        rate_limit: Rate limiting parameters for Discord API calls
    """
    model_config = ConfigDict(extra='forbid')

    webhooks: Dict[str, WebhookConfig] = Field(default_factory=dict)
    routing: Dict[str, Any] = Field(default_factory=dict)
    rate_limit: Dict[str, Any] = Field(default_factory=dict)


class DatabaseConfig(BaseModel):
    """
    SQLite database configuration and validation.

    This class handles database-related settings and validates that the
    database path is writable and the parent directory exists or can be created.

    Attributes:
        path: Full path to SQLite database file
        wal_mode: Whether to enable WAL (Write-Ahead Logging) mode
        vacuum_interval_hours: How often to run VACUUM for maintenance

    Note:
        WAL mode is recommended for concurrent access scenarios as it allows
        multiple readers while a writer is active, improving performance.
    """
    model_config = ConfigDict(extra='forbid')

    path: str = Field(default="/app/data/jellyfin_items.db")
    wal_mode: bool = Field(default=True)
    vacuum_interval_hours: int = Field(default=24, ge=1, le=168)  # 1 hour to 1 week

    @field_validator('path')
    @classmethod
    def validate_db_path(cls, v: str) -> str:
        """
        Validate database path and ensure parent directory is writable.

        This validator checks that:
        1. The path is not empty
        2. The parent directory exists or can be created
        3. We have write permissions to the parent directory

        Args:
            v: Database file path

        Returns:
            Validated path as string

        Raises:
            ValueError: If path is invalid or not writable
        """
        if not v:
            raise ValueError("Database path cannot be empty")

        path = Path(v)
        parent_dir = path.parent

        # Try to create parent directory if it doesn't exist
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            raise ValueError(f"Cannot create database directory {parent_dir}: {e}")

        # Check write permissions
        if not os.access(parent_dir, os.W_OK):
            raise ValueError(f"No write permission for database directory: {parent_dir}")

        return str(path)


class TemplatesConfig(BaseModel):
    """
    Configuration for Jinja2 template files used to format Discord messages.

    This class manages paths to various template files used for different
    notification types. Templates allow customizing the appearance and content
    of Discord embed messages.

    Attributes:
        directory: Base directory containing template files
        Various template filenames for different notification types
    """
    model_config = ConfigDict(extra='forbid')

    directory: str = Field(default="/app/templates")
    new_item_template: str = Field(default="new_item.j2")
    upgraded_item_template: str = Field(default="upgraded_item.j2")
    new_items_by_event_template: str = Field(default="new_items_by_event.j2")
    upgraded_items_by_event_template: str = Field(default="upgraded_items_by_event.j2")
    new_items_by_type_template: str = Field(default="new_items_by_type.j2")
    upgraded_items_by_type_template: str = Field(default="upgraded_items_by_type.j2")
    new_items_grouped_template: str = Field(default="new_items_grouped.j2")
    upgraded_items_grouped_template: str = Field(default="upgraded_items_grouped.j2")

    @field_validator('directory')
    @classmethod
    def validate_template_directory(cls, v: str) -> str:
        """
        Validate that template directory exists and is readable.

        Args:
            v: Template directory path

        Returns:
            Validated directory path

        Raises:
            ValueError: If directory doesn't exist or isn't readable
        """
        if not v:
            raise ValueError("Template directory cannot be empty")

        path = Path(v)
        if not path.exists():
            raise ValueError(f"Template directory does not exist: {path}")

        if not path.is_dir():
            raise ValueError(f"Template path is not a directory: {path}")

        if not os.access(path, os.R_OK):
            raise ValueError(f"No read permission for template directory: {path}")

        return str(path)


class NotificationsConfig(BaseModel):
    """
    Configuration for notification behavior and appearance.

    This class controls which types of changes trigger notifications
    and what colors are used for different notification types.

    Attributes:
        watch_changes: Dict of change types to monitor (resolution, codec, etc.)
        colors: Dict of hex color codes for different notification types
    """
    model_config = ConfigDict(extra='forbid')

    watch_changes: Dict[str, bool] = Field(default_factory=dict)
    colors: Dict[str, int] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    """
    FastAPI server configuration.

    Controls how the web server runs, including bind address, port,
    and logging level.

    Attributes:
        host: IP address to bind to ("0.0.0.0" for all interfaces)
        port: TCP port to listen on
        log_level: Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    model_config = ConfigDict(extra='forbid')

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080, ge=1024, le=65535)
    log_level: str = Field(default="INFO")

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """
        Validate logging level against Python's standard levels.

        Args:
            v: Log level string

        Returns:
            Uppercase log level string

        Raises:
            ValueError: If log level is not recognized
        """
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v


class SyncConfig(BaseModel):
    """
    Configuration for Jellyfin library synchronization behavior.

    Controls when and how the service syncs with Jellyfin's library,
    including startup behavior and performance tuning.

    Attributes:
        startup_sync: Whether to sync library on service startup
        sync_batch_size: Number of items to process per API request
        api_request_delay: Delay between API requests to avoid overwhelming Jellyfin
    """
    model_config = ConfigDict(extra='forbid')

    startup_sync: bool = Field(default=True)
    sync_batch_size: int = Field(default=100, ge=10, le=1000)
    api_request_delay: float = Field(default=0.1, ge=0.0, le=5.0)


class AppConfig(BaseModel):
    """
    Top-level application configuration that combines all sub-configurations.

    This is the main configuration class that validates and holds all
    application settings. It uses composition to organize related settings
    into logical groups.

    Attributes:
        jellyfin: Jellyfin server connection settings
        discord: Discord webhook and notification settings
        database: SQLite database configuration
        templates: Jinja2 template settings
        notifications: Notification behavior settings
        server: Web server configuration
        sync: Library synchronization settings

    Example:
        ```python
        # Load from JSON file with environment overrides
        config = AppConfig(
            jellyfin=JellyfinConfig(...),
            discord=DiscordConfig(...),
            # ... other configs with defaults
        )
        ```
    """
    model_config = ConfigDict(extra='forbid')

    jellyfin: JellyfinConfig
    discord: DiscordConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    templates: TemplatesConfig = Field(default_factory=TemplatesConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)


# ==================== WEBHOOK PAYLOAD MODELS ====================

class WebhookPayload(BaseModel):
    """
    Expected webhook payload structure from Jellyfin Webhook Plugin.

    This class defines the data structure that Jellyfin sends when the
    webhook plugin triggers. It uses Pydantic for automatic validation
    and type conversion of incoming webhook data.

    The Jellyfin webhook plugin can be configured to send various fields
    using template variables like {{ItemId}}, {{Name}}, etc. This model
    represents the expected structure after template expansion.

    Attributes:
        ItemId: Unique identifier for the media item in Jellyfin
        Name: Display name of the media item
        ItemType: Type of media (Movie, Episode, Season, Series, Audio, etc.)
        Year: Release year (for movies) or air year (for TV)
        SeriesName: Name of TV series (for episodes)
        SeasonNumber00: Zero-padded season number (e.g., "01")
        EpisodeNumber00: Zero-padded episode number (e.g., "05")
        Overview: Description/synopsis of the media item

        Video fields (Video_0_*): Information about the primary video stream
        Audio fields (Audio_0_*): Information about the primary audio stream
        Provider fields (Provider_*): External database IDs (IMDb, TMDb, TVDb)

    Example:
        ```python
        # Typical movie payload from Jellyfin:
        payload = WebhookPayload(
            ItemId="abc123def456",
            Name="The Matrix",
            ItemType="Movie",
            Year=1999,
            Video_0_Height=1080,
            Video_0_Codec="h264",
            Audio_0_Codec="ac3",
            Audio_0_Channels=6,
            Provider_imdb="tt0133093"
        )
        ```

    Note:
        The model uses extra='ignore' to handle cases where Jellyfin sends
        additional fields that we don't specifically need to process.
    """
    model_config = ConfigDict(extra='ignore')  # Ignore unknown fields from Jellyfin

    # Required fields that should always be present
    ItemId: str = Field(..., description="Jellyfin item ID")
    Name: str = Field(..., description="Item name")
    ItemType: str = Field(..., description="Item type (Movie, Episode, etc.)")

    # Optional metadata fields
    Year: Optional[int] = Field(default=None, description="Release year")
    SeriesName: Optional[str] = Field(default=None, description="Series name for episodes")
    SeasonNumber00: Optional[str] = Field(default=None, description="Season number (zero-padded)")
    EpisodeNumber00: Optional[str] = Field(default=None, description="Episode number (zero-padded)")
    Overview: Optional[str] = Field(default=None, description="Item overview/description")

    # Video stream information (from MediaStreams)
    Video_0_Height: Optional[int] = Field(default=None, description="Video height in pixels")
    Video_0_Width: Optional[int] = Field(default=None, description="Video width in pixels")
    Video_0_Codec: Optional[str] = Field(default=None, description="Video codec")
    Video_0_Profile: Optional[str] = Field(default=None, description="Video profile")
    Video_0_VideoRange: Optional[str] = Field(default=None, description="Video range (HDR/SDR)")
    Video_0_FrameRate: Optional[float] = Field(default=None, description="Video frame rate")
    Video_0_AspectRatio: Optional[str] = Field(default=None, description="Video aspect ratio")

    # Audio stream information (from MediaStreams)
    Audio_0_Codec: Optional[str] = Field(default=None, description="Audio codec")
    Audio_0_Channels: Optional[int] = Field(default=None, description="Audio channel count")
    Audio_0_Language: Optional[str] = Field(default=None, description="Audio language")
    Audio_0_Bitrate: Optional[int] = Field(default=None, description="Audio bitrate")

    # External provider IDs (from ProviderIds)
    Provider_imdb: Optional[str] = Field(default=None, description="IMDb ID")
    Provider_tmdb: Optional[str] = Field(default=None, description="TMDb ID")
    Provider_tvdb: Optional[str] = Field(default=None, description="TVDb ID")


# ==================== DATA MODELS ====================

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


# ==================== LOGGING SETUP ====================

def setup_logging(log_level: str = "INFO", log_dir: str = "/app/logs") -> logging.Logger:
    """
    Set up comprehensive logging with rotation and custom formatting.

    This function configures Python's logging system with:
    - Console output for immediate feedback
    - File output with automatic rotation to prevent disk space issues
    - Custom formatting that includes timestamps and component names
    - Structured format suitable for both human reading and log analysis

    Args:
        log_level: Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory to store log files

    Returns:
        Configured logger instance for the application

    Example:
        ```python
        logger = setup_logging("DEBUG", "/var/logs/jellynotify")
        logger.info("Application starting up")
        logger.error("Database connection failed", exc_info=True)
        ```

    Note:
        The function uses RotatingFileHandler to automatically manage log file
        sizes and keep old logs as backup files. This prevents logs from
        consuming unlimited disk space over time.
    """
    # Create logs directory if it doesn't exist
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    class BracketFormatter(logging.Formatter):
        """
        Custom log formatter that uses brackets for structured, readable output.

        This formatter creates log lines in the format:
        [timestamp] [user] [level] [component] message

        The structured format makes it easier to parse logs programmatically
        while remaining human-readable.
        """

        def format(self, record):
            # Get UTC timestamp for consistency across time zones
            timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

            # Get user context (could be extended for multi-user scenarios)
            user = getattr(record, 'user', 'system')

            # Format the complete log message
            return f"[{timestamp}] [{user}] [{record.levelname}] [{record.name}] {record.getMessage()}"

    # Create the main application logger
    logger = logging.getLogger("jellynotify")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Clear any existing handlers to prevent duplicate logs
    logger.handlers.clear()

    # Console handler for immediate feedback during development/debugging
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(BracketFormatter())
    logger.addHandler(console_handler)

    # Rotating file handler to prevent logs from consuming unlimited disk space
    # 10MB per file, keep 5 backup files = maximum 50MB total
    file_handler = logging.handlers.RotatingFileHandler(
        filename=Path(log_dir) / "jellynotify.log",
        maxBytes=10 * 1024 * 1024,  # 10MB per file
        backupCount=5,  # Keep 5 backup files (jellynotify.log.1, .2, etc.)
        encoding='utf-8',
        mode='a'  # Append mode - preserves logs across restarts
    )
    file_handler.setLevel(getattr(logging, log_level.upper()))
    file_handler.setFormatter(BracketFormatter())
    logger.addHandler(file_handler)

    # Disable uvicorn's access logger to avoid duplication with our custom logging
    logging.getLogger("uvicorn.access").disabled = True

    # Log the configuration for verification and debugging
    logger.info("=" * 60)
    logger.info("JellyNotify Logging Configuration")
    logger.info("=" * 60)
    logger.info(f"Log Level: {log_level.upper()}")
    logger.info(f"Log Directory: {log_dir}")
    logger.info(f"Main Log File: {Path(log_dir) / 'jellynotify.log'}")
    logger.info(f"Max Log Size: 10MB")
    logger.info(f"Backup Count: 5")
    logger.info(f"Total Handlers: {len(logger.handlers)}")
    for i, handler in enumerate(logger.handlers):
        logger.info(f"Handler {i + 1}: {type(handler).__name__} - Level: {logging.getLevelName(handler.level)}")
    logger.info("=" * 60)

    return logger


# ==================== CONFIGURATION VALIDATION ====================

class ConfigurationValidator:
    """
    Comprehensive configuration validator with environment variable support.

    This class handles the complex process of loading configuration from multiple
    sources (JSON files, environment variables) and validating that all required
    settings are present and correctly formatted.

    The validation process includes:
    1. Loading base configuration from JSON/YAML files
    2. Applying environment variable overrides
    3. Validating using Pydantic models
    4. Performing additional custom validation
    5. Reporting errors and warnings

    Attributes:
        logger: Logger instance for reporting validation progress
        errors: List of validation errors that prevent startup
        warnings: List of validation warnings that don't prevent startup

    Example:
        ```python
        validator = ConfigurationValidator(logger)
        config = validator.load_and_validate_config("/app/config/config.json")
        # Config is now validated and ready to use
        ```
    """

    def __init__(self, logger: logging.Logger):
        """
        Initialize validator with logger and empty error/warning lists.

        Args:
            logger: Logger instance for reporting validation progress
        """
        self.logger = logger
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def load_and_validate_config(self, config_path: str = "/app/config/config.json") -> AppConfig:
        """
        Load configuration from file and environment, then validate.

        This is the main entry point for configuration loading. It orchestrates
        the entire process of loading, merging, and validating configuration data.

        Args:
            config_path: Path to JSON or YAML configuration file

        Returns:
            Fully validated AppConfig instance

        Raises:
            SystemExit: If validation fails with errors

        Example:
            ```python
            # Load config with environment variable overrides
            config = validator.load_and_validate_config()

            # Access validated configuration
            jellyfin_url = config.jellyfin.server_url
            ```
        """
        try:
            # Step 1: Load base configuration from file
            config_data = self._load_config_file(config_path)

            # Step 2: Apply environment variable overrides
            self._apply_env_overrides(config_data)

            # Step 3: Validate using Pydantic models (automatic type conversion and validation)
            config = AppConfig(**config_data)

            # Step 4: Perform additional custom validation
            self._validate_jellyfin_config(config.jellyfin)
            self._validate_discord_config(config.discord)
            self._validate_template_files(config.templates)

            # Step 5: Report results and exit if there are errors
            self._report_validation_results()

            return config

        except Exception as e:
            self.logger.error(f"Configuration validation failed: {e}")
            raise SystemExit(1)

    def _load_config_file(self, config_path: str) -> Dict[str, Any]:
        """
        Load configuration data from JSON or YAML file.

        Args:
            config_path: Path to configuration file

        Returns:
            Dictionary containing configuration data

        Raises:
            Various exceptions for file not found, invalid JSON/YAML, etc.
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                # Support both JSON and YAML formats
                if config_path.endswith('.yaml') or config_path.endswith('.yml'):
                    return yaml.safe_load(f)
                else:
                    return json.load(f)
        except FileNotFoundError:
            self.logger.warning(f"Configuration file not found: {config_path}, using defaults")
            return {}  # Return empty dict to use all defaults
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            self.errors.append(f"Invalid configuration file format: {e}")
            raise
        except Exception as e:
            self.errors.append(f"Error reading configuration file: {e}")
            raise

    def _apply_env_overrides(self, config_data: Dict[str, Any]) -> None:
        """
        Apply environment variable overrides to configuration data.

        This method implements the common pattern of allowing environment variables
        to override file-based configuration. This is especially important for
        containerized deployments where sensitive data (API keys) should not be
        stored in configuration files.

        Args:
            config_data: Configuration dictionary to modify in-place

        Environment Variables:
            JELLYFIN_SERVER_URL: Overrides jellyfin.server_url
            JELLYFIN_API_KEY: Overrides jellyfin.api_key
            JELLYFIN_USER_ID: Overrides jellyfin.user_id
            DISCORD_WEBHOOK_URL: Overrides discord.webhooks.default.url
            DISCORD_WEBHOOK_URL_MOVIES: Overrides discord.webhooks.movies.url
            DISCORD_WEBHOOK_URL_TV: Overrides discord.webhooks.tv.url
            DISCORD_WEBHOOK_URL_MUSIC: Overrides discord.webhooks.music.url

        Example:
            ```bash
            export JELLYFIN_API_KEY="secret_key_here"
            export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
            python main.py  # These values override config file
            ```
        """
        # Initialize nested dictionaries if they don't exist
        # This ensures we can set nested values even if the parent objects aren't in the config file
        if 'jellyfin' not in config_data:
            config_data['jellyfin'] = {}
        if 'discord' not in config_data:
            config_data['discord'] = {'webhooks': {}}
        if 'webhooks' not in config_data['discord']:
            config_data['discord']['webhooks'] = {}

        # Define mapping from environment variables to configuration paths
        # Each tuple represents the nested path to the configuration value
        env_mappings = {
            'JELLYFIN_SERVER_URL': ('jellyfin', 'server_url'),
            'JELLYFIN_API_KEY': ('jellyfin', 'api_key'),
            'JELLYFIN_USER_ID': ('jellyfin', 'user_id'),
            'DISCORD_WEBHOOK_URL': ('discord', 'webhooks', 'default', 'url'),
            'DISCORD_WEBHOOK_URL_MOVIES': ('discord', 'webhooks', 'movies', 'url'),
            'DISCORD_WEBHOOK_URL_TV': ('discord', 'webhooks', 'tv', 'url'),
            'DISCORD_WEBHOOK_URL_MUSIC': ('discord', 'webhooks', 'music', 'url'),
        }

        # Apply environment variable overrides
        for env_var, path in env_mappings.items():
            value = os.getenv(env_var)
            if value:  # Only override if environment variable is set
                self._set_nested_value(config_data, path, value)

        # Auto-enable webhooks that have URLs configured
        # This makes configuration easier by automatically enabling webhooks when URLs are provided
        for webhook_type in ['default', 'movies', 'tv', 'music']:
            webhook_path = ['discord', 'webhooks', webhook_type]
            if self._get_nested_value(config_data, webhook_path + ['url']):
                self._set_nested_value(config_data, webhook_path + ['enabled'], True)
                # Set default names for webhooks
                if webhook_type == 'default':
                    self._set_nested_value(config_data, webhook_path + ['name'], 'General')
                else:
                    self._set_nested_value(config_data, webhook_path + ['name'], webhook_type.title())

        # Enable routing if any specific webhooks are configured
        # This automatically enables smart routing when multiple webhooks are set up
        if any(self._get_nested_value(config_data, ['discord', 'webhooks', wh, 'url'])
               for wh in ['movies', 'tv', 'music']):
            self._set_nested_value(config_data, ['discord', 'routing', 'enabled'], True)

    def _set_nested_value(self, data: Dict[str, Any], path: List[str], value: Any) -> None:
        """
        Set a value at a nested path in a dictionary, creating intermediate dicts as needed.

        Args:
            data: Dictionary to modify
            path: List of keys representing the path to the value
            value: Value to set

        Example:
            ```python
            data = {}
            _set_nested_value(data, ['a', 'b', 'c'], 'value')
            # Result: data = {'a': {'b': {'c': 'value'}}}
            ```
        """
        current = data
        # Navigate to the parent of the target location, creating dicts as needed
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        # Set the final value
        current[path[-1]] = value

    def _get_nested_value(self, data: Dict[str, Any], path: List[str]) -> Any:
        """
        Get a value from a nested path in a dictionary.

        Args:
            data: Dictionary to read from
            path: List of keys representing the path to the value

        Returns:
            Value at the path, or None if path doesn't exist
        """
        current = data
        try:
            for key in path:
                current = current[key]
            return current
        except (KeyError, TypeError):
            return None  # Path doesn't exist

    def _validate_jellyfin_config(self, jellyfin_config: JellyfinConfig) -> None:
        """
        Perform additional validation on Jellyfin configuration.

        Args:
            jellyfin_config: Validated Jellyfin configuration

        Note:
            Most validation is handled by Pydantic models, but this method
            can be extended for runtime validation that requires network
            connectivity or other external resources.
        """
        # Pydantic models already handle most validation
        # This method can be extended for additional checks like:
        # - Testing connectivity to Jellyfin server
        # - Validating API key format
        # - Checking user permissions
        pass

    def _validate_discord_config(self, discord_config: DiscordConfig) -> None:
        """
        Validate Discord webhook configuration and report status.

        Args:
            discord_config: Validated Discord configuration
        """
        # Find all enabled webhooks with valid URLs
        enabled_webhooks = [
            name for name, webhook in discord_config.webhooks.items()
            if webhook.enabled and webhook.url
        ]

        if not enabled_webhooks:
            self.errors.append("No Discord webhooks are configured and enabled")
        else:
            self.logger.info(f"Enabled Discord webhooks: {', '.join(enabled_webhooks)}")

    def _validate_template_files(self, templates_config: TemplatesConfig) -> None:
        """
        Validate that required template files exist and are readable.

        Args:
            templates_config: Validated templates configuration
        """
        template_dir = Path(templates_config.directory)

        # Check that essential templates exist
        required_templates = [
            templates_config.new_item_template,
            templates_config.upgraded_item_template,
        ]

        for template_name in required_templates:
            template_path = template_dir / template_name
            if not template_path.exists():
                self.errors.append(f"Required template file not found: {template_path}")
            elif not template_path.is_file():
                self.errors.append(f"Template path is not a file: {template_path}")

    def _report_validation_results(self) -> None:
        """
        Report validation results and exit if there are errors.

        Raises:
            SystemExit: If there are validation errors
        """
        # Report warnings first
        if self.warnings:
            self.logger.warning("Configuration warnings:")
            for warning in self.warnings:
                self.logger.warning(f"  - {warning}")

        # Report errors and exit if any exist
        if self.errors:
            self.logger.error("Configuration errors:")
            for error in self.errors:
                self.logger.error(f"  - {error}")
            raise SystemExit(1)

        self.logger.info("Configuration validation completed successfully")


# ==================== DATABASE MANAGER ====================

class DatabaseManager:
    """
    Enhanced SQLite database manager with WAL mode and comprehensive error handling.

    This class manages all database operations for the JellyNotify service,
    including table creation, item storage/retrieval, and maintenance tasks.

    Key features:
    - WAL (Write-Ahead Logging) mode for better concurrent access
    - Batch operations for improved performance
    - Content hash-based change detection
    - Automatic database maintenance (VACUUM)
    - Comprehensive error handling and logging

    Attributes:
        config: Database configuration settings
        logger: Logger instance for database operations
        db_path: Full path to SQLite database file
        wal_mode: Whether WAL mode is enabled

    Example:
        ```python
        db_manager = DatabaseManager(config.database, logger)
        await db_manager.initialize()

        # Save a media item
        item = MediaItem(item_id="123", name="Movie", item_type="Movie")
        success = await db_manager.save_item(item)

        # Retrieve it later
        retrieved = await db_manager.get_item("123")
        ```

    Note:
        WAL mode allows multiple readers to access the database while a writer
        is active, which improves performance in concurrent scenarios like
        webhook processing during library syncs.
    """

    def __init__(self, config: DatabaseConfig, logger: logging.Logger):
        """
        Initialize database manager with configuration and logging.

        Args:
            config: Database configuration settings
            logger: Logger instance for database operations
        """
        self.config = config
        self.logger = logger
        self.db_path = config.path
        self.wal_mode = config.wal_mode

        # Ensure the parent directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def initialize(self) -> None:
        """
        Initialize database tables and configure SQLite settings.

        This method sets up the database schema and configures SQLite for
        optimal performance and reliability. It includes:
        - Enabling WAL mode for concurrent access
        - Setting performance-oriented PRAGMA settings
        - Creating tables and indexes

        Raises:
            aiosqlite.Error: Database operation errors
            Exception: Unexpected initialization errors

        Example:
            ```python
            await db_manager.initialize()
            # Database is now ready for use
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Configure SQLite for performance and reliability
                if self.wal_mode:
                    await db.execute("PRAGMA journal_mode=WAL")  # Enable WAL mode
                    await db.execute("PRAGMA synchronous=NORMAL")  # Balanced safety/performance
                    await db.execute("PRAGMA temp_store=memory")  # Use memory for temp data
                    await db.execute("PRAGMA mmap_size=268435456")  # 256MB memory mapping
                    await db.execute("PRAGMA cache_size=-32000")  # 32MB cache
                    await db.execute("PRAGMA busy_timeout=30000")  # 30 second busy timeout

                # Create the main media items table with all metadata fields
                await db.execute("""
                                 CREATE TABLE IF NOT EXISTS media_items
                                 (
                                     -- Core identification
                                     item_id
                                     TEXT
                                     PRIMARY
                                     KEY,
                                     name
                                     TEXT
                                     NOT
                                     NULL,
                                     item_type
                                     TEXT
                                     NOT
                                     NULL,
                                     year
                                     INTEGER,
                                     series_name
                                     TEXT,
                                     season_number
                                     INTEGER,
                                     episode_number
                                     INTEGER,
                                     overview
                                     TEXT,

                                     -- Video specifications
                                     video_height
                                     INTEGER,
                                     video_width
                                     INTEGER,
                                     video_codec
                                     TEXT,
                                     video_profile
                                     TEXT,
                                     video_range
                                     TEXT,
                                     video_framerate
                                     REAL,
                                     aspect_ratio
                                     TEXT,

                                     -- Audio specifications
                                     audio_codec
                                     TEXT,
                                     audio_channels
                                     INTEGER,
                                     audio_language
                                     TEXT,
                                     audio_bitrate
                                     INTEGER,

                                     -- External provider IDs
                                     imdb_id
                                     TEXT,
                                     tmdb_id
                                     TEXT,
                                     tvdb_id
                                     TEXT,

                                     -- Extended metadata from API
                                     date_created
                                     TEXT,
                                     date_modified
                                     TEXT,
                                     runtime_ticks
                                     INTEGER,
                                     official_rating
                                     TEXT,
                                     genres
                                     TEXT, -- JSON string
                                     studios
                                     TEXT, -- JSON string  
                                     tags
                                     TEXT, -- JSON string

                                     -- Music metadata
                                     album
                                     TEXT,
                                     artists
                                     TEXT, -- JSON string
                                     album_artist
                                     TEXT,

                                     -- Image metadata
                                     width
                                     INTEGER,
                                     height
                                     INTEGER,

                                     -- Internal tracking
                                     timestamp
                                     TEXT,
                                     file_path
                                     TEXT,
                                     file_size
                                     INTEGER,
                                     content_hash
                                     TEXT,
                                     last_modified
                                     TEXT,
                                     created_at
                                     DATETIME
                                     DEFAULT
                                     CURRENT_TIMESTAMP,
                                     updated_at
                                     DATETIME
                                     DEFAULT
                                     CURRENT_TIMESTAMP
                                 )
                                 """)

                # Create indexes for common query patterns
                # These indexes speed up lookups and improve overall performance
                indexes = [
                    "CREATE INDEX IF NOT EXISTS idx_item_type ON media_items(item_type)",
                    "CREATE INDEX IF NOT EXISTS idx_series_name ON media_items(series_name)",
                    "CREATE INDEX IF NOT EXISTS idx_updated_at ON media_items(updated_at)",
                    "CREATE INDEX IF NOT EXISTS idx_content_hash ON media_items(content_hash)",
                    "CREATE INDEX IF NOT EXISTS idx_last_modified ON media_items(last_modified)",
                    "CREATE INDEX IF NOT EXISTS idx_date_created ON media_items(date_created)",
                ]

                for index_sql in indexes:
                    await db.execute(index_sql)

                await db.commit()
                self.logger.info("Database initialized successfully")

        except aiosqlite.Error as e:
            self.logger.error(f"Database initialization failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during database initialization: {e}")
            raise

    async def get_item(self, item_id: str) -> Optional[MediaItem]:
        """
        Retrieve a media item by its Jellyfin ID.

        Args:
            item_id: Jellyfin item identifier

        Returns:
            MediaItem instance if found, None otherwise

        Raises:
            aiosqlite.Error: Database query errors
            Exception: Unexpected errors during retrieval

        Example:
            ```python
            item = await db_manager.get_item("abc123")
            if item:
                print(f"Found: {item.name}")
            else:
                print("Item not found")
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Use Row factory to get column names with values
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM media_items WHERE item_id = ?", (item_id,)
                )
                row = await cursor.fetchone()

                if row:
                    # Convert database row to dictionary
                    item_dict = dict(row)

                    # Deserialize JSON fields back to Python lists
                    for field in ['genres', 'studios', 'tags', 'artists']:
                        if field in item_dict and isinstance(item_dict[field], str):
                            try:
                                item_dict[field] = json.loads(item_dict[field])
                            except (json.JSONDecodeError, TypeError):
                                # If JSON parsing fails, use empty list as fallback
                                item_dict[field] = []

                    return MediaItem(**item_dict)
                return None

        except aiosqlite.Error as e:
            self.logger.error(f"Database error retrieving item {item_id}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error retrieving item {item_id}: {e}")
            raise

    async def get_item_hash(self, item_id: str) -> Optional[str]:
        """
        Get only the content hash of an item (performance optimization).

        This method is used for change detection when we only need to know
        if an item has changed, not retrieve all its data.

        Args:
            item_id: Jellyfin item identifier

        Returns:
            Content hash string if item exists, None otherwise

        Example:
            ```python
            hash_value = await db_manager.get_item_hash("abc123")
            if hash_value == new_item.content_hash:
                print("Item unchanged")
            else:
                print("Item has been modified")
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT content_hash FROM media_items WHERE item_id = ?", (item_id,)
                )
                row = await cursor.fetchone()
                return row[0] if row else None

        except aiosqlite.Error as e:
            self.logger.error(f"Database error retrieving hash for {item_id}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error retrieving hash for {item_id}: {e}")
            raise

    async def save_item(self, item: MediaItem) -> bool:
        """
        Save or update a single media item in the database.

        This method uses INSERT OR REPLACE to handle both new items and updates
        to existing items. It automatically sets the updated_at timestamp.

        Args:
            item: MediaItem instance to save

        Returns:
            True if save successful, False otherwise

        Example:
            ```python
            item = MediaItem(item_id="123", name="Movie", item_type="Movie")
            success = await db_manager.save_item(item)
            if success:
                print("Item saved successfully")
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Convert MediaItem to dictionary for database storage
                item_dict = asdict(item)
                item_dict['updated_at'] = datetime.now(timezone.utc).isoformat()

                # Serialize list fields to JSON strings for storage
                for field in ['genres', 'studios', 'tags', 'artists']:
                    if field in item_dict and isinstance(item_dict[field], list):
                        item_dict[field] = json.dumps(item_dict[field])

                # Build dynamic SQL based on available fields
                columns = list(item_dict.keys())
                placeholders = ['?' for _ in columns]
                values = list(item_dict.values())

                sql = f"""
                    INSERT OR REPLACE INTO media_items 
                    ({', '.join(columns)}) 
                    VALUES ({', '.join(placeholders)})
                """

                await db.execute(sql, values)
                await db.commit()
                return True

        except aiosqlite.Error as e:
            self.logger.error(f"Database error saving item {item.item_id}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error saving item {item.item_id}: {e}")
            return False

    async def save_items_batch(self, items: List[MediaItem]) -> int:
        """
        Save multiple items in a single transaction for better performance.

        This method processes a list of MediaItems in a single database transaction,
        which is much faster than individual saves when processing large numbers
        of items (like during library sync).

        Args:
            items: List of MediaItem instances to save

        Returns:
            Number of items successfully saved

        Example:
            ```python
            items = [MediaItem(...), MediaItem(...), ...]
            saved_count = await db_manager.save_items_batch(items)
            print(f"Saved {saved_count}/{len(items)} items")
            ```
        """
        if not items:
            return 0

        saved_count = 0
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Use explicit transaction for atomic batch operation
                await db.execute("BEGIN TRANSACTION")

                for item in items:
                    try:
                        item_dict = asdict(item)
                        item_dict['updated_at'] = datetime.now(timezone.utc).isoformat()

                        # Serialize list fields to JSON
                        for field in ['genres', 'studios', 'tags', 'artists']:
                            if field in item_dict and isinstance(item_dict[field], list):
                                item_dict[field] = json.dumps(item_dict[field])

                        # Build dynamic SQL
                        columns = list(item_dict.keys())
                        placeholders = ['?' for _ in columns]
                        values = list(item_dict.values())

                        sql = f"""
                            INSERT OR REPLACE INTO media_items 
                            ({', '.join(columns)}) 
                            VALUES ({', '.join(placeholders)})
                        """

                        await db.execute(sql, values)
                        saved_count += 1

                    except Exception as e:
                        self.logger.warning(f"Failed to save item {item.item_id} in batch: {e}")
                        # Continue with other items rather than failing the entire batch

                await db.commit()
                self.logger.debug(f"Successfully saved {saved_count}/{len(items)} items in batch")

        except aiosqlite.Error as e:
            self.logger.error(f"Database error during batch save: {e}")
            try:
                await db.rollback()
            except:
                pass  # Rollback might fail if connection is closed
        except Exception as e:
            self.logger.error(f"Unexpected error during batch save: {e}")

        return saved_count

    async def vacuum_database(self) -> None:
        """
        Perform VACUUM operation to reclaim space and optimize database.

        The VACUUM command rebuilds the database file, reclaiming unused space
        and optimizing the database structure. This is important for long-running
        applications that frequently update data.

        Note:
            VACUUM can be time-consuming on large databases and requires
            exclusive access, so it should be run during maintenance windows.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("VACUUM")
                await db.commit()
                self.logger.info("Database vacuum completed successfully")
        except aiosqlite.Error as e:
            self.logger.error(f"Database vacuum failed: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error during database vacuum: {e}")

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive database statistics for monitoring and debugging.

        Returns:
            Dictionary containing database statistics including:
            - total_items: Total number of media items
            - item_types: Breakdown by media type
            - last_updated: Timestamp of most recent update
            - database_path: Path to database file
            - wal_mode: Whether WAL mode is enabled

        Example:
            ```python
            stats = await db_manager.get_stats()
            print(f"Database contains {stats['total_items']} items")
            print(f"Item types: {stats['item_types']}")
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Get total item count
                cursor = await db.execute("SELECT COUNT(*) FROM media_items")
                total_items = (await cursor.fetchone())[0]

                # Get breakdown by item type
                cursor = await db.execute(
                    "SELECT item_type, COUNT(*) FROM media_items GROUP BY item_type ORDER BY COUNT(*) DESC"
                )
                item_types = dict(await cursor.fetchall())

                # Get last update timestamp
                cursor = await db.execute("SELECT MAX(updated_at) FROM media_items")
                last_updated = (await cursor.fetchone())[0]

                return {
                    "total_items": total_items,
                    "item_types": item_types,
                    "last_updated": last_updated,
                    "database_path": self.db_path,
                    "wal_mode": self.wal_mode
                }

        except aiosqlite.Error as e:
            self.logger.error(f"Database error retrieving stats: {e}")
            return {"error": str(e)}
        except Exception as e:
            self.logger.error(f"Unexpected error retrieving stats: {e}")
            return {"error": str(e)}


# ==================== JELLYFIN API ====================

class JellyfinAPI:
    """
    Enhanced Jellyfin API client with retry logic and comprehensive error handling.

    This class manages communication with the Jellyfin server, including:
    - Connection management with automatic retry
    - Authentication using API keys
    - Efficient batch retrieval of library items
    - Media metadata extraction and normalization
    - Connection health monitoring

    The client uses the official jellyfin-apiclient-python library but adds
    additional error handling, retry logic, and batching for better reliability
    in production environments.

    Attributes:
        config: Jellyfin server configuration
        logger: Logger instance for API operations
        client: Jellyfin API client instance
        last_connection_check: Timestamp of last connection check
        connection_check_interval: How often to verify connection
        max_retries: Maximum connection attempts before giving up
        retry_delay: Delay between connection retry attempts

    Example:
        ```python
        jellyfin_api = JellyfinAPI(config.jellyfin, logger)
        if await jellyfin_api.connect():
            items = await jellyfin_api.get_all_items(batch_size=100)
            for item_data in items:
                media_item = jellyfin_api.extract_media_item(item_data)
        ```
    """

    def __init__(self, config: JellyfinConfig, logger: logging.Logger):
        """
        Initialize Jellyfin API client with configuration and logging.

        Args:
            config: Jellyfin server configuration
            logger: Logger instance for API operations
        """
        self.config = config
        self.logger = logger
        self.client = None
        self.last_connection_check = 0
        self.connection_check_interval = 60  # Check connection every 60 seconds
        self.max_retries = 3
        self.retry_delay = 5  # Wait 5 seconds between retries

    async def connect(self) -> bool:
        """
        Connect to Jellyfin server with automatic retry logic.

        This method attempts to establish a connection to the Jellyfin server
        using the configured credentials. It includes retry logic to handle
        temporary network issues or server unavailability.

        Returns:
            True if connection successful, False otherwise

        Example:
            ```python
            if await jellyfin_api.connect():
                print("Connected to Jellyfin successfully")
            else:
                print("Failed to connect after all retries")
            ```

        Note:
            The method uses exponential backoff between retries to avoid
            overwhelming a server that might be temporarily overloaded.
        """
        for attempt in range(self.max_retries):
            try:
                # Create new client instance
                self.client = JellyfinClient()

                # Configure client with application identification
                self.client.config.app(
                    self.config.client_name,
                    self.config.client_version,
                    self.config.device_name,
                    self.config.device_id
                )

                # Configure SSL based on server URL scheme
                self.client.config.data["auth.ssl"] = self.config.server_url.startswith('https')

                # Use API key authentication (preferred for services)
                # This is more secure than username/password for automated services
                credentials = {
                    "Servers": [{
                        "AccessToken": self.config.api_key,
                        "address": self.config.server_url,
                        "UserId": self.config.user_id,
                        "Id": self.config.device_id
                    }]
                }

                self.client.authenticate(credentials, discover=False)

                # Test connection by getting system information
                response = self.client.jellyfin.get_system_info()
                if response:
                    server_name = response.get('ServerName', 'Unknown')
                    server_version = response.get('Version', 'Unknown')
                    self.logger.info(f"Connected to Jellyfin server: {server_name} v{server_version}")
                    return True

                self.logger.warning(f"Connection attempt {attempt + 1} failed: No response from server")

            except Exception as e:
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {e}")

                # Wait before retrying (except on last attempt)
                if attempt < self.max_retries - 1:
                    self.logger.info(f"Retrying connection in {self.retry_delay} seconds...")
                    await asyncio.sleep(self.retry_delay)
                else:
                    self.logger.error(f"Failed to connect to Jellyfin after {self.max_retries} attempts")

        return False

    async def is_connected(self) -> bool:
        """
        Check if currently connected to Jellyfin server.

        This method implements connection caching to avoid excessive API calls.
        It only performs actual connectivity checks at specified intervals.

        Returns:
            True if connected, False otherwise

        Example:
            ```python
            if await jellyfin_api.is_connected():
                # Safe to make API calls
                items = await jellyfin_api.get_all_items()
            else:
                # Need to reconnect
                await jellyfin_api.connect()
            ```
        """
        current_time = time.time()

        # Use cached result if check was recent (avoid spamming the server)
        if current_time - self.last_connection_check < self.connection_check_interval:
            return self.client is not None

        self.last_connection_check = current_time

        if not self.client:
            return False

        try:
            # Simple API call to verify connectivity
            response = self.client.jellyfin.get_system_info()
            is_connected = response is not None
            if not is_connected:
                self.logger.warning("Lost connection to Jellyfin server")
            return is_connected
        except Exception as e:
            self.logger.warning(f"Connection check failed: {e}")
            return False

    async def get_all_items(self, batch_size: int = 1000,
                            process_batch_callback: Optional[callable] = None) -> List[Dict[str, Any]]:
        """
        Retrieve all media items from Jellyfin using efficient batch processing.

        This method handles the complexity of paginated API requests to retrieve
        large libraries efficiently. It supports both collecting all items in
        memory and processing them in batches via a callback.

        Args:
            batch_size: Number of items to request per API call
            process_batch_callback: Optional async function to process each batch
                                  as it's received (memory efficient for large libraries)

        Returns:
            List of item dictionaries (empty if using callback)

        Raises:
            ConnectionError: If not connected to Jellyfin server

        Example:
            ```python
            # Collect all items in memory
            all_items = await jellyfin_api.get_all_items(batch_size=500)

            # Process items in batches (memory efficient)
            async def process_batch(items):
                for item in items:
                    # Process each item
                    pass

            await jellyfin_api.get_all_items(
                batch_size=100,
                process_batch_callback=process_batch
            )
            ```

        Note:
            Using a callback function is recommended for large libraries as it
            processes items incrementally without loading everything into memory.
        """
        # Verify connection before starting
        if not await self.is_connected():
            if not await self.connect():
                raise ConnectionError("Cannot connect to Jellyfin server")

        start_index = 0
        all_items = []
        total_items_processed = 0

        while True:
            try:
                # Request batch of items with comprehensive field selection
                response = self.client.jellyfin.user_items(params={
                    'recursive': True,
                    # Include all media types we care about
                    'includeItemTypes': "Movie,Series,Season,Episode,MusicVideo,Audio,MusicAlbum,MusicArtist,Book,Photo,BoxSet",
                    # Request all metadata fields we might need
                    'fields': "Overview,MediaStreams,ProviderIds,Path,MediaSources,DateCreated,DateModified,ProductionYear,RunTimeTicks,OfficialRating,Genres,Studios,Tags,IndexNumber,ParentIndexNumber,Album,Artists,AlbumArtist,Width,Height",
                    'startIndex': start_index,
                    'limit': batch_size
                })

                # Check for valid response
                if not response or 'Items' not in response:
                    break

                items = response['Items']
                if not items:
                    break  # No more items to process

                # Process this batch
                if process_batch_callback:
                    # Use callback for memory-efficient processing
                    await process_batch_callback(items)
                else:
                    # Collect in memory
                    all_items.extend(items)

                total_items_processed += len(items)
                start_index += len(items)

                # Log progress for large libraries
                if total_items_processed % (batch_size * 10) == 0:
                    self.logger.info(f"Processed {total_items_processed} items from Jellyfin...")

                # Rate limiting to avoid overwhelming Jellyfin server
                await asyncio.sleep(0.1)

            except Exception as e:
                self.logger.error(f"Error fetching items from Jellyfin: {e}")
                break

        self.logger.info(f"Completed processing {total_items_processed} items from Jellyfin")
        return all_items if not process_batch_callback else []

    def extract_media_item(self, jellyfin_item: Dict[str, Any]) -> MediaItem:
        """
        Extract and normalize MediaItem from Jellyfin API response.

        This method converts Jellyfin's API response format into our internal
        MediaItem representation, handling the complex nested structure of
        Jellyfin metadata and providing sensible defaults for missing data.

        Args:
            jellyfin_item: Raw item dictionary from Jellyfin API

        Returns:
            Normalized MediaItem instance

        Example:
            ```python
            # Raw data from Jellyfin API
            jellyfin_data = {
                'Id': 'abc123',
                'Name': 'The Matrix',
                'Type': 'Movie',
                'MediaStreams': [
                    {'Type': 'Video', 'Height': 1080, 'Codec': 'h264'},
                    {'Type': 'Audio', 'Codec': 'ac3', 'Channels': 6}
                ]
            }

            # Convert to our internal format
            media_item = jellyfin_api.extract_media_item(jellyfin_data)
            ```

        Note:
            This method handles the complexity of Jellyfin's variable data
            structure, where different media types may have different available
            fields. It provides robust error handling to ensure a valid
            MediaItem is always returned.
        """
        try:
            # Extract media stream information
            # Jellyfin stores technical details in a MediaStreams array
            media_streams = jellyfin_item.get('MediaStreams', [])
            video_stream = next((s for s in media_streams if s.get('Type') == 'Video'), {})
            audio_stream = next((s for s in media_streams if s.get('Type') == 'Audio'), {})

            # Extract provider IDs for external database linking
            provider_ids = jellyfin_item.get('ProviderIds', {})

            # Handle season/episode indexing based on item type
            # Jellyfin uses IndexNumber and ParentIndexNumber differently for different types
            season_number = None
            episode_number = None

            if jellyfin_item.get('Type') == 'Season':
                season_number = jellyfin_item.get('IndexNumber')
            elif jellyfin_item.get('Type') == 'Episode':
                episode_number = jellyfin_item.get('IndexNumber')
                season_number = jellyfin_item.get('ParentIndexNumber')

            # Create normalized MediaItem with all available data
            return MediaItem(
                # Core identification
                item_id=jellyfin_item['Id'],
                name=jellyfin_item.get('Name', ''),
                item_type=jellyfin_item.get('Type', ''),
                year=jellyfin_item.get('ProductionYear'),
                series_name=jellyfin_item.get('SeriesName'),
                season_number=season_number,
                episode_number=episode_number,
                overview=jellyfin_item.get('Overview'),

                # Video properties from primary video stream
                video_height=video_stream.get('Height'),
                video_width=video_stream.get('Width'),
                video_codec=video_stream.get('Codec'),
                video_profile=video_stream.get('Profile'),
                video_range=video_stream.get('VideoRange'),
                video_framerate=video_stream.get('RealFrameRate'),
                aspect_ratio=video_stream.get('AspectRatio'),

                # Audio properties from primary audio stream
                audio_codec=audio_stream.get('Codec'),
                audio_channels=audio_stream.get('Channels'),
                audio_language=audio_stream.get('Language'),
                audio_bitrate=audio_stream.get('BitRate'),

                # External provider IDs
                imdb_id=provider_ids.get('Imdb'),
                tmdb_id=provider_ids.get('Tmdb'),
                tvdb_id=provider_ids.get('Tvdb'),

                # Enhanced metadata from API
                date_created=jellyfin_item.get('DateCreated'),
                date_modified=jellyfin_item.get('DateModified'),
                runtime_ticks=jellyfin_item.get('RunTimeTicks'),
                official_rating=jellyfin_item.get('OfficialRating'),
                genres=jellyfin_item.get('Genres', []),
                # Handle Studios array - extract names from objects
                studios=[studio.get('Name') for studio in jellyfin_item.get('Studios', [])]
                if isinstance(jellyfin_item.get('Studios'), list) else [],
                tags=jellyfin_item.get('Tags', []),

                # Music-specific metadata
                album=jellyfin_item.get('Album'),
                artists=jellyfin_item.get('Artists', []),
                album_artist=jellyfin_item.get('AlbumArtist'),

                # Photo-specific metadata
                width=jellyfin_item.get('Width'),
                height=jellyfin_item.get('Height'),

                # File system information
                file_path=jellyfin_item.get('Path'),
                file_size=jellyfin_item.get('Size'),
                last_modified=jellyfin_item.get('DateModified')
            )

        except Exception as e:
            self.logger.error(f"Error extracting media item from Jellyfin data: {e}")
            # Return minimal MediaItem to prevent complete failure
            # This ensures the service continues operating even with malformed data
            return MediaItem(
                item_id=jellyfin_item.get('Id', 'unknown'),
                name=jellyfin_item.get('Name', 'Unknown'),
                item_type=jellyfin_item.get('Type', 'Unknown')
            )


# ==================== CHANGE DETECTOR ====================

class ChangeDetector:
    """
    Intelligent change detector for media quality upgrades and modifications.

    This class implements the core logic for detecting meaningful changes between
    versions of the same media item. It focuses on technical improvements that
    users care about, such as resolution upgrades, codec improvements, and
    audio enhancements.

    The detector is configurable to allow users to choose which types of changes
    trigger notifications, providing flexibility for different use cases.

    Attributes:
        config: Notifications configuration
        logger: Logger instance for change detection operations
        watch_changes: Dictionary of change types to monitor

    Example:
        ```python
        detector = ChangeDetector(config.notifications, logger)

        old_item = MediaItem(video_height=720, video_codec="h264")
        new_item = MediaItem(video_height=1080, video_codec="h264")

        changes = detector.detect_changes(old_item, new_item)
        # Returns: [{'type': 'resolution', 'old_value': 720, 'new_value': 1080, ...}]
        ```
    """

    def __init__(self, config: NotificationsConfig, logger: logging.Logger):
        """
        Initialize change detector with configuration and logging.

        Args:
            config: Notifications configuration including change monitoring settings
            logger: Logger instance for change detection operations
        """
        self.config = config
        self.logger = logger
        self.watch_changes = config.watch_changes

    def detect_changes(self, old_item: MediaItem, new_item: MediaItem) -> List[Dict[str, Any]]:
        """
        Detect meaningful changes between two versions of the same media item.

        This method compares technical specifications between old and new versions
        of a media item to identify upgrades worth notifying users about.

        Args:
            old_item: Previous version of the media item
            new_item: Current version of the media item

        Returns:
            List of change dictionaries, each containing:
            - type: Change category (resolution, codec, audio_codec, etc.)
            - field: Database field that changed
            - old_value: Previous value
            - new_value: Current value
            - description: Human-readable description of the change

        Example:
            ```python
            changes = detector.detect_changes(old_movie, new_movie)
            for change in changes:
                print(f"{change['type']}: {change['description']}")
            # Output: "resolution: Resolution changed from 720p to 1080p"
            ```

        Note:
            The method only detects changes that are enabled in the configuration.
            This allows users to customize which types of upgrades they want
            to be notified about.
        """
        changes = []

        try:
            # Resolution changes (most common upgrade scenario)
            if (self.watch_changes.get('resolution', True) and
                    old_item.video_height != new_item.video_height):
                changes.append({
                    'type': 'resolution',
                    'field': 'video_height',
                    'old_value': old_item.video_height,
                    'new_value': new_item.video_height,
                    'description': f"Resolution changed from {old_item.video_height}p to {new_item.video_height}p"
                })

            # Video codec changes (e.g., h264 -> hevc/av1)
            if (self.watch_changes.get('codec', True) and
                    old_item.video_codec != new_item.video_codec):
                changes.append({
                    'type': 'codec',
                    'field': 'video_codec',
                    'old_value': old_item.video_codec,
                    'new_value': new_item.video_codec,
                    'description': f"Video codec changed from {old_item.video_codec or 'Unknown'} to {new_item.video_codec or 'Unknown'}"
                })

            # Audio codec changes (e.g., ac3 -> dts, aac -> flac)
            if (self.watch_changes.get('audio_codec', True) and
                    old_item.audio_codec != new_item.audio_codec):
                changes.append({
                    'type': 'audio_codec',
                    'field': 'audio_codec',
                    'old_value': old_item.audio_codec,
                    'new_value': new_item.audio_codec,
                    'description': f"Audio codec changed from {old_item.audio_codec or 'Unknown'} to {new_item.audio_codec or 'Unknown'}"
                })

            # Audio channel changes (e.g., stereo -> 5.1 surround)
            if (self.watch_changes.get('audio_channels', True) and
                    old_item.audio_channels != new_item.audio_channels):
                # Create user-friendly channel descriptions
                channels_old = f"{old_item.audio_channels or 0} channel{'s' if (old_item.audio_channels or 0) != 1 else ''}"
                channels_new = f"{new_item.audio_channels or 0} channel{'s' if (new_item.audio_channels or 0) != 1 else ''}"
                changes.append({
                    'type': 'audio_channels',
                    'field': 'audio_channels',
                    'old_value': old_item.audio_channels,
                    'new_value': new_item.audio_channels,
                    'description': f"Audio channels changed from {channels_old} to {channels_new}"
                })

            # HDR status changes (SDR -> HDR10/Dolby Vision)
            if (self.watch_changes.get('hdr_status', True) and
                    old_item.video_range != new_item.video_range):
                changes.append({
                    'type': 'hdr_status',
                    'field': 'video_range',
                    'old_value': old_item.video_range,
                    'new_value': new_item.video_range,
                    'description': f"HDR status changed from {old_item.video_range or 'SDR'} to {new_item.video_range or 'SDR'}"
                })

            # File size changes (often indicates quality change)
            if (self.watch_changes.get('file_size', True) and
                    old_item.file_size != new_item.file_size):
                changes.append({
                    'type': 'file_size',
                    'field': 'file_size',
                    'old_value': old_item.file_size,
                    'new_value': new_item.file_size,
                    'description': "File size changed"
                })

            # Provider ID changes (metadata improvements)
            if self.watch_changes.get('provider_ids', True):
                # Check each provider ID separately
                for provider, old_val, new_val in [
                    ('imdb', old_item.imdb_id, new_item.imdb_id),
                    ('tmdb', old_item.tmdb_id, new_item.tmdb_id),
                    ('tvdb', old_item.tvdb_id, new_item.tvdb_id)
                ]:
                    # Only report if the value actually changed and isn't just None -> None
                    if old_val != new_val and (old_val or new_val):
                        changes.append({
                            'type': 'provider_ids',
                            'field': f'{provider}_id',
                            'old_value': old_val,
                            'new_value': new_val,
                            'description': f"{provider.upper()} ID changed from {old_val or 'None'} to {new_val or 'None'}"
                        })

            if changes:
                self.logger.debug(f"Detected {len(changes)} changes for item {new_item.item_id}")

        except Exception as e:
            self.logger.error(f"Error detecting changes for item {new_item.item_id}: {e}")

        return changes


# ==================== DISCORD NOTIFIER ====================

class DiscordNotifier:
    """
    Enhanced Discord webhook notifier with multi-webhook support and rate limiting.

    This class manages Discord webhook notifications with advanced features:
    - Multiple webhook support for different content types
    - Intelligent routing based on media type
    - Rate limiting to respect Discord's API limits
    - Template-based message formatting using Jinja2
    - Retry logic with exponential backoff
    - Server status notifications

    The notifier uses Jinja2 templates to create rich Discord embeds that provide
    detailed information about media additions and upgrades in an attractive format.

    Attributes:
        config: Discord configuration including webhooks and routing
        jellyfin_url: Base URL of Jellyfin server for generating links
        logger: Logger instance for notification operations
        routing_enabled: Whether to route different content to different webhooks
        webhooks: Dictionary of configured webhooks
        routing_config: Rules for content routing
        rate_limit: Rate limiting parameters
        webhook_rate_limits: Per-webhook rate limiting state
        session: aiohttp session for HTTP requests
        template_env: Jinja2 environment for template rendering

    Example:
        ```python
        notifier = DiscordNotifier(config.discord, jellyfin_url, logger)
        await notifier.initialize(config.templates)

        # Send notification for new movie
        success = await notifier.send_notification(movie_item, is_new=True)

        # Send notification for upgrade
        changes = [{'type': 'resolution', ...}]
        success = await notifier.send_notification(movie_item, changes, is_new=False)
        ```
    """

    def __init__(self, config: DiscordConfig, jellyfin_url: str, logger: logging.Logger):
        """
        Initialize Discord notifier with configuration and logging.

        Args:
            config: Discord configuration including webhooks and routing
            jellyfin_url: Base URL of Jellyfin server for generating links
            logger: Logger instance for notification operations
        """
        self.config = config
        self.jellyfin_url = jellyfin_url
        self.logger = logger
        self.routing_enabled = config.routing.get('enabled', False)
        self.webhooks = config.webhooks
        self.routing_config = config.routing
        self.rate_limit = config.rate_limit

        # Rate limiting state tracking per webhook
        self.webhook_rate_limits = {}
        self.session = None

        # Jinja2 template environment (initialized later)
        self.template_env = None

    async def initialize(self, templates_config: TemplatesConfig) -> None:
        """
        Initialize HTTP session and Jinja2 template environment.

        This method sets up the infrastructure needed for sending notifications:
        - HTTP session with appropriate timeouts and limits
        - Jinja2 template environment for message formatting
        - Template validation to ensure required templates exist

        Args:
            templates_config: Template configuration and file paths

        Raises:
            Various exceptions if initialization fails

        Example:
            ```python
            await notifier.initialize(config.templates)
            # Notifier is now ready to send notifications
            ```
        """
        try:
            # Initialize HTTP session with production-ready settings
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={'User-Agent': 'JellyNotify/2.0.0'}
            )

            # Initialize Jinja2 template environment
            self.template_env = Environment(
                loader=FileSystemLoader(templates_config.directory),
                autoescape=True,  # Prevent XSS in template output
                enable_async=False  # We don't need async templates
            )

            # Validate that required templates exist and can be loaded
            required_templates = [
                templates_config.new_item_template,
                templates_config.upgraded_item_template
            ]

            for template_name in required_templates:
                try:
                    self.template_env.get_template(template_name)
                except TemplateNotFound:
                    self.logger.error(f"Required template not found: {template_name}")
                    raise
                except TemplateSyntaxError as e:
                    self.logger.error(f"Template syntax error in {template_name}: {e}")
                    raise

            self.logger.info("Discord notifier initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize Discord notifier: {e}")
            raise

    async def close(self) -> None:
        """
        Clean up HTTP session and other resources.

        This method should be called during application shutdown to properly
        close network connections and release resources.
        """
        if self.session:
            try:
                await self.session.close()
                self.logger.debug("Discord notifier session closed")
            except Exception as e:
                self.logger.warning(f"Error closing Discord session: {e}")

    def _get_webhook_for_item(self, item: MediaItem) -> Optional[Dict[str, Any]]:
        """
        Determine which webhook to use for a specific media item.

        This method implements the routing logic that directs different types
        of content to appropriate Discord channels/webhooks.

        Args:
            item: Media item to find webhook for

        Returns:
            Dictionary with webhook name and config, or None if no webhook available

        Example:
            ```python
            movie = MediaItem(item_type="Movie", ...)
            webhook_info = notifier._get_webhook_for_item(movie)
            if webhook_info:
                webhook_name = webhook_info['name']        # "movies"
                webhook_config = webhook_info['config']    # WebhookConfig instance
            ```

        Note:
            The routing logic includes fallback behavior to ensure notifications
            are sent even if the preferred webhook is unavailable.
        """
        if not self.routing_enabled:
            # Simple mode: use first enabled webhook
            for webhook_name, webhook_config in self.webhooks.items():
                if webhook_config.enabled and webhook_config.url:
                    return {
                        'name': webhook_name,
                        'config': webhook_config
                    }
            return None

        # Smart routing mode: route based on content type
        item_type = item.item_type
        movie_types = self.routing_config.get('movie_types', ['Movie'])
        tv_types = self.routing_config.get('tv_types', ['Episode', 'Season', 'Series'])
        music_types = self.routing_config.get('music_types', ['Audio', 'MusicAlbum', 'MusicArtist'])
        fallback_webhook = self.routing_config.get('fallback_webhook', 'default')

        # Determine target webhook based on content type
        target_webhook = None
        if item_type in movie_types:
            target_webhook = 'movies'
        elif item_type in tv_types:
            target_webhook = 'tv'
        elif item_type in music_types:
            target_webhook = 'music'
        else:
            target_webhook = fallback_webhook

        # Check if target webhook is available
        if (target_webhook in self.webhooks and
                self.webhooks[target_webhook].enabled and
                self.webhooks[target_webhook].url):
            return {
                'name': target_webhook,
                'config': self.webhooks[target_webhook]
            }

        # Fall back to configured fallback webhook
        if (fallback_webhook in self.webhooks and
                self.webhooks[fallback_webhook].enabled and
                self.webhooks[fallback_webhook].url):
            self.logger.debug(f"Target webhook '{target_webhook}' not available, using fallback '{fallback_webhook}'")
            return {
                'name': fallback_webhook,
                'config': self.webhooks[fallback_webhook]
            }

        # Last resort: use any enabled webhook
        for webhook_name, webhook_config in self.webhooks.items():
            if webhook_config.enabled and webhook_config.url:
                self.logger.debug(f"Using '{webhook_name}' as last resort webhook")
                return {
                    'name': webhook_name,
                    'config': webhook_config
                }

        return None

    async def _wait_for_rate_limit(self, webhook_name: str) -> None:
        """
        Implement rate limiting to respect Discord's API limits.

        Discord has strict rate limits on webhook calls. This method ensures
        we don't exceed those limits by tracking request frequency per webhook
        and introducing delays when necessary.

        Args:
            webhook_name: Name of webhook to check rate limit for

        Note:
            Discord's webhook rate limit is typically 5 requests per 2 seconds
            per webhook URL. Exceeding this results in HTTP 429 responses.
        """
        # Initialize rate limit tracking for this webhook
        if webhook_name not in self.webhook_rate_limits:
            self.webhook_rate_limits[webhook_name] = {
                'last_request_time': 0,
                'request_count': 0
            }

        rate_limit_info = self.webhook_rate_limits[webhook_name]
        current_time = time.time()

        # Reset counter if enough time has passed
        period_seconds = self.rate_limit.get('period_seconds', 2)
        if current_time - rate_limit_info['last_request_time'] >= period_seconds:
            rate_limit_info['request_count'] = 0

        # Check if we need to wait to avoid rate limiting
        max_requests = self.rate_limit.get('requests_per_period', 5)
        if rate_limit_info['request_count'] >= max_requests:
            wait_time = period_seconds - (current_time - rate_limit_info['last_request_time'])
            if wait_time > 0:
                self.logger.debug(f"Rate limiting webhook '{webhook_name}', waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                rate_limit_info['request_count'] = 0

        # Update rate limit tracking
        rate_limit_info['last_request_time'] = time.time()
        rate_limit_info['request_count'] += 1

    async def send_notification(self, item: MediaItem, changes: Optional[List[Dict[str, Any]]] = None,
                                is_new: bool = True) -> bool:
        """
        Send a Discord notification for a media item.

        This is the main method for sending notifications. It handles the entire
        process from webhook selection to template rendering to HTTP delivery,
        with comprehensive error handling and retry logic.

        Args:
            item: Media item to notify about
            changes: List of changes (for upgrade notifications)
            is_new: Whether this is a new item (True) or upgrade (False)

        Returns:
            True if notification sent successfully, False otherwise

        Example:
            ```python
            # New item notification
            success = await notifier.send_notification(movie, is_new=True)

            # Upgrade notification
            changes = [{'type': 'resolution', 'old_value': 720, 'new_value': 1080}]
            success = await notifier.send_notification(movie, changes, is_new=False)
            ```
        """
        try:
            # Step 1: Find appropriate webhook for this item
            webhook_info = self._get_webhook_for_item(item)
            if not webhook_info:
                self.logger.warning("No suitable Discord webhook found for notification")
                return False

            webhook_name = webhook_info['name']
            webhook_config = webhook_info['config']
            webhook_url = webhook_config.url

            # Step 2: Apply rate limiting
            await self._wait_for_rate_limit(webhook_name)

            # Step 3: Determine template and color based on notification type
            if is_new:
                template_name = 'new_item.j2'
                color = 0x00FF00  # Green for new items
            else:
                template_name = 'upgraded_item.j2'
                color = self._get_change_color(changes)

            # Step 4: Load and render template
            try:
                template = self.template_env.get_template(template_name)
            except TemplateNotFound:
                self.logger.error(f"Template not found: {template_name}")
                return False
            except TemplateSyntaxError as e:
                self.logger.error(f"Template syntax error in {template_name}: {e}")
                return False

            # Step 5: Prepare template data
            template_data = {
                'item': asdict(item),  # Convert MediaItem to dict
                'changes': changes or [],  # List of changes for upgrades
                'is_new': is_new,  # Boolean flag
                'color': color,  # Embed color
                'jellyfin_url': self.jellyfin_url,  # For generating links
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'webhook_name': webhook_config.name,  # Human-readable webhook name
                'webhook_target': webhook_name  # Technical webhook identifier
            }

            # Step 6: Render template to JSON
            try:
                rendered = template.render(**template_data)
                payload = json.loads(rendered)
            except Exception as e:
                self.logger.error(f"Error rendering template {template_name}: {e}")
                return False

            # Step 7: Send to Discord with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    async with self.session.post(webhook_url, json=payload) as response:
                        if response.status == 204:
                            # Success! Discord webhooks return 204 No Content on success
                            self.logger.info(
                                f"Successfully sent notification for {item.name} to '{webhook_name}' webhook")
                            return True
                        elif response.status == 429:
                            # Rate limited by Discord - respect their Retry-After header
                            retry_after = int(response.headers.get('Retry-After', '60'))
                            self.logger.warning(
                                f"Discord webhook '{webhook_name}' rate limited, retry after {retry_after} seconds")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            # Other HTTP error
                            error_text = await response.text()
                            self.logger.error(
                                f"Discord webhook '{webhook_name}' failed with status {response.status}: {error_text}")

                            if attempt < max_retries - 1:
                                # Exponential backoff for retries
                                await asyncio.sleep(2 ** attempt)
                                continue
                            return False

                except aiohttp.ClientError as e:
                    self.logger.error(f"Network error sending to Discord webhook '{webhook_name}': {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return False
                except Exception as e:
                    self.logger.error(f"Unexpected error sending to Discord webhook '{webhook_name}': {e}")
                    return False

            return False

        except Exception as e:
            self.logger.error(f"Critical error in send_notification: {e}")
            return False

    def _get_change_color(self, changes: List[Dict[str, Any]]) -> int:
        """
        Determine Discord embed color based on the types of changes detected.

        Different types of upgrades get different colors to provide visual
        distinction in Discord. Colors are prioritized by significance.

        Args:
            changes: List of change dictionaries

        Returns:
            Integer color value for Discord embed

        Example:
            ```python
            resolution_change = [{'type': 'resolution', ...}]
            color = notifier._get_change_color(resolution_change)  # Returns gold color
            ```
        """
        try:
            colors = self.config.notifications.colors

            if not changes:
                return colors.get('new_item', 0x00FF00)

            # Extract change types for priority determination
            change_types = [change['type'] for change in changes if isinstance(change, dict) and 'type' in change]

            # Priority order: resolution > codec > HDR > audio > provider IDs
            if 'resolution' in change_types:
                return colors.get('resolution_upgrade', 0xFFD700)  # Gold
            elif 'codec' in change_types:
                return colors.get('codec_upgrade', 0xFF8C00)  # Dark orange
            elif 'hdr_status' in change_types:
                return colors.get('hdr_upgrade', 0xFF1493)  # Deep pink
            elif any(t in change_types for t in ['audio_codec', 'audio_channels']):
                return colors.get('audio_upgrade', 0x9370DB)  # Medium purple
            elif 'provider_ids' in change_types:
                return colors.get('provider_update', 0x1E90FF)  # Dodger blue
            else:
                return colors.get('new_item', 0x00FF00)  # Green fallback

        except Exception as e:
            self.logger.error(f"Error determining change color: {e}")
            return 0x00FF00  # Default to green

    async def send_server_status(self, is_online: bool) -> bool:
        """
        Send server status notification to all enabled webhooks.

        This method sends notifications when the Jellyfin server goes offline
        or comes back online, helping administrators monitor service health.

        Args:
            is_online: True if server is online, False if offline

        Returns:
            True if at least one notification was sent successfully

        Example:
            ```python
            # Server went offline
            await notifier.send_server_status(False)

            # Server came back online
            await notifier.send_server_status(True)
            ```
        """
        if not self.template_env:
            self.logger.error("Template environment not initialized")
            return False

        try:
            # Load server status template
            try:
                template = self.template_env.get_template('server_status.j2')
            except TemplateNotFound:
                self.logger.error("Server status template not found: server_status.j2")
                return False
            except TemplateSyntaxError as e:
                self.logger.error(f"Server status template syntax error: {e}")
                return False

            # Prepare template data
            template_data = {
                'is_online': is_online,
                'jellyfin_url': self.jellyfin_url,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

            # Render template
            try:
                rendered = template.render(**template_data)
                payload = json.loads(rendered)
            except Exception as e:
                self.logger.error(f"Error rendering server status template: {e}")
                return False

            # Send to all enabled webhooks
            success_count = 0
            total_webhooks = 0

            for webhook_name, webhook_config in self.webhooks.items():
                if not webhook_config.enabled or not webhook_config.url:
                    continue

                total_webhooks += 1

                try:
                    await self._wait_for_rate_limit(webhook_name)

                    async with self.session.post(webhook_config.url, json=payload) as response:
                        if response.status == 204:
                            success_count += 1
                            self.logger.debug(f"Server status sent to '{webhook_name}' webhook")
                        elif response.status == 429:
                            # Handle Discord rate limiting
                            retry_after = int(response.headers.get('Retry-After', '60'))
                            self.logger.warning(
                                f"Server status rate limited for '{webhook_name}': retry after {retry_after}s")
                            await asyncio.sleep(retry_after)
                            # Retry once
                            async with self.session.post(webhook_config.url, json=payload) as retry_response:
                                if retry_response.status == 204:
                                    success_count += 1
                                    self.logger.debug(f"Server status sent to '{webhook_name}' webhook (retry)")
                                else:
                                    self.logger.warning(
                                        f"Server status retry failed for '{webhook_name}': {retry_response.status}")
                        else:
                            error_text = await response.text()
                            self.logger.warning(
                                f"Server status failed for '{webhook_name}' webhook: {response.status} - {error_text}")

                except aiohttp.ClientError as e:
                    self.logger.error(f"Network error sending server status to '{webhook_name}': {e}")
                except Exception as e:
                    self.logger.error(f"Unexpected error sending server status to '{webhook_name}': {e}")

            self.logger.info(f"Server status notification sent to {success_count}/{total_webhooks} webhooks")
            return success_count > 0

        except Exception as e:
            self.logger.error(f"Critical error in send_server_status: {e}")
            return False

    def get_webhook_status(self) -> Dict[str, Any]:
        """
        Get status information for all configured webhooks.

        This method provides diagnostic information about webhook configuration
        and current state, useful for monitoring and troubleshooting.

        Returns:
            Dictionary containing webhook status information

        Example:
            ```python
            status = notifier.get_webhook_status()
            print(f"Routing enabled: {status['routing_enabled']}")
            for name, webhook in status['webhooks'].items():
                print(f"{name}: {'enabled' if webhook['enabled'] else 'disabled'}")
            ```
        """
        try:
            status = {
                "routing_enabled": self.routing_enabled,
                "webhooks": {},
                "routing_config": self.routing_config if self.routing_enabled else None
            }

            for webhook_name, webhook_config in self.webhooks.items():
                try:
                    webhook_url = webhook_config.url
                    url_preview = None

                    if webhook_url:
                        # Create safe URL preview (hide token for security)
                        parsed_url = urlparse(webhook_url)
                        if parsed_url.path:
                            path_parts = parsed_url.path.split('/')
                            if len(path_parts) >= 4:
                                webhook_id = path_parts[-2]
                                token_preview = path_parts[-1][:8] + "..." if len(path_parts[-1]) > 8 else path_parts[
                                    -1]
                                url_preview = f"https://discord.com/api/webhooks/{webhook_id}/{token_preview}"
                            else:
                                url_preview = webhook_url[:50] + "..." if len(webhook_url) > 50 else webhook_url
                        else:
                            url_preview = webhook_url[:50] + "..." if len(webhook_url) > 50 else webhook_url

                    status["webhooks"][webhook_name] = {
                        "name": webhook_config.name,
                        "enabled": webhook_config.enabled,
                        "has_url": bool(webhook_config.url),
                        "url_preview": url_preview,
                        "grouping": webhook_config.grouping,
                        "rate_limit_info": self.webhook_rate_limits.get(webhook_name, {})
                    }

                except Exception as e:
                    self.logger.warning(f"Error processing webhook '{webhook_name}' status: {e}")
                    status["webhooks"][webhook_name] = {
                        "name": webhook_name,
                        "enabled": False,
                        "has_url": False,
                        "url_preview": None,
                        "grouping": {},
                        "error": str(e)
                    }

            return status

        except Exception as e:
            self.logger.error(f"Error getting webhook status: {e}")
            return {
                "error": "Failed to get webhook status",
                "routing_enabled": False,
                "webhooks": {}
            }


# ==================== WEBHOOK SERVICE ====================

class WebhookService:
    """
    Main webhook service that orchestrates all components.

    This is the central service class that coordinates all the other components
    to provide the complete JellyNotify functionality. It manages:

    - Service initialization and configuration
    - Background maintenance tasks
    - Webhook processing from Jellyfin
    - Library synchronization
    - Health monitoring and diagnostics
    - Graceful shutdown procedures

    The service follows an async-first design and includes comprehensive error
    handling to ensure reliable operation in production environments.

    Attributes:
        logger: Main application logger
        config: Validated application configuration
        db: Database manager instance
        jellyfin: Jellyfin API client
        change_detector: Change detection logic
        discord: Discord notification manager

        Service state tracking:
        last_vacuum: Timestamp of last database vacuum
        server_was_offline: Whether Jellyfin server was previously offline
        sync_in_progress: Whether library sync is currently running
        is_background_sync: Whether current sync is running in background
        initial_sync_complete: Whether initial startup sync finished
        shutdown_event: AsyncIO event for coordinating shutdown

    Example:
        ```python
        service = WebhookService()
        await service.initialize()

        # Process webhook from Jellyfin
        result = await service.process_webhook(webhook_payload)

        # Get service health status
        health = await service.health_check()
        ```
    """

    def __init__(self):
        """
        Initialize webhook service with logging and configuration loading.

        This constructor handles the early initialization that must happen
        synchronously, including logging setup and configuration validation.
        The actual async initialization happens in the initialize() method.

        Raises:
            SystemExit: If configuration loading/validation fails
        """
        # Set up logging first so we can log everything else
        self.logger = setup_logging()

        # Initialize component references
        self.config = None
        self.db = None
        self.jellyfin = None
        self.change_detector = None
        self.discord = None

        # Service state tracking
        self.last_vacuum = 0
        self.server_was_offline = False
        self.sync_in_progress = False
        self.is_background_sync = False
        self.initial_sync_complete = False
        self.shutdown_event = asyncio.Event()

        # Load and validate configuration
        try:
            validator = ConfigurationValidator(self.logger)
            self.config = validator.load_and_validate_config()
            self.logger.info("Configuration loaded and validated successfully")
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise SystemExit(1)

        # Initialize components with validated configuration
        try:
            self.db = DatabaseManager(self.config.database, self.logger)
            self.jellyfin = JellyfinAPI(self.config.jellyfin, self.logger)
            self.change_detector = ChangeDetector(self.config.notifications, self.logger)
            self.discord = DiscordNotifier(self.config.discord, self.config.jellyfin.server_url, self.logger)
        except Exception as e:
            self.logger.error(f"Failed to initialize service components: {e}")
            raise SystemExit(1)

    async def initialize(self) -> None:
        """
        Perform async initialization of all service components.

        This method handles the initialization tasks that require async operations,
        including database setup, API connections, and initial library sync.

        The initialization process includes smart sync behavior:
        - If this is the first run, perform a blocking initial sync
        - If an init_complete marker exists, perform background sync
        - Handle connection failures gracefully

        Raises:
            Exception: If critical initialization steps fail
        """
        try:
            self.logger.info("Initializing JellyNotify Discord Webhook Service...")

            # Step 1: Initialize database
            try:
                await self.db.initialize()
                self.logger.info("Database initialized successfully")
            except Exception as e:
                self.logger.error(f"Database initialization failed: {e}")
                raise

            # Step 2: Initialize Discord notifier
            try:
                await self.discord.initialize(self.config.templates)
                self.logger.info("Discord notifier initialized successfully")
            except Exception as e:
                self.logger.error(f"Discord notifier initialization failed: {e}")
                raise

            # Step 3: Connect to Jellyfin and handle initial sync
            try:
                if await self.jellyfin.connect():
                    self.logger.info("Successfully connected to Jellyfin")

                    # Check for init_complete marker to determine sync strategy
                    init_complete_path = Path("/app/data/init_complete")

                    if self.config.sync.startup_sync:
                        if init_complete_path.exists():
                            # Subsequent startup - run background sync
                            self.logger.info("Init complete marker found - performing full background sync")
                            await self._perform_background_startup_sync()
                            self.initial_sync_complete = True
                        else:
                            # First startup - run blocking sync
                            self.logger.info("No init complete marker found - performing blocking initial sync")
                            await self._perform_startup_sync()
                            self.initial_sync_complete = True
                    else:
                        self.logger.info("Startup sync disabled in configuration")
                        self.initial_sync_complete = True
                else:
                    self.logger.error("Failed to connect to Jellyfin server")
                    self.server_was_offline = True

            except Exception as e:
                self.logger.error(f"Jellyfin connection failed: {e}")
                self.server_was_offline = True

            self.logger.info("JellyNotify service initialization completed")

        except Exception as e:
            self.logger.error(f"Service initialization failed: {e}")
            raise

    async def _perform_background_startup_sync(self) -> None:
        """
        Perform full background startup sync for subsequent service starts.

        This method runs a full library sync in the background without blocking
        webhook processing. It's used on subsequent service starts when we
        already have a populated database.
        """
        try:
            self.logger.info("Starting full background startup sync (init_complete marker found)...")

            # Create background task for sync
            sync_task = asyncio.create_task(self.sync_jellyfin_library(background=True))

            # Set up completion callback for logging
            def log_completion(task):
                try:
                    result = task.result()
                    if result.get("status") == "success":
                        self.logger.info("Background startup sync completed successfully")
                    else:
                        self.logger.warning(
                            f"Background startup sync completed with status: {result.get('status', 'unknown')} - keeping init_complete marker")
                except Exception as e:
                    self.logger.error(f"Background startup sync failed: {e} - keeping init_complete marker")

            sync_task.add_done_callback(log_completion)

        except Exception as e:
            self.logger.error(f"Failed to start background startup sync: {e}")

    async def _perform_startup_sync(self) -> None:
        """
        Perform blocking startup sync for first-time service initialization.

        This method runs a complete library sync and blocks webhook processing
        until it completes. It's only used on the first service startup to
        populate the database.
        """
        try:
            self.logger.info("Starting initial Jellyfin library sync...")
            result = await self.sync_jellyfin_library()

            # Create init_complete marker only if sync was successful
            if result.get("status") == "success":
                init_complete_path = Path("/app/data/init_complete")
                try:
                    init_complete_path.touch(exist_ok=True)
                    self.logger.info("Initial sync completed successfully - created init_complete marker")
                except Exception as e:
                    self.logger.warning(f"Could not create init_complete marker: {e}")
            else:
                self.logger.warning(f"Initial sync completed with status: {result.get('status', 'unknown')}")

        except Exception as e:
            self.logger.error(f"Initial sync failed: {e}")
            # Don't raise - service can continue without initial sync

    async def sync_jellyfin_library(self, background: bool = False) -> Dict[str, Any]:
        """
        Synchronize entire Jellyfin library to local database.

        This method performs a complete sync of the Jellyfin library, processing
        all items in batches for efficiency. It includes:
        - Concurrent sync prevention
        - Progress monitoring and logging
        - Error handling and recovery
        - Performance metrics

        Args:
            background: Whether this is a background sync (non-blocking)

        Returns:
            Dictionary with sync results including status, item counts, and timing

        Example:
            ```python
            # Foreground sync (blocks webhook processing)
            result = await service.sync_jellyfin_library()

            # Background sync (webhook processing continues)
            result = await service.sync_jellyfin_library(background=True)
            ```
        """
        # Prevent concurrent syncs which could cause database conflicts
        if self.sync_in_progress:
            message = "Library sync already in progress, skipping new request"
            self.logger.warning(message)
            return {"status": "warning", "message": message}

        self.sync_in_progress = True
        self.is_background_sync = background
        sync_start_time = time.time()

        try:
            sync_type = "background" if background else "initial"
            self.logger.info(f"Starting {sync_type} Jellyfin library sync...")

            # Verify Jellyfin connectivity before starting
            if not await self.jellyfin.is_connected():
                if not await self.jellyfin.connect():
                    raise ConnectionError("Cannot connect to Jellyfin server for sync")

            batch_size = self.config.sync.sync_batch_size
            items_processed = 0

            # Use callback-based processing for memory efficiency
            async def count_processed_items(items):
                nonlocal items_processed
                items_processed += len(items)
                await self.process_batch(items)

            await self.jellyfin.get_all_items(
                batch_size=batch_size,
                process_batch_callback=count_processed_items
            )

            sync_duration = time.time() - sync_start_time
            self.logger.info(
                f"Jellyfin library sync completed successfully. "
                f"Processed {items_processed} items in {sync_duration:.1f} seconds"
            )

            return {
                "status": "success",
                "message": f"Library sync completed. Processed {items_processed} items",
                "items_processed": items_processed,
                "duration_seconds": round(sync_duration, 1)
            }

        except Exception as e:
            sync_duration = time.time() - sync_start_time
            error_msg = f"Library sync failed after {sync_duration:.1f} seconds: {e}"
            self.logger.error(error_msg)
            return {"status": "error", "message": error_msg}

        finally:
            self.sync_in_progress = False
            self.is_background_sync = False

    async def process_batch(self, jellyfin_items: List[Dict[str, Any]]) -> None:
        """
        Process a batch of items from Jellyfin API efficiently.

        This method handles batch processing during library sync, including:
        - Converting Jellyfin data to MediaItem objects
        - Change detection using content hashes
        - Batch database operations for performance
        - Error handling for individual items

        Args:
            jellyfin_items: List of item dictionaries from Jellyfin API

        Example:
            ```python
            # Called automatically during sync
            await service.process_batch(jellyfin_items_batch)
            ```
        """
        if not jellyfin_items:
            return

        processed_count = 0
        error_count = 0
        media_items = []

        for jellyfin_item in jellyfin_items:
            try:
                # Convert Jellyfin data to MediaItem
                media_item = self.jellyfin.extract_media_item(jellyfin_item)

                # Check if item has changed using hash comparison
                existing_hash = await self.db.get_item_hash(media_item.item_id)

                if existing_hash and existing_hash == media_item.content_hash:
                    # Item unchanged - skip to save processing time
                    continue

                media_items.append(media_item)
                processed_count += 1

            except Exception as e:
                error_count += 1
                item_id = jellyfin_item.get('Id', 'unknown') if isinstance(jellyfin_item, dict) else 'unknown'
                self.logger.warning(f"Error processing item {item_id}: {e}")
                continue

        # Save all changed items in a single database transaction
        if media_items:
            try:
                saved_count = await self.db.save_items_batch(media_items)
                self.logger.debug(f"Saved {saved_count}/{len(media_items)} changed items in batch")
            except Exception as e:
                self.logger.error(f"Error saving batch of {len(media_items)} items: {e}")

        if error_count > 0:
            self.logger.warning(
                f"Batch processing completed with {error_count} errors out of {len(jellyfin_items)} items")

    async def process_webhook(self, payload: WebhookPayload) -> Dict[str, Any]:
        """
        Process incoming webhook from Jellyfin with comprehensive handling.

        This is the main entry point for webhook processing. It handles:
        - Payload validation and extraction
        - Waiting for initial sync completion
        - Change detection and notification sending
        - Performance monitoring and logging
        - Error handling and reporting

        Args:
            payload: Validated webhook payload from Jellyfin

        Returns:
            Dictionary with processing results and metrics

        Raises:
            HTTPException: For client errors (400) or server errors (500)

        Example:
            ```python
            # Called by FastAPI endpoint
            result = await service.process_webhook(webhook_payload)
            # Returns: {'status': 'success', 'action': 'new_item', 'notification_sent': True}
            ```
        """
        request_start_time = time.time()

        try:
            # Wait for initial sync if needed (with smart timeout handling)
            if not self.initial_sync_complete and self.sync_in_progress:
                await self._wait_for_initial_sync()

            # Extract and validate media item from webhook payload
            try:
                media_item = self._extract_from_webhook(payload)
            except Exception as e:
                self.logger.error(f"Error extracting media item from webhook payload: {e}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid webhook payload: {str(e)}"
                )

            # Process the media item (change detection, notifications, database updates)
            try:
                result = await self._process_media_item(media_item)

                processing_time = (time.time() - request_start_time) * 1000
                self.logger.info(
                    f"Webhook processed successfully for {media_item.name} "
                    f"(ID: {media_item.item_id}) in {processing_time:.2f}ms"
                )

                return {
                    "status": "success",
                    "item_id": media_item.item_id,
                    "item_name": media_item.name,
                    "processing_time_ms": round(processing_time, 2),
                    **result
                }

            except Exception as e:
                self.logger.error(f"Error processing media item {media_item.item_id}: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error processing media item: {str(e)}"
                )

        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise
        except Exception as e:
            processing_time = (time.time() - request_start_time) * 1000
            self.logger.error(f"Webhook processing failed after {processing_time:.2f}ms: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error during webhook processing"
            )

    async def _wait_for_initial_sync(self) -> None:
        """
        Wait for initial sync to complete with smart timeout handling.

        This method implements different waiting strategies based on sync type:
        - Background sync: Don't wait (process webhooks immediately)
        - Foreground sync: Wait indefinitely until completion

        This ensures optimal user experience in both scenarios.
        """
        # Don't wait for background syncs - process webhooks immediately
        if getattr(self, 'is_background_sync', False):
            self.logger.debug("Background sync in progress, proceeding with webhook processing immediately")
            return

        # For blocking initial sync, wait indefinitely (no timeout)
        check_interval = 2  # Check every 2 seconds
        wait_time = 0

        while self.sync_in_progress:
            self.logger.debug(f"Waiting for initial full sync to complete... ({wait_time}s)")
            await asyncio.sleep(check_interval)
            wait_time += check_interval

    async def _process_media_item(self, media_item: MediaItem) -> Dict[str, Any]:
        """
        Process a media item for changes and send appropriate notifications.

        This method implements the core business logic for handling media items:
        1. Check if item exists in database
        2. Detect changes using content hash comparison
        3. Perform detailed change analysis if hash differs
        4. Send appropriate notification (new vs upgrade)
        5. Update database with current item state

        Args:
            media_item: MediaItem to process

        Returns:
            Dictionary with processing results including:
            - action: Type of action taken (new_item, upgraded, no_changes, etc.)
            - changes_count: Number of changes detected
            - notification_sent: Whether notification was sent
            - changes: List of change types (for upgrades)

        Example:
            ```python
            result = await service._process_media_item(media_item)
            if result['action'] == 'upgraded':
                print(f"Found {result['changes_count']} changes")
            ```
        """
        # Check if item exists and get its current hash for comparison
        existing_hash = await self.db.get_item_hash(media_item.item_id)

        if existing_hash:
            # Item exists - check if it has changed
            if existing_hash != media_item.content_hash:
                # Hash changed - need to detect specific changes
                existing_item = await self.db.get_item(media_item.item_id)

                if existing_item:
                    # Perform detailed change detection
                    changes = self.change_detector.detect_changes(existing_item, media_item)

                    if changes:
                        # Meaningful changes detected - send upgrade notification
                        notification_sent = await self.discord.send_notification(
                            media_item, changes, is_new=False
                        )

                        self.logger.info(f"Processed upgrade for {media_item.name} with {len(changes)} changes")

                        return {
                            "action": "upgraded",
                            "changes_count": len(changes),
                            "notification_sent": notification_sent,
                            "changes": [change['type'] for change in changes]
                        }
                    else:
                        # Hash changed but no significant changes detected
                        self.logger.debug(f"Hash changed but no significant changes detected for {media_item.name}")
                        return {
                            "action": "hash_updated",
                            "changes_count": 0,
                            "notification_sent": False
                        }
                else:
                    # Couldn't retrieve existing item for comparison
                    self.logger.warning(
                        f"Could not retrieve existing item {media_item.item_id} for change detection")
                    return {
                        "action": "error_retrieving_existing",
                        "changes_count": 0,
                        "notification_sent": False
                    }
            else:
                # No changes detected (hash matches)
                self.logger.debug(f"No changes detected for {media_item.name} (hash match)")
                return {
                    "action": "no_changes",
                    "changes_count": 0,
                    "notification_sent": False
                }
        else:
            # New item - send new item notification
            notification_sent = await self.discord.send_notification(media_item, is_new=True)
            self.logger.info(f"Processed new item: {media_item.name}")

            result = {
                "action": "new_item",
                "changes_count": 0,
                "notification_sent": notification_sent
            }

        # Save/update item in database (only if new or changed)
        if not existing_hash or existing_hash != media_item.content_hash:
            save_success = await self.db.save_item(media_item)
            if not save_success:
                self.logger.warning(f"Failed to save item {media_item.item_id} to database")

        return result

    def _extract_from_webhook(self, payload: WebhookPayload) -> MediaItem:
        """
        Extract MediaItem from Jellyfin webhook payload with robust error handling.

        This method converts the webhook payload from Jellyfin into our internal
        MediaItem representation, handling data type conversion and validation.

        Args:
            payload: Validated webhook payload from Jellyfin

        Returns:
            MediaItem instance with normalized data

        Raises:
            ValueError: If required fields are missing or invalid

        Example:
            ```python
            payload = WebhookPayload(ItemId="123", Name="Movie", ItemType="Movie")
            media_item = service._extract_from_webhook(payload)
            ```
        """
        try:
            # Validate required fields
            if not payload.ItemId:
                raise ValueError("ItemId is required")
            if not payload.Name:
                raise ValueError("Name is required")
            if not payload.ItemType:
                raise ValueError("ItemType is required")

            # Extract and validate season/episode numbers
            season_number = None
            episode_number = None

            if payload.SeasonNumber00:
                try:
                    season_number = int(payload.SeasonNumber00)
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Invalid season number '{payload.SeasonNumber00}': {e}")

            if payload.EpisodeNumber00:
                try:
                    episode_number = int(payload.EpisodeNumber00)
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Invalid episode number '{payload.EpisodeNumber00}': {e}")

            # Create MediaItem with webhook data
            return MediaItem(
                # Core identification
                item_id=payload.ItemId,
                name=payload.Name,
                item_type=payload.ItemType,
                year=payload.Year,
                series_name=payload.SeriesName,
                season_number=season_number,
                episode_number=episode_number,
                overview=payload.Overview,

                # Video properties from webhook
                video_height=payload.Video_0_Height,
                video_width=payload.Video_0_Width,
                video_codec=payload.Video_0_Codec,
                video_profile=payload.Video_0_Profile,
                video_range=payload.Video_0_VideoRange,
                video_framerate=payload.Video_0_FrameRate,
                aspect_ratio=payload.Video_0_AspectRatio,

                # Audio properties from webhook
                audio_codec=payload.Audio_0_Codec,
                audio_channels=payload.Audio_0_Channels,
                audio_language=payload.Audio_0_Language,
                audio_bitrate=payload.Audio_0_Bitrate,

                # Provider IDs from webhook
                imdb_id=payload.Provider_imdb,
                tmdb_id=payload.Provider_tmdb,
                tvdb_id=payload.Provider_tvdb,

                # Set defaults for API-only fields (not available in webhook)
                date_created=None,
                date_modified=None,
                runtime_ticks=None,
                official_rating=None,
                genres=[],
                studios=[],
                tags=[],
                album=None,
                artists=[],
                album_artist=None,
                width=payload.Video_0_Width,
                height=payload.Video_0_Height,

                # Metadata
                timestamp=datetime.now(timezone.utc).isoformat(),
                file_path=None,
                file_size=None,
                last_modified=None
            )

        except Exception as e:
            self.logger.error(f"Error extracting MediaItem from webhook payload: {e}")
            raise

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform comprehensive health check of all service components.

        This method provides detailed status information about:
        - Overall service health
        - Individual component status (Jellyfin, Database, Discord)
        - Current operational state
        - Version and timestamp information

        Returns:
            Dictionary with comprehensive health information

        Example:
            ```python
            health = await service.health_check()
            if health['status'] == 'healthy':
                print("Service is operating normally")
            else:
                print(f"Service issues detected: {health}")
            ```
        """
        try:
            health_data = {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "2.0.0",
                "components": {}
            }

            # Check Jellyfin connection status
            try:
                jellyfin_connected = await self.jellyfin.is_connected()
                health_data["components"]["jellyfin"] = {
                    "status": "healthy" if jellyfin_connected else "unhealthy",
                    "connected": jellyfin_connected,
                    "server_url": self.config.jellyfin.server_url
                }
            except Exception as e:
                health_data["components"]["jellyfin"] = {
                    "status": "error",
                    "connected": False,
                    "error": str(e)
                }

            # Check database status
            try:
                db_stats = await self.db.get_stats()
                health_data["components"]["database"] = {
                    "status": "healthy" if "error" not in db_stats else "unhealthy",
                    "path": self.config.database.path,
                    "wal_mode": self.config.database.wal_mode,
                    **db_stats
                }
            except Exception as e:
                health_data["components"]["database"] = {
                    "status": "error",
                    "error": str(e)
                }

            # Check Discord webhook status
            try:
                webhook_status = self.discord.get_webhook_status()
                enabled_webhooks = sum(1 for wh in webhook_status["webhooks"].values() if wh.get("enabled", False))
                health_data["components"]["discord"] = {
                    "status": "healthy" if enabled_webhooks > 0 else "unhealthy",
                    "enabled_webhooks": enabled_webhooks,
                    "routing_enabled": webhook_status["routing_enabled"]
                }
            except Exception as e:
                health_data["components"]["discord"] = {
                    "status": "error",
                    "error": str(e)
                }

            # Add service operational status
            health_data.update({
                "sync_in_progress": self.sync_in_progress,
                "initial_sync_complete": self.initial_sync_complete,
                "server_was_offline": self.server_was_offline
            })

            # Determine overall status based on component health
            component_statuses = [comp.get("status") for comp in health_data["components"].values()]
            if "error" in component_statuses:
                health_data["status"] = "error"
            elif "unhealthy" in component_statuses:
                health_data["status"] = "degraded"

            return health_data

        except Exception as e:
            self.logger.error(f"Error performing health check: {e}")
            return {
                "status": "error",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e)
            }

    async def manual_sync(self) -> Dict[str, Any]:
        """
        Trigger manual library synchronization.

        This method allows administrators to manually trigger a library sync
        through the API. The sync runs in the background to avoid blocking
        webhook processing.

        Returns:
            Dictionary with sync initiation status

        Example:
            ```python
            result = await service.manual_sync()
            if result['status'] == 'success':
                print("Manual sync started successfully")
            ```
        """
        try:
            if self.sync_in_progress:
                return {
                    "status": "warning",
                    "message": "Library sync already in progress"
                }

            # Verify Jellyfin connectivity before starting
            if not await self.jellyfin.is_connected():
                return {
                    "status": "error",
                    "message": "Cannot start sync: Jellyfin server is not connected"
                }

            # Start sync in background (don't await)
            sync_task = asyncio.create_task(self.sync_jellyfin_library(background=True))

            return {
                "status": "success",
                "message": "Library sync started in background"
            }

        except Exception as e:
            self.logger.error(f"Error starting manual sync: {e}")
            return {
                "status": "error",
                "message": f"Failed to start sync: {str(e)}"
            }

    async def get_service_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive service statistics for monitoring and debugging.

        Returns:
            Dictionary with detailed service statistics including:
            - Service uptime and version
            - Database statistics
            - Webhook configuration status
            - Jellyfin connection status

        Example:
            ```python
            stats = await service.get_service_stats()
            print(f"Service uptime: {stats['service']['uptime_seconds']} seconds")
            print(f"Database items: {stats['database']['total_items']}")
            ```
        """
        try:
            stats = {
                "service": {
                    "version": "2.0.0",
                    "uptime_seconds": time.time() - getattr(self, '_start_time', time.time()),
                    "sync_in_progress": self.sync_in_progress,
                    "initial_sync_complete": self.initial_sync_complete
                }
            }

            # Get database statistics
            try:
                db_stats = await self.db.get_stats()
                stats["database"] = db_stats
            except Exception as e:
                stats["database"] = {"error": str(e)}

            # Get webhook status
            try:
                webhook_status = self.discord.get_webhook_status()
                stats["webhooks"] = webhook_status
            except Exception as e:
                stats["webhooks"] = {"error": str(e)}

            # Get Jellyfin connection status
            try:
                jellyfin_connected = await self.jellyfin.is_connected()
                stats["jellyfin"] = {
                    "connected": jellyfin_connected,
                    "server_url": self.config.jellyfin.server_url,
                    "server_was_offline": self.server_was_offline
                }
            except Exception as e:
                stats["jellyfin"] = {"error": str(e)}

            return stats

        except Exception as e:
            self.logger.error(f"Error getting service stats: {e}")
            return {"error": str(e)}

    async def background_tasks(self) -> None:
        """
        Run background maintenance tasks for the service.

        This method runs continuously in the background, performing:
        - Database maintenance (VACUUM operations)
        - Jellyfin connection monitoring
        - Periodic library syncs
        - Health monitoring and alerting

        The tasks run on a 60-second cycle with comprehensive error handling
        to ensure one task failure doesn't break the entire background loop.

        Example:
            ```python
            # Started automatically by FastAPI lifespan manager
            background_task = asyncio.create_task(service.background_tasks())
            ```
        """
        self._start_time = time.time()

        # Initial delay to allow service startup to complete
        await asyncio.sleep(60)

        self.logger.info("Background maintenance tasks started")

        while not self.shutdown_event.is_set():
            try:
                await self._run_maintenance_cycle()

                # Sleep for 60 seconds or until shutdown signal
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=60.0)
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    continue  # Normal timeout, continue loop

            except Exception as e:
                self.logger.error(f"Error in background maintenance cycle: {e}")
                # Sleep before retrying to avoid rapid error loops
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=60.0)
                    break
                except asyncio.TimeoutError:
                    continue

        self.logger.info("Background maintenance tasks stopped")

    async def _run_maintenance_cycle(self) -> None:
        """
        Execute a single maintenance cycle with all background tasks.

        This method coordinates all background maintenance activities:
        - Database vacuum operations
        - Jellyfin connection health monitoring
        - Periodic library synchronization checks
        """
        current_time = time.time()

        # Database maintenance
        await self._perform_database_maintenance(current_time)

        # Jellyfin connection monitoring
        await self._monitor_jellyfin_connection()

        # Periodic sync checking
        await self._check_periodic_sync()

    async def _perform_database_maintenance(self, current_time: float) -> None:
        """
        Perform database maintenance tasks including VACUUM operations.

        Args:
            current_time: Current timestamp for interval checking
        """
        try:
            vacuum_interval = self.config.database.vacuum_interval_hours * 3600
            if current_time - self.last_vacuum > vacuum_interval:
                self.logger.info("Starting database vacuum...")
                await self.db.vacuum_database()
                self.last_vacuum = current_time
                self.logger.info("Database vacuum completed")
        except Exception as e:
            self.logger.error(f"Database maintenance error: {e}")

    async def _monitor_jellyfin_connection(self) -> None:
        """
        Monitor Jellyfin server connectivity and send status notifications.

        This method tracks Jellyfin server availability and sends Discord
        notifications when the server goes offline or comes back online.
        It also triggers recovery syncs when the server returns.
        """
        try:
            jellyfin_connected = await self.jellyfin.is_connected()

            if not jellyfin_connected and not self.server_was_offline:
                # Server went offline - notify users
                await self.discord.send_server_status(False)
                self.server_was_offline = True
                self.logger.warning("Jellyfin server went offline")

            elif jellyfin_connected and self.server_was_offline:
                # Server came back online - notify users and sync
                await self.discord.send_server_status(True)
                self.server_was_offline = False
                self.logger.info("Jellyfin server is back online")

                # Trigger recovery sync to catch up on any missed changes
                if not self.sync_in_progress:
                    self.logger.info("Starting recovery sync after server came back online")
                    asyncio.create_task(self.sync_jellyfin_library(background=True))

        except Exception as e:
            self.logger.error(f"Error monitoring Jellyfin connection: {e}")

    async def _check_periodic_sync(self) -> None:
        """
        Check if periodic library sync is needed and trigger if necessary.

        This method implements periodic background syncs to ensure the local
        database stays in sync with Jellyfin even if webhooks are missed.
        """
        try:
            if self.sync_in_progress:
                return

            sync_interval = 24 * 3600  # 24 hours in seconds

            # Get last sync time from database
            last_sync_time_str = await self.db.get_last_sync_time()

            if last_sync_time_str:
                try:
                    # Handle various timestamp formats
                    if last_sync_time_str.endswith('Z'):
                        last_sync_time_str = last_sync_time_str[:-1] + '+00:00'
                    elif '+' not in last_sync_time_str and last_sync_time_str.count(':') == 2:
                        last_sync_time_str += '+00:00'

                    last_sync = datetime.fromisoformat(last_sync_time_str)
                    now = datetime.now(timezone.utc)
                    seconds_since_sync = (now - last_sync).total_seconds()

                    if seconds_since_sync > sync_interval:
                        self.logger.info(
                            f"Starting periodic background sync "
                            f"({seconds_since_sync / 3600:.1f} hours since last sync)"
                        )
                        asyncio.create_task(self.sync_jellyfin_library(background=True))

                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Error parsing last sync time '{last_sync_time_str}': {e}")

        except Exception as e:
            self.logger.error(f"Error checking periodic sync: {e}")

    async def cleanup(self) -> None:
        """
        Clean up service resources during shutdown.

        This method handles graceful shutdown of all service components:
        - Signal background tasks to stop
        - Close network connections
        - Close database connections
        - Release other resources

        Called automatically by FastAPI lifespan manager during shutdown.
        """
        self.logger.info("Starting service cleanup...")

        try:
            # Signal shutdown to background tasks
            self.shutdown_event.set()

            # Close Discord notifier and its HTTP session
            if self.discord:
                try:
                    await self.discord.close()
                    self.logger.debug("Discord notifier closed")
                except Exception as e:
                    self.logger.warning(f"Error closing Discord notifier: {e}")

            # Close database connections
            if self.db:
                try:
                    # SQLite connections are closed automatically by aiosqlite
                    self.logger.debug("Database connections closed")
                except Exception as e:
                    self.logger.warning(f"Error during database cleanup: {e}")

            self.logger.info("Service cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during service cleanup: {e}")


# ==================== FASTAPI APPLICATION ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage FastAPI application lifespan with proper startup and shutdown handling.

    This async context manager handles:
    - Service initialization during startup
    - Background task management
    - Graceful shutdown procedures
    - Error handling during lifecycle events

    Args:
        app: FastAPI application instance

    Yields:
        Control to FastAPI application during normal operation

    Example:
        ```python
        # Used automatically by FastAPI
        app = FastAPI(lifespan=lifespan)
        ```

    Note:
        This function uses the modern FastAPI lifespan pattern which replaces
        the older startup/shutdown event handlers for better async support.
    """
    # Startup sequence
    service = app.state.service
    try:
        # Initialize service components
        await service.initialize()

        # Start background maintenance tasks
        background_task = asyncio.create_task(service.background_tasks())
        app.state.background_task = background_task

        app.state.logger.info("FastAPI application started successfully")

        # Yield control to FastAPI for normal operation
        yield

    except Exception as e:
        app.state.logger.error(f"Application startup failed: {e}")
        raise
    finally:
        # Shutdown sequence
        try:
            app.state.logger.info("Shutting down FastAPI application...")

            # Cancel background tasks gracefully
            if hasattr(app.state, 'background_task'):
                app.state.background_task.cancel()
                try:
                    await app.state.background_task
                except asyncio.CancelledError:
                    pass  # Expected when cancelling

            # Clean up service resources
            await service.cleanup()
            app.state.logger.info("FastAPI application shutdown completed")

        except Exception as e:
            app.state.logger.error(f"Error during application shutdown: {e}")


# Create FastAPI application with metadata and lifespan management
app = FastAPI(
    title="JellyNotify Discord Webhook Service",
    version="2.0.0",
    description="Enhanced webhook service for Jellyfin to Discord notifications",
    lifespan=lifespan
)

# Initialize service and attach to application state
service = WebhookService()
app.state.service = service
app.state.logger = service.logger


# ==================== GLOBAL EXCEPTION HANDLERS ====================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle all unhandled exceptions with comprehensive logging and safe error responses.

    This handler ensures that:
    - All errors are properly logged with context
    - Sensitive information is not exposed to clients
    - Proper HTTP status codes are returned
    - Request context is captured for debugging

    Args:
        request: FastAPI request object
        exc: The unhandled exception

    Returns:
        JSONResponse with appropriate error message and status code

    Example:
        ```python
        # Handles any unhandled exception automatically
        # Logs: "Unhandled exception in GET /health from 192.168.1.1:12345: Connection timeout"
        # Returns: {"error": "Internal server error"}
        ```
    """
    logger = app.state.logger

    # Extract request context for logging
    client_host = getattr(getattr(request, "client", None), "host", "unknown")
    client_port = getattr(getattr(request, "client", None), "port", "unknown")
    method = request.method
    url = str(request.url)

    # Log the error with full context and stack trace
    logger.error(
        f"Unhandled exception in {method} {url} from {client_host}:{client_port}: {exc}",
        exc_info=True  # Include full stack trace
    )

    # Return appropriate response based on exception type
    if isinstance(exc, HTTPException):
        # HTTPException already has appropriate status and message
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail}
        )
    else:
        # Don't expose internal error details in production
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"}
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Handle Pydantic validation errors with detailed error information.

    This handler provides detailed validation error information to help
    clients understand what went wrong with their requests while maintaining
    security by not exposing internal system details.

    Args:
        request: FastAPI request object
        exc: Pydantic validation error

    Returns:
        JSONResponse with detailed validation error information

    Example:
        ```python
        # For invalid webhook payload:
        # Returns: {
        #   "error": "Validation failed",
        #   "details": [
        #     {"field": "ItemId", "message": "field required", "type": "value_error.missing"}
        #   ]
        # }
        ```
    """
    logger = app.state.logger
    client_host = getattr(getattr(request, "client", None), "host", "unknown")

    # Format validation errors for better readability
    formatted_errors = []
    for error in exc.errors():
        formatted_errors.append({
            "field": " -> ".join(str(x) for x in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
            "input": error.get("input")
        })

    logger.warning(f"Validation error from {client_host}: {formatted_errors}")

    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation failed",
            "details": formatted_errors
        }
    )


# ==================== API ENDPOINTS ====================

@app.post("/webhook")
async def webhook_endpoint(payload: WebhookPayload, request: Request) -> Dict[str, Any]:
    """
    Main webhook endpoint for receiving notifications from Jellyfin.

    This endpoint receives webhook payloads from the Jellyfin Webhook Plugin
    and processes them to detect new media additions or quality upgrades.

    Args:
        payload: Validated webhook payload from Jellyfin
        request: FastAPI request object for logging context

    Returns:
        Dictionary with processing results

    Raises:
        HTTPException: For processing errors (400/500 status codes)

    Example:
        ```bash
        curl -X POST http://localhost:8080/webhook \
             -H "Content-Type: application/json" \
             -d '{"ItemId": "123", "Name": "Movie", "ItemType": "Movie"}'
        ```
    """
    client_host = getattr(getattr(request, "client", None), "host", "unknown")
    app.state.logger.debug(f"Webhook received from {client_host} for item: {payload.ItemId}")

    try:
        result = await app.state.service.process_webhook(payload)
        return result
    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        app.state.logger.error(f"Webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")


@app.post("/webhook/debug")
async def webhook_debug_endpoint(request: Request) -> Dict[str, Any]:
    """
    Debug webhook endpoint for troubleshooting webhook configuration issues.

    This endpoint accepts any JSON payload and provides detailed analysis
    of the request, including validation results and field analysis.
    It's useful for debugging Jellyfin webhook plugin configuration.

    Args:
        request: FastAPI request object containing raw webhook data

    Returns:
        Dictionary with debug information including validation results

    Example:
        ```bash
        curl -X POST http://localhost:8080/webhook/debug \
             -H "Content-Type: application/json" \
             -d '{"InvalidField": "test"}'
        # Returns detailed analysis of what's wrong with the payload
        ```
    """
    logger = app.state.logger
    client_host = getattr(getattr(request, "client", None), "host", "unknown")

    try:
        # Get raw request data for analysis
        raw_body = await request.body()
        content_type = request.headers.get("content-type", "")

        logger.info(f"Debug webhook received from {client_host}")
        logger.debug(f"Content-Type: {content_type}, Body length: {len(raw_body)} bytes")

        # Attempt to parse JSON
        try:
            json_data = json.loads(raw_body)
            logger.debug(f"Parsed JSON keys: {list(json_data.keys())}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            return {
                "error": "Invalid JSON",
                "details": str(e),
                "raw_body_preview": raw_body.decode('utf-8', errors='replace')[:200]
            }

        # Attempt Pydantic validation
        try:
            payload = WebhookPayload(**json_data)
            logger.info(f"Validation successful for item: {payload.ItemId}")

            # Process normally if validation passes
            result = await app.state.service.process_webhook(payload)
            return {
                "status": "success",
                "validation": "passed",
                "result": result
            }

        except ValidationError as e:
            logger.warning(f"Validation failed: {e}")

            # Provide detailed validation analysis
            validation_details = {
                "validation_errors": [],
                "received_payload": json_data,
                "field_analysis": {}
            }

            for error in e.errors():
                field_path = " -> ".join(str(x) for x in error['loc'])
                validation_details["validation_errors"].append({
                    "field": field_path,
                    "error": error['msg'],
                    "type": error['type'],
                    "input": error.get('input')
                })

            # Analyze each field in the payload
            for key, value in json_data.items():
                validation_details["field_analysis"][key] = {
                    "received_type": type(value).__name__,
                    "received_value": str(value)[:100] if value is not None else None,
                    "is_expected": key in WebhookPayload.model_fields
                }

            return {
                "status": "validation_failed",
                "details": validation_details
            }

    except Exception as e:
        logger.error(f"Debug webhook error: {e}", exc_info=True)
        return {"error": str(e)}


@app.get("/health")
async def health_endpoint() -> Dict[str, Any]:
    """
    Comprehensive health check endpoint for monitoring service status.

    Returns detailed health information about all service components,
    useful for monitoring systems and load balancers.

    Returns:
        Dictionary with detailed health status

    Example:
        ```bash
        curl http://localhost:8080/health
        # Returns: {"status": "healthy", "components": {...}, ...}
        ```
    """
    return await app.state.service.health_check()


@app.post("/sync")
async def sync_endpoint() -> Dict[str, Any]:
    """
    Manually trigger Jellyfin library synchronization.

    This endpoint allows administrators to manually trigger a full library
    sync without waiting for the scheduled periodic sync.

    Returns:
        Dictionary with sync initiation status

    Example:
        ```bash
        curl -X POST http://localhost:8080/sync
        # Returns: {"status": "success", "message": "Library sync started in background"}
        ```
    """
    return await app.state.service.manual_sync()


@app.get("/stats")
async def stats_endpoint() -> Dict[str, Any]:
    """
    Get comprehensive service statistics for monitoring and debugging.

    Returns detailed statistics about service operation, database content,
    and component status.

    Returns:
        Dictionary with service statistics

    Example:
        ```bash
        curl http://localhost:8080/stats
        # Returns: {"service": {...}, "database": {...}, "webhooks": {...}}
        ```
    """
    return await app.state.service.get_service_stats()


@app.get("/webhooks")
async def webhooks_endpoint() -> Dict[str, Any]:
    """
    Get Discord webhook configuration and status information.

    Returns information about configured webhooks, routing settings,
    and current operational status.

    Returns:
        Dictionary with webhook configuration and status

    Example:
        ```bash
        curl http://localhost:8080/webhooks
        # Returns: {"routing_enabled": true, "webhooks": {...}}
        ```
    """
    return app.state.service.discord.get_webhook_status()


@app.post("/test-webhook")
async def test_webhook_endpoint(webhook_name: str = "default") -> Dict[str, Any]:
    """
    Test a specific Discord webhook by sending a test notification.

    This endpoint is useful for verifying webhook configuration and
    connectivity to Discord.

    Args:
        webhook_name: Name of webhook to test (default: "default")

    Returns:
        Dictionary with test results

    Example:
        ```bash
        curl -X POST "http://localhost:8080/test-webhook?webhook_name=movies"
        # Sends test notification to movies webhook
        ```
    """
    try:
        # Create a test media item
        test_item = MediaItem(
            item_id="test-item-" + str(int(time.time())),
            name="Test Movie",
            item_type="Movie"
        )

        webhook_info = app.state.service.discord._get_webhook_for_item(test_item)

        if not webhook_info:
            return {
                "status": "error",
                "message": "No webhook available for testing"
            }

        # Create test notification payload
        test_payload = {
            "embeds": [{
                "title": "🧪 Webhook Test",
                "description": f"Test notification from {webhook_info['config'].name} webhook",
                "color": 65280,  # Green
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {
                    "text": "JellyNotify Test",
                    "icon_url": app.state.service.config.jellyfin.server_url + "/web/favicon.ico"
                }
            }]
        }

        webhook_url = webhook_info['config'].url

        # Send test notification
        async with app.state.service.discord.session.post(webhook_url, json=test_payload) as response:
            if response.status == 204:
                return {
                    "status": "success",
                    "webhook": webhook_info['name'],
                    "message": "Test notification sent successfully"
                }
            else:
                error_text = await response.text()
                return {
                    "status": "error",
                    "webhook": webhook_info['name'],
                    "message": f"HTTP {response.status}: {error_text}"
                }

    except Exception as e:
        app.state.logger.error(f"Webhook test error: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@app.get("/config")
async def config_endpoint() -> Dict[str, Any]:
    """
    Get sanitized configuration information for debugging and verification.

    Returns configuration data with sensitive information (API keys, webhook URLs)
    removed or masked for security.

    Returns:
        Dictionary with sanitized configuration

    Example:
        ```bash
        curl http://localhost:8080/config
        # Returns configuration without sensitive data
        ```
    """
    try:
        config = app.state.service.config

        # Return sanitized configuration (remove sensitive data)
        sanitized_config = {
            "jellyfin": {
                "server_url": config.jellyfin.server_url,
                "client_name": config.jellyfin.client_name,
                "client_version": config.jellyfin.client_version
            },
            "database": {
                "path": config.database.path,
                "wal_mode": config.database.wal_mode,
                "vacuum_interval_hours": config.database.vacuum_interval_hours
            },
            "discord": {
                "routing": config.discord.routing,
                "rate_limit": config.discord.rate_limit,
                "webhooks": {
                    name: {
                        "name": webhook.name,
                        "enabled": webhook.enabled,
                        "has_url": bool(webhook.url),
                        "grouping": webhook.grouping
                    }
                    for name, webhook in config.discord.webhooks.items()
                }
            },
            "templates": {
                "directory": config.templates.directory
            },
            "notifications": config.notifications.model_dump(),
            "server": config.server.model_dump(),
            "sync": config.sync.model_dump()
        }

        return sanitized_config

    except Exception as e:
        app.state.logger.error(f"Config endpoint error: {e}")
        return {"error": str(e)}


# ==================== MAIN ENTRY POINT ====================

if __name__ == "__main__":
    """
    Main entry point for running the JellyNotify service.

    This section handles:
    - Signal handler registration for graceful shutdown
    - Uvicorn server configuration and startup
    - Error handling for startup failures

    The service runs using Uvicorn with production-ready settings including
    disabled reload, custom logging, and security headers.

    Example:
        ```bash
        python main.py
        # Starts server on configured host:port with all features enabled
        ```
    """


    def signal_handler(signum, frame):
        """
        Handle shutdown signals gracefully.

        This function is called when the process receives SIGINT (Ctrl+C)
        or SIGTERM (termination request). It logs the signal and initiates
        graceful shutdown through the lifespan manager.

        Args:
            signum: Signal number received
            frame: Current stack frame (unused)
        """
        logger = service.logger
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        # The lifespan manager will handle the actual cleanup
        sys.exit(0)


    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination request

    # Run the service with Uvicorn
    try:
        uvicorn.run(
            "main:app",  # Application module and variable
            host=service.config.server.host,  # Bind address
            port=service.config.server.port,  # Port number
            log_level=service.config.server.log_level.lower(),  # Logging level
            reload=False,  # Disable auto-reload in production
            access_log=False,  # We handle our own access logging
            server_header=False,  # Don't expose server type
            date_header=False  # Don't expose server date
        )
    except Exception as e:
        service.logger.error(f"Failed to start server: {e}")
        sys.exit(1)