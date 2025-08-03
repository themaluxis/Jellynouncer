# Jellynouncer Templates

This folder contains Jinja2 templates used to generate Discord embed notifications. These templates allow you to fully customize the appearance and content of your Discord notifications.

## Quick Start

To use a custom template, simply modify the template configuration in your `config.json`:

```json
{
  "templates": {
    "directory": "/app/templates",
    "new_item_template": "new_item.j2",
    "upgraded_item_template": "upgraded_item.j2"
  }
}
```

## Template Types

Jellynouncer uses different templates depending on your notification settings:

### Individual Notifications
- **`new_item.j2`** - Used when a new media item is added
- **`upgraded_item.j2`** - Used when an existing item is upgraded (better quality, codec, etc.)

### Grouped Notifications
When grouping is enabled, different templates are used based on your grouping mode:

- **`new_items_by_event.j2`** - Groups new items together
- **`upgraded_items_by_event.j2`** - Groups upgraded items together  
- **`new_items_by_type.j2`** - Groups new items by media type (Movies, TV, Music)
- **`upgraded_items_by_type.j2`** - Groups upgraded items by media type
- **`new_items_grouped.j2`** - Complete grouping (both type and event) for new items
- **`upgraded_items_grouped.j2`** - Complete grouping (both type and event) for upgrades

## Available Variables

### Core Media Item Properties

All templates have access to an `item` object with the following properties:

#### Basic Information
```jinja2
{{ item.name }}              <!-- Media title -->
{{ item.item_type }}         <!-- "Movie", "Episode", "Series", "Audio", etc. -->
{{ item.item_id }}           <!-- Unique Jellyfin ID -->
{{ item.year }}              <!-- Release year -->
{{ item.overview }}          <!-- Description/synopsis -->
```

#### TV Show Specific
```jinja2
{{ item.series_name }}       <!-- TV series name -->
{{ item.series_id }}         <!-- Series Jellyfin ID -->
{{ item.season_number }}     <!-- Season number -->
{{ item.episode_number }}    <!-- Episode number -->
```

#### Video Technical Details
```jinja2
{{ item.video_height }}      <!-- Resolution (1080, 2160, etc.) -->
{{ item.video_width }}       <!-- Video width in pixels -->
{{ item.video_codec }}       <!-- "h264", "hevc", "av1", etc. -->
{{ item.video_profile }}     <!-- Codec profile -->
{{ item.video_range }}       <!-- "HDR", "SDR", etc. -->
{{ item.video_framerate }}   <!-- Frame rate -->
{{ item.aspect_ratio }}      <!-- Aspect ratio -->
```

#### Audio Technical Details
```jinja2
{{ item.audio_codec }}       <!-- "ac3", "dts", "aac", etc. -->
{{ item.audio_channels }}    <!-- Number of audio channels -->
{{ item.audio_language }}    <!-- Audio language -->
{{ item.audio_bitrate }}     <!-- Audio bitrate -->
```

#### Music Specific
```jinja2
{{ item.album }}             <!-- Album name -->
{{ item.album_artist }}      <!-- Album artist -->
{{ item.artists }}           <!-- List of artists -->
```

#### File Information
```jinja2
{{ item.file_path }}         <!-- File path -->
{{ item.file_size }}         <!-- File size in bytes -->
```

#### External IDs
```jinja2
{{ item.imdb_id }}           <!-- IMDb identifier -->
{{ item.tmdb_id }}           <!-- TMDb identifier -->
{{ item.tvdb_id }}           <!-- TVDb identifier -->
```

#### Additional Metadata
```jinja2
{{ item.genres }}            <!-- List of genres -->
{{ item.studios }}           <!-- List of studios -->
{{ item.tags }}              <!-- List of tags -->
{{ item.runtime_ticks }}     <!-- Duration in Jellyfin ticks -->
{{ item.date_created }}      <!-- Creation timestamp -->
{{ item.date_modified }}     <!-- Last modified timestamp -->
```

### Template Context Variables

#### Global Variables
```jinja2
{{ jellyfin_url }}           <!-- Your Jellyfin server URL -->
{{ color }}                  <!-- Discord embed color (integer) -->
```

#### For Upgrade Notifications
```jinja2
{{ changes }}                <!-- List of detected changes -->
```

Each change object contains:
```jinja2
{{ change.type }}            <!-- "resolution", "codec", "audio_codec", etc. -->
{{ change.old_value }}       <!-- Previous value -->
{{ change.new_value }}       <!-- New value -->
{{ change.description }}     <!-- Human-readable description -->
```

#### For Grouped Notifications
```jinja2
{{ categories }}             <!-- Dictionary of grouped items -->
{{ total_items }}            <!-- Total number of items -->
```

Categories structure:
```jinja2
{{ categories.movies.new }}         <!-- List of new movies -->
{{ categories.movies.upgraded }}    <!-- List of upgraded movies -->
{{ categories.tv.new }}             <!-- List of new TV content -->
{{ categories.tv.upgraded }}        <!-- List of upgraded TV content -->
{{ categories.music.new }}          <!-- List of new music -->
{{ categories.music.upgraded }}     <!-- List of upgraded music -->
{{ categories.other.new }}          <!-- List of other new content -->
{{ categories.other.upgraded }}     <!-- List of other upgraded content -->
```

## Jinja2 Syntax Reference

### Variables
Display values using double curly braces:
```jinja2
{{ item.name }}
{{ item.year }}
```

### Conditionals
```jinja2
{% if item.year %}
  Released in {{ item.year }}
{% endif %}

{% if item.item_type == 'Episode' %}
  Episode content
{% elif item.item_type == 'Movie' %}
  Movie content
{% else %}
  Other content
{% endif %}
```

### Loops
```jinja2
{% for artist in item.artists %}
  {{ artist }}{% if not loop.last %}, {% endif %}
{% endfor %}
```

### Filters
Apply transformations to variables:
```jinja2
{{ item.name|upper }}                    <!-- UPPERCASE -->
{{ item.overview|truncate(150) }}        <!-- Limit length -->
{{ item.file_size / 1073741824 }}        <!-- Convert bytes to GB -->
{{ '%02d'|format(item.season_number) }}  <!-- Zero-pad numbers -->
```

### Comments
```jinja2
{# This is a comment and won't appear in the output #}
```

## Example Templates

### Simple New Item Template
```jinja2
{
  "embeds": [
    {
      "title": "🎬 New {{ item.item_type }} Added",
      "description": "**{{ item.name }}**{% if item.year %} ({{ item.year }}){% endif %}",
      "color": {{ color }},
      "fields": [
        {% if item.video_height %}
        {
          "name": "Quality",
          "value": "{{ item.video_height }}p",
          "inline": true
        },
        {% endif %}
        {% if item.video_codec %}
        {
          "name": "Video Codec",
          "value": "{{ item.video_codec.upper() }}",
          "inline": true
        }
        {% endif %}
      ]
    }
  ]
}
```

### Simple Upgrade Template
```jinja2
{
  "embeds": [
    {
      "title": "⬆️ {{ item.item_type }} Upgraded",
      "description": "**{{ item.name }}**{% if item.year %} ({{ item.year }}){% endif %}",
      "color": {{ color }},
      "fields": [
        {
          "name": "Changes",
          "value": "{% for change in changes %}{{ change.description }}{% if not loop.last %}\n{% endif %}{% endfor %}",
          "inline": false
        }
      ]
    }
  ]
}
```

## Sample Discord Notifications

### New Movie Added
Using the default `new_item.j2` template, a new movie would create a Discord embed like:

```json
{
  "embeds": [
    {
      "title": "🎬 New Movie Added",
      "description": "**The Matrix** (1999)",
      "color": 65280,
      "fields": [
        {
          "name": "📐 Quality",
          "value": "2160p HDR",
          "inline": true
        },
        {
          "name": "🎞️ Video",
          "value": "HEVC Main10",
          "inline": true
        },
        {
          "name": "🔊 Audio",
          "value": "DTS-HD 7.1",
          "inline": true
        }
      ]
    }
  ]
}
```

### Movie Upgrade
Using the default `upgraded_item.j2` template:

```json
{
  "embeds": [
    {
      "title": "⬆️ Movie Resolution Upgraded",
      "description": "**The Matrix** (1999)",
      "color": 16766720,
      "fields": [
        {
          "name": "📐 Resolution Upgrade",
          "value": "1080p → **2160p**",
          "inline": false
        },
        {
          "name": "🌈 HDR Status Upgrade", 
          "value": "SDR → **HDR**",
          "inline": false
        }
      ]
    }
  ]
}
```

### Grouped Notifications
Using `new_items_grouped.j2` template:

```json
{
  "embeds": [
    {
      "title": "📺 New Content Added (5 items)",
      "color": 65280,
      "fields": [
        {
          "name": "🎬 Movies",
          "value": "**New Movies (2)**: The Matrix, Blade Runner\n**Upgraded Movies (1)**: Inception",
          "inline": false
        },
        {
          "name": "📺 TV Shows", 
          "value": "**New Episodes (2)**: Breaking Bad S01E05, The Office S02E10",
          "inline": false
        }
      ]
    }
  ]
}
```

## Configuration Integration

To use your custom templates, update your `config.json`:

```json
{
  "templates": {
    "directory": "/app/templates",
    "new_item_template": "my_custom_new.j2",
    "upgraded_item_template": "my_custom_upgrade.j2",
    "new_items_by_event_template": "new_items_by_event.j2",
    "upgraded_items_by_event_template": "upgraded_items_by_event.j2",
    "new_items_by_type_template": "new_items_by_type.j2", 
    "upgraded_items_by_type_template": "upgraded_items_by_type.j2",
    "new_items_grouped_template": "new_items_grouped.j2",
    "upgraded_items_grouped_template": "upgraded_items_grouped.j2"
  }
}
```

## Tips for Template Development

1. **Test Your Templates**: Use the `/test-webhook` API endpoint to test template changes
2. **Handle Missing Data**: Always check if variables exist before using them
3. **Discord Limits**: Keep embed descriptions under 4096 characters
4. **Field Limits**: Maximum 25 fields per embed, each field value under 1024 characters
5. **Color Values**: Use integer values for colors (not hex strings)
6. **JSON Validation**: Ensure your template outputs valid JSON

## Troubleshooting

**Template Not Loading**: Verify the file exists and the path in `config.json` is correct  
**Syntax Errors**: Check Jinja2 syntax - all `{% %}` and `{{ }}` tags must be properly closed  
**Missing Variables**: Use conditional checks: `{% if item.property %}{{ item.property }}{% endif %}`  
**JSON Errors**: Validate template output with a JSON validator  
**Discord Errors**: Check Discord webhook URL and ensure embed format is valid  

## Further Reading

- [Jinja2 Documentation](https://jinja.palletsprojects.com/en/stable/)
- [Discord Webhook Documentation](https://discord.com/developers/docs/resources/webhook)
- [Discord Embed Limits](https://discord.com/developers/docs/resources/channel#embed-limits)