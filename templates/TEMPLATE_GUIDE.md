# Jellynouncer Template Development Guide

## JSON Escaping Best Practices

### Always Use the `tojson` Filter

When outputting any dynamic value in a JSON context, **always** use the `| tojson` filter. This properly escapes special characters that could break JSON parsing.

#### ✅ Correct Examples:
```jinja2
"title": {{ item.name | tojson }}
"description": {{ item.overview | tojson }}
"value": {{ (item.video_height ~ "p") | tojson }}
```

#### ❌ Incorrect Examples (will break with special characters):
```jinja2
"title": "{{ item.name }}"  # Breaks if name contains quotes
"description": "{{ item.overview }}"  # Breaks if overview contains newlines or quotes
```

### Common Special Characters That Break JSON

Without proper escaping, these characters will break JSON parsing:
- Double quotes (`"`) - Common in titles like: `The "Real" Story`
- Backslashes (`\`) - Common in file paths
- Newlines (`\n`) - Common in descriptions and overviews
- Tabs (`\t`) - Sometimes in formatted text
- Unicode characters - Emojis, accented characters

### Complex String Building

When building complex strings with multiple parts, apply `tojson` to the final result:

```jinja2
{# Build the string first, then escape it #}
"value": {{ (item.video_codec | upper ~ " " ~ item.video_profile) | tojson }}

{# For conditional parts, build inside parentheses #}
"description": {{ ("Episode " ~ item.episode_number ~ 
  ({% if item.name %} ": " ~ item.name {% else %} "" {% endif %})) | tojson }}
```

### Default Values and Truncation

Apply filters before `tojson`:

```jinja2
{# Chain filters: default → truncate → tojson #}
"title": {{ item.name | default('Unknown') | truncate(256, True, '...') | tojson }}
```

### Working with Lists

When joining lists, apply `tojson` after joining:

```jinja2
"genres": {{ item.genres | join(', ') | tojson }}
```

### Numbers and Booleans

Numbers and booleans don't need `tojson` but it doesn't hurt:

```jinja2
"color": {{ color_value }}  # OK for numbers
"inline": true  # OK for boolean literals
"inline": {{ should_inline | tojson }}  # Safer for variables
```

### Template Testing

Test your templates with items containing special characters:
- Movie: `Mission: Impossible – Dead Reckoning Part One`
- Episode with quotes: `The "Truth" About Lying`
- Overview with newlines and special chars

### Validation Checklist

Before committing template changes:
- [ ] All dynamic string values use `| tojson`
- [ ] Complex concatenations wrapped in parentheses before `| tojson`
- [ ] Tested with special characters in titles/descriptions
- [ ] JSON validates properly (no syntax errors)
- [ ] Character limits respected (title: 256, description: 4096, field value: 1024)

## Additional Safety in Python

While templates handle escaping, the Python code should avoid pre-modifying strings:

### ❌ Don't Do This in Python:
```python
# Don't try to escape in Python
item.name = item.name.replace('"', '\\"')  # Let Jinja handle it!
```

### ✅ Do This Instead:
```python
# Pass raw data to templates
template_vars = {
    "item": item,  # Pass as-is, let template handle escaping
    "action": action
}
```

## Template Debugging

If JSON parsing fails:
1. Check the rendered output in logs
2. Look for unescaped quotes or newlines
3. Validate JSON with an online validator
4. Add `| tojson` to any dynamic values missing it

## Common Patterns

### Episode Titles
```jinja2
"title": {{ ("📺 " ~ item.series_name ~ " - S" ~ 
  "%02d"|format(item.season_number) ~ "E" ~ 
  "%02d"|format(item.episode_number) ~ 
  ({% if item.name %} ": " ~ item.name {% else %} "" {% endif %})) | tojson }}
```

### Conditional Fields
```jinja2
{% if item.overview %}
"description": {{ item.overview | truncate(4096, True, '...') | tojson }},
{% endif %}
```

### Formatted Numbers
```jinja2
"value": {{ (("%.1f"|format(item.file_size / 1073741824)) ~ " GB") | tojson }}
```

## Summary

The `| tojson` filter is your friend! It:
- Properly escapes ALL special characters
- Handles Unicode correctly
- Prevents JSON parsing errors
- Is already implemented in our templates

Always use it for dynamic string values in JSON contexts!