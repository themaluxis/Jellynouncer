import pytest
from dataclasses import replace
from config_models import NotificationsConfig
from media_models import MediaItem
from change_detector import ChangeDetector

@pytest.fixture
def default_config():
    """Provides a default NotificationsConfig with all changes watched."""
    return NotificationsConfig(
        watch_changes={
            'resolution': True,
            'codec': True,
            'audio_codec': True,
            'audio_channels': True,
            'hdr_status': True,
            'file_size': True,
            'provider_ids': True,
        }
    )

@pytest.fixture
def base_media_item():
    """Provides a base MediaItem to be used as the 'old' item in tests."""
    return MediaItem(
        item_id="1",
        name="Test Movie",
        item_type="Movie",
        video_height=1080,
        video_codec="h264",
        audio_codec="aac",
        audio_channels=2,
        video_range="SDR",
        file_size=1000,
        imdb_id="tt1234567",
        tmdb_id="123",
        tvdb_id="456",
    )

def test_no_changes(default_config, base_media_item):
    """Tests that no changes are detected when items are identical."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 0

def test_resolution_change(default_config, base_media_item):
    """Tests detection of a resolution change."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.video_height = 2160
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 1
    assert changes[0]['type'] == 'resolution'
    assert changes[0]['old_value'] == 1080
    assert changes[0]['new_value'] == 2160

def test_video_codec_change(default_config, base_media_item):
    """Tests detection of a video codec change."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.video_codec = "hevc"
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 1
    assert changes[0]['type'] == 'codec'
    assert changes[0]['old_value'] == 'h264'
    assert changes[0]['new_value'] == 'hevc'

def test_audio_codec_change(default_config, base_media_item):
    """Tests detection of an audio codec change."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.audio_codec = "dts"
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 1
    assert changes[0]['type'] == 'audio_codec'
    assert changes[0]['old_value'] == 'aac'
    assert changes[0]['new_value'] == 'dts'

def test_audio_channels_change(default_config, base_media_item):
    """Tests detection of an audio channel change."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.audio_channels = 6
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 1
    assert changes[0]['type'] == 'audio_channels'
    assert changes[0]['old_value'] == 2
    assert changes[0]['new_value'] == 6

def test_hdr_status_change(default_config, base_media_item):
    """Tests detection of an HDR status change."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.video_range = "HDR10"
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 1
    assert changes[0]['type'] == 'hdr_status'
    assert changes[0]['old_value'] == 'SDR'
    assert changes[0]['new_value'] == 'HDR10'

def test_file_size_significant_change(default_config, base_media_item):
    """Tests detection of a significant file size change (>10%)."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.file_size = 1200  # 20% increase
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 1
    assert changes[0]['type'] == 'file_size'

def test_file_size_insignificant_change(default_config, base_media_item):
    """Tests that an insignificant file size change (<10%) is ignored."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.file_size = 1050  # 5% increase
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 0

def test_provider_id_change(default_config, base_media_item):
    """Tests detection of a provider ID change."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.imdb_id = "tt9876543"
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 1
    assert changes[0]['type'] == 'provider_ids'
    assert changes[0]['field'] == 'imdb_id'

def test_multiple_changes(default_config, base_media_item):
    """Tests detection of multiple simultaneous changes."""
    detector = ChangeDetector(default_config)
    new_item = replace(base_media_item)
    new_item.video_height = 2160
    new_item.video_codec = "hevc"
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 2
    change_types = {c['type'] for c in changes}
    assert 'resolution' in change_types
    assert 'codec' in change_types

def test_disabled_change_detection(base_media_item):
    """Tests that changes are not detected if disabled in config."""
    config = NotificationsConfig(
        watch_changes={'resolution': False}
    )
    detector = ChangeDetector(config)
    new_item = replace(base_media_item)
    new_item.video_height = 2160
    changes = detector.detect_changes(base_media_item, new_item)
    assert len(changes) == 0
