import pytest
import json
from dataclasses import asdict
from jinja2 import Environment, FileSystemLoader

from media_models import MediaItem

@pytest.fixture
def jinja_env():
    """Provides a Jinja2 environment configured to load templates."""
    return Environment(
        loader=FileSystemLoader('templates/'),
        trim_blocks=True,
        lstrip_blocks=True
    )

@pytest.fixture
def sample_media_item():
    """Provides a rich sample MediaItem for rendering tests."""
    return MediaItem(
        item_id="1",
        name="Test Movie",
        item_type="Movie",
        year=2024,
        overview="This is a test overview.",
        runtime_ticks=72000000000, # 2 hours
        video_height=1080,
        video_codec="hevc",
        audio_codec="dts",
        audio_channels=6,
        imdb_id="tt1234567",
        tmdb_id="123",
        library_name="Test Library",
        server_name="Test Server",
        ratings={"imdb": {"value": "8.5/10"}, "tmdb": {"value": "8.2/10"}},
    )

@pytest.fixture
def sample_template_vars(sample_media_item):
    """Provides a dictionary of variables for rendering templates."""
    return {
        "item": asdict(sample_media_item),
        "action": "new_item",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "changes": [],
        "timestamp": "2024-01-01T12:00:00Z",
        "jellyfin_url": "http://jellyfin.example.com",
        "color": 65280,
    }

def test_new_item_template_renders_valid_json(jinja_env, sample_template_vars):
    """Tests that new_item.j2 renders to valid JSON."""
    try:
        template = jinja_env.get_template('new_item.j2')
        rendered_output = template.render(sample_template_vars)
        json.loads(rendered_output)
    except (json.JSONDecodeError, TypeError) as e:
        pytest.fail(f"new_item.j2 failed to render valid JSON: {e}\nOutput:\n{rendered_output}")

def test_upgraded_item_template_renders_valid_json(jinja_env, sample_template_vars):
    """Tests that upgraded_item.j2 renders to valid JSON."""
    sample_template_vars['action'] = 'upgraded_item'
    sample_template_vars['changes'] = [{'description': 'Resolution changed from 720p to 1080p'}]
    try:
        template = jinja_env.get_template('upgraded_item.j2')
        rendered_output = template.render(sample_template_vars)
        json.loads(rendered_output)
    except (json.JSONDecodeError, TypeError) as e:
        pytest.fail(f"upgraded_item.j2 failed to render valid JSON: {e}\nOutput:\n{rendered_output}")
