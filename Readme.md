# JellyNotify

JellyNotify is an intermediate webhook service that sits between Jellyfin and Discord, providing intelligent notifications for new media additions and quality upgrades.

## Features

- 🎬 **Smart Change Detection**: Differentiate between new items and upgrades (resolution, codec, audio, HDR)
- 🔍 **Full Jellyfin Integration**: Complete library sync with rich metadata extraction
- 🎨 **Customizable Templates**: Jinja2-powered Discord embed templates
- 📊 **SQLite Database**: WAL mode enabled for concurrent access and performance
- ⚡ **Rate Limit Handling**: Respects Discord's webhook rate limits
- 🔄 **Auto-Recovery**: Monitors Jellyfin server status and notifies on outages
- 🐳 **Docker Ready**: Complete containerized solution

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd jellyfin-discord-webhook
```

### 2. Environment Configuration

Create a `.env` file:

```env
JELLYFIN_SERVER_URL=http://your-jellyfin-server:8096
JELLYFIN_API_KEY=your_jellyfin_api_key_here
JELLYFIN_USER_ID=your_user_id_here

# Single webhook (default behavior)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your/webhook/url

# Optional: Separate webhooks for movies and TV shows
DISCORD_WEBHOOK_URL_MOVIES=https://discord.com/api/webhooks/your/movies/webhook/url
DISCORD_WEBHOOK_URL_TV=https://discord.com/api/webhooks/your/tv/webhook/url
DISCORD_WEBHOOK_URL_MUSIC=https://discord.com/api/webhooks/your/music/webhook/url
```

**Getting Jellyfin API Key:**
1. Log into Jellyfin web interface as admin
2. Go to Dashboard → Advanced → API Keys
3. Create new API Key
4. Copy the generated key

**Getting User ID:**
1. In Jellyfin web interface, go to Users
2. Click on your user
3. Look at the URL - the user ID is the long string after `/users/`

### 3. Configure Jellyfin Webhook Plugin

1. Install the Jellyfin Webhook Plugin
2. Go to Dashboard → Plugins → Webhook
3. Add a new "Generic" destination
4. Set URL to: `http://your-docker-host:8080/webhook`
5. Enable "Item Added" notification type
6. Use the template from `jellyfin-webhook-template.json`

### 4. Deploy with Docker

```bash
docker-compose up -d
```

## Multiple Discord Webhooks

The service supports routing different content types to different Discord webhooks:

### Configuration

#### Option 1: Single Webhook (Default)
Just set `DISCORD_WEBHOOK_URL` and all notifications go to one channel.

#### Option 2: Multiple Webhooks with Routing
1. Set multiple webhook URLs in environment variables
2. Enable routing in `config.json`
3. Configure which content types go to which webhooks

```json
{
  "discord": {
    "webhooks": {
      "default": {
        "url": null,
        "name": "General",
        "enabled": true
      },
      "movies": {
        "url": null,
        "name": "Movies",
        "enabled": true
      },
      "tv": {
        "url": null,
        "name": "TV Shows", 
        "enabled": true
      },
      "music": {
        "url": null,
        "name": "Music",
        "enabled": true
      }
    },
    "routing": {
      "enabled": true,
      "movie_types": ["Movie"],
      "tv_types": ["Episode", "Season", "Series"],
      "music_types": ["Audio", "MusicAlbum", "MusicArtist"],
      "fallback_webhook": "default"
    }
  }
}
```

### Environment Variables for Multiple Webhooks

```env
# General/fallback webhook
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_GENERAL_WEBHOOK

# Movies webhook
DISCORD_WEBHOOK_URL_MOVIES=https://discord.com/api/webhooks/YOUR_MOVIES_WEBHOOK

# TV Shows webhook  
DISCORD_WEBHOOK_URL_TV=https://discord.com/api/webhooks/YOUR_TV_WEBHOOK

# Music webhook
DISCORD_WEBHOOK_URL_MUSIC=https://discord.com/api/webhooks/YOUR_MUSIC_WEBHOOK
```

### Webhook Management

#### Check Webhook Status
```bash
curl http://localhost:8080/webhooks
```

Response:
```json
{
  "routing_enabled": true,
  "webhooks": {
    "default": {
      "name": "General",
      "enabled": true,
      "has_url": true,
      "url_preview": "https://discord.com/api/webhooks/1234567890/..."
    },
    "movies": {
      "name": "Movies", 
      "enabled": true,
      "has_url": true,
      "url_preview": "https://discord.com/api/webhooks/0987654321/..."
    },
    "tv": {
      "name": "TV Shows",
      "enabled": true,
      "has_url": true,
      "url_preview": "https://discord.com/api/webhooks/1122334455/..."
    },
    "music": {
      "name": "Music",
      "enabled": true,
      "has_url": true,
      "url_preview": "https://discord.com/api/webhooks/2233445566/..."
    }
  },
  "routing_config": {
    "movie_types": ["Movie"],
    "tv_types": ["Episode", "Season", "Series"],
    "music_types": ["Audio", "MusicAlbum", "MusicArtist"],
    "fallback_webhook": "default"
  }
}
```

#### Test Individual Webhooks
```bash
# Test default webhook
curl -X POST "http://localhost:8080/test-webhook?webhook_name=default"

# Test movies webhook
curl -X POST "http://localhost:8080/test-webhook?webhook_name=movies"

# Test TV webhook
curl -X POST "http://localhost:8080/test-webhook?webhook_name=tv"

# Test music webhook
curl -X POST "http://localhost:8080/test-webhook?webhook_name=music"
```

### Routing Logic

1. **Routing Disabled**: All notifications go to the first enabled webhook
2. **Routing Enabled**: 
   - Movies → `movies` webhook (if enabled)
   - Episodes/Seasons/Series → `tv` webhook (if enabled)
   - Audio/Music Albums/Artists → `music` webhook (if enabled)
   - Other types → `fallback_webhook`
   - If target webhook unavailable → falls back to `fallback_webhook`
   - If fallback unavailable → uses any enabled webhook

### Advanced Routing

You can customize which item types go to which webhooks:

```json
{
  "discord": {
    "routing": {
      "enabled": true,
      "movie_types": ["Movie", "BoxSet"],
      "tv_types": ["Episode", "Season", "Series"],
      "music_types": ["Audio", "MusicAlbum", "MusicArtist"],
      "fallback_webhook": "default"
    },
    "webhooks": {
      "default": {"url": "...", "enabled": true},
      "movies": {"url": "...", "enabled": true},
      "tv": {"url": "...", "enabled": true},
      "music": {"url": "...", "enabled": true}
    }
  }
}
```

## Manual Commands

### Library Sync
```bash
# Full library sync
curl -X POST http://localhost:8080/sync

# Check sync status
curl http://localhost:8080/stats
```

### Health Check
```bash
curl http://localhost:8080/health
```

### Database Maintenance
The service automatically performs database maintenance, but you can also run it manually:

```bash
# Enter the container
docker exec -it jellyfin-discord-webhook bash

# Manual vacuum
sqlite3 /app/data/jellyfin_items.db "VACUUM;"
```

## Configuration

### Main Configuration (`config/config.json`)

The service supports extensive configuration through JSON:

```json
{
  "notifications": {
    "watch_changes": {
      "resolution": true,        // Watch for resolution changes
      "codec": true,            // Watch for video codec changes
      "audio_codec": true,      // Watch for audio codec changes
      "audio_channels": true,   // Watch for audio channel changes
      "hdr_status": true,       // Watch for HDR/SDR changes
      "file_size": true,        // Watch for file size changes
      "provider_ids": true      // Watch for provider ID changes
    },
    "colors": {
      "new_item": 65280,           // Green for new items
      "resolution_upgrade": 16766720,  // Gold for resolution upgrades
      "codec_upgrade": 16747520,       // Orange for codec upgrades
      "audio_upgrade": 9662683,        // Purple for audio upgrades
      "hdr_upgrade": 16716947,         // Pink for HDR upgrades
      "provider_update": 2003199       // Blue for provider updates
    }
  }
}
```

### Custom Templates

Templates are located in the `templates/` directory and use Jinja2 syntax:

#### Available Template Variables:

**Item Data:**
- `item.name` - Item name
- `item.item_type` - Movie, Episode, etc.
- `item.year` - Release year
- `item.series_name` - TV series name
- `item.season_number` / `item.episode_number` - Episode info
- `item.overview` - Description

**Video Properties:**
- `item.video_height` / `item.video_width` - Resolution
- `item.video_codec` - Video codec (h264, hevc, etc.)
- `item.video_profile` - Codec profile (High, Main, etc.)
- `item.video_range` - SDR/HDR status
- `item.video_framerate` - Frame rate
- `item.aspect_ratio` - Aspect ratio

**Audio Properties:**
- `item.audio_codec` - Audio codec (aac, ac3, dts, etc.)
- `item.audio_channels` - Number of audio channels
- `item.audio_language` - Audio language
- `item.audio_bitrate` - Audio bitrate

**Provider IDs:**
- `item.imdb_id` - IMDb ID
- `item.tmdb_id` - TMDb ID
- `item.tvdb_id` - TVDb ID

**Change Data (for upgraded items):**
- `changes` - List of detected changes
- `changes[].type` - Change type (resolution, codec, etc.)
- `changes[].old_value` / `changes[].new_value` - Before/after values
- `changes[].description` - Human-readable description

**Metadata:**
- `is_new` - Boolean indicating if this is a new item
- `color` - Calculated embed color based on change type
- `jellyfin_url` - Your Jellyfin server URL
- `timestamp` - Current timestamp

### Creating Custom Templates

1. Copy an existing template from `templates/`
2. Modify the Jinja2 template syntax
3. Update `config.json` to reference your new template
4. Restart the service

Example custom template:
```jinja2
{
  "embeds": [
    {
      "title": "🎬 {{ item.name }}",
      "description": "{% if item.overview %}{{ item.overview[:200] }}...{% endif %}",
      "color": {{ color }},
      "fields": [
        {% if changes %}
        {% for change in changes %}
        {
          "name": "Change: {{ change.type|title }}",
          "value": "{{ change.description }}",
          "inline": false
        }{% if not loop.last %},{% endif %}
        {% endfor %}
        {% endif %}
      ]
    }
  ]
}
```

## Discord Rate Limits

The service respects Discord's webhook rate limits:
- **5 requests per 2 seconds** per webhook URL
- **30 messages per minute** per channel (shared among all webhooks)
- **50 requests per second** global rate limit per IP

The service automatically queues and throttles requests to stay within these limits.

## Database Schema

The SQLite database stores complete media metadata:

```sql
CREATE TABLE media_items (
    item_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    item_type TEXT NOT NULL,
    year INTEGER,
    series_name TEXT,
    season_number INTEGER,
    episode_number INTEGER,
    overview TEXT,
    video_height INTEGER,
    video_width INTEGER,
    video_codec TEXT,
    video_profile TEXT,
    video_range TEXT,
    video_framerate REAL,
    aspect_ratio TEXT,
    audio_codec TEXT,
    audio_channels INTEGER,
    audio_language TEXT,
    audio_bitrate INTEGER,
    imdb_id TEXT,
    tmdb_id TEXT,
    tvdb_id TEXT,
    timestamp TEXT,
    file_path TEXT,
    file_size INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## API Endpoints

### POST /webhook
Main endpoint for Jellyfin webhooks. Accepts the configured webhook payload and processes changes.

### GET /health
Health check endpoint that returns:
```json
{
  "status": "healthy",
  "jellyfin_connected": true,
  "timestamp": "2025-01-29T12:00:00Z"
}
```

### POST /sync
Triggers a manual full library sync:
```json
{
  "status": "success",
  "message": "Library sync completed"
}
```

### GET /stats
Returns database statistics:
```json
{
  "total_items": 1250,
  "item_types": {
    "Movie": 800,
    "Episode": 400,
    "Season": 30,
    "Series": 20
  },
  "last_updated": "2025-01-29T12:00:00Z"
}
```

### GET /webhooks
Returns configuration and status of all Discord webhooks:
```json
{
  "routing_enabled": true,
  "webhooks": {
    "default": {
      "name": "General",
      "enabled": true,
      "has_url": true,
      "rate_limit_info": {...}
    }
  },
  "routing_config": {...}
}
```

### POST /test-webhook
Tests a specific webhook by sending a test notification:
```bash
curl -X POST "http://localhost:8080/test-webhook?webhook_name=movies"
```

Returns:
```json
{
  "status": "success",
  "webhook": "movies",
  "message": "Test notification sent successfully"
}
```

## Logging

The service provides comprehensive logging at multiple levels:

- **DEBUG**: Detailed processing information
- **INFO**: General operational messages
- **WARNING**: Non-critical issues (rate limits, temporary failures)
- **ERROR**: Serious errors that don't stop the service
- **CRITICAL**: Fatal errors that stop the service

Logs are written to both:
- Console output (visible in `docker logs`)
- `/app/logs/service.log` file

Configure log level in `config.json`:
```json
{
  "server": {
    "log_level": "INFO"
  }
}
```

## Troubleshooting

### Common Issues

**1. Jellyfin Connection Failed**
- Verify `JELLYFIN_SERVER_URL` is accessible from container
- Check API key is valid and has sufficient permissions
- Ensure Jellyfin server is running

**2. Discord Webhooks Not Sending**
- Verify Discord webhook URL is correct
- Check for rate limiting in logs
- Ensure Discord channel/server permissions allow webhooks

**3. Database Locked Errors**
- WAL mode should prevent this, but if it occurs:
  ```bash
  docker exec -it jellyfin-discord-webhook sqlite3 /app/data/jellyfin_items.db "PRAGMA journal_mode=WAL;"
  ```

**4. Template Rendering Errors**
- Check Jinja2 syntax in custom templates
- Verify all referenced variables exist
- Look for JSON syntax errors in template output

### Debug Mode

Enable debug logging for detailed troubleshooting:

```json
{
  "server": {
    "log_level": "DEBUG"
  }
}
```

### Container Logs

View live logs:
```bash
docker logs -f jellyfin-discord-webhook
```

## Advanced Configuration

### Custom Notification Colors

Colors are specified as integer values (Discord embed colors):

```python
# Convert hex to integer
hex_color = 0xFF0000  # Red
int_color = 16711680  # Same color as integer
```

Common colors:
- Green: `65280` (#00FF00)
- Red: `16711680` (#FF0000)
- Blue: `255` (#0000FF)
- Gold: `16766720` (#FFD700)
- Purple: `8388736` (#800080)

### Performance Tuning

For large libraries, adjust these settings:

```json
{
  "sync": {
    "sync_batch_size": 50,        // Smaller batches for slower systems
    "api_request_delay": 0.2      // Longer delay between API calls
  },
  "database": {
    "vacuum_interval_hours": 168  // Weekly vacuum instead of daily
  }
}
```

### Multiple Discord Channels

To send notifications to multiple Discord channels, you can:

1. **Run multiple instances** with different webhook URLs
2. **Create a custom template** that sends to multiple webhooks
3. **Use Discord's webhook forwarding** features

## Security Considerations

- **API Keys**: Store in environment variables, not in config files
- **Database**: SQLite file contains all media metadata - secure appropriately
- **Network**: Consider running on internal network only
- **Logs**: May contain sensitive data - rotate and secure log files

## Contributing

### Development Setup

1. Clone repository
2. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # or
   venv\Scripts\activate     # Windows
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run locally:
   ```bash
   python main.py
   ```

### Adding New Change Detection

To add detection for new types of changes:

1. Add the field to the `MediaItem` dataclass
2. Update the `extract_media_item` method in `JellyfinAPI`
3. Add detection logic in `ChangeDetector.detect_changes`
4. Update templates to display the new change type
5. Add configuration option in `config.json`

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues, feature requests, or questions:
1. Check the troubleshooting section above
2. Review container logs for error messages
3. Create an issue on GitHub with:
   - Docker logs output
   - Configuration files (with sensitive data removed)
   - Steps to reproduce the issue

## Version History

### v1.0.0
- Initial release
- Full Jellyfin integration
- Discord webhook notifications
- Change detection for resolution, codec, audio, HDR
- SQLite database with WAL mode
- Docker containerization
- Jinja2 templating system
- Rate limit handling
- Health monitoring