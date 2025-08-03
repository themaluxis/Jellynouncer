"""
JellyNotify Configuration Models and Validation

This module contains all Pydantic configuration models and the configuration
validation logic. Keeps tightly coupled configuration components together.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, ConfigDict, ValidationError, field_validator


# ==================== CONFIGURATION MODELS ====================

class JellyfinConfig(BaseModel):
    """Configuration model for Jellyfin server connection settings."""
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
        """Validate and normalize Jellyfin server URL."""
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
        """Validate that required string fields are not empty or just whitespace."""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class WebhookConfig(BaseModel):
    """Configuration for individual Discord webhooks."""
    model_config = ConfigDict(extra='forbid')

    url: Optional[str] = Field(default=None, description="Discord webhook URL")
    name: str = Field(..., description="Webhook display name")
    enabled: bool = Field(default=False, description="Whether webhook is enabled")
    grouping: Dict[str, Any] = Field(default_factory=dict, description="Grouping configuration")

    @field_validator('url')
    @classmethod
    def validate_webhook_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate Discord webhook URL format."""
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
    """Overall Discord integration configuration."""
    model_config = ConfigDict(extra='forbid')

    webhooks: Dict[str, WebhookConfig] = Field(default_factory=dict)
    routing: Dict[str, Any] = Field(default_factory=dict)
    rate_limit: Dict[str, Any] = Field(default_factory=dict)


class DatabaseConfig(BaseModel):
    """SQLite database configuration and validation."""
    model_config = ConfigDict(extra='forbid')

    path: str = Field(default="/app/data/jellyfin_items.db")
    wal_mode: bool = Field(default=True)
    vacuum_interval_hours: int = Field(default=24, ge=1, le=168)  # 1 hour to 1 week

    @field_validator('path')
    @classmethod
    def validate_db_path(cls, v: str) -> str:
        """Validate database path and ensure parent directory is writable."""
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
    """Configuration for Jinja2 template files used to format Discord messages."""
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
        """Validate that template directory exists and is readable."""
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
    """Configuration for notification behavior and appearance."""
    model_config = ConfigDict(extra='forbid')

    watch_changes: Dict[str, bool] = Field(default_factory=dict)
    colors: Dict[str, int] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    """FastAPI server configuration."""
    model_config = ConfigDict(extra='forbid')

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080, ge=1024, le=65535)
    log_level: str = Field(default="INFO")

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate logging level against Python's standard levels."""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v


class SyncConfig(BaseModel):
    """Configuration for Jellyfin library synchronization behavior."""
    model_config = ConfigDict(extra='forbid')

    startup_sync: bool = Field(default=True)
    sync_batch_size: int = Field(default=100, ge=10, le=1000)
    api_request_delay: float = Field(default=0.1, ge=0.0, le=5.0)


class RatingServiceConfig(BaseModel):
    """Configuration for individual rating service (OMDb, TMDb, TVDb)."""
    model_config = ConfigDict(extra='forbid')

    enabled: bool = Field(default=False, description="Whether rating service is enabled")
    api_key: Optional[str] = Field(default=None, description="API key for the service")
    base_url: str = Field(..., description="Base URL for the service API")


class TVDBConfig(BaseModel):
    """Enhanced TVDB configuration supporting both licensed and subscriber models."""
    model_config = ConfigDict(extra='forbid')

    enabled: bool = Field(default=False, description="Whether TVDB service is enabled")
    api_key: Optional[str] = Field(default=None, description="TVDB v4 API key")
    subscriber_pin: Optional[str] = Field(default=None, description="TVDB subscriber PIN (for user-supported keys)")
    base_url: str = Field(default="https://api4.thetvdb.com/v4", description="TVDB API v4 base URL")
    access_mode: str = Field(default="auto", description="Access mode: 'auto', 'subscriber', or 'licensed'")

    @field_validator('api_key')
    @classmethod
    def validate_api_key(cls, v: Optional[str]) -> Optional[str]:
        """Validate API key format."""
        if v is None:
            return None
        if not v.strip():
            return None
        return v.strip()

    @field_validator('subscriber_pin')
    @classmethod
    def validate_subscriber_pin(cls, v: Optional[str]) -> Optional[str]:
        """Validate subscriber PIN format."""
        if v is None:
            return None
        if not v.strip():
            return None
        return v.strip()

    @field_validator('access_mode')
    @classmethod
    def validate_access_mode(cls, v: str) -> str:
        """Validate access mode."""
        valid_modes = ['auto', 'subscriber', 'licensed']
        if v.lower() not in valid_modes:
            raise ValueError(f"Access mode must be one of: {valid_modes}")
        return v.lower()


class RatingServicesConfig(BaseModel):
    """Configuration for all external rating services."""
    model_config = ConfigDict(extra='forbid')

    enabled: bool = Field(default=True, description="Global rating services enabled flag")
    omdb: RatingServiceConfig = Field(
        default_factory=lambda: RatingServiceConfig(
            enabled=False,
            api_key=None,
            base_url="http://www.omdbapi.com/"
        )
    )
    tmdb: RatingServiceConfig = Field(
        default_factory=lambda: RatingServiceConfig(
            enabled=False,
            api_key=None,
            base_url="https://api.themoviedb.org/3/"
        )
    )
    tvdb: TVDBConfig = Field(
        default_factory=lambda: TVDBConfig(
            enabled=False,
            api_key=None,
            subscriber_pin=None
        )
    )
    cache_duration_hours: int = Field(default=168, ge=1, le=8760, description="Rating cache duration in hours")
    request_timeout_seconds: int = Field(default=10, ge=1, le=60, description="HTTP request timeout")
    retry_attempts: int = Field(default=3, ge=1, le=10, description="Number of retry attempts")


class AppConfig(BaseModel):
    """Top-level application configuration that combines all sub-configurations."""
    model_config = ConfigDict(extra='forbid')

    jellyfin: JellyfinConfig
    discord: DiscordConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    templates: TemplatesConfig = Field(default_factory=TemplatesConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    rating_services: RatingServicesConfig = Field(default_factory=RatingServicesConfig)


# ==================== CONFIGURATION VALIDATION ====================

class ConfigurationValidator:
    """Comprehensive configuration validator with environment variable support."""

    def __init__(self, logger: logging.Logger):
        """Initialize validator with logger and empty error/warning lists."""
        self.logger = logger
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def load_and_validate_config(self, config_path: str = "/app/config/config.json") -> AppConfig:
        """Load configuration from file and environment, then validate."""
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
        """Load configuration data from JSON or YAML file."""
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
        """Apply environment variable overrides to configuration data."""
        # Initialize nested dictionaries if they don't exist
        if 'jellyfin' not in config_data:
            config_data['jellyfin'] = {}
        if 'discord' not in config_data:
            config_data['discord'] = {'webhooks': {}}
        if 'webhooks' not in config_data['discord']:
            config_data['discord']['webhooks'] = {}

        # Initialize rating services configuration
        if 'rating_services' not in config_data:
            config_data['rating_services'] = {
                'enabled': True,
                'omdb': {},
                'tmdb': {},
                'tvdb': {}
            }

        # Apply existing environment variable mappings
        env_mappings = {
            'JELLYFIN_SERVER_URL': ('jellyfin', 'server_url'),
            'JELLYFIN_API_KEY': ('jellyfin', 'api_key'),
            'JELLYFIN_USER_ID': ('jellyfin', 'user_id'),
            'DISCORD_WEBHOOK_URL': ('discord', 'webhooks', 'default', 'url'),
            'DISCORD_WEBHOOK_URL_MOVIES': ('discord', 'webhooks', 'movies', 'url'),
            'DISCORD_WEBHOOK_URL_TV': ('discord', 'webhooks', 'tv', 'url'),
            'DISCORD_WEBHOOK_URL_MUSIC': ('discord', 'webhooks', 'music', 'url'),

            # Rating service API keys
            'OMDB_API_KEY': ('rating_services', 'omdb', 'api_key'),
            'TMDB_API_KEY': ('rating_services', 'tmdb', 'api_key'),

            # TVDB v4 API configuration (updated)
            'TVDB_API_KEY': ('rating_services', 'tvdb', 'api_key'),
            'TVDB_SUBSCRIBER_PIN': ('rating_services', 'tvdb', 'subscriber_pin'),
        }

        # Apply environment variable overrides
        for env_var, path in env_mappings.items():
            value = os.getenv(env_var)
            if value:  # Only override if environment variable is set
                self._set_nested_value(config_data, path, value)

        # Auto-enable rating services that have API keys configured
        rating_services = config_data.get('rating_services', {})
        for service in ['omdb', 'tmdb']:
            service_config = rating_services.get(service, {})
            if service_config.get('api_key'):
                service_config['enabled'] = True
                self.logger.info(f"Auto-enabled {service.upper()} rating service (API key provided)")

        # Special handling for TVDB (enhanced logic)
        tvdb_config = rating_services.get('tvdb', {})
        tvdb_api_key = tvdb_config.get('api_key')
        tvdb_pin = tvdb_config.get('subscriber_pin')

        if tvdb_api_key:
            tvdb_config['enabled'] = True

            # Determine access mode based on available credentials
            if tvdb_pin:
                tvdb_config['access_mode'] = 'subscriber'
                self.logger.info("Auto-enabled TVDB rating service (subscriber mode: API key + PIN provided)")
            else:
                tvdb_config['access_mode'] = 'licensed'
                self.logger.info("Auto-enabled TVDB rating service (licensed mode: API key only provided)")

            # Set different cache durations based on access mode
            if tvdb_pin:
                # More aggressive caching for subscriber mode (7 days)
                config_data.setdefault('rating_services', {})['cache_duration_hours'] = 168
            else:
                # Less aggressive caching for licensed mode (24 hours)
                config_data.setdefault('rating_services', {})['cache_duration_hours'] = 24

        # Auto-enable webhooks that have URLs configured (existing logic)
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
        """Set a value at a nested path in a dictionary, creating intermediate dicts as needed."""
        current = data
        # Navigate to the parent of the target location, creating dicts as needed
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        # Set the final value
        current[path[-1]] = value

    def _get_nested_value(self, data: Dict[str, Any], path: List[str]) -> Any:
        """Get a value from a nested path in a dictionary."""
        current = data
        try:
            for key in path:
                current = current[key]
            return current
        except (KeyError, TypeError):
            return None  # Path doesn't exist

    def _validate_jellyfin_config(self, jellyfin_config: JellyfinConfig) -> None:
        """Perform additional validation on Jellyfin configuration."""
        # Pydantic models already handle most validation
        # This method can be extended for additional checks like:
        # - Testing connectivity to Jellyfin server
        # - Validating API key format
        # - Checking user permissions
        pass

    def _validate_discord_config(self, discord_config: DiscordConfig) -> None:
        """Validate Discord webhook configuration and report status."""
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
        """Validate that required template files exist and are readable."""
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
        """Report validation results and exit if there are errors."""
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