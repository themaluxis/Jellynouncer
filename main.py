#!/usr/bin/env python3
"""
JellyNotify Discord Webhook Service
A comprehensive intermediate webhook service for Jellyfin to Discord notifications
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
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Union
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import aiosqlite
import yaml
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from jellyfin_apiclient_python import JellyfinClient
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError
from pydantic import BaseModel, Field, ConfigDict, ValidationError, field_validator
import uvicorn


# ==================== CONFIGURATION MODELS ====================

class JellyfinConfig(BaseModel):
    """Jellyfin server configuration"""
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
        """Validate Jellyfin server URL format"""
        if not v:
            raise ValueError("Jellyfin server URL cannot be empty")

        parsed = urlparse(v)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid Jellyfin server URL format: {v}")

        if parsed.scheme not in ['http', 'https']:
            raise ValueError(f"Jellyfin server URL must use http or https: {v}")

        # Remove trailing slash
        return v.rstrip('/')

    @field_validator('api_key', 'user_id')
    @classmethod
    def validate_required_strings(cls, v: str) -> str:
        """Validate required string fields"""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class WebhookConfig(BaseModel):
    """Individual webhook configuration"""
    model_config = ConfigDict(extra='forbid')

    url: Optional[str] = Field(default=None, description="Discord webhook URL")
    name: str = Field(..., description="Webhook display name")
    enabled: bool = Field(default=False, description="Whether webhook is enabled")
    grouping: Dict[str, Any] = Field(default_factory=dict, description="Grouping configuration")

    @field_validator('url')
    @classmethod
    def validate_webhook_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate Discord webhook URL format"""
        if v is None:
            return None

        if not v.strip():
            return None

        v = v.strip()
        if not v.startswith('https://discord.com/api/webhooks/'):
            raise ValueError(f"Discord webhook URL must start with 'https://discord.com/api/webhooks/': {v}")

        return v


class DiscordConfig(BaseModel):
    """Discord configuration"""
    model_config = ConfigDict(extra='forbid')

    webhooks: Dict[str, WebhookConfig] = Field(default_factory=dict)
    routing: Dict[str, Any] = Field(default_factory=dict)
    rate_limit: Dict[str, Any] = Field(default_factory=dict)


class DatabaseConfig(BaseModel):
    """Database configuration"""
    model_config = ConfigDict(extra='forbid')

    path: str = Field(default="/app/data/jellyfin_items.db")
    wal_mode: bool = Field(default=True)
    vacuum_interval_hours: int = Field(default=24, ge=1, le=168)  # 1 hour to 1 week

    @field_validator('path')
    @classmethod
    def validate_db_path(cls, v: str) -> str:
        """Validate database path"""
        if not v:
            raise ValueError("Database path cannot be empty")

        path = Path(v)
        parent_dir = path.parent

        # Check if parent directory exists or can be created
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            raise ValueError(f"Cannot create database directory {parent_dir}: {e}")

        # Check write permissions
        if not os.access(parent_dir, os.W_OK):
            raise ValueError(f"No write permission for database directory: {parent_dir}")

        return str(path)


class TemplatesConfig(BaseModel):
    """Templates configuration"""
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
        """Validate template directory exists and is readable"""
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
    """Notifications configuration"""
    model_config = ConfigDict(extra='forbid')

    watch_changes: Dict[str, bool] = Field(default_factory=dict)
    colors: Dict[str, int] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    """Server configuration"""
    model_config = ConfigDict(extra='forbid')

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080, ge=1024, le=65535)
    log_level: str = Field(default="INFO")

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level"""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v


class SyncConfig(BaseModel):
    """Sync configuration"""
    model_config = ConfigDict(extra='forbid')

    startup_sync: bool = Field(default=True)
    sync_batch_size: int = Field(default=100, ge=10, le=1000)
    api_request_delay: float = Field(default=0.1, ge=0.0, le=5.0)


class AppConfig(BaseModel):
    """Main application configuration"""
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
    """Expected webhook payload from Jellyfin"""
    model_config = ConfigDict(extra='ignore')  # Ignore unknown fields

    ItemId: str = Field(..., description="Jellyfin item ID")
    Name: str = Field(..., description="Item name")
    ItemType: str = Field(..., description="Item type (Movie, Episode, etc.)")
    Year: Optional[int] = Field(default=None, description="Release year")
    SeriesName: Optional[str] = Field(default=None, description="Series name for episodes")
    SeasonNumber00: Optional[str] = Field(default=None, description="Season number (zero-padded)")
    EpisodeNumber00: Optional[str] = Field(default=None, description="Episode number (zero-padded)")
    Overview: Optional[str] = Field(default=None, description="Item overview/description")

    # Video info
    Video_0_Height: Optional[int] = Field(default=None, description="Video height in pixels")
    Video_0_Width: Optional[int] = Field(default=None, description="Video width in pixels")
    Video_0_Codec: Optional[str] = Field(default=None, description="Video codec")
    Video_0_Profile: Optional[str] = Field(default=None, description="Video profile")
    Video_0_VideoRange: Optional[str] = Field(default=None, description="Video range (HDR/SDR)")
    Video_0_FrameRate: Optional[float] = Field(default=None, description="Video frame rate")
    Video_0_AspectRatio: Optional[str] = Field(default=None, description="Video aspect ratio")

    # Audio info
    Audio_0_Codec: Optional[str] = Field(default=None, description="Audio codec")
    Audio_0_Channels: Optional[int] = Field(default=None, description="Audio channel count")
    Audio_0_Language: Optional[str] = Field(default=None, description="Audio language")
    Audio_0_Bitrate: Optional[int] = Field(default=None, description="Audio bitrate")

    # Provider IDs
    Provider_imdb: Optional[str] = Field(default=None, description="IMDb ID")
    Provider_tmdb: Optional[str] = Field(default=None, description="TMDb ID")
    Provider_tvdb: Optional[str] = Field(default=None, description="TVDb ID")


# ==================== DATA MODELS ====================

@dataclass
class MediaItem:
    """Represents a media item with all its metadata"""
    item_id: str
    name: str
    item_type: str
    year: Optional[int] = None
    series_name: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    overview: Optional[str] = None

    # Video properties
    video_height: Optional[int] = None
    video_width: Optional[int] = None
    video_codec: Optional[str] = None
    video_profile: Optional[str] = None
    video_range: Optional[str] = None
    video_framerate: Optional[float] = None
    aspect_ratio: Optional[str] = None

    # Audio properties
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None
    audio_language: Optional[str] = None
    audio_bitrate: Optional[int] = None

    # Provider IDs
    imdb_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None

    # Enhanced metadata from API
    date_created: Optional[str] = None
    date_modified: Optional[str] = None
    runtime_ticks: Optional[int] = None
    official_rating: Optional[str] = None
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

    # Metadata
    timestamp: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    content_hash: Optional[str] = None
    last_modified: Optional[str] = None

    def __post_init__(self):
        """Initialize default values and generate content hash"""
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

        # Initialize list fields if None
        for field in ['genres', 'studios', 'tags', 'artists']:
            if getattr(self, field) is None:
                setattr(self, field, [])

        # Generate content hash if not provided
        if self.content_hash is None:
            self.content_hash = self.generate_content_hash()

    def generate_content_hash(self) -> str:
        """Generate a hash representing the content state"""
        key_fields = [
            str(self.video_height or ''),
            str(self.video_codec or ''),
            str(self.audio_codec or ''),
            str(self.audio_channels or ''),
            str(self.video_range or ''),
            str(self.file_size or ''),
            str(self.imdb_id or ''),
            str(self.tmdb_id or ''),
            str(self.tvdb_id or '')
        ]

        hash_input = "|".join(key_fields)
        return hashlib.md5(hash_input.encode()).hexdigest()


# ==================== LOGGING SETUP ====================

def setup_logging(log_level: str = "INFO", log_dir: str = "/app/logs") -> logging.Logger:
    """Setup comprehensive logging with rotation and custom formatting"""

    # Create logs directory
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Custom formatter with brackets
    class BracketFormatter(logging.Formatter):
        """Custom formatter with brackets for structured logging"""

        def format(self, record):
            # Get timestamp
            timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

            # Get user context (could be extended for multi-user scenarios)
            user = getattr(record, 'user', 'system')

            # Format the message
            return f"[{timestamp}] [{user}] [{record.levelname}] [{record.name}] {record.getMessage()}"

    # Create logger
    logger = logging.getLogger("jellynotify")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Clear existing handlers
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(BracketFormatter())
    logger.addHandler(console_handler)

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        filename=Path(log_dir) / "jellynotify.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(getattr(logging, log_level.upper()))
    file_handler.setFormatter(BracketFormatter())
    logger.addHandler(file_handler)

    # Error file handler
    error_handler = logging.handlers.RotatingFileHandler(
        filename=Path(log_dir) / "jellynotify_errors.log",
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(BracketFormatter())
    logger.addHandler(error_handler)

    # Disable uvicorn access logger to avoid duplication
    logging.getLogger("uvicorn.access").disabled = True

    return logger


# ==================== CONFIGURATION VALIDATION ====================

class ConfigurationValidator:
    """Comprehensive configuration validator"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def load_and_validate_config(self, config_path: str = "/app/config/config.json") -> AppConfig:
        """Load and validate configuration with environment variable overrides"""

        try:
            # Load base configuration
            config_data = self._load_config_file(config_path)

            # Apply environment variable overrides
            self._apply_env_overrides(config_data)

            # Validate using Pydantic model
            config = AppConfig(**config_data)

            # Perform additional validation
            self._validate_jellyfin_config(config.jellyfin)
            self._validate_discord_config(config.discord)
            self._validate_template_files(config.templates)

            # Report validation results
            self._report_validation_results()

            return config

        except Exception as e:
            self.logger.error(f"Configuration validation failed: {e}")
            raise SystemExit(1)

    def _load_config_file(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                if config_path.endswith('.yaml') or config_path.endswith('.yml'):
                    return yaml.safe_load(f)
                else:
                    return json.load(f)
        except FileNotFoundError:
            self.logger.warning(f"Configuration file not found: {config_path}, using defaults")
            return {}
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            self.errors.append(f"Invalid configuration file format: {e}")
            raise
        except Exception as e:
            self.errors.append(f"Error reading configuration file: {e}")
            raise

    def _apply_env_overrides(self, config_data: Dict[str, Any]) -> None:
        """Apply environment variable overrides to configuration"""

        # Initialize nested dictionaries if they don't exist
        if 'jellyfin' not in config_data:
            config_data['jellyfin'] = {}
        if 'discord' not in config_data:
            config_data['discord'] = {'webhooks': {}}
        if 'webhooks' not in config_data['discord']:
            config_data['discord']['webhooks'] = {}

        # Jellyfin configuration
        env_mappings = {
            'JELLYFIN_SERVER_URL': ('jellyfin', 'server_url'),
            'JELLYFIN_API_KEY': ('jellyfin', 'api_key'),
            'JELLYFIN_USER_ID': ('jellyfin', 'user_id'),
            'DISCORD_WEBHOOK_URL': ('discord', 'webhooks', 'default', 'url'),
            'DISCORD_WEBHOOK_URL_MOVIES': ('discord', 'webhooks', 'movies', 'url'),
            'DISCORD_WEBHOOK_URL_TV': ('discord', 'webhooks', 'tv', 'url'),
            'DISCORD_WEBHOOK_URL_MUSIC': ('discord', 'webhooks', 'music', 'url'),
        }

        for env_var, path in env_mappings.items():
            value = os.getenv(env_var)
            if value:
                self._set_nested_value(config_data, path, value)

        # Enable webhooks that have URLs
        for webhook_type in ['default', 'movies', 'tv', 'music']:
            webhook_path = ['discord', 'webhooks', webhook_type]
            if self._get_nested_value(config_data, webhook_path + ['url']):
                self._set_nested_value(config_data, webhook_path + ['enabled'], True)
                if webhook_type == 'default':
                    self._set_nested_value(config_data, webhook_path + ['name'], 'General')
                else:
                    self._set_nested_value(config_data, webhook_path + ['name'], webhook_type.title())

        # Enable routing if any specific webhooks are configured
        if any(self._get_nested_value(config_data, ['discord', 'webhooks', wh, 'url'])
               for wh in ['movies', 'tv', 'music']):
            self._set_nested_value(config_data, ['discord', 'routing', 'enabled'], True)

    def _set_nested_value(self, data: Dict[str, Any], path: List[str], value: Any) -> None:
        """Set a nested value in a dictionary"""
        current = data
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[path[-1]] = value

    def _get_nested_value(self, data: Dict[str, Any], path: List[str]) -> Any:
        """Get a nested value from a dictionary"""
        current = data
        try:
            for key in path:
                current = current[key]
            return current
        except (KeyError, TypeError):
            return None

    def _validate_jellyfin_config(self, jellyfin_config: JellyfinConfig) -> None:
        """Validate Jellyfin configuration"""
        # These validations are already handled by Pydantic model
        # Additional runtime validations can be added here
        pass

    def _validate_discord_config(self, discord_config: DiscordConfig) -> None:
        """Validate Discord webhook configuration"""
        enabled_webhooks = [
            name for name, webhook in discord_config.webhooks.items()
            if webhook.enabled and webhook.url
        ]

        if not enabled_webhooks:
            self.errors.append("No Discord webhooks are configured and enabled")
        else:
            self.logger.info(f"Enabled Discord webhooks: {', '.join(enabled_webhooks)}")

    def _validate_template_files(self, templates_config: TemplatesConfig) -> None:
        """Validate that required template files exist"""
        template_dir = Path(templates_config.directory)
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
        """Report validation results and exit if there are errors"""
        if self.warnings:
            self.logger.warning("Configuration warnings:")
            for warning in self.warnings:
                self.logger.warning(f"  - {warning}")

        if self.errors:
            self.logger.error("Configuration errors:")
            for error in self.errors:
                self.logger.error(f"  - {error}")
            raise SystemExit(1)

        self.logger.info("Configuration validation completed successfully")


# ==================== DATABASE MANAGER ====================

class DatabaseManager:
    """Enhanced SQLite database manager with comprehensive error handling"""

    def __init__(self, config: DatabaseConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.db_path = config.path
        self.wal_mode = config.wal_mode

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def initialize(self) -> None:
        """Initialize database and create tables with comprehensive error handling"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                if self.wal_mode:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA synchronous=NORMAL")
                    await db.execute("PRAGMA temp_store=memory")
                    await db.execute("PRAGMA mmap_size=268435456")  # 256MB
                    await db.execute("PRAGMA cache_size=-32000")  # 32MB cache
                    await db.execute("PRAGMA busy_timeout=30000")  # 30 second timeout

                # Create media_items table
                await db.execute("""
                                 CREATE TABLE IF NOT EXISTS media_items
                                 (
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

                                     audio_codec
                                     TEXT,
                                     audio_channels
                                     INTEGER,
                                     audio_language
                                     TEXT,
                                     audio_bitrate
                                     INTEGER,

                                     imdb_id
                                     TEXT,
                                     tmdb_id
                                     TEXT,
                                     tvdb_id
                                     TEXT,

                                     date_created
                                     TEXT,
                                     date_modified
                                     TEXT,
                                     runtime_ticks
                                     INTEGER,
                                     official_rating
                                     TEXT,
                                     genres
                                     TEXT,
                                     studios
                                     TEXT,
                                     tags
                                     TEXT,

                                     album
                                     TEXT,
                                     artists
                                     TEXT,
                                     album_artist
                                     TEXT,

                                     width
                                     INTEGER,
                                     height
                                     INTEGER,

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

                # Create indexes for performance
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
        """Get media item by ID with error handling"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM media_items WHERE item_id = ?", (item_id,)
                )
                row = await cursor.fetchone()

                if row:
                    item_dict = dict(row)

                    # Deserialize list fields from JSON strings
                    for field in ['genres', 'studios', 'tags', 'artists']:
                        if field in item_dict and isinstance(item_dict[field], str):
                            try:
                                item_dict[field] = json.loads(item_dict[field])
                            except (json.JSONDecodeError, TypeError):
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
        """Get only the content hash of an item by ID"""
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
        """Save or update a single media item with error handling"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                item_dict = asdict(item)
                item_dict['updated_at'] = datetime.now(timezone.utc).isoformat()

                # Serialize list fields to JSON strings
                for field in ['genres', 'studios', 'tags', 'artists']:
                    if field in item_dict and isinstance(item_dict[field], list):
                        item_dict[field] = json.dumps(item_dict[field])

                # Prepare SQL
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
        """Save multiple items in a single transaction with error handling"""
        if not items:
            return 0

        saved_count = 0
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("BEGIN TRANSACTION")

                for item in items:
                    try:
                        item_dict = asdict(item)
                        item_dict['updated_at'] = datetime.now(timezone.utc).isoformat()

                        # Serialize list fields to JSON strings
                        for field in ['genres', 'studios', 'tags', 'artists']:
                            if field in item_dict and isinstance(item_dict[field], list):
                                item_dict[field] = json.dumps(item_dict[field])

                        # Prepare SQL
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
                        continue

                await db.commit()
                self.logger.debug(f"Successfully saved {saved_count}/{len(items)} items in batch")

        except aiosqlite.Error as e:
            self.logger.error(f"Database error during batch save: {e}")
            try:
                await db.rollback()
            except:
                pass
        except Exception as e:
            self.logger.error(f"Unexpected error during batch save: {e}")

        return saved_count

    async def vacuum_database(self) -> None:
        """Vacuum database for maintenance with error handling"""
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
        """Get database statistics with error handling"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Total items
                cursor = await db.execute("SELECT COUNT(*) FROM media_items")
                total_items = (await cursor.fetchone())[0]

                # Items by type
                cursor = await db.execute(
                    "SELECT item_type, COUNT(*) FROM media_items GROUP BY item_type ORDER BY COUNT(*) DESC"
                )
                item_types = dict(await cursor.fetchall())

                # Last updated
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
    """Enhanced Jellyfin API client with comprehensive error handling"""

    def __init__(self, config: JellyfinConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.client = None
        self.last_connection_check = 0
        self.connection_check_interval = 60  # seconds
        self.max_retries = 3
        self.retry_delay = 5  # seconds

    async def connect(self) -> bool:
        """Connect to Jellyfin server with retry logic and error handling"""
        for attempt in range(self.max_retries):
            try:
                self.client = JellyfinClient()

                # Configure client
                self.client.config.app(
                    self.config.client_name,
                    self.config.client_version,
                    self.config.device_name,
                    self.config.device_id
                )

                # Set SSL mode based on URL
                self.client.config.data["auth.ssl"] = self.config.server_url.startswith('https')

                # Use API key authentication
                credentials = {
                    "Servers": [{
                        "AccessToken": self.config.api_key,
                        "address": self.config.server_url,
                        "UserId": self.config.user_id,
                        "Id": self.config.device_id
                    }]
                }

                self.client.authenticate(credentials, discover=False)

                # Test connection
                response = self.client.jellyfin.get_system_info()
                if response:
                    server_name = response.get('ServerName', 'Unknown')
                    server_version = response.get('Version', 'Unknown')
                    self.logger.info(f"Connected to Jellyfin server: {server_name} v{server_version}")
                    return True

                self.logger.warning(f"Connection attempt {attempt + 1} failed: No response from server")

            except Exception as e:
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {e}")

                if attempt < self.max_retries - 1:
                    self.logger.info(f"Retrying connection in {self.retry_delay} seconds...")
                    await asyncio.sleep(self.retry_delay)
                else:
                    self.logger.error(f"Failed to connect to Jellyfin after {self.max_retries} attempts")

        return False

    async def is_connected(self) -> bool:
        """Check if connected to Jellyfin with caching"""
        current_time = time.time()

        # Only check connection every minute to avoid spam
        if current_time - self.last_connection_check < self.connection_check_interval:
            return self.client is not None

        self.last_connection_check = current_time

        if not self.client:
            return False

        try:
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
        """Get all media items from Jellyfin with comprehensive error handling"""
        if not await self.is_connected():
            if not await self.connect():
                raise ConnectionError("Cannot connect to Jellyfin server")

        start_index = 0
        all_items = []
        total_items_processed = 0

        while True:
            try:
                response = self.client.jellyfin.user_items(params={
                    'recursive': True,
                    'includeItemTypes': "Movie,Series,Season,Episode,MusicVideo,Audio,MusicAlbum,MusicArtist,Book,Photo,BoxSet",
                    'fields': "Overview,MediaStreams,ProviderIds,Path,MediaSources,DateCreated,DateModified,ProductionYear,RunTimeTicks,OfficialRating,Genres,Studios,Tags,IndexNumber,ParentIndexNumber,Album,Artists,AlbumArtist,Width,Height",
                    'startIndex': start_index,
                    'limit': batch_size
                })

                if not response or 'Items' not in response:
                    break

                items = response['Items']
                if not items:
                    break

                # Process this batch
                if process_batch_callback:
                    await process_batch_callback(items)
                else:
                    all_items.extend(items)

                total_items_processed += len(items)
                start_index += len(items)

                # Log progress periodically
                if total_items_processed % (batch_size * 10) == 0:
                    self.logger.info(f"Processed {total_items_processed} items from Jellyfin...")

                # Respect API rate limits
                await asyncio.sleep(0.1)

            except Exception as e:
                self.logger.error(f"Error fetching items from Jellyfin: {e}")
                break

        self.logger.info(f"Completed processing {total_items_processed} items from Jellyfin")
        return all_items if not process_batch_callback else []

    def extract_media_item(self, jellyfin_item: Dict[str, Any]) -> MediaItem:
        """Extract MediaItem from Jellyfin API response with error handling"""
        try:
            # Get media streams
            media_streams = jellyfin_item.get('MediaStreams', [])
            video_stream = next((s for s in media_streams if s.get('Type') == 'Video'), {})
            audio_stream = next((s for s in media_streams if s.get('Type') == 'Audio'), {})

            # Get provider IDs
            provider_ids = jellyfin_item.get('ProviderIds', {})

            # Handle index numbers based on item type
            season_number = None
            episode_number = None

            if jellyfin_item.get('Type') == 'Season':
                season_number = jellyfin_item.get('IndexNumber')
            elif jellyfin_item.get('Type') == 'Episode':
                episode_number = jellyfin_item.get('IndexNumber')
                season_number = jellyfin_item.get('ParentIndexNumber')

            # Create the MediaItem object
            return MediaItem(
                item_id=jellyfin_item['Id'],
                name=jellyfin_item.get('Name', ''),
                item_type=jellyfin_item.get('Type', ''),
                year=jellyfin_item.get('ProductionYear'),
                series_name=jellyfin_item.get('SeriesName'),
                season_number=season_number,
                episode_number=episode_number,
                overview=jellyfin_item.get('Overview'),

                # Video properties
                video_height=video_stream.get('Height'),
                video_width=video_stream.get('Width'),
                video_codec=video_stream.get('Codec'),
                video_profile=video_stream.get('Profile'),
                video_range=video_stream.get('VideoRange'),
                video_framerate=video_stream.get('RealFrameRate'),
                aspect_ratio=video_stream.get('AspectRatio'),

                # Audio properties
                audio_codec=audio_stream.get('Codec'),
                audio_channels=audio_stream.get('Channels'),
                audio_language=audio_stream.get('Language'),
                audio_bitrate=audio_stream.get('BitRate'),

                # Provider IDs
                imdb_id=provider_ids.get('Imdb'),
                tmdb_id=provider_ids.get('Tmdb'),
                tvdb_id=provider_ids.get('Tvdb'),

                # Enhanced metadata
                date_created=jellyfin_item.get('DateCreated'),
                date_modified=jellyfin_item.get('DateModified'),
                runtime_ticks=jellyfin_item.get('RunTimeTicks'),
                official_rating=jellyfin_item.get('OfficialRating'),
                genres=jellyfin_item.get('Genres', []),
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

                # File info
                file_path=jellyfin_item.get('Path'),
                file_size=jellyfin_item.get('Size'),
                last_modified=jellyfin_item.get('DateModified')
            )

        except Exception as e:
            self.logger.error(f"Error extracting media item from Jellyfin data: {e}")
            # Return a minimal MediaItem to prevent complete failure
            return MediaItem(
                item_id=jellyfin_item.get('Id', 'unknown'),
                name=jellyfin_item.get('Name', 'Unknown'),
                item_type=jellyfin_item.get('Type', 'Unknown')
            )


# ==================== CHANGE DETECTOR ====================

class ChangeDetector:
    """Enhanced change detector with comprehensive error handling"""

    def __init__(self, config: NotificationsConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.watch_changes = config.watch_changes

    def detect_changes(self, old_item: MediaItem, new_item: MediaItem) -> List[Dict[str, Any]]:
        """Detect changes between two media items with error handling"""
        changes = []

        try:
            # Resolution changes
            if (self.watch_changes.get('resolution', True) and
                    old_item.video_height != new_item.video_height):
                changes.append({
                    'type': 'resolution',
                    'field': 'video_height',
                    'old_value': old_item.video_height,
                    'new_value': new_item.video_height,
                    'description': f"Resolution changed from {old_item.video_height}p to {new_item.video_height}p"
                })

            # Codec changes
            if (self.watch_changes.get('codec', True) and
                    old_item.video_codec != new_item.video_codec):
                changes.append({
                    'type': 'codec',
                    'field': 'video_codec',
                    'old_value': old_item.video_codec,
                    'new_value': new_item.video_codec,
                    'description': f"Video codec changed from {old_item.video_codec or 'Unknown'} to {new_item.video_codec or 'Unknown'}"
                })

            # Audio codec changes
            if (self.watch_changes.get('audio_codec', True) and
                    old_item.audio_codec != new_item.audio_codec):
                changes.append({
                    'type': 'audio_codec',
                    'field': 'audio_codec',
                    'old_value': old_item.audio_codec,
                    'new_value': new_item.audio_codec,
                    'description': f"Audio codec changed from {old_item.audio_codec or 'Unknown'} to {new_item.audio_codec or 'Unknown'}"
                })

            # Audio channels changes
            if (self.watch_changes.get('audio_channels', True) and
                    old_item.audio_channels != new_item.audio_channels):
                channels_old = f"{old_item.audio_channels or 0} channel{'s' if (old_item.audio_channels or 0) != 1 else ''}"
                channels_new = f"{new_item.audio_channels or 0} channel{'s' if (new_item.audio_channels or 0) != 1 else ''}"
                changes.append({
                    'type': 'audio_channels',
                    'field': 'audio_channels',
                    'old_value': old_item.audio_channels,
                    'new_value': new_item.audio_channels,
                    'description': f"Audio channels changed from {channels_old} to {channels_new}"
                })

            # HDR status changes
            if (self.watch_changes.get('hdr_status', True) and
                    old_item.video_range != new_item.video_range):
                changes.append({
                    'type': 'hdr_status',
                    'field': 'video_range',
                    'old_value': old_item.video_range,
                    'new_value': new_item.video_range,
                    'description': f"HDR status changed from {old_item.video_range or 'SDR'} to {new_item.video_range or 'SDR'}"
                })

            # File size changes
            if (self.watch_changes.get('file_size', True) and
                    old_item.file_size != new_item.file_size):
                changes.append({
                    'type': 'file_size',
                    'field': 'file_size',
                    'old_value': old_item.file_size,
                    'new_value': new_item.file_size,
                    'description': "File size changed"
                })

            # Provider ID changes
            if self.watch_changes.get('provider_ids', True):
                for provider, old_val, new_val in [
                    ('imdb', old_item.imdb_id, new_item.imdb_id),
                    ('tmdb', old_item.tmdb_id, new_item.tmdb_id),
                    ('tvdb', old_item.tvdb_id, new_item.tvdb_id)
                ]:
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
    """Enhanced Discord webhook notifier with comprehensive error handling"""

    def __init__(self, config: DiscordConfig, jellyfin_url: str, logger: logging.Logger):
        self.config = config
        self.jellyfin_url = jellyfin_url
        self.logger = logger
        self.routing_enabled = config.routing.get('enabled', False)
        self.webhooks = config.webhooks
        self.routing_config = config.routing
        self.rate_limit = config.rate_limit

        # Per-webhook rate limiting tracking
        self.webhook_rate_limits = {}
        self.session = None

        # Initialize Jinja2 templates
        self.template_env = None

        # Validate webhook configuration
        self._validate_webhook_config()

    async def initialize(self, templates_config: TemplatesConfig) -> None:
        """Initialize the notifier with error handling"""
        try:
            # Initialize HTTP session
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={'User-Agent': 'JellyNotify/2.0.0'}
            )

            # Initialize Jinja2 templates
            self.template_env = Environment(
                loader=FileSystemLoader(templates_config.directory),
                autoescape=True,
                enable_async=False
            )

            # Test template loading
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
        """Close the notifier session"""
        if self.session:
            try:
                await self.session.close()
                self.logger.debug("Discord notifier session closed")
            except Exception as e:
                self.logger.warning(f"Error closing Discord session: {e}")

    def _validate_webhook_config(self) -> None:
        """Validate webhook configuration and set up fallbacks"""
        enabled_webhooks = [
            name for name, webhook in self.webhooks.items()
            if webhook.enabled and webhook.url
        ]

        if not enabled_webhooks:
            self.logger.warning("No enabled Discord webhooks configured")
            return

        # Initialize rate limiting for each webhook
        for webhook_name, webhook_config in self.webhooks.items():
            if webhook_config.enabled:
                self.webhook_rate_limits[webhook_name] = {
                    'last_request_time': 0,
                    'request_count': 0
                }

        self.logger.info(f"Configured Discord webhooks: {', '.join(enabled_webhooks)}")

    def _get_webhook_for_item(self, item: MediaItem) -> Optional[Dict[str, Any]]:
        """Determine which webhook to use for a given item"""
        if not self.routing_enabled:
            # Use default webhook or first enabled webhook
            for webhook_name, webhook_config in self.webhooks.items():
                if webhook_config.enabled and webhook_config.url:
                    return {
                        'name': webhook_name,
                        'config': webhook_config
                    }
            return None

        # Routing is enabled - determine based on item type
        item_type = item.item_type
        movie_types = self.routing_config.get('movie_types', ['Movie'])
        tv_types = self.routing_config.get('tv_types', ['Episode', 'Season', 'Series'])
        music_types = self.routing_config.get('music_types', ['Audio', 'MusicAlbum', 'MusicArtist'])
        fallback_webhook = self.routing_config.get('fallback_webhook', 'default')

        target_webhook = None

        if item_type in movie_types:
            target_webhook = 'movies'
        elif item_type in tv_types:
            target_webhook = 'tv'
        elif item_type in music_types:
            target_webhook = 'music'
        else:
            target_webhook = fallback_webhook

        # Check if target webhook is enabled and has URL
        if (target_webhook in self.webhooks and
                self.webhooks[target_webhook].enabled and
                self.webhooks[target_webhook].url):
            return {
                'name': target_webhook,
                'config': self.webhooks[target_webhook]
            }

        # Fall back to fallback webhook
        if (fallback_webhook in self.webhooks and
                self.webhooks[fallback_webhook].enabled and
                self.webhooks[fallback_webhook].url):
            self.logger.debug(f"Target webhook '{target_webhook}' not available, using fallback '{fallback_webhook}'")
            return {
                'name': fallback_webhook,
                'config': self.webhooks[fallback_webhook]
            }

        # Fall back to any enabled webhook
        for webhook_name, webhook_config in self.webhooks.items():
            if webhook_config.enabled and webhook_config.url:
                self.logger.debug(f"Using '{webhook_name}' as last resort webhook")
                return {
                    'name': webhook_name,
                    'config': webhook_config
                }

        return None

    async def _wait_for_rate_limit(self, webhook_name: str) -> None:
        """Wait for rate limit if necessary for specific webhook"""
        if webhook_name not in self.webhook_rate_limits:
            self.webhook_rate_limits[webhook_name] = {
                'last_request_time': 0,
                'request_count': 0
            }

        rate_limit_info = self.webhook_rate_limits[webhook_name]
        current_time = time.time()

        # Reset counter if period has passed
        period_seconds = self.rate_limit.get('period_seconds', 2)
        if current_time - rate_limit_info['last_request_time'] >= period_seconds:
            rate_limit_info['request_count'] = 0

        # Check if we need to wait
        max_requests = self.rate_limit.get('requests_per_period', 5)
        if rate_limit_info['request_count'] >= max_requests:
            wait_time = period_seconds - (current_time - rate_limit_info['last_request_time'])
            if wait_time > 0:
                self.logger.debug(f"Rate limiting webhook '{webhook_name}', waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                rate_limit_info['request_count'] = 0

        rate_limit_info['last_request_time'] = time.time()
        rate_limit_info['request_count'] += 1

    async def send_notification(self, item: MediaItem, changes: Optional[List[Dict[str, Any]]] = None,
                                is_new: bool = True) -> bool:
        """Send Discord notification with comprehensive error handling"""
        try:
            webhook_info = self._get_webhook_for_item(item)

            if not webhook_info:
                self.logger.warning("No suitable Discord webhook found for notification")
                return False

            webhook_name = webhook_info['name']
            webhook_config = webhook_info['config']
            webhook_url = webhook_config.url

            await self._wait_for_rate_limit(webhook_name)

            # Determine template and color
            if is_new:
                template_name = 'new_item.j2'
                color = 0x00FF00  # Green
            else:
                template_name = 'upgraded_item.j2'
                color = self._get_change_color(changes)

            # Load and render template
            try:
                template = self.template_env.get_template(template_name)
            except TemplateNotFound:
                self.logger.error(f"Template not found: {template_name}")
                return False
            except TemplateSyntaxError as e:
                self.logger.error(f"Template syntax error in {template_name}: {e}")
                return False

            # Prepare template data
            template_data = {
                'item': asdict(item),
                'changes': changes or [],
                'is_new': is_new,
                'color': color,
                'jellyfin_url': self.jellyfin_url,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'webhook_name': webhook_config.name,
                'webhook_target': webhook_name
            }

            # Render the template
            try:
                rendered = template.render(**template_data)
                payload = json.loads(rendered)
            except Exception as e:
                self.logger.error(f"Error rendering template {template_name}: {e}")
                return False

            # Send to Discord with retries
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    async with self.session.post(webhook_url, json=payload) as response:
                        if response.status == 204:
                            self.logger.info(
                                f"Successfully sent notification for {item.name} to '{webhook_name}' webhook")
                            return True
                        elif response.status == 429:
                            # Rate limited
                            retry_after = int(response.headers.get('Retry-After', '60'))
                            self.logger.warning(
                                f"Discord webhook '{webhook_name}' rate limited, retry after {retry_after} seconds")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            error_text = await response.text()
                            self.logger.error(
                                f"Discord webhook '{webhook_name}' failed with status {response.status}: {error_text}")

                            if attempt < max_retries - 1:
                                await asyncio.sleep(2 ** attempt)  # Exponential backoff
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
        """Get color based on change types with error handling"""
        try:
            colors = self.config.notifications.colors

            if not changes:
                return colors.get('new_item', 0x00FF00)

            # Prioritize change types
            change_types = [change['type'] for change in changes if isinstance(change, dict) and 'type' in change]

            if 'resolution' in change_types:
                return colors.get('resolution_upgrade', 0xFFD700)
            elif 'codec' in change_types:
                return colors.get('codec_upgrade', 0xFF8C00)
            elif 'hdr_status' in change_types:
                return colors.get('hdr_upgrade', 0xFF1493)
            elif any(t in change_types for t in ['audio_codec', 'audio_channels']):
                return colors.get('audio_upgrade', 0x9370DB)
            elif 'provider_ids' in change_types:
                return colors.get('provider_update', 0x1E90FF)
            else:
                return colors.get('new_item', 0x00FF00)

        except Exception as e:
            self.logger.error(f"Error determining change color: {e}")
            return 0x00FF00  # Default to green

    async def send_server_status(self, is_online: bool) -> bool:
        """Send server status notification to all enabled webhooks with comprehensive error handling"""
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
                            # Rate limited - get retry after header
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
        """Get status of all configured webhooks with error handling"""
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
                        # Create safe URL preview (hide sensitive parts)
                        parsed_url = urlparse(webhook_url)
                        if parsed_url.path:
                            # Extract webhook ID for preview
                            path_parts = parsed_url.path.split('/')
                            if len(path_parts) >= 4:
                                webhook_id = path_parts[-2]
                                token_preview = path_parts[-1][:8] + "..." if len(path_parts[-1]) > 8 else \
                                path_parts[-1]
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
    """Enhanced main webhook service with comprehensive error handling"""

    def __init__(self):
        """Initialize webhook service with proper error handling"""
        self.logger = setup_logging()
        self.config = None
        self.db = None
        self.jellyfin = None
        self.change_detector = None
        self.discord = None

        # Service state
        self.last_vacuum = 0
        self.server_was_offline = False
        self.sync_in_progress = False
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

        # Initialize components with validated config
        try:
            self.db = DatabaseManager(self.config.database, self.logger)
            self.jellyfin = JellyfinAPI(self.config.jellyfin, self.logger)
            self.change_detector = ChangeDetector(self.config.notifications, self.logger)
            self.discord = DiscordNotifier(self.config.discord, self.config.jellyfin.server_url, self.logger)
        except Exception as e:
            self.logger.error(f"Failed to initialize service components: {e}")
            raise SystemExit(1)

    async def initialize(self) -> None:
        """Initialize all components with comprehensive error handling"""
        try:
            self.logger.info("Initializing JellyNotify Discord Webhook Service...")

            # Initialize database
            try:
                await self.db.initialize()
                self.logger.info("Database initialized successfully")
            except Exception as e:
                self.logger.error(f"Database initialization failed: {e}")
                raise

            # Initialize Discord notifier
            try:
                await self.discord.initialize(self.config.templates)
                self.logger.info("Discord notifier initialized successfully")
            except Exception as e:
                self.logger.error(f"Discord notifier initialization failed: {e}")
                raise

            # Connect to Jellyfin
            try:
                if await self.jellyfin.connect():
                    self.logger.info("Successfully connected to Jellyfin")

                    # Perform startup sync if enabled
                    if self.config.sync.startup_sync:
                        await self._perform_startup_sync()
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

    async def _perform_startup_sync(self) -> None:
        """Perform startup sync with error handling"""
        try:
            self.logger.info("Starting initial Jellyfin library sync...")
            await self.sync_jellyfin_library()
            self.logger.info("Initial sync completed successfully")
        except Exception as e:
            self.logger.error(f"Initial sync failed: {e}")
            # Don't raise - service can continue without initial sync

    async def process_batch(self, jellyfin_items: List[Dict[str, Any]]) -> None:
        """Process a batch of items from Jellyfin API with error handling"""
        if not jellyfin_items:
            return

        processed_count = 0
        error_count = 0
        media_items = []

        for jellyfin_item in jellyfin_items:
            try:
                media_item = self.jellyfin.extract_media_item(jellyfin_item)

                # Check if item exists and has changed (using hash)
                existing_hash = await self.db.get_item_hash(media_item.item_id)

                if existing_hash and existing_hash == media_item.content_hash:
                    # Item exists and hasn't changed - skip
                    continue

                media_items.append(media_item)
                processed_count += 1

            except Exception as e:
                error_count += 1
                item_id = jellyfin_item.get('Id', 'unknown') if isinstance(jellyfin_item, dict) else 'unknown'
                self.logger.warning(f"Error processing item {item_id}: {e}")
                continue

        # Save all changed items in a single transaction
        if media_items:
            try:
                saved_count = await self.db.save_items_batch(media_items)
                self.logger.debug(f"Saved {saved_count}/{len(media_items)} changed items in batch")
            except Exception as e:
                self.logger.error(f"Error saving batch of {len(media_items)} items: {e}")

        if error_count > 0:
            self.logger.warning(
                f"Batch processing completed with {error_count} errors out of {len(jellyfin_items)} items")

    async def sync_jellyfin_library(self, background: bool = False) -> Dict[str, Any]:
        """Sync entire Jellyfin library to database with comprehensive error handling"""
        # Prevent concurrent syncs
        if self.sync_in_progress:
            message = "Library sync already in progress, skipping new request"
            self.logger.warning(message)
            return {"status": "warning", "message": message}

        self.sync_in_progress = True
        sync_start_time = time.time()

        try:
            sync_type = "background" if background else "initial"
            self.logger.info(f"Starting {sync_type} Jellyfin library sync...")

            # Check Jellyfin connection
            if not await self.jellyfin.is_connected():
                if not await self.jellyfin.connect():
                    raise ConnectionError("Cannot connect to Jellyfin server for sync")

            batch_size = self.config.sync.sync_batch_size

            # Use the incremental API with callback for immediate processing
            items_processed = 0
            try:
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
                self.logger.error(f"Error during library sync iteration: {e}")
                raise

        except Exception as e:
            sync_duration = time.time() - sync_start_time
            error_msg = f"Library sync failed after {sync_duration:.1f} seconds: {e}"
            self.logger.error(error_msg)
            return {"status": "error", "message": error_msg}

        finally:
            self.sync_in_progress = False

    async def process_webhook(self, payload: WebhookPayload) -> Dict[str, Any]:
        """Process incoming webhook from Jellyfin with comprehensive error handling"""
        request_start_time = time.time()

        try:
            # Wait for initial sync if needed (with timeout)
            if not self.initial_sync_complete and self.sync_in_progress:
                await self._wait_for_initial_sync()

            # Extract media item from webhook
            try:
                media_item = self._extract_from_webhook(payload)
            except Exception as e:
                self.logger.error(f"Error extracting media item from webhook payload: {e}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid webhook payload: {str(e)}"
                )

            # Process the item
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
        """Wait for initial sync to complete with timeout"""
        max_wait_time = 300  # 5 minutes
        check_interval = 2  # 2 seconds
        wait_time = 0

        while self.sync_in_progress and wait_time < max_wait_time:
            self.logger.debug(f"Waiting for initial sync to complete... ({wait_time}s/{max_wait_time}s)")
            await asyncio.sleep(check_interval)
            wait_time += check_interval

        if self.sync_in_progress:
            self.logger.warning(
                f"Initial sync still in progress after {max_wait_time}s timeout, "
                "proceeding with webhook processing"
            )

    async def _process_media_item(self, media_item: MediaItem) -> Dict[str, Any]:
        """Process a media item for changes and notifications"""
        # Check if item exists and has changed using content hash
        existing_hash = await self.db.get_item_hash(media_item.item_id)

        if existing_hash:
            # Item exists, check if it has changed
            if existing_hash != media_item.content_hash:
                # Fetch full item to detect specific changes
                existing_item = await self.db.get_item(media_item.item_id)

                if existing_item:
                    # Detect changes
                    changes = self.change_detector.detect_changes(existing_item, media_item)

                    if changes:
                        # Item was updated/upgraded
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
                    self.logger.warning(
                        f"Could not retrieve existing item {media_item.item_id} for change detection")
                    return {
                        "action": "error_retrieving_existing",
                        "changes_count": 0,
                        "notification_sent": False
                    }
            else:
                # No changes (hash matches)
                self.logger.debug(f"No changes detected for {media_item.name} (hash match)")
                return {
                    "action": "no_changes",
                    "changes_count": 0,
                    "notification_sent": False
                }
        else:
            # New item
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
        """Extract MediaItem from webhook payload with enhanced error handling"""
        try:
            # Validate required fields
            if not payload.ItemId:
                raise ValueError("ItemId is required")
            if not payload.Name:
                raise ValueError("Name is required")
            if not payload.ItemType:
                raise ValueError("ItemType is required")

            # Extract season and episode numbers safely
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

            return MediaItem(
                item_id=payload.ItemId,
                name=payload.Name,
                item_type=payload.ItemType,
                year=payload.Year,
                series_name=payload.SeriesName,
                season_number=season_number,
                episode_number=episode_number,
                overview=payload.Overview,

                # Video properties
                video_height=payload.Video_0_Height,
                video_width=payload.Video_0_Width,
                video_codec=payload.Video_0_Codec,
                video_profile=payload.Video_0_Profile,
                video_range=payload.Video_0_VideoRange,
                video_framerate=payload.Video_0_FrameRate,
                aspect_ratio=payload.Video_0_AspectRatio,

                # Audio properties
                audio_codec=payload.Audio_0_Codec,
                audio_channels=payload.Audio_0_Channels,
                audio_language=payload.Audio_0_Language,
                audio_bitrate=payload.Audio_0_Bitrate,

                # Provider IDs
                imdb_id=payload.Provider_imdb,
                tmdb_id=payload.Provider_tmdb,
                tvdb_id=payload.Provider_tvdb,

                # Defaults for API-only fields
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
        """Comprehensive health check with detailed status information"""
        try:
            health_data = {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "2.0.0",
                "components": {}
            }

            # Check Jellyfin connection
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

            # Check database
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

            # Check Discord webhooks
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

            # Service status
            health_data.update({
                "sync_in_progress": self.sync_in_progress,
                "initial_sync_complete": self.initial_sync_complete,
                "server_was_offline": self.server_was_offline
            })

            # Determine overall status
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
        """Manual sync command with enhanced error handling"""
        try:
            if self.sync_in_progress:
                return {
                    "status": "warning",
                    "message": "Library sync already in progress"
                }

            # Check Jellyfin connection before starting sync
            if not await self.jellyfin.is_connected():
                return {
                    "status": "error",
                    "message": "Cannot start sync: Jellyfin server is not connected"
                }

            # Start sync in background
            sync_task = asyncio.create_task(self.sync_jellyfin_library(background=True))

            # Don't wait for completion, return immediately
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
        """Get comprehensive service statistics"""
        try:
            stats = {
                "service": {
                    "version": "2.0.0",
                    "uptime_seconds": time.time() - getattr(self, '_start_time', time.time()),
                    "sync_in_progress": self.sync_in_progress,
                    "initial_sync_complete": self.initial_sync_complete
                }
            }

            # Database stats
            try:
                db_stats = await self.db.get_stats()
                stats["database"] = db_stats
            except Exception as e:
                stats["database"] = {"error": str(e)}

            # Webhook stats
            try:
                webhook_status = self.discord.get_webhook_status()
                stats["webhooks"] = webhook_status
            except Exception as e:
                stats["webhooks"] = {"error": str(e)}

            # Jellyfin connection status
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
        """Enhanced background maintenance tasks with comprehensive error handling"""
        self._start_time = time.time()

        # Initial delay to allow startup to complete
        await asyncio.sleep(60)

        self.logger.info("Background maintenance tasks started")

        while not self.shutdown_event.is_set():
            try:
                await self._run_maintenance_cycle()

                # Sleep for 60 seconds or until shutdown
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=60.0)
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    continue  # Normal timeout, continue loop

            except Exception as e:
                self.logger.error(f"Error in background maintenance cycle: {e}")
                # Sleep before retrying
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=60.0)
                    break
                except asyncio.TimeoutError:
                    continue

        self.logger.info("Background maintenance tasks stopped")

    async def _run_maintenance_cycle(self) -> None:
        """Run a single maintenance cycle"""
        current_time = time.time()

        # Database vacuum
        await self._perform_database_maintenance(current_time)

        # Jellyfin connection monitoring
        await self._monitor_jellyfin_connection()

        # Periodic background sync
        await self._check_periodic_sync()

    async def _perform_database_maintenance(self, current_time: float) -> None:
        """Perform database maintenance tasks"""
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
        """Monitor Jellyfin connection status and send notifications"""
        try:
            jellyfin_connected = await self.jellyfin.is_connected()

            if not jellyfin_connected and not self.server_was_offline:
                # Server went offline
                await self.discord.send_server_status(False)
                self.server_was_offline = True
                self.logger.warning("Jellyfin server went offline")

            elif jellyfin_connected and self.server_was_offline:
                # Server came back online
                await self.discord.send_server_status(True)
                self.server_was_offline = False
                self.logger.info("Jellyfin server is back online")

                # Do a background sync after server comes back online
                if not self.sync_in_progress:
                    self.logger.info("Starting recovery sync after server came back online")
                    asyncio.create_task(self.sync_jellyfin_library(background=True))

        except Exception as e:
            self.logger.error(f"Error monitoring Jellyfin connection: {e}")

    async def _check_periodic_sync(self) -> None:
        """Check if periodic sync is needed"""
        try:
            if self.sync_in_progress:
                return

            sync_interval = 24 * 3600  # 24 hours in seconds
            last_sync_time_str = await self.db.get_last_sync_time()

            if last_sync_time_str:
                try:
                    # Handle both with and without 'Z' suffix
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
        """Cleanup resources with comprehensive error handling"""
        self.logger.info("Starting service cleanup...")

        try:
            # Signal shutdown to background tasks
            self.shutdown_event.set()

            # Close Discord notifier
            if self.discord:
                try:
                    await self.discord.close()
                    self.logger.debug("Discord notifier closed")
                except Exception as e:
                    self.logger.warning(f"Error closing Discord notifier: {e}")

            # Close database connections
            if self.db:
                try:
                    # Any specific database cleanup can go here
                    self.logger.debug("Database connections closed")
                except Exception as e:
                    self.logger.warning(f"Error during database cleanup: {e}")

            self.logger.info("Service cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during service cleanup: {e}")

# ==================== FASTAPI APPLICATION ====================

# Application lifespan manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan with proper error handling"""
    # Startup
    service = app.state.service
    try:
        await service.initialize()
        # Start background tasks
        background_task = asyncio.create_task(service.background_tasks())
        app.state.background_task = background_task
        app.state.logger.info("FastAPI application started successfully")
        yield
    except Exception as e:
        app.state.logger.error(f"Application startup failed: {e}")
        raise
    finally:
        # Shutdown
        try:
            app.state.logger.info("Shutting down FastAPI application...")

            # Cancel background tasks
            if hasattr(app.state, 'background_task'):
                app.state.background_task.cancel()
                try:
                    await app.state.background_task
                except asyncio.CancelledError:
                    pass

            # Cleanup service
            await service.cleanup()
            app.state.logger.info("FastAPI application shutdown completed")
        except Exception as e:
            app.state.logger.error(f"Error during application shutdown: {e}")

# Create FastAPI application
app = FastAPI(
    title="JellyNotify Discord Webhook Service",
    version="2.0.0",
    description="Enhanced webhook service for Jellyfin to Discord notifications",
    lifespan=lifespan
)

# Initialize service
service = WebhookService()
app.state.service = service
app.state.logger = service.logger

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler with comprehensive error handling"""
    logger = app.state.logger

    # Get request info
    client_host = getattr(getattr(request, "client", None), "host", "unknown")
    client_port = getattr(getattr(request, "client", None), "port", "unknown")
    method = request.method
    url = str(request.url)

    # Log the error with context
    logger.error(
        f"Unhandled exception in {method} {url} from {client_host}:{client_port}: {exc}",
        exc_info=True
    )

    # Don't expose internal errors in production
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail}
        )
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"}
        )

# Request validation error handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic validation errors with detailed information"""
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
    """Main webhook endpoint for Jellyfin with enhanced logging"""
    client_host = getattr(getattr(request, "client", None), "host", "unknown")
    app.state.logger.debug(f"Webhook received from {client_host} for item: {payload.ItemId}")

    try:
        result = await app.state.service.process_webhook(payload)
        return result
    except HTTPException:
        raise
    except Exception as e:
        app.state.logger.error(f"Webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")

@app.post("/webhook/debug")
async def webhook_debug_endpoint(request: Request) -> Dict[str, Any]:
    """Debug webhook endpoint with comprehensive error analysis"""
    logger = app.state.logger
    client_host = getattr(getattr(request, "client", None), "host", "unknown")

    try:
        # Get raw request data
        raw_body = await request.body()
        content_type = request.headers.get("content-type", "")

        logger.info(f"Debug webhook received from {client_host}")
        logger.debug(f"Content-Type: {content_type}, Body length: {len(raw_body)} bytes")

        # Parse JSON
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

        # Validate with Pydantic
        try:
            payload = WebhookPayload(**json_data)
            logger.info(f"Validation successful for item: {payload.ItemId}")

            # Process normally
            result = await app.state.service.process_webhook(payload)
            return {
                "status": "success",
                "validation": "passed",
                "result": result
            }

        except ValidationError as e:
            logger.warning(f"Validation failed: {e}")

            # Detailed validation analysis
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

            # Analyze payload fields
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
    """Enhanced health check endpoint"""
    return await app.state.service.health_check()

@app.post("/sync")
async def sync_endpoint() -> Dict[str, Any]:
    """Manual sync endpoint with enhanced response"""
    return await app.state.service.manual_sync()

@app.get("/stats")
async def stats_endpoint() -> Dict[str, Any]:
    """Get comprehensive service statistics"""
    return await app.state.service.get_service_stats()

@app.get("/webhooks")
async def webhooks_endpoint() -> Dict[str, Any]:
    """Get webhook configuration and status"""
    return app.state.service.discord.get_webhook_status()

@app.post("/test-webhook")
async def test_webhook_endpoint(webhook_name: str = "default") -> Dict[str, Any]:
    """Test a specific webhook with enhanced error handling"""
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

        # Create test payload
        test_payload = {
            "embeds": [{
                "title": "🧪 Webhook Test",
                "description": f"Test notification from {webhook_info['config'].name} webhook",
                "color": 65280,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {
                    "text": "JellyNotify Test",
                    "icon_url": app.state.service.config.jellyfin.server_url + "/web/favicon.ico"
                }
            }]
        }

        webhook_url = webhook_info['config'].url

        # Send test
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
    """Get sanitized configuration information"""
    try:
        config = app.state.service.config

        # Return sanitized config (remove sensitive data)
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
    import uvicorn

    # Signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger = service.logger
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        # The lifespan manager will handle the actual cleanup
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run with uvicorn
    try:
        uvicorn.run(
            "main:app",
            host=service.config.server.host,
            port=service.config.server.port,
            log_level=service.config.server.log_level.lower(),
            reload=False,
            access_log=False,  # We handle our own logging
            server_header=False,
            date_header=False
        )
    except Exception as e:
        service.logger.error(f"Failed to start server: {e}")
        sys.exit(1)