# JellyNotify

> ⚠️ **ALPHA SOFTWARE - UNDER ACTIVE DEVELOPMENT** ⚠️
> 
> This project is in early alpha stage and is actively being developed. Expect breaking changes, bugs, and incomplete features. Use at your own risk in production environments. Feedback and contributions are welcome!

JellyNotify is an intermediate webhook service that sits between Jellyfin and Discord, providing intelligent notifications for new media additions and quality upgrades.

## Features

- 🎬 **Smart Change Detection**: Differentiate between new items and upgrades (resolution, codec, audio, HDR)
- 🔍 **Full Jellyfin Integration**: Complete library sync with rich metadata extraction
- 🎨 **Customizable Templates**: Jinja2-powered Discord embed templates
- 📊 **SQLite Database**: WAL mode enabled for concurrent access and performance
- ⚡ **Rate Limit Handling**: Respects Discord's webhook rate limits
- 🔄 **Auto-Recovery**: Monitors Jellyfin server status and notifies on outages
- 🐳 **Docker Ready**: Complete containerized solution
- 📩 **Smart Notification Grouping**: Reduce notification spam by grouping similar media updates

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
6. Use the template from `templates/Default_Jellyfin_Webhook_Template.txt`

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
        "enabled": true,
        "grouping": {
          "mode": "none",
          "delay_minutes": 5,
          "max_items": 25
        }
      },
      "movies": {
        "url": null,
        "name": "Movies",
        "enabled": true,
        "grouping": {
          "mode": "none",
          "delay_minutes": 5,
          "max_items": 25
        }
      },
      "tv": {
        "url": null,
        "name": "TV Shows", 
        "enabled": true,
        "grouping": {
          "mode": "none",
          "delay_minutes": 5,
          "max_items": 25
        }
      },
      "music": {
        "url": null,
        "name": "Music",
        "enabled": true,
        "grouping": {
          "mode": "none",
          "delay_minutes": 5,
          "max_items": 25
        }
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

## Notification Grouping

JellyNotify now supports grouping of similar notifications to reduce spam in your Discord channels.

### How Grouping Works

1. Notifications are collected for a configurable period (default: 5 minutes)
2. When time expires or maximum items reached, a single notification is sent
3. Items are grouped based on the configured grouping mode

### Grouping Modes

Each webhook can be configured with one of the following grouping modes:

- **none** (default): No grouping, send notifications immediately
- **item_type**: Group by content category (Movies, TV Shows, Music)
- **event_type**: Group by event (New vs. Upgraded)
- **both**: Group by both content type and event

### Configuring Grouping

In `config.json`, set the grouping options for each webhook:

```json
"webhooks": {
  "default": {
    "url": "...",
    "name": "General",
    "enabled": true,
    "grouping": {
      "mode": "both",       // Options: "none", "item_type", "event_type", "both"
      "delay_minutes": 5,   // How long to wait before sending
      "max_items": 25       // Maximum items per notification
    }
  }
}
```

### Checking Queue Status

```bash
curl http://localhost:8080/queues
```

Response:
```json
{
  "default": {
    "total_items": 12,
    "new_items": 8,
    "upgraded_items": 4,
    "timer_active": true,
    "seconds_since_last_item": 45.3
  },
  "movies": {
    "total_items": 3,
    "new_items": 3,
    "upgraded_items": 0,
    "timer_active": true,
    "seconds_since_last_item": 120.7
  }
}
```

### Manually Processing Queues

To force process all notification queues immediately:

```bash
curl -X POST http://localhost:8080/flush-queues
```

To process a specific webhook's queue:

```bash
curl -X POST "http://localhost:8080/flush-queues?webhook_name=movies"
```

## Webhook Management

### Check Webhook Status
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
      "url_preview": "https://discord.com/api/webhooks/1234567890/...",
      "grouping": {
        "mode": "both",
        "delay_minutes": 5,
        "max_items": 25
      }
    },
    "movies": {
      "name": "Movies", 
      "enabled": true,
      "has_url": true,
      "url_preview": "https://discord.com/api/webhooks/0987654321/...",
      "grouping": {
        "mode": "item_type",
        "delay_minutes": 5,
        "max_items": 25
      }
    }
  },
  "notification_queues": {
    "default": {
      "total_items": 5,
      "new_items": 3,
      "upgraded_items": 2,
      "timer_active": true,
      "seconds_since_last_item": 45.3
    }
  }
}
```

### Test Individual Webhooks
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

## Routing Logic

1. **Routing Disabled**: All notifications go to the first enabled webhook
2. **Routing Enabled**: 
   - Movies → `movies` webhook (if enabled)
   - Episodes/Seasons/Series → `tv` webhook (if enabled)
   - Audio/Music Albums/Artists → `music` webhook (if enabled)
   - Other types → `fallback_webhook`
   - If target webhook unavailable → falls back to `fallback_webhook`
   - If fallback unavailable → uses any enabled webhook

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
  },
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

### Custom Templates

Templates are located in the `templates/` directory and use Jinja2 syntax. The following templates are used for grouped notifications:

- **new_items_by_event.j2**: New items grouped by event type
- **upgraded_items_by_event.j2**: Upgraded items grouped by event type
- **new_items_by_type.j2**: Items grouped by content type
- **upgraded_items_by_type.j2**: Items grouped by content type
- **new_items_grouped.j2**: Complete grouping (both type and event)
- **upgraded_items_grouped.j2**: Complete grouping (both type and event)

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
Returns configuration and status of all Discord webhooks.

### GET /queues
Returns notification queue status for all webhooks.

### POST /flush-queues
Manually triggers queue processing.

### POST /test-webhook
Tests a specific webhook by sending a test notification.

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

**3. Notification Grouping Not Working**
- Check that grouping mode is set correctly in config.json
- Verify that the webhook has the correct URL and is enabled
- Check the queue status with GET /queues to see if items are being queued

**4. Database Locked Errors**
- WAL mode should prevent this, but if it occurs:
  ```bash
  docker exec -it jellyfin-discord-webhook sqlite3 /app/data/jellyfin_items.db "PRAGMA journal_mode=WAL;"
  ```

**5. Template Rendering Errors**
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

### v1.1.0
- Added notification grouping feature
- Added new API endpoints for queue management
- Added new Jinja2 templates for grouped notifications
- Added configurable grouping modes and timers

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