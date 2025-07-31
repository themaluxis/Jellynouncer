# Jellyfin Discord Webhook Service
# A comprehensive intermediate webhook service for Jellyfin to Discord notifications

import os
import json
import sqlite3
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml
import aiohttp
import aiosqlite
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from jellyfin_apiclient_python import JellyfinClient
from jinja2 import Environment, FileSystemLoader, Template
from pydantic import BaseModel
import uvicorn


# Configuration and Models
@dataclass
class MediaItem:
    """Represents a media item with all its metadata"""
    item_id: str
    name: str
    item_type: str
    year: Optional[int] = None
    series_name: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    overview: Optional[str] = None
    
    # Video properties
    video_height: Optional[int] = None
    video_width: Optional[int] = None
    video_codec: Optional[str] = None
    video_profile: Optional[str] = None
    video_range: Optional[str] = None
    video_framerate: Optional[float] = None
    aspect_ratio: Optional[str] = None
    
    # Audio properties
    audio_codec: Optional[str] = None
    audio_channels: Optional[int] = None
    audio_language: Optional[str] = None
    audio_bitrate: Optional[int] = None
    
    # Provider IDs
    imdb_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None
    
    # Metadata
    timestamp: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class WebhookPayload(BaseModel):
    """Expected webhook payload from Jellyfin"""
    ItemId: str
    Name: str
    ItemType: str
    Year: Optional[int] = None
    SeriesName: Optional[str] = None
    SeasonNumber00: Optional[str] = None
    EpisodeNumber00: Optional[str] = None
    Overview: Optional[str] = None
    
    # Video info
    Video_0_Height: Optional[int] = None
    Video_0_Width: Optional[int] = None
    Video_0_Codec: Optional[str] = None
    Video_0_Profile: Optional[str] = None
    Video_0_VideoRange: Optional[str] = None
    Video_0_FrameRate: Optional[float] = None
    Video_0_AspectRatio: Optional[str] = None
    
    # Audio info
    Audio_0_Codec: Optional[str] = None
    Audio_0_Channels: Optional[int] = None
    Audio_0_Language: Optional[str] = None
    Audio_0_Bitrate: Optional[int] = None
    
    # Provider IDs
    Provider_imdb: Optional[str] = None
    Provider_tmdb: Optional[str] = None
    Provider_tvdb: Optional[str] = None


class Config:
    """Configuration manager"""
    
    def __init__(self, config_path: str = "/app/config/config.json"):
        self.config_path = config_path
        self.config = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file with environment variable overrides"""
        default_config = {
            "jellyfin": {
                "server_url": os.getenv("JELLYFIN_SERVER_URL"),
                "api_key": os.getenv("JELLYFIN_API_KEY"),
                "user_id": os.getenv("JELLYFIN_USER_ID"),
                "client_name": "JellyNotify",
                "client_version": "1.0.0",
                "device_name": "jellynotify-webhook",
                "device_id": "jellynotify-discord-webhook-001"
            },
            "discord": {
                # Single/fallback webhook
                "webhook_url": os.getenv("DISCORD_WEBHOOK_URL"),
                # Multi-webhook configuration
                "webhooks": {
                    "default": {
                        "url": os.getenv("DISCORD_WEBHOOK_URL"),
                        "name": "General",
                        "enabled": True,
                        "grouping": {
                            "mode": "none",
                            "delay_minutes": 5,
                            "max_items": 25
                        }
                    },
                    "movies": {
                        "url": os.getenv("DISCORD_WEBHOOK_URL_MOVIES"),
                        "name": "Movies",
                        "enabled": False,
                        "grouping": {
                            "mode": "none",
                            "delay_minutes": 5,
                            "max_items": 25
                        }
                    },
                    "tv": {
                        "url": os.getenv("DISCORD_WEBHOOK_URL_TV"),
                        "name": "TV Shows",
                        "enabled": False,
                        "grouping": {
                            "mode": "none",
                            "delay_minutes": 5,
                            "max_items": 25
                        }
                    },
                    "music": {
                        "url": os.getenv("DISCORD_WEBHOOK_URL_MUSIC"),
                        "name": "Music",
                        "enabled": False,
                        "grouping": {
                            "mode": "none",
                            "delay_minutes": 5,
                            "max_items": 25
                        }
                    }
                },
                "routing": {
                    "enabled": False,
                    "movie_types": ["Movie"],
                    "tv_types": ["Episode", "Season", "Series"],
                    "music_types": ["Audio", "MusicAlbum", "MusicArtist"],
                    "fallback_webhook": "default"
                },
                "rate_limit": {
                    "requests_per_period": 5,
                    "period_seconds": 2,
                    "channel_limit_per_minute": 30
                }
            },
            "database": {
                "path": "/app/data/jellyfin_items.db",
                "wal_mode": True,
                "vacuum_interval_hours": 24
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
            },
            "notifications": {
                "watch_changes": {
                    "resolution": True,
                    "codec": True,
                    "audio_codec": True,
                    "audio_channels": True,
                    "hdr_status": True,
                    "file_size": True,
                    "provider_ids": True
                },
                "colors": {
                    "new_item": 65280,
                    "resolution_upgrade": 16766720,
                    "codec_upgrade": 16747520,
                    "audio_upgrade": 9662683,
                    "hdr_upgrade": 16716947,
                    "provider_update": 2003199
                }
            },
            "server": {
                "host": "0.0.0.0",
                "port": 8080,
                "log_level": "INFO"
            },
            "sync": {
                "startup_sync": True,
                "sync_batch_size": 100,
                "api_request_delay": 0.1
            }
        }
        
        # Load from file if exists
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    file_config = json.load(f)
                    self._deep_update(default_config, file_config)
            except Exception as e:
                logging.error(f"Error loading config file: {e}")
        
        return default_config
    
    def _deep_update(self, base_dict: Dict, update_dict: Dict):
        """Recursively update nested dictionary"""
        for key, value in update_dict.items():
            if key in base_dict and isinstance(base_dict[key], dict) and isinstance(value, dict):
                self._deep_update(base_dict[key], value)
            else:
                base_dict[key] = value
    
    def get(self, key_path: str, default=None):
        """Get config value using dot notation (e.g., 'jellyfin.server_url')"""
        keys = key_path.split('.')
        value = self.config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value


class DatabaseManager:
    """SQLite database manager with WAL mode support"""
    
    def __init__(self, db_path: str, wal_mode: bool = True):
        self.db_path = db_path
        self.wal_mode = wal_mode
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
    async def initialize(self):
        """Initialize database and create tables"""
        async with aiosqlite.connect(self.db_path) as db:
            if self.wal_mode:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA synchronous=NORMAL")
                await db.execute("PRAGMA temp_store=memory")
                await db.execute("PRAGMA mmap_size=268435456")  # 256MB
            
            # Create media_items table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS media_items (
                    item_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    year INTEGER,
                    series_name TEXT,
                    season_number INTEGER,
                    episode_number INTEGER,
                    overview TEXT,
                    video_height INTEGER,
                    video_width INTEGER,
                    video_codec TEXT,
                    video_profile TEXT,
                    video_range TEXT,
                    video_framerate REAL,
                    aspect_ratio TEXT,
                    audio_codec TEXT,
                    audio_channels INTEGER,
                    audio_language TEXT,
                    audio_bitrate INTEGER,
                    imdb_id TEXT,
                    tmdb_id TEXT,
                    tvdb_id TEXT,
                    timestamp TEXT,
                    file_path TEXT,
                    file_size INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_item_type ON media_items(item_type)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_series_name ON media_items(series_name)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_updated_at ON media_items(updated_at)")
            
            await db.commit()
    
    async def get_item(self, item_id: str) -> Optional[MediaItem]:
        """Get media item by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM media_items WHERE item_id = ?", (item_id,)
            )
            row = await cursor.fetchone()
            
            if row:
                return MediaItem(**dict(row))
            return None
    
    async def save_item(self, item: MediaItem) -> bool:
        """Save or update media item"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Convert dataclass to dict
                item_dict = asdict(item)
                item_dict['updated_at'] = datetime.now(timezone.utc).isoformat()
                
                # Prepare SQL
                columns = list(item_dict.keys())
                placeholders = ['?' for _ in columns]
                values = list(item_dict.values())
                
                sql = f"""
                    INSERT OR REPLACE INTO media_items 
                    ({', '.join(columns)}) 
                    VALUES ({', '.join(placeholders)})
                """
                
                await db.execute(sql, values)
                await db.commit()
                
                return True
        except Exception as e:
            logging.error(f"Error saving item {item.item_id}: {e}")
            return False

    async def get_all_items(self) -> List[MediaItem]:
        """Get all media items"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM media_items ORDER BY updated_at DESC")
            rows = await cursor.fetchall()

            return [MediaItem(**dict(row)) for row in rows]
    
    async def vacuum_database(self):
        """Vacuum database for maintenance"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("VACUUM")
                await db.commit()
                logging.info("Database vacuum completed")
        except Exception as e:
            logging.error(f"Error during database vacuum: {e}")


class JellyfinAPI:
    """Jellyfin API client wrapper"""
    
    def __init__(self, config: Config):
        self.config = config
        self.client = None
        self.last_connection_check = 0
        self.connection_check_interval = 60  # seconds

    async def connect(self) -> bool:
        """Connect to Jellyfin server"""
        try:
            self.client = JellyfinClient()

            # Configure client
            self.client.config.app(
                self.config.get('jellyfin.client_name'),
                self.config.get('jellyfin.client_version'),
                self.config.get('jellyfin.device_name'),
                self.config.get('jellyfin.device_id')
            )

            server_url = self.config.get('jellyfin.server_url')
            api_key = self.config.get('jellyfin.api_key')
            user_id = self.config.get('jellyfin.user_id')

            if not server_url or not api_key or not user_id:
                missing = []
                if not server_url: missing.append("server_url")
                if not api_key: missing.append("api_key")
                if not user_id: missing.append("user_id")
                raise ValueError(f"Missing required Jellyfin configuration: {', '.join(missing)}")

            # Remove trailing slash from server URL if present
            if server_url.endswith('/'):
                server_url = server_url[:-1]

            # Use API key authentication
            self.client.config.data["auth.ssl"] = server_url.startswith('https')
            self.client.config.data["auth.user_id"] = user_id  # Add this line

            credentials = {
                "Servers": [{
                    "AccessToken": api_key,
                    "userId": user_id,  # Add this line
                    "address": server_url,
                    "Id": "jellyfin-webhook-service"
                }]
            }

            self.client.authenticate(credentials, discover=False)

            # Test connection
            response = self.client.jellyfin.get_system_info()
            if response:
                logging.info(f"Connected to Jellyfin server: {response.get('ServerName', 'Unknown')}")
                return True

            return False

        except Exception as e:
            logging.error(f"Failed to connect to Jellyfin: {e}")
            return False
    
    async def is_connected(self) -> bool:
        """Check if connected to Jellyfin"""
        current_time = time.time()
        
        # Only check connection every minute to avoid spam
        if current_time - self.last_connection_check < self.connection_check_interval:
            return self.client is not None
        
        self.last_connection_check = current_time
        
        if not self.client:
            return False
        
        try:
            response = self.client.jellyfin.get_system_info()
            return response is not None
        except Exception:
            return False

    async def get_all_items(self, batch_size: int = 100) -> List[Dict[str, Any]]:
        """Get all media items from Jellyfin"""
        if not await self.is_connected():
            if not await self.connect():
                raise Exception("Cannot connect to Jellyfin server")

        all_items = []
        start_index = 0
        user_id = self.config.get('jellyfin.user_id')

        while True:
            try:
                response = self.client.jellyfin.get_items(
                    userId=user_id,  # Add this line
                    parent_id=None,
                    start_index=start_index,
                    limit=batch_size,
                    include_item_types="Movie,Series,Season,Episode",
                    fields="Overview,MediaStreams,ProviderIds,Path,MediaSources"
                )

                if not response or 'Items' not in response:
                    break

                items = response['Items']
                if not items:
                    break

                all_items.extend(items)
                start_index += len(items)

                # Respect API rate limits
                await asyncio.sleep(self.config.get('sync.api_request_delay', 0.1))

                logging.info(f"Fetched {len(all_items)} items from Jellyfin...")

            except Exception as e:
                logging.error(f"Error fetching items from Jellyfin: {e}")
                break

        return all_items
    
    def extract_media_item(self, jellyfin_item: Dict[str, Any]) -> MediaItem:
        """Extract MediaItem from Jellyfin API response"""
        # Get media streams
        media_streams = jellyfin_item.get('MediaStreams', [])
        video_stream = next((s for s in media_streams if s.get('Type') == 'Video'), {})
        audio_stream = next((s for s in media_streams if s.get('Type') == 'Audio'), {})
        
        # Get provider IDs
        provider_ids = jellyfin_item.get('ProviderIds', {})
        
        return MediaItem(
            item_id=jellyfin_item['Id'],
            name=jellyfin_item.get('Name', ''),
            item_type=jellyfin_item.get('Type', ''),
            year=jellyfin_item.get('ProductionYear'),
            series_name=jellyfin_item.get('SeriesName'),
            season_number=jellyfin_item.get('IndexNumber') if jellyfin_item.get('Type') == 'Season' else None,
            episode_number=jellyfin_item.get('IndexNumber') if jellyfin_item.get('Type') == 'Episode' else None,
            overview=jellyfin_item.get('Overview'),
            
            # Video properties
            video_height=video_stream.get('Height'),
            video_width=video_stream.get('Width'),
            video_codec=video_stream.get('Codec'),
            video_profile=video_stream.get('Profile'),
            video_range=video_stream.get('VideoRange'),
            video_framerate=video_stream.get('RealFrameRate'),
            aspect_ratio=video_stream.get('AspectRatio'),
            
            # Audio properties
            audio_codec=audio_stream.get('Codec'),
            audio_channels=audio_stream.get('Channels'),
            audio_language=audio_stream.get('Language'),
            audio_bitrate=audio_stream.get('BitRate'),
            
            # Provider IDs
            imdb_id=provider_ids.get('Imdb'),
            tmdb_id=provider_ids.get('Tmdb'),
            tvdb_id=provider_ids.get('Tvdb'),
            
            # File info
            file_path=jellyfin_item.get('Path'),
            file_size=jellyfin_item.get('Size')
        )


class ChangeDetector:
    """Detects changes between media items"""
    
    def __init__(self, config: Config):
        self.config = config
        self.watch_changes = config.get('notifications.watch_changes', {})
    
    def detect_changes(self, old_item: MediaItem, new_item: MediaItem) -> List[Dict[str, Any]]:
        """Detect changes between two media items"""
        changes = []
        
        # Resolution changes
        if (self.watch_changes.get('resolution', True) and 
            old_item.video_height != new_item.video_height):
            changes.append({
                'type': 'resolution',
                'field': 'video_height',
                'old_value': old_item.video_height,
                'new_value': new_item.video_height,
                'description': f"Resolution changed from {old_item.video_height}p to {new_item.video_height}p"
            })
        
        # Codec changes
        if (self.watch_changes.get('codec', True) and 
            old_item.video_codec != new_item.video_codec):
            changes.append({
                'type': 'codec',
                'field': 'video_codec',
                'old_value': old_item.video_codec,
                'new_value': new_item.video_codec,
                'description': f"Video codec changed from {old_item.video_codec or 'Unknown'} to {new_item.video_codec or 'Unknown'}"
            })
        
        # Audio codec changes
        if (self.watch_changes.get('audio_codec', True) and 
            old_item.audio_codec != new_item.audio_codec):
            changes.append({
                'type': 'audio_codec',
                'field': 'audio_codec',
                'old_value': old_item.audio_codec,
                'new_value': new_item.audio_codec,
                'description': f"Audio codec changed from {old_item.audio_codec or 'Unknown'} to {new_item.audio_codec or 'Unknown'}"
            })
        
        # Audio channels changes
        if (self.watch_changes.get('audio_channels', True) and 
            old_item.audio_channels != new_item.audio_channels):
            channels_old = f"{old_item.audio_channels or 0} channel{'s' if (old_item.audio_channels or 0) != 1 else ''}"
            channels_new = f"{new_item.audio_channels or 0} channel{'s' if (new_item.audio_channels or 0) != 1 else ''}"
            changes.append({
                'type': 'audio_channels',
                'field': 'audio_channels',
                'old_value': old_item.audio_channels,
                'new_value': new_item.audio_channels,
                'description': f"Audio channels changed from {channels_old} to {channels_new}"
            })
        
        # HDR status changes
        if (self.watch_changes.get('hdr_status', True) and 
            old_item.video_range != new_item.video_range):
            changes.append({
                'type': 'hdr_status',
                'field': 'video_range',
                'old_value': old_item.video_range,
                'new_value': new_item.video_range,
                'description': f"HDR status changed from {old_item.video_range or 'SDR'} to {new_item.video_range or 'SDR'}"
            })
        
        # File size changes
        if (self.watch_changes.get('file_size', True) and 
            old_item.file_size != new_item.file_size):
            changes.append({
                'type': 'file_size',
                'field': 'file_size',
                'old_value': old_item.file_size,
                'new_value': new_item.file_size,
                'description': f"File size changed"
            })
        
        # Provider ID changes
        if self.watch_changes.get('provider_ids', True):
            for provider, old_val, new_val in [
                ('imdb', old_item.imdb_id, new_item.imdb_id),
                ('tmdb', old_item.tmdb_id, new_item.tmdb_id),
                ('tvdb', old_item.tvdb_id, new_item.tvdb_id)
            ]:
                if old_val != new_val and (old_val or new_val):
                    changes.append({
                        'type': 'provider_ids',
                        'field': f'{provider}_id',
                        'old_value': old_val,
                        'new_value': new_val,
                        'description': f"{provider.upper()} ID changed from {old_val or 'None'} to {new_val or 'None'}"
                    })
        
        return changes


class NotificationQueue:
    """
    Manages grouped notifications with time-based batching.
    Groups items based on webhook, item type, and event type.
    """

    def __init__(self, discord_notifier):
        self.discord_notifier = discord_notifier
        self.queues = {}  # Keyed by webhook_name
        self.timers = {}  # Keyed by webhook_name
        self.processing_locks = {}  # Prevent concurrent queue processing
        
    async def add_item(self, webhook_name: str, item, changes: List[Dict] = None, is_new: bool = True):
        """
        Add an item to the appropriate queue based on webhook and grouping settings.
        """
        # Get webhook configuration
        webhook_config = self.discord_notifier.webhooks.get(webhook_name, {})
        grouping_config = webhook_config.get('grouping', {})
        grouping_mode = grouping_config.get('mode', 'none')
        
        # If no grouping, send immediately
        if grouping_mode == 'none':
            await self.discord_notifier.send_notification(item, changes, is_new)
            return
            
        # Create queue for this webhook if it doesn't exist
        if webhook_name not in self.queues:
            self.queues[webhook_name] = {
                'new': {
                    'movies': [],
                    'tv': [],
                    'music': [],
                    'other': []
                },
                'upgraded': {
                    'movies': [],
                    'tv': [],
                    'music': [],
                    'other': []
                },
                'last_added': time.time(),
                'timer_started': False
            }
            self.processing_locks[webhook_name] = asyncio.Lock()
            
        # Determine category based on item type
        item_type = item.item_type
        routing_config = self.discord_notifier.routing_config
        
        if item_type in routing_config.get('movie_types', ['Movie']):
            category = 'movies'
        elif item_type in routing_config.get('tv_types', ['Episode', 'Season', 'Series']):
            category = 'tv'
        elif item_type in routing_config.get('music_types', ['Audio', 'MusicAlbum', 'MusicArtist']):
            category = 'music'
        else:
            category = 'other'
            
        # Add to appropriate queue
        event_type = 'new' if is_new else 'upgraded'
        item_data = {
            'item': item,
            'changes': changes or [],
            'is_new': is_new,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        self.queues[webhook_name][event_type][category].append(item_data)
        self.queues[webhook_name]['last_added'] = time.time()
        
        # Start timer if not already running
        if not self.queues[webhook_name]['timer_started']:
            self.queues[webhook_name]['timer_started'] = True
            delay_minutes = grouping_config.get('delay_minutes', 5)
            self.timers[webhook_name] = asyncio.create_task(
                self._process_queue_after_delay(webhook_name, delay_minutes * 60)
            )
            
        # Check if we've reached max items and should send now
        max_items = grouping_config.get('max_items', 25)
        total_items = sum(len(items) for event in self.queues[webhook_name].values() 
                        if isinstance(event, dict) 
                        for items in event.values())
        
        if total_items >= max_items:
            # Cancel existing timer
            if webhook_name in self.timers and not self.timers[webhook_name].done():
                self.timers[webhook_name].cancel()
                
            # Process queue immediately
            asyncio.create_task(self._process_queue(webhook_name))
            
    async def _process_queue_after_delay(self, webhook_name: str, delay_seconds: int):
        """Wait for the specified delay, then process the queue."""
        try:
            await asyncio.sleep(delay_seconds)
            await self._process_queue(webhook_name)
        except asyncio.CancelledError:
            # Timer was cancelled, probably because queue reached max items
            pass
        except Exception as e:
            logging.error(f"Error in notification queue timer for {webhook_name}: {e}")
            
    async def _process_queue(self, webhook_name: str):
        """Process the queue for a specific webhook."""
        # Use a lock to prevent concurrent processing
        async with self.processing_locks[webhook_name]:
            try:
                # Skip if queue is empty
                if webhook_name not in self.queues:
                    return
                    
                # Get queue data
                queue = self.queues[webhook_name]
                
                # Skip if all queues are empty
                total_items = sum(len(items) for event in queue.values() 
                              if isinstance(event, dict) 
                              for items in event.values())
                              
                if total_items == 0:
                    self.queues[webhook_name]['timer_started'] = False
                    return
                
                # Get webhook config
                webhook_config = self.discord_notifier.webhooks.get(webhook_name, {})
                grouping_mode = webhook_config.get('grouping', {}).get('mode', 'none')
                
                # Send notifications based on grouping mode
                if grouping_mode == 'item_type':
                    await self._send_by_item_type(webhook_name, queue)
                elif grouping_mode == 'event_type':
                    await self._send_by_event_type(webhook_name, queue)
                elif grouping_mode == 'both':
                    await self._send_grouped(webhook_name, queue)
                    
                # Clear queue and reset timer
                self.queues[webhook_name] = {
                    'new': {
                        'movies': [],
                        'tv': [],
                        'music': [],
                        'other': []
                    },
                    'upgraded': {
                        'movies': [],
                        'tv': [],
                        'music': [],
                        'other': []
                    },
                    'last_added': time.time(),
                    'timer_started': False
                }
                
            except Exception as e:
                logging.error(f"Error processing notification queue for {webhook_name}: {e}")
                # Reset timer status so future items can start a new timer
                if webhook_name in self.queues:
                    self.queues[webhook_name]['timer_started'] = False
                    
    async def _send_by_item_type(self, webhook_name: str, queue: Dict):
        """Send notifications grouped by item type."""
        # Combine new and upgraded items by category
        categories = ['movies', 'tv', 'music', 'other']
        
        for category in categories:
            new_items = queue['new'][category]
            upgraded_items = queue['upgraded'][category]
            
            # Skip empty categories
            if not new_items and not upgraded_items:
                continue
                
            # Prepare data for template
            template_data = {
                'category': category,
                'new_items': new_items,
                'upgraded_items': upgraded_items,
                'has_new': bool(new_items),
                'has_upgraded': bool(upgraded_items),
                'total_items': len(new_items) + len(upgraded_items),
                'jellyfin_url': self.discord_notifier.config.get('jellyfin.server_url'),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'webhook_name': webhook_name
            }
            
            # Determine template to use
            template_name = self.discord_notifier.config.get('templates.new_items_by_type_template')
            
            # Render and send
            await self.discord_notifier.send_grouped_notification(
                webhook_name, template_name, template_data
            )
            
    async def _send_by_event_type(self, webhook_name: str, queue: Dict):
        """Send notifications grouped by event type (new vs upgraded)."""
        # Process new items if any
        new_items = []
        for category in ['movies', 'tv', 'music', 'other']:
            new_items.extend(queue['new'][category])
            
        if new_items:
            template_data = {
                'items': new_items,
                'is_new': True,
                'total_items': len(new_items),
                'jellyfin_url': self.discord_notifier.config.get('jellyfin.server_url'),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'webhook_name': webhook_name
            }
            
            template_name = self.discord_notifier.config.get('templates.new_items_by_event_template')
            await self.discord_notifier.send_grouped_notification(
                webhook_name, template_name, template_data
            )
            
        # Process upgraded items if any
        upgraded_items = []
        for category in ['movies', 'tv', 'music', 'other']:
            upgraded_items.extend(queue['upgraded'][category])
            
        if upgraded_items:
            template_data = {
                'items': upgraded_items,
                'is_new': False,
                'total_items': len(upgraded_items),
                'jellyfin_url': self.discord_notifier.config.get('jellyfin.server_url'),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'webhook_name': webhook_name
            }
            
            template_name = self.discord_notifier.config.get('templates.upgraded_items_by_event_template')
            await self.discord_notifier.send_grouped_notification(
                webhook_name, template_name, template_data
            )
            
    async def _send_grouped(self, webhook_name: str, queue: Dict):
        """Send notifications grouped by both item type and event type."""
        categories = ['movies', 'tv', 'music', 'other']
        event_types = ['new', 'upgraded']
        
        # Check if we have any items
        total_items = sum(len(queue[event][category]) 
                        for event in event_types 
                        for category in categories)
                        
        if total_items == 0:
            return
            
        # Prepare data structure
        grouped_data = {
            'categories': {},
            'total_items': total_items,
            'jellyfin_url': self.discord_notifier.config.get('jellyfin.server_url'),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'webhook_name': webhook_name
        }
        
        # Build category data
        for category in categories:
            category_items = {
                'new': queue['new'][category],
                'upgraded': queue['upgraded'][category],
                'total_items': len(queue['new'][category]) + len(queue['upgraded'][category])
            }
            
            # Only include non-empty categories
            if category_items['total_items'] > 0:
                grouped_data['categories'][category] = category_items
                
        # Determine if we have new or upgraded items
        has_new = any(len(queue['new'][category]) > 0 for category in categories)
        has_upgraded = any(len(queue['upgraded'][category]) > 0 for category in categories)
        
        # Select template based on item types present
        if has_new and has_upgraded:
            template_name = self.discord_notifier.config.get('templates.new_items_grouped_template')
        elif has_new:
            template_name = self.discord_notifier.config.get('templates.new_items_by_type_template')
        else:
            template_name = self.discord_notifier.config.get('templates.upgraded_items_by_type_template')
            
        # Send the notification
        await self.discord_notifier.send_grouped_notification(
            webhook_name, template_name, grouped_data
        )
        
    def get_queue_status(self) -> Dict[str, Any]:
        """Get status of all notification queues."""
        status = {}
        
        for webhook_name, queue in self.queues.items():
            # Count items in each category
            new_count = sum(len(items) for items in queue['new'].values())
            upgraded_count = sum(len(items) for items in queue['upgraded'].values())
            
            # Get time since last item was added
            time_since_last = time.time() - queue['last_added']
            
            status[webhook_name] = {
                'total_items': new_count + upgraded_count,
                'new_items': new_count,
                'upgraded_items': upgraded_count,
                'timer_active': queue['timer_started'],
                'seconds_since_last_item': round(time_since_last, 1)
            }
            
        return status


class DiscordNotifier:
    """Discord webhook notification sender with multi-webhook and grouping support"""
    
    def __init__(self, config: Config):
        self.config = config
        self.routing_enabled = config.get('discord.routing.enabled', False)
        self.webhooks = config.get('discord.webhooks', {})
        self.routing_config = config.get('discord.routing', {})
        self.rate_limit = config.get('discord.rate_limit', {})
        
        # Per-webhook rate limiting tracking
        self.webhook_rate_limits = {}
        self.session = None
        
        # Initialize notification queue
        self.notification_queue = NotificationQueue(self)
        
        # Initialize Jinja2 templates
        self.template_env = Environment(
            loader=FileSystemLoader(config.get('templates.directory', '/app/templates')),
            autoescape=True
        )
        
        # Validate webhook configuration
        self._validate_webhook_config()
    
    def _validate_webhook_config(self):
        """Validate webhook configuration and set up fallbacks"""
        # Backwards compatibility: if old webhook_url is set but no webhooks config
        legacy_webhook = self.config.get('discord.webhook_url')
        if legacy_webhook and not self.webhooks:
            self.webhooks = {
                "default": {
                    "url": legacy_webhook,
                    "name": "General",
                    "enabled": True,
                    "grouping": {
                        "mode": "none",
                        "delay_minutes": 5,
                        "max_items": 25
                    }
                }
            }
            logging.info("Using legacy webhook URL configuration")
        
        # Ensure we have at least one enabled webhook
        enabled_webhooks = [name for name, webhook in self.webhooks.items() 
                          if webhook.get('enabled', False) and webhook.get('url')]
        
        if not enabled_webhooks:
            logging.error("No enabled Discord webhooks configured!")
            # Try to enable default if it has a URL
            if 'default' in self.webhooks and self.webhooks['default'].get('url'):
                self.webhooks['default']['enabled'] = True
                logging.warning("Enabled default webhook as fallback")
        
        # Initialize rate limiting for each webhook
        for webhook_name, webhook_config in self.webhooks.items():
            if webhook_config.get('enabled', False):
                self.webhook_rate_limits[webhook_name] = {
                    'last_request_time': 0,
                    'request_count': 0
                }
                
            # Ensure grouping settings exist
            if 'grouping' not in webhook_config:
                webhook_config['grouping'] = {
                    "mode": "none",
                    "delay_minutes": 5,
                    "max_items": 25
                }
    
    def _get_webhook_for_item(self, item: MediaItem) -> Optional[Dict[str, Any]]:
        """Determine which webhook to use for a given item"""
        if not self.routing_enabled:
            # Use default webhook or first enabled webhook
            for webhook_name, webhook_config in self.webhooks.items():
                if webhook_config.get('enabled', False) and webhook_config.get('url'):
                    return {
                        'name': webhook_name,
                        'config': webhook_config
                    }
            return None
        
        # Routing is enabled - determine based on item type
        item_type = item.item_type
        movie_types = self.routing_config.get('movie_types', ['Movie'])
        tv_types = self.routing_config.get('tv_types', ['Episode', 'Season', 'Series'])
        music_types = self.routing_config.get('music_types', ['Audio', 'MusicAlbum', 'MusicArtist'])
        fallback_webhook = self.routing_config.get('fallback_webhook', 'default')
        
        target_webhook = None
        
        if item_type in movie_types:
            target_webhook = 'movies'
        elif item_type in tv_types:
            target_webhook = 'tv'
        elif item_type in music_types:
            target_webhook = 'music'
        else:
            target_webhook = fallback_webhook
        
        # Check if target webhook is enabled and has URL
        if (target_webhook in self.webhooks and 
            self.webhooks[target_webhook].get('enabled', False) and
            self.webhooks[target_webhook].get('url')):
            return {
                'name': target_webhook,
                'config': self.webhooks[target_webhook]
            }
        
        # Fall back to fallback webhook
        if (fallback_webhook in self.webhooks and
            self.webhooks[fallback_webhook].get('enabled', False) and
            self.webhooks[fallback_webhook].get('url')):
            logging.warning(f"Target webhook '{target_webhook}' not available, using fallback '{fallback_webhook}'")
            return {
                'name': fallback_webhook,
                'config': self.webhooks[fallback_webhook]
            }
        
        # Fall back to any enabled webhook
        for webhook_name, webhook_config in self.webhooks.items():
            if webhook_config.get('enabled', False) and webhook_config.get('url'):
                logging.warning(f"Using '{webhook_name}' as last resort webhook")
                return {
                    'name': webhook_name,
                    'config': webhook_config
                }
        
        return None
    
    async def initialize(self):
        """Initialize the notifier"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        """Close the notifier session"""
        if self.session:
            await self.session.close()
    
    async def _wait_for_rate_limit(self, webhook_name: str):
        """Wait for rate limit if necessary for specific webhook"""
        if webhook_name not in self.webhook_rate_limits:
            self.webhook_rate_limits[webhook_name] = {
                'last_request_time': 0,
                'request_count': 0
            }
        
        rate_limit_info = self.webhook_rate_limits[webhook_name]
        current_time = time.time()
        
        # Reset counter if period has passed
        if current_time - rate_limit_info['last_request_time'] >= self.rate_limit.get('period_seconds', 2):
            rate_limit_info['request_count'] = 0
        
        # Check if we need to wait
        if rate_limit_info['request_count'] >= self.rate_limit.get('requests_per_period', 5):
            wait_time = self.rate_limit.get('period_seconds', 2) - (current_time - rate_limit_info['last_request_time'])
            if wait_time > 0:
                logging.debug(f"Rate limiting webhook '{webhook_name}', waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                rate_limit_info['request_count'] = 0
        
        rate_limit_info['last_request_time'] = time.time()
        rate_limit_info['request_count'] += 1
    
    async def send_notification(self, item: MediaItem, changes: List[Dict[str, Any]] = None, is_new: bool = True) -> bool:
        """Send Discord notification to appropriate webhook"""
        webhook_info = self._get_webhook_for_item(item)
        
        if not webhook_info:
            logging.error("No suitable Discord webhook found for notification")
            return False
        
        webhook_name = webhook_info['name']
        webhook_config = webhook_info['config']
        
        # Check if grouping is enabled for this webhook
        grouping_mode = webhook_config.get('grouping', {}).get('mode', 'none')
        
        if grouping_mode != 'none':
            # Add to queue instead of sending immediately
            await self.notification_queue.add_item(webhook_name, item, changes, is_new)
            return True
        
        # No grouping, send immediately
        webhook_url = webhook_config['url']
        
        await self._wait_for_rate_limit(webhook_name)
        
        try:
            # Determine template and color
            if is_new:
                template_name = self.config.get('templates.new_item_template', 'new_item.j2')
                color = self.config.get('notifications.colors.new_item', 0x00FF00)
            else:
                template_name = self.config.get('templates.upgraded_item_template', 'upgraded_item.j2')
                # Determine color based on change type
                color = self._get_change_color(changes)
            
            # Load and render template
            template = self.template_env.get_template(template_name)
            
            # Prepare template data
            template_data = {
                'item': asdict(item),
                'changes': changes or [],
                'is_new': is_new,
                'color': color,
                'jellyfin_url': self.config.get('jellyfin.server_url'),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'webhook_name': webhook_config.get('name', webhook_name),
                'webhook_target': webhook_name
            }
            
            # Render the template
            rendered = template.render(**template_data)
            
            # Parse the rendered JSON
            payload = json.loads(rendered)
            
            # Send to Discord
            async with self.session.post(webhook_url, json=payload) as response:
                if response.status == 204:
                    logging.info(f"Successfully sent notification for {item.name} to '{webhook_name}' webhook")
                    return True
                elif response.status == 429:
                    # Rate limited
                    retry_after = response.headers.get('Retry-After', '60')
                    logging.warning(f"Discord webhook '{webhook_name}' rate limited, retry after {retry_after} seconds")
                    await asyncio.sleep(int(retry_after))
                    return await self.send_notification(item, changes, is_new)
                else:
                    error_text = await response.text()
                    logging.error(f"Discord webhook '{webhook_name}' failed with status {response.status}: {error_text}")
                    return False
                    
        except Exception as e:
            logging.error(f"Error sending Discord notification to '{webhook_name}': {e}")
            return False
    
    async def send_grouped_notification(self, webhook_name: str, template_name: str, template_data: Dict[str, Any]) -> bool:
        """Send a grouped notification using the specified template"""
        if webhook_name not in self.webhooks:
            logging.error(f"Webhook '{webhook_name}' not found")
            return False
            
        webhook_config = self.webhooks[webhook_name]
        webhook_url = webhook_config.get('url')
        
        if not webhook_url:
            logging.error(f"No URL for webhook '{webhook_name}'")
            return False
            
        await self._wait_for_rate_limit(webhook_name)
        
        try:
            # Load and render template
            template = self.template_env.get_template(template_name)
            
            # Render the template
            rendered = template.render(**template_data)
            
            # Parse the rendered JSON
            payload = json.loads(rendered)
            
            # Send to Discord
            async with self.session.post(webhook_url, json=payload) as response:
                if response.status == 204:
                    item_count = template_data.get('total_items', 0)
                    logging.info(f"Successfully sent grouped notification with {item_count} items to '{webhook_name}' webhook")
                    return True
                elif response.status == 429:
                    # Rate limited
                    retry_after = response.headers.get('Retry-After', '60')
                    logging.warning(f"Discord webhook '{webhook_name}' rate limited, retry after {retry_after} seconds")
                    await asyncio.sleep(int(retry_after))
                    return await self.send_grouped_notification(webhook_name, template_name, template_data)
                else:
                    error_text = await response.text()
                    logging.error(f"Discord webhook '{webhook_name}' failed with status {response.status}: {error_text}")
                    return False
                    
        except Exception as e:
            logging.error(f"Error sending grouped Discord notification to '{webhook_name}': {e}")
            return False
    
    def _get_change_color(self, changes: List[Dict[str, Any]]) -> int:
        """Get color based on change types"""
        colors = self.config.get('notifications.colors', {})
        
        if not changes:
            return colors.get('new_item', 0x00FF00)
        
        # Prioritize change types
        change_types = [change['type'] for change in changes]
        
        if 'resolution' in change_types:
            return colors.get('resolution_upgrade', 0xFFD700)
        elif 'codec' in change_types:
            return colors.get('codec_upgrade', 0xFF8C00)
        elif 'hdr_status' in change_types:
            return colors.get('hdr_upgrade', 0xFF1493)
        elif any(t in change_types for t in ['audio_codec', 'audio_channels']):
            return colors.get('audio_upgrade', 0x9370DB)
        elif 'provider_ids' in change_types:
            return colors.get('provider_update', 0x1E90FF)
        else:
            return colors.get('new_item', 0x00FF00)
    
    async def send_server_status(self, is_online: bool) -> bool:
        """Send server status notification to all enabled webhooks"""
        try:
            embed = {
                "title": f"{'🟢 Jellyfin Server Online' if is_online else '🔴 Jellyfin Server Offline'}",
                "description": f"{'Jellyfin server connection has been restored.' if is_online else 'Unable to connect to Jellyfin server. Will retry every minute.'}",
                "color": 0x00FF00 if is_online else 0xFF0000,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            payload = {"embeds": [embed]}
            
            # Send to all enabled webhooks
            success_count = 0
            total_webhooks = 0
            
            for webhook_name, webhook_config in self.webhooks.items():
                if not webhook_config.get('enabled', False) or not webhook_config.get('url'):
                    continue
                
                total_webhooks += 1
                await self._wait_for_rate_limit(webhook_name)
                
                try:
                    async with self.session.post(webhook_config['url'], json=payload) as response:
                        if response.status == 204:
                            success_count += 1
                            logging.debug(f"Server status sent to '{webhook_name}' webhook")
                        else:
                            logging.warning(f"Server status failed for '{webhook_name}' webhook: {response.status}")
                except Exception as e:
                    logging.error(f"Error sending server status to '{webhook_name}': {e}")
            
            logging.info(f"Server status notification sent to {success_count}/{total_webhooks} webhooks")
            return success_count > 0
                
        except Exception as e:
            logging.error(f"Error sending server status notification: {e}")
            return False
    
    def get_webhook_status(self) -> Dict[str, Any]:
        """Get status of all configured webhooks"""
        status = {
            "routing_enabled": self.routing_enabled,
            "webhooks": {},
            "routing_config": self.routing_config if self.routing_enabled else None,
            "notification_queues": self.notification_queue.get_queue_status()
        }
        
        for webhook_name, webhook_config in self.webhooks.items():
            status["webhooks"][webhook_name] = {
                "name": webhook_config.get('name', webhook_name),
                "enabled": webhook_config.get('enabled', False),
                "has_url": bool(webhook_config.get('url')),
                "url_preview": webhook_config.get('url', '')[:50] + '...' if webhook_config.get('url') else None,
                "rate_limit_info": self.webhook_rate_limits.get(webhook_name, {}),
                "grouping": webhook_config.get('grouping', {'mode': 'none'})
            }
        
        return status


class WebhookService:
    """Main webhook service"""
    
    def __init__(self):
        self.config = Config()
        self.db = DatabaseManager(
            self.config.get('database.path'),
            self.config.get('database.wal_mode', True)
        )
        self.jellyfin = JellyfinAPI(self.config)
        self.change_detector = ChangeDetector(self.config)
        self.discord = DiscordNotifier(self.config)
        
        self.last_vacuum = 0
        self.server_was_offline = False
        
        # Setup logging
        log_level = getattr(logging, self.config.get('server.log_level', 'INFO').upper())
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler('/app/logs/service.log', mode='a')
            ]
        )
        
        # Ensure log directory exists
        os.makedirs('/app/logs', exist_ok=True)
    
    async def initialize(self):
        """Initialize all components"""
        logging.info("Initializing Jellyfin Discord Webhook Service...")
        
        await self.db.initialize()
        await self.discord.initialize()
        
        # Connect to Jellyfin
        if await self.jellyfin.connect():
            logging.info("Successfully connected to Jellyfin")
            
            # Perform startup sync if enabled
            if self.config.get('sync.startup_sync', True):
                await self.sync_jellyfin_library()
        else:
            logging.error("Failed to connect to Jellyfin server")
            self.server_was_offline = True
    
    async def sync_jellyfin_library(self):
        """Sync entire Jellyfin library to database"""
        logging.info("Starting Jellyfin library sync...")
        
        try:
            jellyfin_items = await self.jellyfin.get_all_items(
                self.config.get('sync.sync_batch_size', 100)
            )
            
            for jellyfin_item in jellyfin_items:
                media_item = self.jellyfin.extract_media_item(jellyfin_item)
                await self.db.save_item(media_item)
            
            logging.info(f"Synced {len(jellyfin_items)} items from Jellyfin library")
            
        except Exception as e:
            logging.error(f"Error during library sync: {e}")
    
    async def process_webhook(self, payload: WebhookPayload) -> Dict[str, Any]:
        """Process incoming webhook from Jellyfin"""
        try:
            # Extract media item from webhook
            media_item = self._extract_from_webhook(payload)
            
            # Check if item exists in database
            existing_item = await self.db.get_item(media_item.item_id)
            
            if existing_item:
                # Detect changes
                changes = self.change_detector.detect_changes(existing_item, media_item)
                
                if changes:
                    # Item was updated/upgraded
                    await self.discord.send_notification(media_item, changes, is_new=False)
                    logging.info(f"Processed upgrade for {media_item.name} with {len(changes)} changes")
                else:
                    # No significant changes
                    logging.debug(f"No significant changes detected for {media_item.name}")
            else:
                # New item
                await self.discord.send_notification(media_item, is_new=True)
                logging.info(f"Processed new item: {media_item.name}")
            
            # Save/update item in database
            await self.db.save_item(media_item)
            
            return {"status": "success", "item_id": media_item.item_id}
            
        except Exception as e:
            logging.error(f"Error processing webhook: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    def _extract_from_webhook(self, payload: WebhookPayload) -> MediaItem:
        """Extract MediaItem from webhook payload"""
        return MediaItem(
            item_id=payload.ItemId,
            name=payload.Name,
            item_type=payload.ItemType,
            year=payload.Year,
            series_name=payload.SeriesName,
            season_number=int(payload.SeasonNumber00) if payload.SeasonNumber00 else None,
            episode_number=int(payload.EpisodeNumber00) if payload.EpisodeNumber00 else None,
            overview=payload.Overview,
            
            # Video properties
            video_height=payload.Video_0_Height,
            video_width=payload.Video_0_Width,
            video_codec=payload.Video_0_Codec,
            video_profile=payload.Video_0_Profile,
            video_range=payload.Video_0_VideoRange,
            video_framerate=payload.Video_0_FrameRate,
            aspect_ratio=payload.Video_0_AspectRatio,
            
            # Audio properties
            audio_codec=payload.Audio_0_Codec,
            audio_channels=payload.Audio_0_Channels,
            audio_language=payload.Audio_0_Language,
            audio_bitrate=payload.Audio_0_Bitrate,
            
            # Provider IDs
            imdb_id=payload.Provider_imdb,
            tmdb_id=payload.Provider_tmdb,
            tvdb_id=payload.Provider_tvdb
        )
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check endpoint"""
        jellyfin_connected = await self.jellyfin.is_connected()
        
        return {
            "status": "healthy" if jellyfin_connected else "degraded",
            "jellyfin_connected": jellyfin_connected,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def manual_sync(self) -> Dict[str, Any]:
        """Manual sync command"""
        try:
            await self.sync_jellyfin_library()
            return {"status": "success", "message": "Library sync completed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    async def background_tasks(self):
        """Background maintenance tasks"""
        while True:
            try:
                current_time = time.time()
                
                # Database vacuum
                vacuum_interval = self.config.get('database.vacuum_interval_hours', 24) * 3600
                if current_time - self.last_vacuum > vacuum_interval:
                    await self.db.vacuum_database()
                    self.last_vacuum = current_time
                
                # Check Jellyfin connection
                jellyfin_connected = await self.jellyfin.is_connected()
                
                if not jellyfin_connected and not self.server_was_offline:
                    # Server went offline
                    await self.discord.send_server_status(False)
                    self.server_was_offline = True
                    logging.warning("Jellyfin server went offline")
                    
                elif jellyfin_connected and self.server_was_offline:
                    # Server came back online
                    await self.discord.send_server_status(True)
                    self.server_was_offline = False
                    logging.info("Jellyfin server is back online")
                
                # Sleep for 60 seconds
                await asyncio.sleep(60)
                
            except Exception as e:
                logging.error(f"Error in background tasks: {e}")
                await asyncio.sleep(60)
    
    async def cleanup(self):
        """Cleanup resources"""
        await self.discord.close()
        
    async def get_queue_status(self) -> Dict[str, Any]:
        """Get status of notification queues"""
        return self.discord.notification_queue.get_queue_status()


# FastAPI application
app = FastAPI(title="Jellyfin Discord Webhook Service", version="1.0.0")
service = WebhookService()


@app.on_event("startup")
async def startup():
    await service.initialize()
    # Start background tasks
    asyncio.create_task(service.background_tasks())


@app.on_event("shutdown")
async def shutdown():
    await service.cleanup()


@app.post("/webhook")
async def webhook_endpoint(payload: WebhookPayload):
    """Main webhook endpoint for Jellyfin"""
    return await service.process_webhook(payload)


@app.get("/health")
async def health_endpoint():
    """Health check endpoint"""
    return await service.health_check()


@app.post("/sync")
async def sync_endpoint():
    """Manual sync endpoint"""
    return await service.manual_sync()


@app.get("/stats")
async def stats_endpoint():
    """Get database statistics"""
    try:
        items = await service.db.get_all_items()
        
        stats = {
            "total_items": len(items),
            "item_types": {},
            "last_updated": None
        }
        
        for item in items:
            item_type = item.item_type
            if item_type not in stats["item_types"]:
                stats["item_types"][item_type] = 0
            stats["item_types"][item_type] += 1
            
            if not stats["last_updated"] or item.timestamp > stats["last_updated"]:
                stats["last_updated"] = item.timestamp
        
        return stats
        
    except Exception as e:
        return {"error": str(e)}


@app.get("/webhooks")
async def webhooks_endpoint():
    """Get webhook configuration and status"""
    return service.discord.get_webhook_status()


@app.get("/queues")
async def queues_endpoint():
    """Get notification queue status"""
    return await service.get_queue_status()


@app.post("/test-webhook")
async def test_webhook_endpoint(webhook_name: str = "default"):
    """Test a specific webhook"""
    try:
        webhook_info = service.discord._get_webhook_for_item(
            MediaItem(
                item_id="test-item",
                name="Test Item",
                item_type="Movie"
            )
        )
        
        if not webhook_info:
            return {"error": "No webhook available for testing"}
        
        test_payload = {
            "embeds": [{
                "title": "🧪 Webhook Test",
                "description": f"Test notification from {webhook_info['config'].get('name', webhook_info['name'])} webhook",
                "color": 65280,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]
        }
        
        webhook_url = webhook_info['config']['url']
        
        async with service.discord.session.post(webhook_url, json=test_payload) as response:
            if response.status == 204:
                return {
                    "status": "success", 
                    "webhook": webhook_info['name'],
                    "message": "Test notification sent successfully"
                }
            else:
                error_text = await response.text()
                return {
                    "status": "error",
                    "webhook": webhook_info['name'],
                    "message": f"HTTP {response.status}: {error_text}"
                }
                
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/flush-queues")
async def flush_queues_endpoint(webhook_name: str = None):
    """Force process notification queues immediately"""
    try:
        queue = service.discord.notification_queue
        
        if webhook_name:
            # Process specific webhook queue
            if webhook_name in queue.queues:
                asyncio.create_task(queue._process_queue(webhook_name))
                return {
                    "status": "success",
                    "message": f"Processing queue for webhook '{webhook_name}'",
                    "webhook": webhook_name
                }
            else:
                return {
                    "status": "error",
                    "message": f"Webhook '{webhook_name}' not found or has no queue"
                }
        else:
            # Process all queues
            processed = []
            for webhook in queue.queues.keys():
                asyncio.create_task(queue._process_queue(webhook))
                processed.append(webhook)
                
            return {
                "status": "success",
                "message": f"Processing {len(processed)} queues",
                "webhooks": processed
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    # Run with uvicorn
    uvicorn.run(
        "main:app",
        host=service.config.get('server.host', '0.0.0.0'),
        port=service.config.get('server.port', 8080),
        log_level=service.config.get('server.log_level', 'info').lower(),
        reload=False
    )
