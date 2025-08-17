#!/usr/bin/env python3
"""
Jellynouncer Database Models

This module contains the DatabaseItem class used for storing media items in the database.
DatabaseItem contains only the essential fields needed for change detection and identification,
reducing database size and improving sync performance.

When notifications are needed, DatabaseItem is converted to a full MediaItem and enriched
with additional metadata from the Jellyfin API.

Classes:
    DatabaseItem: Slim media representation for database storage

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


@dataclass
class DatabaseItem:
    """
    Slim media item representation for database storage.
    
    This class contains only the essential fields needed for:
    1. Unique identification (item_id, name, item_type)
    2. Change detection (technical specifications)
    3. Content routing (item_type, series info)
    
    All other metadata (provider IDs, images, descriptions, etc.) is fetched
    fresh from webhooks or API calls when notifications are generated.
    
    **Why DatabaseItem?**
    - Reduces database size by ~70% compared to full MediaItem
    - Speeds up sync operations (less data to serialize/deserialize)
    - Focuses on what matters: detecting quality upgrades
    - Clear separation between persisted and ephemeral data
    
    Attributes:
        item_id: Unique Jellyfin identifier (primary key)
        name: Display name of the item
        item_type: Media type (Movie, Episode, Series, etc.)
        
        # TV Series Data
        series_name: TV series name
        series_id: Parent series ID
        season_number: Season number
        episode_number: Episode number
        year: Release/air year
        
        # Video Specifications (for change detection)
        video_height: Resolution height in pixels
        video_width: Resolution width in pixels
        video_codec: Video codec (h264, hevc, av1, etc.)
        video_profile: Codec profile
        video_range: Video range (SDR, HDR10, Dolby Vision, etc.)
        video_framerate: Frames per second
        video_bitrate: Video bitrate
        video_bitdepth: Color bit depth
        
        # Audio Specifications (for change detection)
        audio_codec: Audio codec
        audio_channels: Number of audio channels
        audio_language: Primary audio language
        audio_bitrate: Audio bitrate
        audio_samplerate: Sample rate in Hz
        
        # Subtitle Information (for change detection)
        subtitle_count: Total number of subtitle tracks
        subtitle_languages: List of available languages
        subtitle_formats: List of subtitle formats
        
        # File Information
        file_path: File system path (for rename detection)
        file_size: File size in bytes
        library_name: Jellyfin library name
        
        # Internal Tracking
        content_hash: Hash for change detection
        timestamp_created: When this record was created
    """
    
    # ==================== CORE IDENTIFICATION ====================
    item_id: str
    name: str
    item_type: str
    
    # ==================== TV SERIES DATA ====================
    series_name: Optional[str] = None
    series_id: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    year: Optional[int] = None
    
    # ==================== VIDEO SPECIFICATIONS ====================
    video_height: Optional[int] = None
    video_width: Optional[int] = None
    video_codec: Optional[str] = None
    video_profile: Optional[str] = None
    video_range: Optional[str] = None
    video_framerate: Optional[float] = None
    video_bitrate: Optional[int] = None
    video_bitdepth: Optional[int] = None
    
    # ==================== AUDIO SPECIFICATIONS ====================
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None
    audio_language: Optional[str] = None
    audio_bitrate: Optional[int] = None
    audio_samplerate: Optional[int] = None
    
    # ==================== SUBTITLE INFORMATION ====================
    subtitle_count: Optional[int] = None
    subtitle_languages: List[str] = field(default_factory=list)
    subtitle_formats: List[str] = field(default_factory=list)
    
    # ==================== FILE INFORMATION ====================
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    library_name: Optional[str] = None
    
    # ==================== INTERNAL TRACKING ====================
    content_hash: str = field(default="", init=False)
    timestamp_created: str = field(default="", init=False)
    
    def __post_init__(self) -> None:
        """Initialize timestamp and generate content hash after dataclass construction."""
        if not self.timestamp_created:
            self.timestamp_created = datetime.now(timezone.utc).isoformat()
        
        # Generate content hash immediately
        if not self.content_hash:
            self.content_hash = self._generate_content_hash()
    
    def _generate_content_hash(self) -> str:
        """
        Generate content hash for change detection.
        
        Uses the same hashing algorithm as MediaItem to ensure compatibility
        when comparing DatabaseItem from database with MediaItem from webhook.
        
        Returns:
            str: Blake2b hash of technical specifications
        """
        # Same hash data structure as MediaItem for compatibility
        hash_data = {
            # Core identification
            "name": self.name,
            "item_type": self.item_type,
            
            # Video specifications
            "video_height": self.video_height,
            "video_width": self.video_width,
            "video_codec": self.video_codec,
            "video_profile": self.video_profile,
            "video_range": self.video_range,
            "video_framerate": self.video_framerate,
            "video_bitrate": self.video_bitrate,
            "video_bitdepth": self.video_bitdepth,
            
            # Audio specifications
            "audio_codec": self.audio_codec,
            "audio_channels": self.audio_channels,
            "audio_bitrate": self.audio_bitrate,
            "audio_samplerate": self.audio_samplerate,
            
            # Subtitle information
            "subtitle_count": self.subtitle_count,
            "subtitle_languages": sorted(self.subtitle_languages) if self.subtitle_languages else [],
            "subtitle_formats": sorted(self.subtitle_formats) if self.subtitle_formats else [],
            
            # File size
            "file_size": self.file_size,
        }
        
        # Use Blake2b for faster hashing
        hash_string = json.dumps(hash_data, sort_keys=True, default=str)
        return hashlib.blake2b(
            hash_string.encode('utf-8'), 
            digest_size=32
        ).hexdigest()
    
    @classmethod
    def from_media_item(cls, media_item) -> 'DatabaseItem':
        """
        Create a DatabaseItem from a full MediaItem.
        
        This extracts only the fields needed for database storage and
        change detection, discarding all metadata that can be fetched
        on-demand.
        
        Args:
            media_item: Full MediaItem instance
            
        Returns:
            DatabaseItem: Slim version for database storage
            
        Example:
            ```python
            full_item = MediaItem(...)  # Full item from webhook
            db_item = DatabaseItem.from_media_item(full_item)
            # db_item now contains only change detection fields
            ```
        """
        return cls(
            # Core identification
            item_id=media_item.item_id,
            name=media_item.name,
            item_type=media_item.item_type,
            
            # TV series data
            series_name=getattr(media_item, 'series_name', None),
            series_id=getattr(media_item, 'series_id', None),
            season_number=getattr(media_item, 'season_number', None),
            episode_number=getattr(media_item, 'episode_number', None),
            year=getattr(media_item, 'year', None),
            
            # Video specifications
            video_height=getattr(media_item, 'video_height', None),
            video_width=getattr(media_item, 'video_width', None),
            video_codec=getattr(media_item, 'video_codec', None),
            video_profile=getattr(media_item, 'video_profile', None),
            video_range=getattr(media_item, 'video_range', None),
            video_framerate=getattr(media_item, 'video_framerate', None),
            video_bitrate=getattr(media_item, 'video_bitrate', None),
            video_bitdepth=getattr(media_item, 'video_bitdepth', None),
            
            # Audio specifications
            audio_codec=getattr(media_item, 'audio_codec', None),
            audio_channels=getattr(media_item, 'audio_channels', None),
            audio_language=getattr(media_item, 'audio_language', None),
            audio_bitrate=getattr(media_item, 'audio_bitrate', None),
            audio_samplerate=getattr(media_item, 'audio_samplerate', None),
            
            # Subtitle information
            subtitle_count=getattr(media_item, 'subtitle_count', None),
            subtitle_languages=getattr(media_item, 'subtitle_languages', []),
            subtitle_formats=getattr(media_item, 'subtitle_formats', []),
            
            # File information
            file_path=getattr(media_item, 'file_path', None),
            file_size=getattr(media_item, 'file_size', None),
            library_name=getattr(media_item, 'library_name', None),
        )
    
    def to_media_item(self, **additional_fields):
        """
        Convert DatabaseItem to a MediaItem with optional additional fields.
        
        This creates a MediaItem instance with the core fields from the database,
        ready to be enriched with additional metadata from the API.
        
        Args:
            **additional_fields: Any additional fields to set on the MediaItem
            
        Returns:
            MediaItem: Full media item ready for enrichment
            
        Example:
            ```python
            db_item = DatabaseItem(...)
            media_item = db_item.to_media_item(
                imdb_id="tt0133093",
                overview="A computer hacker learns..."
            )
            ```
        """
        from .media_models import MediaItem
        
        # Start with our database fields
        fields = {
            'item_id': self.item_id,
            'name': self.name,
            'item_type': self.item_type,
            'series_name': self.series_name,
            'series_id': self.series_id,
            'season_number': self.season_number,
            'episode_number': self.episode_number,
            'year': self.year,
            'video_height': self.video_height,
            'video_width': self.video_width,
            'video_codec': self.video_codec,
            'video_profile': self.video_profile,
            'video_range': self.video_range,
            'video_framerate': self.video_framerate,
            'video_bitrate': self.video_bitrate,
            'video_bitdepth': self.video_bitdepth,
            'audio_codec': self.audio_codec,
            'audio_channels': self.audio_channels,
            'audio_language': self.audio_language,
            'audio_bitrate': self.audio_bitrate,
            'audio_samplerate': self.audio_samplerate,
            'subtitle_count': self.subtitle_count,
            'subtitle_languages': self.subtitle_languages,
            'subtitle_formats': self.subtitle_formats,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'library_name': self.library_name,
        }
        
        # Add any additional fields provided
        fields.update(additional_fields)
        
        return MediaItem(**fields)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for database storage.
        
        Returns:
            dict: All fields as a dictionary
        """
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DatabaseItem':
        """
        Create DatabaseItem from dictionary (database row).
        
        Args:
            data: Dictionary with field values
            
        Returns:
            DatabaseItem: Reconstructed instance
        """
        # Remove computed fields that shouldn't be passed to __init__
        content_hash = data.pop('content_hash', None)
        timestamp_created = data.pop('timestamp_created', None)
        
        # Create instance
        instance = cls(**data)
        
        # Set computed fields directly
        if content_hash:
            instance.content_hash = content_hash
        if timestamp_created:
            instance.timestamp_created = timestamp_created
            
        return instance