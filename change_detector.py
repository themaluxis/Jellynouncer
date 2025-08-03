"""
JellyNotify Change Detector

This module implements intelligent change detection for media quality upgrades
and modifications between versions of the same media item.
"""

import logging
from typing import List, Dict, Any

from config_models import NotificationsConfig
from media_models import MediaItem


class ChangeDetector:
    """
    Intelligent change detector for media quality upgrades and modifications.

    This class implements the core logic for detecting meaningful changes between
    versions of the same media item. It focuses on technical improvements that
    users care about, such as resolution upgrades, codec improvements, and
    audio enhancements.

    The detector is configurable to allow users to choose which types of changes
    trigger notifications, providing flexibility for different use cases.

    Attributes:
        config: Notifications configuration
        logger: Logger instance for change detection operations
        watch_changes: Dictionary of change types to monitor

    Example:
        ```python
        detector = ChangeDetector(config.notifications, logger)

        old_item = MediaItem(video_height=720, video_codec="h264")
        new_item = MediaItem(video_height=1080, video_codec="h264")

        changes = detector.detect_changes(old_item, new_item)
        # Returns: [{'type': 'resolution', 'old_value': 720, 'new_value': 1080, ...}]
        ```
    """

    def __init__(self, config: NotificationsConfig, logger: logging.Logger):
        """
        Initialize change detector with configuration and logging.

        Args:
            config: Notifications configuration including change monitoring settings
            logger: Logger instance for change detection operations
        """
        self.config = config
        self.logger = logger
        self.watch_changes = config.watch_changes

    def detect_changes(self, old_item: MediaItem, new_item: MediaItem) -> List[Dict[str, Any]]:
        """
        Detect meaningful changes between two versions of the same media item.

        This method compares technical specifications between old and new versions
        of a media item to identify upgrades worth notifying users about.

        Args:
            old_item: Previous version of the media item
            new_item: Current version of the media item

        Returns:
            List of change dictionaries, each containing:
            - type: Change category (resolution, codec, audio_codec, etc.)
            - field: Database field that changed
            - old_value: Previous value
            - new_value: Current value
            - description: Human-readable description of the change

        Example:
            ```python
            changes = detector.detect_changes(old_movie, new_movie)
            for change in changes:
                print(f"{change['type']}: {change['description']}")
            # Output: "resolution: Resolution changed from 720p to 1080p"
            ```

        Note:
            The method only detects changes that are enabled in the configuration.
            This allows users to customize which types of upgrades they want
            to be notified about.
        """
        changes = []

        try:
            # Resolution changes (most common upgrade scenario)
            if (self.watch_changes.get('resolution', True) and
                    old_item.video_height != new_item.video_height):
                changes.append({
                    'type': 'resolution',
                    'field': 'video_height',
                    'old_value': old_item.video_height,
                    'new_value': new_item.video_height,
                    'description': f"Resolution changed from {old_item.video_height}p to {new_item.video_height}p"
                })

            # Video codec changes (e.g., h264 -> hevc/av1)
            if (self.watch_changes.get('codec', True) and
                    old_item.video_codec != new_item.video_codec):
                changes.append({
                    'type': 'codec',
                    'field': 'video_codec',
                    'old_value': old_item.video_codec,
                    'new_value': new_item.video_codec,
                    'description': f"Video codec changed from {old_item.video_codec or 'Unknown'} to {new_item.video_codec or 'Unknown'}"
                })

            # Audio codec changes (e.g., ac3 -> dts, aac -> flac)
            if (self.watch_changes.get('audio_codec', True) and
                    old_item.audio_codec != new_item.audio_codec):
                changes.append({
                    'type': 'audio_codec',
                    'field': 'audio_codec',
                    'old_value': old_item.audio_codec,
                    'new_value': new_item.audio_codec,
                    'description': f"Audio codec changed from {old_item.audio_codec or 'Unknown'} to {new_item.audio_codec or 'Unknown'}"
                })

            # Audio channel changes (e.g., stereo -> 5.1 surround)
            if (self.watch_changes.get('audio_channels', True) and
                    old_item.audio_channels != new_item.audio_channels):
                # Create user-friendly channel descriptions
                channels_old = f"{old_item.audio_channels or 0} channel{'s' if (old_item.audio_channels or 0) != 1 else ''}"
                channels_new = f"{new_item.audio_channels or 0} channel{'s' if (new_item.audio_channels or 0) != 1 else ''}"
                changes.append({
                    'type': 'audio_channels',
                    'field': 'audio_channels',
                    'old_value': old_item.audio_channels,
                    'new_value': new_item.audio_channels,
                    'description': f"Audio channels changed from {channels_old} to {channels_new}"
                })

            # HDR status changes (SDR -> HDR10/Dolby Vision)
            if (self.watch_changes.get('hdr_status', True) and
                    old_item.video_range != new_item.video_range):
                changes.append({
                    'type': 'hdr_status',
                    'field': 'video_range',
                    'old_value': old_item.video_range,
                    'new_value': new_item.video_range,
                    'description': f"HDR status changed from {old_item.video_range or 'SDR'} to {new_item.video_range or 'SDR'}"
                })

            # File size changes (often indicates quality change)
            if (self.watch_changes.get('file_size', True) and
                    old_item.file_size != new_item.file_size):
                changes.append({
                    'type': 'file_size',
                    'field': 'file_size',
                    'old_value': old_item.file_size,
                    'new_value': new_item.file_size,
                    'description': "File size changed"
                })

            # Provider ID changes (metadata improvements)
            if self.watch_changes.get('provider_ids', True):
                # Check each provider ID separately
                for provider, old_val, new_val in [
                    ('imdb', old_item.imdb_id, new_item.imdb_id),
                    ('tmdb', old_item.tmdb_id, new_item.tmdb_id),
                    ('tvdb', old_item.tvdb_id, new_item.tvdb_id)
                ]:
                    # Only report if the value actually changed and isn't just None -> None
                    if old_val != new_val and (old_val or new_val):
                        changes.append({
                            'type': 'provider_ids',
                            'field': f'{provider}_id',
                            'old_value': old_val,
                            'new_value': new_val,
                            'description': f"{provider.upper()} ID changed from {old_val or 'None'} to {new_val or 'None'}"
                        })

            if changes:
                self.logger.debug(f"Detected {len(changes)} changes for item {new_item.item_id}")

        except Exception as e:
            self.logger.error(f"Error detecting changes for item {new_item.item_id}: {e}")

        return changes