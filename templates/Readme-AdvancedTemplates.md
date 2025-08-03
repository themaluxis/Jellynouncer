# Advanced Jellynouncer Templates

This guide focuses on the advanced `-full` templates that provide comprehensive, detailed Discord notifications with rich metadata and enhanced formatting.

## Overview

The `-full` templates are enhanced versions of the standard templates that include:
- **Extended metadata display** (genres, studios, runtime, file details)
- **Rich technical specifications** (detailed video/audio information)
- **Enhanced visual layout** with better organization
- **Comprehensive content descriptions** with plot summaries
- **Advanced conditional logic** for different media types

## Available Advanced Templates

- **`new_item-full.j2`** - Comprehensive new item notifications
- **`upgraded_item-full.j2`** - Detailed upgrade notifications with full context

## Template Structure Breakdown

### 1. Discord Embed Foundation

Every advanced template starts with the Discord embed structure:

```json
{
  "embeds": [
    {
      "title": "Dynamic title based on content type",
      "description": "Rich description with plot/context",
      "color": "Context-aware color coding",
      "author": "Series information for episodes",
      "fields": [
        "Multiple organized information fields"
      ]
    }
  ]
}
```

### 2. Dynamic Title Generation

The advanced templates use sophisticated title logic:

```jinja2
{% if item.item_type == 'Episode' %}
  📺 New Episode Added
{% elif item.item_type == 'Series' %}
  📺 New Series Added
{% elif item.item_type == 'Movie' %}
  🎬 New Movie Added
{% elif item.item_type == 'Season' %}
  📺 New Season Added
{% elif item.item_type == 'Audio' %}
  🎵 New Audio Added
{% elif item.item_type == 'MusicAlbum' %}
  💿 New Album Added
{% elif item.item_type == 'MusicArtist' %}
  🎤 New Artist Added
{% elif item.item_type == 'Photo' %}
  📷 New Photo Added
{% else %}
  📁 New {{ item.item_type }} Added
{% endif %}
```

### 3. Rich Description Logic

Advanced templates include comprehensive descriptions with conditional content:

#### Episode Descriptions
```jinja2
{% if item.item_type == 'Episode' %}
  **{{ item.series_name }}** 
  S{{ '%02d'|format(item.season_number or 0) }}E{{ '%02d'|format(item.episode_number or 0) }}
  {{ item.name }}
```

#### Music Descriptions
```jinja2
{% elif item.item_type == 'Audio' %}
  {% if item.album %}
    **{{ item.album }}**
    {% if item.album_artist %} by {{ item.album_artist }}{% endif %}
  {% endif %}
  {{ item.name }}
  {% if item.artists and item.artists|length > 0 %}
    by {{ item.artists|join(', ') }}
  {% endif %}
```

#### Standard Media with Plot Summary
```jinja2
{% else %}
  **{{ item.name }}**
  {% if item.year %} ({{ item.year }}){% endif %}
{% endif %}

{% if item.overview and item.overview|length > 0 %}
  {{ (item.overview[:200] + '...') if item.overview|length > 200 else item.overview }}
{% endif %}
```

### 4. Author Section for Series

Episodes get special treatment with series branding:

```jinja2
{% if item.item_type == 'Episode' and item.series_id %}
"author": {
  "name": "{{ item.series_name }}",
  "icon_url": "{{ jellyfin_url }}/Items/{{ item.series_id }}/Images/Logo?maxHeight=64&maxWidth=200",
  "url": "{{ jellyfin_url }}/web/index.html#!/details?id={{ item.series_id }}"
},
{% endif %}
```

## Field Organization System

The advanced templates use a sophisticated field organization system with inline and full-width fields for optimal visual layout.

### Technical Specifications Summary (Inline)

Quick overview fields displayed side-by-side:

```jinja2
{# Video Quality #}
{% if item.video_height and item.item_type not in ['Audio', 'MusicAlbum', 'MusicArtist'] %}
{
  "name": "📐 Quality",
  "value": "{{ item.video_height }}p
           {% if item.video_range and item.video_range != 'SDR' %} {{ item.video_range }}{% endif %}
           {% if item.video_width %} ({{ item.video_width }}×{{ item.video_height }}){% endif %}",
  "inline": true
},
{% endif %}

{# Video Codec #}
{% if item.video_codec and item.item_type not in ['Audio', 'MusicAlbum', 'MusicArtist'] %}
{
  "name": "🎞️ Video",
  "value": "{{ item.video_codec.upper() }}
           {% if item.video_profile %} {{ item.video_profile }}{% endif %}
           {% if item.video_framerate %} @ {{ item.video_framerate }}fps{% endif %}",
  "inline": true
},
{% endif %}

{# Audio Summary #}
{% if item.audio_codec %}
{
  "name": "🔊 Audio", 
  "value": "{{ item.audio_codec.upper() }}
           {% if item.audio_channels %} {{ item.audio_channels }}.
           {% if item.audio_channels > 2 %}1{% else %}0{% endif %}{% endif %}
           {% if item.audio_language %} ({{ item.audio_language }}){% endif %}",
  "inline": true
},
{% endif %}
```

### Detailed Technical Information (Full Width)

Comprehensive technical details in non-inline fields:

#### Video Technical Details
```jinja2
{% if item.item_type not in ['Audio', 'MusicAlbum', 'MusicArtist'] and 
     (item.video_codec or item.video_height or item.aspect_ratio) %}
{
  "name": "🎥 Video Technical Details",
  "value": "
    {% if item.video_height %}
      **Resolution:** {{ item.video_height }}p
      {% if item.video_width %} ({{ item.video_width }}×{{ item.video_height }}){% endif %}
    {% endif %}
    {% if item.video_codec %}
      **Codec:** {{ item.video_codec.upper() }}
      {% if item.video_profile %} ({{ item.video_profile }}){% endif %}
    {% endif %}
    {% if item.video_range and item.video_range != 'SDR' %}
      **HDR:** {{ item.video_range }}
    {% endif %}
    {% if item.aspect_ratio %}
      **Aspect Ratio:** {{ item.aspect_ratio }}
    {% endif %}
    {% if item.video_framerate %}
      **Frame Rate:** {{ item.video_framerate }} fps
    {% endif %}",
  "inline": false
},
{% endif %}
```

#### Audio Technical Details
```jinja2
{% if item.audio_codec or item.audio_channels or item.audio_bitrate %}
{
  "name": "🔊 Audio Technical Details",
  "value": "
    {% if item.audio_codec %}
      **Codec:** {{ item.audio_codec.upper() }}
    {% endif %}
    {% if item.audio_channels %}
      **Channels:** {{ item.audio_channels }}.
      {% if item.audio_channels > 2 %}1{% else %}0{% endif %}
    {% endif %}
    {% if item.audio_bitrate %}
      **Bitrate:** {{ item.audio_bitrate }} kbps
    {% endif %}
    {% if item.audio_language %}
      **Language:** {{ item.audio_language }}
    {% endif %}",
  "inline": false
},
{% endif %}
```

### File and Media Information

```jinja2
{% if item.file_size or item.runtime_ticks %}
{
  "name": "💾 File Information",
  "value": "
    {% if item.file_size %}
      **Size:** {{ "%.2f"|format(item.file_size / 1073741824) }} GB
    {% endif %}
    {% if item.runtime_ticks %}
      **Duration:** {{ ((item.runtime_ticks / 10000000) / 60)|round|int }} minutes
    {% endif %}",
  "inline": false
},
{% endif %}
```

### Content Metadata

```jinja2
{% if item.genres or item.studios %}
{
  "name": "📋 Content Information",
  "value": "
    {% if item.genres %}
      **Genres:** {{ item.genres|join(', ') }}
    {% endif %}
    {% if item.studios %}
      **Studios:** {{ item.studios|join(', ') }}
    {% endif %}",
  "inline": false
}
{% endif %}
```

## Upgrade Template Breakdown

The `upgraded_item-full.j2` template includes additional complexity for handling upgrade information:

### Change Summary Section

```jinja2
{
  "name": "🔄 Upgrade Summary",
  "value": "
    {% for change in changes[:5] %}
      **{% if change.type == 'resolution' %}📐 Resolution:
      {% elif change.type == 'codec' %}🎞️ Video Codec:
      {% elif change.type == 'audio_codec' %}🔊 Audio Codec:
      {% elif change.type == 'audio_channels' %}🔊 Audio Channels:
      {% elif change.type == 'hdr_status' %}🌈 HDR Status:
      {% elif change.type == 'file_size' %}💾 File:
      {% elif change.type == 'provider_ids' %}🔗 Metadata:
      {% else %}🔄 {{ change.type|title }}:
      {% endif %}**
      
      {% if change.type == 'resolution' %}
        {{ change.old_value or 'Unknown' }}p → **{{ change.new_value or 'Unknown' }}p**
      {% elif change.type == 'codec' %}
        {{ change.old_value or 'Unknown' }} → **{{ change.new_value or 'Unknown' }}**
      {% elif change.type == 'audio_codec' %}
        {{ change.old_value or 'Unknown' }} → **{{ change.new_value or 'Unknown' }}**
      {% elif change.type == 'audio_channels' %}
        {{ change.old_value or 0 }} ch → **{{ change.new_value or 0 }} ch**
      {% elif change.type == 'hdr_status' %}
        {{ change.old_value or 'SDR' }} → **{{ change.new_value or 'SDR' }}**
      {% elif change.type == 'file_size' %}
        File replaced{% if item.file_size %} ({{ "%.1f"|format(item.file_size / 1073741824) }}GB){% endif %}
      {% elif change.type == 'provider_ids' %}
        External IDs updated
      {% else %}
        {{ change.description or 'Updated' }}
      {% endif %}
      
      {% if not loop.last %}{% endif %}
    {% endfor %}
    
    {% if changes|length > 5 %}
      *...and {{ changes|length - 5 }} more changes*
    {% endif %}",
  "inline": false
},
```

## Advanced Formatting Techniques

### Conditional Field Display

The templates use smart field counting to prevent Discord embed limits:

```jinja2
{% set field_count = 0 %}

{% if item.video_height %}
  {# Video quality field #}
  {% set field_count = field_count + 1 %}
{% endif %}

{% if item.video_codec and field_count < 9 %}
  {# Video codec field #}  
  {% set field_count = field_count + 1 %}
{% endif %}
```

### Smart Text Truncation

```jinja2
{# Truncate overview to prevent Discord limits #}
{% if item.overview and item.overview|length > 0 %}
  {{ (item.overview[:200] + '...') if item.overview|length > 200 else item.overview }}
{% endif %}
```

### Audio Channel Formatting

```jinja2
{# Convert channel count to surround sound notation #}
{% if item.audio_channels %}
  {{ item.audio_channels }}.{% if item.audio_channels > 2 %}1{% else %}0{% endif %}
{% endif %}
```

### File Size Conversion

```jinja2
{# Convert bytes to GB with 2 decimal places #}
{% if item.file_size %}
  {{ "%.2f"|format(item.file_size / 1073741824) }} GB
{% endif %}
```

### Runtime Formatting

```jinja2
{# Convert Jellyfin ticks to minutes #}
{% if item.runtime_ticks %}
  {{ ((item.runtime_ticks / 10000000) / 60)|round|int }} minutes
{% endif %}
```

## Example Advanced Outputs

### New Movie (Full Template)

```json
{
  "embeds": [
    {
      "title": "🎬 New Movie Added",
      "description": "**The Matrix** (1999)\n\n*A computer hacker learns from mysterious rebels about the true nature of his reality and his role in the war against its controllers.*",
      "color": 65280,
      "fields": [
        {
          "name": "📐 Quality",
          "value": "2160p HDR (3840×2160)",
          "inline": true
        },
        {
          "name": "🎞️ Video",
          "value": "HEVC Main10 @ 23.976fps",
          "inline": true
        },
        {
          "name": "🔊 Audio",
          "value": "DTS-HD 7.1 (English)",
          "inline": true
        },
        {
          "name": "🎥 Video Technical Details",
          "value": "**Resolution:** 2160p (3840×2160)\n**Codec:** HEVC (Main10)\n**HDR:** HDR10\n**Aspect Ratio:** 2.39:1\n**Frame Rate:** 23.976 fps",
          "inline": false
        },
        {
          "name": "🔊 Audio Technical Details", 
          "value": "**Codec:** DTS-HD\n**Channels:** 7.1\n**Bitrate:** 1536 kbps\n**Language:** English",
          "inline": false
        },
        {
          "name": "💾 File Information",
          "value": "**Size:** 45.67 GB\n**Duration:** 136 minutes",
          "inline": false
        },
        {
          "name": "📋 Content Information",
          "value": "**Genres:** Action, Science Fiction\n**Studios:** Warner Bros., Village Roadshow",
          "inline": false
        }
      ]
    }
  ]
}
```

### TV Episode Upgrade (Full Template)

```json
{
  "embeds": [
    {
      "title": "⬆️ Episode Resolution Upgraded",
      "description": "**Breaking Bad** S01E01\nPilot",
      "color": 16766720,
      "author": {
        "name": "Breaking Bad",
        "icon_url": "https://jellyfin.example.com/Items/123456/Images/Logo?maxHeight=64&maxWidth=200",
        "url": "https://jellyfin.example.com/web/index.html#!/details?id=123456"
      },
      "fields": [
        {
          "name": "🔄 Upgrade Summary",
          "value": "**📐 Resolution:** 1080p → **2160p**\n**🌈 HDR Status:** SDR → **HDR10**\n**💾 File:** File replaced (12.3GB)",
          "inline": false
        },
        {
          "name": "📐 Current Quality",
          "value": "2160p HDR10 (3840×2160)",
          "inline": true
        },
        {
          "name": "🎞️ Current Video",
          "value": "HEVC Main10 @ 23.976fps",
          "inline": true
        },
        {
          "name": "🔊 Current Audio",
          "value": "AC3 5.1 (English)",
          "inline": true
        }
      ]
    }
  ]
}
```

## Best Practices for Advanced Templates

### 1. Field Organization
- Use inline fields for quick technical specs (3 per row)
- Use non-inline fields for detailed information
- Group related information together

### 2. Content Prioritization
- Most important info in title and description
- Technical details in organized fields
- Keep description under 4096 characters

### 3. Visual Hierarchy
- Use emojis consistently for field identification
- Bold important values and labels
- Maintain consistent formatting patterns

### 4. Error Handling
- Always check if variables exist before using them
- Provide fallback values for missing data
- Handle edge cases gracefully

### 5. Performance Considerations
- Limit field count to stay under Discord's 25 field limit
- Use field counting to manage complex layouts
- Truncate long text appropriately

## Customization Tips

1. **Modify Field Order**: Rearrange fields based on your priorities
2. **Add Custom Fields**: Include additional metadata important to you
3. **Adjust Truncation**: Change text length limits based on your needs
4. **Color Coding**: Modify colors for different content types
5. **Icon Changes**: Update emojis to match your Discord server's style

The advanced templates provide a comprehensive foundation that you can customize to create the perfect Discord notifications for your media collection.