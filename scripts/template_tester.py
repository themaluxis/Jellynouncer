#!/usr/bin/env python3
# scripts/template_tester.py - Test Jinja2 templates

import json
import sys
from jinja2 import Environment, FileSystemLoader
from datetime import datetime, timezone

def test_templates():
    """Test the Jinja2 templates with sample data"""
    
    # Sample data
    sample_item = {
        "item_id": "test-123",
        "name": "The Matrix",
        "item_type": "Movie",
        "year": 1999,
        "overview": "A computer programmer discovers reality isn't what it seems.",
        "video_height": 1080,
        "video_width": 1920,
        "video_codec": "h264",
        "video_profile": "High",
        "video_range": "SDR",
        "video_framerate": 23.976,
        "aspect_ratio": "16:9",
        "audio_codec": "ac3",
        "audio_channels": 6,
        "audio_language": "eng",
        "imdb_id": "tt0133093",
        "tmdb_id": "603"
    }
    
    sample_changes = [
        {
            "type": "resolution",
            "field": "video_height",
            "old_value": 720,
            "new_value": 1080,
            "description": "Resolution changed from 720p to 1080p"
        },
        {
            "type": "codec",
            "field": "video_codec",
            "old_value": "h264",
            "new_value": "hevc",
            "description": "Video codec changed from h264 to hevc"
        }
    ]
    
    template_data = {
        "item": sample_item,
        "changes": sample_changes,
        "is_new": False,
        "color": 16766720,  # Gold
        "jellyfin_url": "http://localhost:8096",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Test templates
    template_env = Environment(loader=FileSystemLoader('./templates'))
    
    templates = ['new_item.j2', 'upgraded_item.j2']
    
    for template_name in templates:
        print(f"\n🧪 Testing template: {template_name}")
        print("=" * 50)
        
        try:
            template = template_env.get_template(template_name)
            
            # Use appropriate data for template type
            if 'new' in template_name:
                test_data = {**template_data, "is_new": True, "changes": []}
            else:
                test_data = template_data
            
            rendered = template.render(**test_data)
            
            # Validate JSON
            try:
                parsed = json.loads(rendered)
                print("✅ Template renders valid JSON")
                print("📄 Sample output:")
                print(json.dumps(parsed, indent=2)[:500] + "...")
                
                # Check required Discord fields
                if 'embeds' in parsed and parsed['embeds']:
                    embed = parsed['embeds'][0]
                    required_fields = ['title', 'color']
                    for field in required_fields:
                        if field not in embed:
                            print(f"⚠️  Missing required field: {field}")
                        
            except json.JSONDecodeError as e:
                print(f"❌ Template produces invalid JSON: {e}")
                print("📄 Raw output:")
                print(rendered[:500] + "...")
                
        except Exception as e:
            print(f"❌ Error rendering template: {e}")

if __name__ == "__main__":
    test_templates()