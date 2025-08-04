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
- **`new_item.j2`** - Standard template for new media items
- **`new_item-full.j2`** - Comprehensive template with extended metadata and technical details
- **`new_item-compact.j2`** - Compact template with essential information in minimal space
- **`upgraded_item.j2`** - Standard template for upgraded items (better quality, codec, etc.)
- **`upgraded_item-full.j2`** - Comprehensive template for upgrades with complete technical specifications
- **`upgraded_item-compact.j2`** - Compact template for upgrade notifications

### Grouped Notifications
When grouping is enabled, different templates are used based on your grouping mode:

- **`new_items_by_event.j2`** - Groups new items together
- **`upgraded_items_by_event.j2`** - Groups upgraded items together  
- **`new_items_by_type.j2`** - Groups new items by media type (Movies, TV, Music)
- **`upgraded_items_by_type.j2`** - Groups upgraded items by media type
- **`new_items_grouped.j2`** - Complete grouping (both type and event) for new items
- **`upgraded_items_grouped.j2`** - Complete grouping (both type and event) for upgrades

### Template Variants

**Standard Templates**: Balanced approach with commonly used metadata and clean formatting.

**Full Templates (`-full`)**: Comprehensive templates that include:
- Extended technical specifications for video and audio
- Complete metadata including genres, studios, and ratings
- Detailed file information and timestamps
- Enhanced visual layout with rich formatting

**Compact Templates (`-compact`)**: Minimalist templates that provide:
- Essential information only (title, basic quality, timestamps)
- Single-line technical specs
- Smaller embed footprint for channels with high notification volume

## Available Variables

All templates have access to an `item` object with the following properties, organized alphabetically by category:

### Audio Technical Specifications
```jinja2
{{ item.audio_bitrate }}        <!-- Audio bitrate in bits per second -->
{{ item.audio_channels }}       <!-- Number of audio channels (2, 6, 8 for stereo, 5.1, 7.1) -->
{{ item.audio_codec }}          <!-- Audio codec (aac, ac3, dts, flac, mp3, etc.) -->
{{ item.audio_default }}        <!-- Whether this is the default audio track (true/false) -->
{{ item.audio_language }}       <!-- Audio language code (eng, spa, fra, etc.) -->
{{ item.audio_samplerate }}     <!-- Sample rate in Hz (48000, 44100, 96000, etc.) -->
{{ item.audio_title }}          <!-- Audio stream title/name from container -->
{{ item.audio_type }}           <!-- Stream type identifier -->
```

### Basic Information
```jinja2
{{ item.item_id }}              <!-- Unique Jellyfin ID -->
{{ item.item_type }}            <!-- "Movie", "Episode", "Series", "Audio", etc. -->
{{ item.name }}                 <!-- Media title -->
{{ item.overview }}             <!-- Description/synopsis -->
{{ item.year }}                 <!-- Release year -->
```

### Extended Metadata
```jinja2
{{ item.date_created }}         <!-- When item was added to Jellyfin -->
{{ item.date_modified }}        <!-- When item was last modified -->
{{ item.genres }}               <!-- List of genre names (Action, Comedy, Drama, etc.) -->
{{ item.official_rating }}      <!-- MPAA rating (G, PG, R), TV rating (TV-MA), etc. -->
{{ item.runtime_formatted }}    <!-- Human-readable runtime (1h 30m) -->
{{ item.runtime_ticks }}        <!-- Jellyfin duration in ticks (10,000 ticks = 1ms) -->
{{ item.studios }}              <!-- List of production companies/studios -->
{{ item.tagline }}              <!-- Marketing tagline or promotional text -->
{{ item.tags }}                 <!-- List of user-defined or imported tags -->
```

### External References
```jinja2
{{ item.imdb_id }}              <!-- Internet Movie Database identifier (tt1234567) -->
{{ item.tmdb_id }}              <!-- The Movie Database identifier -->
{{ item.tvdb_id }}              <!-- The TV Database identifier -->
{{ item.tvdb_slug }}            <!-- TVDB URL slug identifier -->
```

### File System Information
```jinja2
{{ item.file_path }}            <!-- Full file path on server -->
{{ item.file_size }}            <!-- File size in bytes -->
{{ item.library_name }}         <!-- Jellyfin library name -->
```

### Music-Specific Metadata
```jinja2
{{ item.album }}                <!-- Album name (for music tracks) -->
{{ item.album_artist }}         <!-- Primary album artist -->
{{ item.artists }}              <!-- List of artist names -->
```

### Photo-Specific Metadata
```jinja2
{{ item.height }}               <!-- Image height in pixels -->
{{ item.width }}                <!-- Image width in pixels -->
```

### Server Information
```jinja2
{{ item.notification_type }}    <!-- Type of notification event (ItemAdded, etc.) -->
{{ item.server_id }}            <!-- Jellyfin server unique identifier -->
{{ item.server_name }}          <!-- Human-readable server name -->
{{ item.server_url }}           <!-- Public URL of the Jellyfin server -->
{{ item.server_version }}       <!-- Jellyfin server version string -->
```

### Subtitle Information
```jinja2
{{ item.subtitle_codec }}       <!-- Subtitle format (srt, ass, pgs, vtt, etc.) -->
{{ item.subtitle_default }}     <!-- Whether this is the default subtitle track -->
{{ item.subtitle_external }}    <!-- Whether subtitle is external file vs embedded -->
{{ item.subtitle_forced }}      <!-- Whether subtitle is forced display -->
{{ item.subtitle_language }}    <!-- Subtitle language code (eng, spa, fra, etc.) -->
{{ item.subtitle_title }}       <!-- Subtitle stream title/name -->
{{ item.subtitle_type }}        <!-- Subtitle stream type identifier -->
```

### Timestamps
```jinja2
{{ item.air_time }}             <!-- Original air time for TV episodes -->
{{ item.premiere_date }}        <!-- Original release/air date -->
{{ item.series_premiere_date }} <!-- When the TV series originally premiered -->
{{ item.timestamp }}            <!-- Local timestamp with timezone from webhook -->
{{ item.timestamp_created }}    <!-- When this MediaItem object was created -->
{{ item.utc_timestamp }}        <!-- UTC timestamp from webhook -->
```

### TV Show Specific
```jinja2
{{ item.episode_number }}       <!-- Episode number -->
{{ item.episode_number_padded }} <!-- Episode number with leading zero (01, 02, etc.) -->
{{ item.episode_number_padded_3 }} <!-- Episode number with 3 digits (001, 002, etc.) -->
{{ item.season_id }}            <!-- Season Jellyfin ID -->
{{ item.season_number }}        <!-- Season number -->
{{ item.season_number_padded }} <!-- Season number with leading zero (01, 02, etc.) -->
{{ item.season_number_padded_3 }} <!-- Season number with 3 digits (001, 002, etc.) -->
{{ item.series_id }}            <!-- Series Jellyfin ID -->
{{ item.series_name }}          <!-- TV series name -->
```

### Video Technical Specifications
```jinja2
{{ item.aspect_ratio }}         <!-- Display aspect ratio (16:9, 4:3, 2.35:1, etc.) -->
{{ item.video_bitdepth }}       <!-- Color bit depth (8, 10, 12) -->
{{ item.video_bitrate }}        <!-- Video bitrate in bits per second -->
{{ item.video_codec }}          <!-- Video codec (h264, hevc, av1, mpeg2, etc.) -->
{{ item.video_colorprimaries }} <!-- Color primaries specification (bt709, bt2020, etc.) -->
{{ item.video_colorspace }}     <!-- Color space specification (bt709, bt2020nc, etc.) -->
{{ item.video_colortransfer }}  <!-- Color transfer characteristics (bt709, smpte2084, etc.) -->
{{ item.video_framerate }}      <!-- Frames per second (23.976, 24, 25, 29.97, etc.) -->
{{ item.video_height }}         <!-- Video resolution height in pixels (720, 1080, 2160, etc.) -->
{{ item.video_interlaced }}     <!-- Whether video uses interlaced scanning (true/false) -->
{{ item.video_language }}       <!-- Video stream language code (eng, spa, fra, etc.) -->
{{ item.video_level }}          <!-- Codec level specification (3.1, 4.0, 5.1, etc.) -->
{{ item.video_pixelformat }}    <!-- Pixel format (yuv420p, yuv420p10le, etc.) -->
{{ item.video_profile }}        <!-- Codec profile (High, Main, Main10, etc.) -->
{{ item.video_range }}          <!-- Video range (SDR, HDR10, HDR10+, Dolby Vision) -->
{{ item.video_refframes }}      <!-- Number of reference frames used by codec -->
{{ item.video_title }}          <!-- Video stream title/name from container -->
{{ item.video_type }}           <!-- Stream type identifier -->
{{ item.video_width }}          <!-- Video resolution width in pixels (1280, 1920, 3840, etc.) -->
```

## Template Examples

### Simple Example - Basic New Item

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
        }
        {% endif %}
      ]
    }
  ]
}
```

### Advanced Example - Technical Specifications

```jinja2
{
  "embeds": [
    {
      "title": "🎬 New {{ item.item_type }} Added",
      "description": "**{{ item.name }}**{% if item.year %} ({{ item.year }}){% endif %}{% if item.overview %}\n\n{{ (item.overview[:200] + '...') if item.overview|length > 200 else item.overview }}{% endif %}",
      "color": {{ color }},
      "fields": [
        {% if item.video_height or item.video_codec %}
        {
          "name": "🎥 Video Details",
          "value": "{% if item.video_height %}**Resolution:** {{ item.video_height }}p{% if item.video_width %} ({{ item.video_width }}×{{ item.video_height }}){% endif %}{% endif %}{% if item.video_codec %}\n**Codec:** {{ item.video_codec.upper() }}{% if item.video_profile %} ({{ item.video_profile }}){% endif %}{% endif %}{% if item.video_range and item.video_range != 'SDR' %}\n**HDR:** {{ item.video_range }}{% endif %}{% if item.video_bitdepth and item.video_bitdepth > 8 %}\n**Bit Depth:** {{ item.video_bitdepth }}-bit{% endif %}",
          "inline": false
        },
        {% endif %}
        {% if item.audio_codec %}
        {
          "name": "🔊 Audio Details",
          "value": "{% if item.audio_codec %}**Codec:** {{ item.audio_codec.upper() }}{% endif %}{% if item.audio_channels %}\n**Channels:** {{ item.audio_channels }}.{% if item.audio_channels > 2 %}1{% else %}0{% endif %}{% endif %}{% if item.audio_samplerate %}\n**Sample Rate:** {{ item.audio_samplerate }}Hz{% endif %}{% if item.audio_language %}\n**Language:** {{ item.audio_language.upper() }}{% endif %}",
          "inline": true
        },
        {% endif %}
        {% if item.subtitle_language %}
        {
          "name": "📝 Subtitles",
          "value": "{% if item.subtitle_language %}**Language:** {{ item.subtitle_language.upper() }}{% endif %}{% if item.subtitle_codec %}\n**Format:** {{ item.subtitle_codec.upper() }}{% endif %}{% if item.subtitle_forced %}\n**Type:** Forced{% elif item.subtitle_default %}\n**Type:** Default{% endif %}",
          "inline": true
        },
        {% endif %}
        {% if item.file_size %}
        {
          "name": "💾 File Info",
          "value": "**Size:** {{ '%.2f'|format(item.file_size / 1073741824) }} GB{% if item.runtime_ticks %}\n**Duration:** {% set hours = (item.runtime_ticks / 36000000000) | int %}{% set minutes = ((item.runtime_ticks / 600000000) % 60) | int %}{{ hours }}h {{ minutes }}m{% endif %}",
          "inline": true
        }
        {% endif %}
      ]
    }
  ]
}
```

### Music-Specific Example

```jinja2
{
  "embeds": [
    {
      "title": "🎵 New {{ item.item_type }} Added",
      "description": "**{{ item.name }}**{% if item.artists and item.artists|length > 0 %}\nby {{ item.artists|join(', ') }}{% elif item.album_artist %}\nby {{ item.album_artist }}{% endif %}{% if item.album %}\n\nAlbum: **{{ item.album }}**{% endif %}",
      "color": {{ color }},
      "fields": [
        {% if item.genres and item.genres|length > 0 %}
        {
          "name": "🎭 Genres",
          "value": "{{ item.genres|join(', ') }}",
          "inline": true
        },
        {% endif %}
        {% if item.audio_codec or item.audio_samplerate %}
        {
          "name": "🔊 Audio Quality",
          "value": "{% if item.audio_codec %}**Codec:** {{ item.audio_codec.upper() }}{% endif %}{% if item.audio_samplerate %}\n**Sample Rate:** {{ item.audio_samplerate }}Hz{% endif %}{% if item.audio_bitrate %}\n**Bitrate:** {{ item.audio_bitrate }}bps{% endif %}",
          "inline": true
        },
        {% endif %}
        {% if item.runtime_ticks %}
        {
          "name": "⏱️ Duration",
          "value": "{{ ((item.runtime_ticks / 10000000) / 60) | round(1) }} minutes",
          "inline": true
        }
        {% endif %}
      ]
    }
  ]
}
```

### TV Episode Example

```jinja2
{
  "embeds": [
    {
      "title": "📺 New Episode Added",
      "description": "**{{ item.series_name }}**\nS{{ '%02d'|format(item.season_number or 0) }}E{{ '%02d'|format(item.episode_number or 0) }} • {{ item.name }}{% if item.overview %}\n\n{{ (item.overview[:150] + '...') if item.overview|length > 150 else item.overview }}{% endif %}",
      "color": {{ color }},
      "fields": [
        {% if item.air_time %}
        {
          "name": "📅 Air Date",
          "value": "{{ item.air_time }}",
          "inline": true
        },
        {% endif %}
        {% if item.video_height %}
        {
          "name": "📐 Quality",
          "value": "{{ item.video_height }}p{% if item.video_range and item.video_range != 'SDR' %} {{ item.video_range }}{% endif %}",
          "inline": true
        },
        {% endif %}
        {% if item.runtime_ticks %}
        {
          "name": "⏱️ Runtime",
          "value": "{% set minutes = (item.runtime_ticks / 600000000) | int %}{{ minutes }} minutes",
          "inline": true
        }
        {% endif %}
      ]
    }
  ]
}
```

### Photo Example

```jinja2
{
  "embeds": [
    {
      "title": "📷 New Photo Added",
      "description": "**{{ item.name }}**",
      "color": {{ color }},
      "fields": [
        {% if item.width and item.height %}
        {
          "name": "📐 Dimensions",
          "value": "{{ item.width }}×{{ item.height }} pixels",
          "inline": true
        },
        {% endif %}
        {% if item.file_size %}
        {
          "name": "💾 File Size",
          "value": "{{ '%.2f'|format(item.file_size / 1048576) }} MB",
          "inline": true
        },
        {% endif %}
        {% if item.date_created %}
        {
          "name": "📅 Date Added",
          "value": "{{ item.date_created[:10] }}",
          "inline": true
        }
        {% endif %}
      ]
    }
  ]
}
```

### Compact Template Example

```jinja2
{
  "embeds": [
    {
      "title": "{% if item.item_type == 'Movie' %}🎬{% elif item.item_type == 'Episode' %}📺{% elif item.item_type == 'Audio' %}🎵{% else %}📁{% endif %} New {{ item.item_type }}",
      "description": "**{{ item.name }}**{% if item.year %} ({{ item.year }}){% endif %}{% if item.video_height %} • {{ item.video_height }}p{% endif %}{% if item.video_codec %} • {{ item.video_codec.upper() }}{% endif %}{% if item.audio_codec %} • {{ item.audio_codec.upper() }}{% endif %}",
      "color": {{ color }},
      "timestamp": "{{ timestamp }}"
    }
  ]
}
```

### Comprehensive Full Template Example

```jinja2
{
  "embeds": [
    {
      "title": "🎬 New {{ item.item_type }} Added",
      "description": "**{{ item.name }}**{% if item.year %} ({{ item.year }}){% endif %}{% if item.tagline %}\n*{{ item.tagline }}*{% endif %}{% if item.overview %}\n\n{{ (item.overview[:300] + '...') if item.overview|length > 300 else item.overview }}{% endif %}",
      "color": {{ color }},
      "fields": [
        {% if item.genres and item.genres|length > 0 %}
        {
          "name": "🎭 Genres",
          "value": "{{ item.genres[:5]|join(', ') }}{% if item.genres|length > 5 %} +{{ item.genres|length - 5 }} more{% endif %}",
          "inline": true
        },
        {% endif %}
        {% if item.official_rating %}
        {
          "name": "🛡️ Rating",
          "value": "{{ item.official_rating }}",
          "inline": true
        },
        {% endif %}
        {% if item.studios and item.studios|length > 0 %}
        {
          "name": "🏢 Studio",
          "value": "{{ item.studios[0] }}{% if item.studios|length > 1 %} +{{ item.studios|length - 1 }} more{% endif %}",
          "inline": true
        },
        {% endif %}
        {
          "name": "🎥 Video Specifications",
          "value": "{% if item.video_height %}**Resolution:** {{ item.video_height }}p{% if item.video_width %} ({{ item.video_width }}×{{ item.video_height }}){% endif %}{% endif %}{% if item.video_codec %}\n**Codec:** {{ item.video_codec.upper() }}{% if item.video_profile %} {{ item.video_profile }}{% endif %}{% endif %}{% if item.video_range and item.video_range != 'SDR' %}\n**HDR:** {{ item.video_range }}{% endif %}{% if item.video_bitdepth and item.video_bitdepth > 8 %}\n**Bit Depth:** {{ item.video_bitdepth }}-bit{% endif %}{% if item.video_framerate %}\n**Frame Rate:** {{ item.video_framerate }}fps{% endif %}{% if item.video_colorspace %}\n**Color Space:** {{ item.video_colorspace }}{% endif %}",
          "inline": false
        },
        {% if item.audio_codec %}
        {
          "name": "🔊 Audio Specifications",
          "value": "{% if item.audio_codec %}**Codec:** {{ item.audio_codec.upper() }}{% endif %}{% if item.audio_channels %}\n**Channels:** {{ item.audio_channels }}.{% if item.audio_channels > 2 %}1{% else %}0{% endif %}{% endif %}{% if item.audio_samplerate %}\n**Sample Rate:** {{ item.audio_samplerate }}Hz{% endif %}{% if item.audio_bitrate %}\n**Bitrate:** {{ item.audio_bitrate }}bps{% endif %}{% if item.audio_language %}\n**Language:** {{ item.audio_language.upper() }}{% endif %}",
          "inline": false
        },
        {% endif %}
        {% if item.subtitle_language %}
        {
          "name": "📝 Subtitle Information",
          "value": "{% if item.subtitle_language %}**Languages:** {{ item.subtitle_language.upper() }}{% endif %}{% if item.subtitle_codec %}\n**Format:** {{ item.subtitle_codec.upper() }}{% endif %}{% if item.subtitle_external %}{% if item.subtitle_external %}\n**Type:** External File{% else %}\n**Type:** Embedded{% endif %}{% endif %}{% if item.subtitle_forced %}\n**Forced:** Yes{% endif %}",
          "inline": true
        },
        {% endif %}
        {
          "name": "💾 File Information",
          "value": "{% if item.file_size %}**Size:** {{ '%.2f'|format(item.file_size / 1073741824) }} GB{% endif %}{% if item.runtime_ticks %}\n**Duration:** {% set hours = (item.runtime_ticks / 36000000000) | int %}{% set minutes = ((item.runtime_ticks / 600000000) % 60) | int %}{{ hours }}h {{ minutes }}m{% endif %}{% if item.library_name %}\n**Library:** {{ item.library_name }}{% endif %}",
          "inline": true
        },
        {% if item.server_name %}
        {
          "name": "🖥️ Server Info",
          "value": "{% if item.server_name %}**Server:** {{ item.server_name }}{% endif %}{% if item.server_version %}\n**Version:** {{ item.server_version }}{% endif %}",
          "inline": true
        }
        {% endif %}
      ],
      "thumbnail": {
        "url": "{{ jellyfin_url }}/Items/{{ item.item_id }}/Images/Primary?maxHeight=300&maxWidth=200"
      },
      "footer": {
        "text": "Added {{ item.date_created[:10] if item.date_created else timestamp[:10] }} • {{ item.server_name or 'Jellyfin' }}",
        "icon_url": "{{ jellyfin_url }}/web/favicon.ico"
      },
      "timestamp": "{{ timestamp }}",
      "url": "{{ jellyfin_url }}/web/index.html#!/details?id={{ item.item_id }}"
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

## Advanced Template Techniques

### Conditional Logic for Different Media Types

```jinja2
{% if item.item_type == 'Episode' %}
  <!-- TV Episode specific formatting -->
  "title": "📺 New Episode: {{ item.series_name }}",
  "description": "S{{ '%02d'|format(item.season_number or 0) }}E{{ '%02d'|format(item.episode_number or 0) }} • {{ item.name }}"
{% elif item.item_type == 'Movie' %}
  <!-- Movie specific formatting -->
  "title": "🎬 New Movie Added",
  "description": "**{{ item.name }}**{% if item.year %} ({{ item.year }}){% endif %}"
{% elif item.item_type in ['Audio', 'MusicAlbum'] %}
  <!-- Music specific formatting -->
  "title": "🎵 New Music Added",
  "description": "**{{ item.name }}**{% if item.album_artist %} by {{ item.album_artist }}{% endif %}"
{% endif %}
```

### Quality Upgrade Detection

```jinja2
<!-- For upgrade templates, show what improved -->
{% if old_item and item %}
  {% if item.video_height > old_item.video_height %}
    "value": "{{ old_item.video_height }}p → **{{ item.video_height }}p**"
  {% elif item.video_range != old_item.video_range and item.video_range != 'SDR' %}
    "value": "{{ old_item.video_range or 'SDR' }} → **{{ item.video_range }}**"
  {% elif item.video_codec != old_item.video_codec %}
    "value": "{{ old_item.video_codec }} → **{{ item.video_codec }}**"
  {% endif %}
{% endif %}
```

### Dynamic Field Counting

```jinja2
<!-- Limit fields to stay within Discord's 25 field limit -->
{% set field_count = 0 %}
{% if item.video_height and field_count < 20 %}
  {% set field_count = field_count + 1 %}
  {
    "name": "📐 Quality",
    "value": "{{ item.video_height }}p",
    "inline": true
  }{% if field_count < 20 %},{% endif %}
{% endif %}
```

### External Link Generation

```jinja2
<!-- Generate links to external databases -->
{% if item.imdb_id %}
  "url": "https://www.imdb.com/title/{{ item.imdb_id }}/"
{% elif item.tmdb_id %}
  "url": "https://www.themoviedb.org/{% if item.item_type == 'Movie' %}movie{% else %}tv{% endif %}/{{ item.tmdb_id }}"
{% elif item.tvdb_id %}
  "url": "https://thetvdb.com/dereferrer/series/{{ item.tvdb_id }}"
{% else %}
  "url": "{{ jellyfin_url }}/web/index.html#!/details?id={{ item.item_id }}"
{% endif %}
```

## Tips for Template Development

1. **Test Your Templates**: Use the `/test-webhook` API endpoint to test template changes
2. **Handle Missing Data**: Always check if variables exist before using them:
   ```jinja2
   {% if item.property %}{{ item.property }}{% endif %}
   ```
3. **Discord Limits**: Keep embed descriptions under 4096 characters
4. **Field Limits**: Maximum 25 fields per embed, each field value under 1024 characters
5. **Color Values**: Use integer values for colors (not hex strings)
6. **JSON Validation**: Ensure your template outputs valid JSON
7. **List Handling**: Use Jinja2 filters for lists:
   ```jinja2
   {{ item.genres|join(', ') }}                    <!-- Join list with commas -->
   {{ item.artists[:3]|join(', ') }}              <!-- Show first 3 artists -->
   {% if item.genres|length > 5 %}+more{% endif %} <!-- Show count if too many -->
   ```

## Troubleshooting

**Template Not Loading**: Verify the file exists and the path in `config.json` is correct  
**Syntax Errors**: Check Jinja2 syntax - all `{% %}` and `{{ }}` tags must be properly closed  
**Missing Variables**: Use conditional checks: `{% if item.property %}{{ item.property }}{% endif %}`  
**JSON Errors**: Validate template output with a JSON validator  
**Discord Errors**: Check Discord webhook URL and ensure embed format is valid  
**Field Overflow**: Monitor field count - Discord has a 25 field limit per embed
**Character Limits**: Keep descriptions under 4096 characters and field values under 1024

## Further Reading

- [Jinja2 Documentation](https://jinja.palletsprojects.com/en/stable/)
- [Discord Webhook Documentation](https://discord.com/developers/docs/resources/webhook)
- [Discord Embed Limits](https://discord.com/developers/docs/resources/channel#embed-limits)
- [Advanced Templates Guide](Readme-AdvancedTemplates.md)