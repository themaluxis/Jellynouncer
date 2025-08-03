# Jellynouncer Configuration Guide

This guide covers all configuration options for Jellynouncer, a Discord notification system for Jellyfin media servers. The configuration system supports both JSON/YAML files and environment variable overrides for flexible deployment scenarios.

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration File Structure](#configuration-file-structure)
- [Core Configuration Sections](#core-configuration-sections)
  - [Jellyfin Settings](#jellyfin-settings)
  - [Discord Webhooks](#discord-webhooks)
  - [Database Configuration](#database-configuration)
  - [Template Settings](#template-settings)
  - [Notification Behavior](#notification-behavior)
  - [Web Server Settings](#web-server-settings)
  - [Library Synchronization](#library-synchronization)
  - [Rating Services](#rating-services)
- [Environment Variable Overrides](#environment-variable-overrides)
- [Configuration Examples](#configuration-examples)
- [Validation and Troubleshooting](#validation-and-troubleshooting)
- [Best Practices](#best-practices)

## Quick Start

### Minimal Configuration

Create a `config.json` file with the minimum required settings:

```json
{
  "jellyfin": {
    "server_url": "http://your-jellyfin-server:8096",
    "api_key": "your_jellyfin_api_key_here",
    "user_id": "your_user_id_here"
  },
  "discord": {
    "webhooks": {
      "default": {
        "name": "General Notifications",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/your/webhook/url"
      }
    }
  }
}
```

### Using Environment Variables

For Docker deployments, you can override settings using environment variables:

```bash
export JELLYFIN_SERVER_URL="http://jellyfin:8096"
export JELLYFIN_API_KEY="your_api_key_here"
export JELLYFIN_USER_ID="your_user_id_here"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

## Configuration File Structure

Jellynouncer supports both JSON and YAML configuration formats. The configuration is organized into logical sections:

```json
{
  "jellyfin": { /* Jellyfin server connection settings */ },
  "discord": { /* Discord webhook configurations */ },
  "database": { /* SQLite database settings */ },
  "templates": { /* Jinja2 template configuration */ },
  "notifications": { /* Notification behavior settings */ },
  "server": { /* Web server configuration */ },
  "sync": { /* Library synchronization settings */ },
  "rating_services": { /* External rating APIs */ }
}
```

## Core Configuration Sections

### Jellyfin Settings

The `jellyfin` section configures connection to your Jellyfin media server.

```json
{
  "jellyfin": {
    "server_url": "http://localhost:8096",
    "api_key": "your_jellyfin_api_key_here",
    "user_id": "your_user_id_here",
    "timeout": 30,
    "verify_ssl": true
  }
}
```

#### Options:

- **`server_url`** *(required)*: Full URL to your Jellyfin server
  - Example: `"http://jellyfin:8096"` or `"https://jellyfin.yourdomain.com"`
- **`api_key`** *(required)*: Jellyfin API key for authentication
  - Get this from Jellyfin Dashboard → API Keys
- **`user_id`** *(required)*: Jellyfin user ID for library access
  - Find in Jellyfin Dashboard → Users → [User] → copy the ID from URL
- **`timeout`** *(optional)*: Request timeout in seconds (default: 30)
- **`verify_ssl`** *(optional)*: Verify SSL certificates (default: true)

### Discord Webhooks

The `discord` section manages webhook configurations and routing rules.

```json
{
  "discord": {
    "webhooks": {
      "default": {
        "name": "General Notifications",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/123456789/abc...",
        "grouping": {
          "mode": "both",
          "delay_minutes": 5,
          "max_items": 25
        }
      },
      "movies": {
        "name": "Movie Notifications",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/987654321/def...",
        "grouping": {
          "mode": "item_type",
          "delay_minutes": 3,
          "max_items": 15
        }
      },
      "tv": {
        "name": "TV Show Notifications",
        "enabled": false,
        "url": null
      },
      "music": {
        "name": "Music Notifications",
        "enabled": false,
        "url": null
      }
    },
    "routing": {
      "enabled": true,
      "fallback_webhook": "default",
      "rules": {
        "Movie": "movies",
        "Episode": "tv",
        "Season": "tv",
        "Series": "tv",
        "Audio": "music",
        "MusicAlbum": "music",
        "MusicArtist": "music"
      }
    },
    "rate_limit": {
      "requests_per_minute": 30,
      "burst_size": 5
    }
  }
}
```

#### Webhook Options:

- **`name`** *(required)*: Human-readable name for the webhook
- **`enabled`** *(required)*: Whether this webhook is active
- **`url`** *(optional)*: Discord webhook URL (can be null if disabled)
- **`grouping`** *(optional)*: Notification batching configuration
  - **`mode`**: Grouping strategy
    - `"none"`: Send notifications immediately (default)
    - `"item_type"`: Group by content type (Movies, TV, Music)
    - `"event_type"`: Group by event (New vs. Upgraded)
    - `"both"`: Group by both content type and event
  - **`delay_minutes`**: How long to wait before sending grouped notifications (default: 5)
  - **`max_items`**: Maximum items per notification before forcing send (default: 25)

#### Routing Options:

- **`enabled`** *(optional)*: Enable content-type routing (default: true)
- **`fallback_webhook`** *(optional)*: Default webhook when routing fails (default: "default")
- **`rules`** *(optional)*: Map content types to webhook names

#### Rate Limiting Options:

- **`requests_per_minute`** *(optional)*: Discord API rate limit (default: 30)
- **`burst_size`** *(optional)*: Maximum burst requests (default: 5)

### Database Configuration

The `database` section configures SQLite database settings.

```json
{
  "database": {
    "path": "/app/data/jellynouncer.db",
    "wal_mode": true,
    "backup_enabled": true,
    "backup_retention_days": 7
  }
}
```

#### Options:

- **`path`** *(optional)*: Database file location (default: "/app/data/jellynouncer.db")
- **`wal_mode`** *(optional)*: Enable Write-Ahead Logging for better performance (default: true)
- **`backup_enabled`** *(optional)*: Enable automatic database backups (default: true)
- **`backup_retention_days`** *(optional)*: Days to keep backup files (default: 7)

### Template Settings

The `templates` section configures Jinja2 templates for Discord embeds.

```json
{
  "templates": {
    "directory": "/app/templates",
    "new_item_template": "new_item.j2",
    "upgraded_item_template": "upgraded_item.j2",
    "new_items_by_event_template": "new_items_by_event.j2",
    "upgraded_items_by_event_template": "upgraded_items_by_event.j2",
    "new_items_by_type_template": "new_items_by_type.j2",
    "upgraded_items_by_type_template": "upgraded_items_by_type.j2",
    "new_items_grouped_template": "new_items_grouped.j2",
    "upgraded_items_grouped_template": "upgraded_items_grouped.j2"
  }
}
```

#### Options:

- **`directory`** *(optional)*: Template files directory (default: "/app/templates")
- **`new_item_template`** *(optional)*: Template for single new items (default: "new_item.j2")
- **`upgraded_item_template`** *(optional)*: Template for single upgraded items (default: "upgraded_item.j2")
- **`new_items_by_event_template`** *(optional)*: Grouped new items template
- **`upgraded_items_by_event_template`** *(optional)*: Grouped upgraded items template
- **`new_items_by_type_template`** *(optional)*: New items grouped by type template
- **`upgraded_items_by_type_template`** *(optional)*: Upgraded items grouped by type template
- **`new_items_grouped_template`** *(optional)*: Comprehensive new items grouping template
- **`upgraded_items_grouped_template`** *(optional)*: Comprehensive upgraded items grouping template

### Notification Behavior

The `notifications` section controls notification logic and filtering.

```json
{
  "notifications": {
    "enabled": true,
    "send_on_startup": false,
    "ignore_reprocessing": true,
    "min_file_size_mb": 10,
    "exclude_item_types": ["Photo", "PhotoAlbum"],
    "require_changes_for_upgrade": true,
    "significant_changes": {
      "resolution_upgrade": true,
      "codec_change": true,
      "audio_upgrade": true,
      "hdr_upgrade": true,
      "file_size_increase_percent": 20
    }
  }
}
```

#### Options:

- **`enabled`** *(optional)*: Master notification toggle (default: true)
- **`send_on_startup`** *(optional)*: Send notifications for existing items on startup (default: false)
- **`ignore_reprocessing`** *(optional)*: Skip items that were recently processed (default: true)
- **`min_file_size_mb`** *(optional)*: Minimum file size for notifications in MB (default: 10)
- **`exclude_item_types`** *(optional)*: Item types to never notify about (default: ["Photo", "PhotoAlbum"])
- **`require_changes_for_upgrade`** *(optional)*: Only notify upgrades with significant changes (default: true)
- **`significant_changes`** *(optional)*: Define what constitutes a significant upgrade
  - **`resolution_upgrade`**: Notify on resolution improvements (default: true)
  - **`codec_change`**: Notify on codec changes (default: true)  
  - **`audio_upgrade`**: Notify on audio quality improvements (default: true)
  - **`hdr_upgrade`**: Notify on HDR additions (default: true)
  - **`file_size_increase_percent`**: Minimum file size increase percentage (default: 20)

### Web Server Settings

The `server` section configures the FastAPI web server.

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080,
    "log_level": "INFO",
    "workers": 1,
    "reload": false,
    "access_log": true
  }
}
```

#### Options:

- **`host`** *(optional)*: Server bind address (default: "0.0.0.0")
- **`port`** *(optional)*: Server port number (default: 8080)
- **`log_level`** *(optional)*: Logging level (default: "INFO")
  - Options: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
- **`workers`** *(optional)*: Number of worker processes (default: 1)
- **`reload`** *(optional)*: Enable auto-reload on code changes (default: false)
- **`access_log`** *(optional)*: Enable HTTP access logging (default: true)

### Library Synchronization

The `sync` section controls background library synchronization.

```json
{
  "sync": {
    "enabled": true,
    "interval_hours": 6,
    "startup_delay_minutes": 5,
    "full_sync_enabled": true,
    "incremental_sync_enabled": true,
    "max_items_per_sync": 1000,
    "libraries": ["Movies", "TV Shows", "Music"]
  }
}
```

#### Options:

- **`enabled`** *(optional)*: Enable background sync (default: true)
- **`interval_hours`** *(optional)*: Hours between sync cycles (default: 6)
- **`startup_delay_minutes`** *(optional)*: Delay before first sync (default: 5)
- **`full_sync_enabled`** *(optional)*: Enable complete library scans (default: true)
- **`incremental_sync_enabled`** *(optional)*: Enable incremental updates (default: true)
- **`max_items_per_sync`** *(optional)*: Maximum items to process per sync (default: 1000)
- **`libraries`** *(optional)*: Library names to synchronize (default: ["Movies", "TV Shows", "Music"])

### Rating Services

The `rating_services` section configures external rating API integrations.

```json
{
  "rating_services": {
    "enabled": true,
    "omdb": {
      "enabled": false,
      "api_key": null,
      "timeout": 10,
      "cache_duration_hours": 168
    },
    "tmdb": {
      "enabled": false,
      "api_key": null,
      "timeout": 10,
      "cache_duration_hours": 168
    },
    "tvdb": {
      "enabled": false,
      "api_key": null,
      "subscriber_pin": null,
      "timeout": 15,
      "cache_duration_hours": 168,
      "max_retries": 3
    }
  }
}
```

#### General Options:

- **`enabled`** *(optional)*: Master toggle for all rating services (default: true)

#### Service-Specific Options:

Each service (omdb, tmdb, tvdb) supports:

- **`enabled`** *(optional)*: Enable this specific service (default: false)
- **`api_key`** *(optional)*: API key for the service (default: null)
- **`timeout`** *(optional)*: Request timeout in seconds
- **`cache_duration_hours`** *(optional)*: How long to cache results (default: 168 hours = 1 week)

#### TVDB-Specific Options:

- **`subscriber_pin`** *(optional)*: TVDB subscriber PIN for enhanced access (default: null)
- **`max_retries`** *(optional)*: Maximum API retry attempts (default: 3)

#### Getting API Keys:

- **OMDb**: Get a free key at [http://www.omdbapi.com/apikey.aspx](http://www.omdbapi.com/apikey.aspx) (1000 requests/day free)
- **TMDb**: Get a free key at [https://www.themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) (free for non-commercial use)
- **TVDB**: Get an API key at [https://thetvdb.com/api-information](https://thetvdb.com/api-information)

## Environment Variable Overrides

Environment variables can override any configuration file setting. This is especially useful for Docker deployments and keeping sensitive data out of configuration files.

### Supported Environment Variables

| Environment Variable | Configuration Path | Description |
|---------------------|-------------------|-------------|
| `JELLYFIN_SERVER_URL` | `jellyfin.server_url` | Jellyfin server URL |
| `JELLYFIN_API_KEY` | `jellyfin.api_key` | Jellyfin API key |
| `JELLYFIN_USER_ID` | `jellyfin.user_id` | Jellyfin user ID |
| `DISCORD_WEBHOOK_URL` | `discord.webhooks.default.url` | Default Discord webhook |
| `DISCORD_WEBHOOK_URL_MOVIES` | `discord.webhooks.movies.url` | Movies webhook |
| `DISCORD_WEBHOOK_URL_TV` | `discord.webhooks.tv.url` | TV shows webhook |
| `DISCORD_WEBHOOK_URL_MUSIC` | `discord.webhooks.music.url` | Music webhook |
| `OMDB_API_KEY` | `rating_services.omdb.api_key` | OMDb API key |
| `TMDB_API_KEY` | `rating_services.tmdb.api_key` | TMDb API key |
| `TVDB_API_KEY` | `rating_services.tvdb.api_key` | TVDB API key |
| `TVDB_SUBSCRIBER_PIN` | `rating_services.tvdb.subscriber_pin` | TVDB subscriber PIN |

### Docker Environment File

Create a `.env` file for Docker deployments:

```bash
# Jellyfin Configuration
JELLYFIN_SERVER_URL=http://jellyfin:8096
JELLYFIN_API_KEY=your_jellyfin_api_key_here
JELLYFIN_USER_ID=your_user_id_here

# Discord Webhooks
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your/webhook/url
DISCORD_WEBHOOK_URL_MOVIES=https://discord.com/api/webhooks/your/movies/webhook/url
DISCORD_WEBHOOK_URL_TV=https://discord.com/api/webhooks/your/tv/webhook/url

# Rating Services (Optional)
OMDB_API_KEY=your_omdb_api_key_here
TMDB_API_KEY=your_tmdb_api_key_here
TVDB_API_KEY=your_tvdb_v4_api_key_here

# System Settings
PUID=1000
PGID=1000
TZ=America/New_York
```

## Configuration Examples

### Minimal Setup

Perfect for getting started quickly:

```json
{
  "jellyfin": {
    "server_url": "http://localhost:8096",
    "api_key": "your_api_key_here",
    "user_id": "your_user_id_here"
  },
  "discord": {
    "webhooks": {
      "default": {
        "name": "Jellyfin Notifications",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/..."
      }
    }
  }
}
```

### Multi-Webhook Setup

Separate channels for different content types:

```json
{
  "jellyfin": {
    "server_url": "http://jellyfin:8096",
    "api_key": "your_api_key_here",
    "user_id": "your_user_id_here"
  },
  "discord": {
    "webhooks": {
      "movies": {
        "name": "🎬 Movies",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/.../movies",
        "grouping": {
          "mode": "event_type",
          "delay_minutes": 3,
          "max_items": 10
        }
      },
      "tv": {
        "name": "📺 TV Shows",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/.../tv",
        "grouping": {
          "mode": "both",
          "delay_minutes": 5,
          "max_items": 15
        }
      },
      "music": {
        "name": "🎵 Music",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/.../music"
      }
    },
    "routing": {
      "enabled": true,
      "fallback_webhook": "movies"
    }
  }
}
```

### Comprehensive Configuration

Full configuration with all optional features:

```json
{
  "jellyfin": {
    "server_url": "https://jellyfin.yourdomain.com",
    "api_key": "your_api_key_here",
    "user_id": "your_user_id_here",
    "timeout": 30,
    "verify_ssl": true
  },
  "discord": {
    "webhooks": {
      "default": {
        "name": "🌟 General",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/.../general",
        "grouping": {
          "mode": "both",
          "delay_minutes": 5,
          "max_items": 25
        }
      },
      "movies": {
        "name": "🎬 Movies",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/.../movies",
        "grouping": {
          "mode": "event_type",
          "delay_minutes": 2,
          "max_items": 8
        }
      },
      "tv": {
        "name": "📺 TV Shows",
        "enabled": true,
        "url": "https://discord.com/api/webhooks/.../tv",
        "grouping": {
          "mode": "item_type",
          "delay_minutes": 3,
          "max_items": 12
        }
      }
    },
    "routing": {
      "enabled": true,
      "fallback_webhook": "default",
      "rules": {
        "Movie": "movies",
        "Episode": "tv",
        "Season": "tv",
        "Series": "tv"
      }
    },
    "rate_limit": {
      "requests_per_minute": 25,
      "burst_size": 3
    }
  },
  "database": {
    "path": "/app/data/jellynouncer.db",
    "wal_mode": true,
    "backup_enabled": true,
    "backup_retention_days": 14
  },
  "templates": {
    "directory": "/app/templates",
    "new_item_template": "new_item-full.j2",
    "upgraded_item_template": "upgraded_item-full.j2"
  },
  "notifications": {
    "enabled": true,
    "send_on_startup": false,
    "ignore_reprocessing": true,
    "min_file_size_mb": 50,
    "exclude_item_types": ["Photo", "PhotoAlbum", "Folder"],
    "require_changes_for_upgrade": true,
    "significant_changes": {
      "resolution_upgrade": true,
      "codec_change": true,
      "audio_upgrade": true,
      "hdr_upgrade": true,
      "file_size_increase_percent": 15
    }
  },
  "server": {
    "host": "0.0.0.0",
    "port": 8080,
    "log_level": "INFO",
    "access_log": true
  },
  "sync": {
    "enabled": true,
    "interval_hours": 4,
    "startup_delay_minutes": 2,
    "max_items_per_sync": 2000,
    "libraries": ["Movies", "TV Shows", "Music", "Audiobooks"]
  },
  "rating_services": {
    "enabled": true,
    "omdb": {
      "enabled": true,
      "api_key": "your_omdb_key_here",
      "timeout": 8,
      "cache_duration_hours": 336
    },
    "tmdb": {
      "enabled": true,
      "api_key": "your_tmdb_key_here",
      "timeout": 10,
      "cache_duration_hours": 168
    },
    "tvdb": {
      "enabled": true,
      "api_key": "your_tvdb_key_here",
      "subscriber_pin": "your_pin_here",
      "timeout": 15,
      "cache_duration_hours": 168,
      "max_retries": 2
    }
  }
}
```

## Validation and Troubleshooting

### Configuration Validation

Jellynouncer validates your configuration on startup and provides detailed error messages for any issues:

#### Common Configuration Errors

**Invalid Jellyfin URL:**
```
Configuration model validation failed:
  jellyfin -> server_url: Jellyfin server URL must be a valid HTTP/HTTPS URL
```

**Missing Discord Webhook URL:**
```
Configuration model validation failed:
  discord -> webhooks -> default -> url: Discord webhook URL must start with 'https://discord.com/api/webhooks/'
```

**Invalid Template Directory:**
```
Configuration validation failed: Template directory does not exist: /invalid/path/templates
```

### Testing Configuration

#### Validate Configuration

Check if your configuration is valid without starting the service:

```bash
# Using Docker
docker run --rm -v ./config:/app/config jellynouncer:latest python -c "
from config_models import ConfigurationValidator
from utils import setup_logging
logger = setup_logging()
validator = ConfigurationValidator(logger)
config = validator.load_and_validate_config()
print('Configuration is valid!')
"
```

#### Test Webhooks

Test individual webhooks to ensure they're working:

```bash
# Test default webhook
curl -X POST "http://localhost:8080/test-webhook?webhook_name=default"

# Test movies webhook
curl -X POST "http://localhost:8080/test-webhook?webhook_name=movies"
```

#### Check Webhook Status

View current webhook configurations:

```bash
curl http://localhost:8080/webhooks
```

### Debug Mode

Enable debug logging for detailed troubleshooting:

```json
{
  "server": {
    "log_level": "DEBUG"
  }
}
```

Or set environment variable:
```bash
export LOG_LEVEL=DEBUG
```

### Configuration File Formats

Jellynouncer supports both JSON and YAML formats:

#### JSON Format (config.json)
```json
{
  "jellyfin": {
    "server_url": "http://localhost:8096"
  }
}
```

#### YAML Format (config.yaml)
```yaml
jellyfin:
  server_url: http://localhost:8096
  # Comments are supported in YAML
  api_key: your_key_here
```

### Common Issues and Solutions

#### Issue: "Template directory does not exist"
**Solution:** Ensure the templates directory exists and contains the required template files.

```bash
# Create templates directory
mkdir -p /app/templates

# Check if templates exist
ls -la /app/templates/
```

#### Issue: "Failed to connect to Jellyfin server"
**Solution:** Verify the Jellyfin URL and network connectivity.

```bash
# Test Jellyfin connectivity
curl -v "http://your-jellyfin-server:8096/health"
```

#### Issue: "Discord webhook URL is invalid" 
**Solution:** Ensure webhook URLs follow the correct Discord format.

```
✅ Correct: https://discord.com/api/webhooks/123456789/abc...
❌ Wrong: https://discordapp.com/api/webhooks/123456789/abc...
❌ Wrong: http://discord.com/api/webhooks/123456789/abc...
```

#### Issue: "Database is locked"
**Solution:** Enable WAL mode or check file permissions.

```json
{
  "database": {
    "wal_mode": true,
    "path": "/app/data/jellynouncer.db"
  }
}
```

## Best Practices

### Security

1. **Use Environment Variables for Secrets**
   - Never commit API keys to version control
   - Use environment variables for sensitive data
   - Consider using Docker secrets for production

2. **Enable SSL Verification**
   ```json
   {
     "jellyfin": {
       "verify_ssl": true
     }
   }
   ```

3. **Secure File Permissions**
   ```bash
   chmod 600 config.json  # Read/write for owner only
   ```

### Performance

1. **Enable Database WAL Mode**
   ```json
   {
     "database": {
       "wal_mode": true
     }
   }
   ```

2. **Configure Appropriate Rate Limits**
   ```json
   {
     "discord": {
       "rate_limit": {
         "requests_per_minute": 30,
         "burst_size": 5
       }
     }
   }
   ```

3. **Use Notification Grouping**
   ```json
   {
     "discord": {
       "webhooks": {
         "default": {
           "grouping": {
             "mode": "both",
             "delay_minutes": 5,
             "max_items": 25
           }
         }
       }
     }
   }
   ```

### Organization

1. **Use Multiple Webhooks**
   - Separate channels for movies, TV shows, and music
   - Different notification styles for different content types

2. **Configure Meaningful Names**
   ```json
   {
     "discord": {
       "webhooks": {
         "movies": {
           "name": "🎬 Movie Notifications"
         },
         "tv": {
           "name": "📺 TV Show Updates"
         }
       }
     }
   }
   ```

3. **Set Up Proper Filtering**
   ```json
   {
     "notifications": {
       "min_file_size_mb": 10,
       "exclude_item_types": ["Photo", "PhotoAlbum"],
       "require_changes_for_upgrade": true
     }
   }
   ```

### Maintenance

1. **Enable Database Backups**
   ```json
   {
     "database": {
       "backup_enabled": true,
       "backup_retention_days": 14
     }
   }
   ```

2. **Configure Library Sync**
   ```json
   {
     "sync": {
       "enabled": true,
       "interval_hours": 6,
       "max_items_per_sync": 1000
     }
   }
   ```

3. **Monitor Log Levels**
   ```json
   {
     "server": {
       "log_level": "INFO",
       "access_log": true
     }
   }
   ```

---

For more information about templates, see the [Templates README](../templates/Readme.md). For general usage and setup instructions, see the main project README.