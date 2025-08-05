#!/usr/bin/env python3
"""
Jellynouncer Database Manager

This module handles all SQLite database operations including table creation,
item storage/retrieval, and maintenance tasks. It provides an async-first interface
for database operations with comprehensive error handling and performance optimizations.

The DatabaseManager class encapsulates all database logic, providing a clean interface
for other service components while handling the complexities of SQLite configuration,
connection management, and data serialization.

Classes:
    DatabaseManager: Enhanced SQLite database manager with WAL mode and error handling

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import os
import json
import logging
from datetime import datetime, timezone
from dataclasses import asdict
from typing import Dict, Any, Optional, List

import aiosqlite

from config_models import DatabaseConfig
from media_models import MediaItem
from utils import get_logger


class DatabaseManager:
    """
    Enhanced SQLite database manager with WAL mode and comprehensive error handling.

    This class manages all database operations for the Jellynouncer service,
    including table creation, item storage/retrieval, and maintenance tasks.
    It provides an async-first interface optimized for concurrent webhook processing.

    **Understanding Async/Await for Beginners:**

    Async/await is Python's way of handling asynchronous operations - tasks that
    might take time to complete (like database operations) without blocking the
    entire program. Key concepts:

    - `async def`: Defines an asynchronous function that can be paused and resumed
    - `await`: Pauses execution until the awaited operation completes
    - Database operations are I/O-bound and benefit greatly from async handling
    - Multiple webhook requests can be processed concurrently

    **SQLite WAL Mode:**

    WAL (Write-Ahead Logging) is a SQLite feature that improves concurrent access:
    - Allows multiple readers while a writer is active
    - Better performance for applications with mixed read/write workloads
    - Reduces lock contention during webhook processing
    - Automatic checkpointing maintains database consistency

    **Key Features:**
    - WAL (Write-Ahead Logging) mode for better concurrent access
    - Batch operations for improved performance
    - Content hash-based change detection
    - Automatic database maintenance (VACUUM, ANALYZE)
    - Comprehensive error handling and logging
    - JSON serialization for complex data types
    - Connection pooling through aiosqlite

    **Table Schema:**
    The media_items table stores comprehensive metadata with the following structure:
    - Primary key: item_id (TEXT)
    - Core fields: name, item_type, year, etc.
    - Technical specs: video_height, video_codec, audio_channels, etc.
    - JSON fields: genres, studios, tags, artists (stored as JSON strings)
    - Timestamps: date_created, date_modified, timestamp
    - Content tracking: content_hash, file_path, file_size

    Attributes:
        config (DatabaseConfig): Database configuration settings
        logger (logging.Logger): Logger instance for database operations
        db_path (str): Full path to SQLite database file
        wal_mode (bool): Whether WAL mode is enabled
        _connection_count (int): Track active connections for monitoring

    Example:
        ```python
        # Initialize database manager
        db_config = DatabaseConfig(path="/data/jellynouncer.db", wal_mode=True)
        db_manager = DatabaseManager(db_config, logger)

        # Initialize database (creates tables, enables WAL mode)
        await db_manager.initialize()

        # Save a media item
        item = MediaItem(
            item_id="abc123",
            name="The Matrix",
            item_type="Movie",
            video_height=1080,
            video_codec="h264"
        )
        success = await db_manager.save_item(item)

        # Retrieve item later
        retrieved = await db_manager.get_item("abc123")
        if retrieved:
            logger.info(f"Found: {retrieved.name} ({retrieved.video_height}p)")

        # Batch operations for efficiency
        items = [item1, item2, item3]
        results = await db_manager.save_items_batch(items)
        ```

    Note:
        WAL mode creates additional files (.wal, .shm) alongside the main database.
        These are automatically managed by SQLite and improve concurrent performance.
        The class handles all WAL-specific configuration and maintenance automatically.
    """

    def __init__(self, config: DatabaseConfig):
        """
        Initialize database manager with configuration and logging.

        Sets up the database manager with the provided configuration, ensuring
        the parent directory exists and initializing tracking variables.

        Args:
            config (DatabaseConfig): Database configuration with path and WAL settings

        Note:
            This constructor only sets up the initial state. Actual database
            initialization (creating tables, enabling WAL) happens in the
            async initialize() method.
        """
        self.config = config
        self.logger = get_logger("jellynouncer.database")
        self.db_path = config.path
        self.wal_mode = config.wal_mode
        self._connection_count = 0

        # Ensure the parent directory exists for the database file
        database_dir = os.path.dirname(self.db_path)
        if database_dir:  # Only create if there's actually a directory path
            os.makedirs(database_dir, exist_ok=True)
            self.logger.debug(f"Ensured database directory exists: {database_dir}")

    async def initialize(self) -> None:
        """
        Initialize database tables and configure SQLite settings.

        This method sets up the database schema and configures SQLite for
        optimal performance and reliability. It handles WAL mode activation,
        table creation, and initial performance optimizations.

        **Async Database Operations:**
        Database operations are async because they involve file I/O which can
        block the program. Using async/await allows other webhook requests to
        be processed while waiting for database operations to complete.

        **Complete Schema Design:**
        The media_items table now includes ALL fields from the MediaItem dataclass
        to ensure complete webhook field synchronization. This prevents data loss
        and ensures all webhook template variables are available.

        **Schema Categories:**
        - Core identification: item_id, name, item_type, year, overview
        - Server information: server_id, server_name, server_version, server_url
        - Video specifications: Complete video stream metadata (codec, resolution, etc.)
        - Audio specifications: Complete audio stream metadata (codec, channels, etc.)
        - Subtitle information: Complete subtitle/caption metadata
        - TV series hierarchy: Series, season, episode organization with padded numbers
        - Provider IDs: External database identifiers (IMDb, TMDb, TVDb, TVDb slug)
        - Runtime information: Duration in various formats
        - Metadata: Ratings, genres, studios, tags, descriptions
        - Music fields: Album, artist, music-specific metadata
        - Photo fields: Image dimensions and metadata
        - File system: Paths, sizes, library information
        - Timestamps: Creation, modification, and tracking timestamps
        - Internal tracking: Content hashes, change detection

        **Configuration Steps:**
        1. Enable WAL mode for concurrent access
        2. Set performance optimization PRAGMAs
        3. Create the complete media_items table with ALL MediaItem fields
        4. Create indexes for query performance
        5. Create sync tracking table
        6. Log initialization status

        Raises:
            Exception: If database initialization fails

        Note:
            This method should be called once during application startup.
            Multiple calls are safe but unnecessary.
        """
        try:
            self.logger.info("Initializing database manager with complete webhook field schema...")

            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1

                # Enable WAL mode for better concurrent access
                if self.wal_mode:
                    await db.execute("PRAGMA journal_mode=WAL")
                    self.logger.debug("WAL mode enabled for concurrent access")

                # Set performance optimization PRAGMAs
                await db.execute("PRAGMA synchronous=NORMAL")
                await db.execute("PRAGMA temp_store=memory")
                await db.execute("PRAGMA mmap_size=268435456")
                await db.execute("PRAGMA cache_size=-32000")
                await db.execute("PRAGMA busy_timeout=30000")

                # Create the complete media_items table with ALL MediaItem fields
                # This ensures complete webhook field synchronization and prevents data loss
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS media_items
                    (
                        -- ==================== CORE IDENTIFICATION ====================
                        -- Required fields for all items (primary key and basic info)
                        item_id                   TEXT PRIMARY KEY,  -- Unique Jellyfin identifier
                        name                      TEXT NOT NULL,     -- Display name of the item
                        item_type                 TEXT NOT NULL,     -- Media type (Movie, Episode, etc.)

                        -- ==================== CONTENT METADATA ====================
                        -- Basic metadata common across media types
                        year                      INTEGER,           -- Production/release year
                        series_name               TEXT,              -- TV series name (for episodes/seasons)
                        season_number             INTEGER,           -- Season number
                        episode_number            INTEGER,           -- Episode number
                        overview                  TEXT,              -- Description/synopsis

                        -- ==================== SERVER INFORMATION ====================
                        -- Server context from webhook (ServerId, ServerName, etc.)
                        server_id                 TEXT,              -- Jellyfin server unique identifier  
                        server_name               TEXT,              -- Human-readable server name
                        server_version            TEXT,              -- Jellyfin server version string
                        server_url                TEXT,              -- Public URL of the Jellyfin server
                        notification_type         TEXT,              -- Type of notification event (ItemAdded, etc.)

                        -- ==================== VIDEO TECHNICAL SPECIFICATIONS ====================
                        -- Complete video stream metadata for quality analysis and display
                        video_height              INTEGER,           -- Video resolution height (1080, 720, 2160, etc.)
                        video_width               INTEGER,           -- Video resolution width (1920, 1280, 3840, etc.)
                        video_codec               TEXT,              -- Video codec (h264, hevc, av1, vp9, etc.)
                        video_bitrate             INTEGER,           -- Video bitrate in bits per second
                        video_framerate           REAL,              -- Frames per second (23.976, 29.970, 60.000, etc.)
                        video_language            TEXT,              -- Video language code (eng, spa, fra, etc.)
                        video_title               TEXT,              -- Video stream title/name from container
                        video_type                TEXT,              -- Stream type identifier
                        video_profile             TEXT,              -- Codec profile (High, Main, Baseline, etc.)
                        video_level               TEXT,              -- Codec level (4.1, 5.1, etc.)
                        video_pixelaspect         TEXT,              -- Pixel aspect ratio (1:1, etc.)
                        video_colorspace          TEXT,              -- Color space (bt709, bt2020, etc.)
                        video_colortransfer       TEXT,              -- Color transfer characteristics (bt709, smpte2084, etc.)
                        video_colorprimaries      TEXT,              -- Color primaries specification (bt709, bt2020, etc.)
                        video_pixelformat         TEXT,              -- Pixel format (yuv420p, yuv420p10le, etc.)
                        video_refframes           INTEGER,           -- Number of reference frames used by codec

                        -- ==================== AUDIO TECHNICAL SPECIFICATIONS ====================
                        -- Complete audio stream metadata for quality detection and display
                        audio_codec               TEXT,              -- Audio codec (aac, ac3, dts, flac, mp3, etc.)
                        audio_channels            INTEGER,           -- Number of audio channels (2, 6, 8 for stereo, 5.1, 7.1)
                        audio_language            TEXT,              -- Audio language code (eng, spa, fra, etc.)
                        audio_bitrate             INTEGER,           -- Audio bitrate in bits per second
                        audio_title               TEXT,              -- Audio stream title/name from container
                        audio_type                TEXT,              -- Stream type identifier
                        audio_samplerate          INTEGER,           -- Sample rate in Hz (48000, 44100, 96000, etc.)
                        audio_default             BOOLEAN,           -- Whether this is the default audio track

                        -- ==================== SUBTITLE INFORMATION ====================
                        -- Subtitle/caption tracks available for the content
                        subtitle_title            TEXT,              -- Subtitle stream title/name
                        subtitle_type             TEXT,              -- Subtitle stream type identifier
                        subtitle_language         TEXT,              -- Subtitle language code (eng, spa, fra, etc.)
                        subtitle_codec            TEXT,              -- Subtitle format (srt, ass, pgs, vtt, etc.)
                        subtitle_default          BOOLEAN,           -- Default subtitle track flag
                        subtitle_forced           BOOLEAN,           -- Forced subtitle display flag
                        subtitle_external         BOOLEAN,           -- External subtitle file flag

                        -- ==================== EXTERNAL PROVIDER IDS ====================
                        -- Links to external movie/TV databases for metadata enrichment
                        imdb_id                   TEXT,              -- Internet Movie Database ID (tt1234567)
                        tmdb_id                   TEXT,              -- The Movie Database ID
                        tvdb_id                   TEXT,              -- The TV Database ID
                        tvdb_slug                 TEXT,              -- TVDB URL slug identifier [FIXED: Previously missing]

                        -- ==================== TV SERIES HIERARCHY ====================
                        -- TV series organization and hierarchy for episodes and seasons
                        series_id                 TEXT,              -- Parent series unique identifier
                        series_premiere_date      TEXT,              -- Series original premiere date
                        season_id                 TEXT,              -- Season unique identifier
                        season_number_padded      TEXT,              -- Zero-padded season number (02, 03, etc.)
                        season_number_padded_3    TEXT,              -- Zero-padded season number (002, 003, etc.)
                        episode_number_padded     TEXT,              -- Zero-padded episode number (04, 05, etc.)
                        episode_number_padded_3   TEXT,              -- Zero-padded episode number (004, 005, etc.)
                        air_time                  TEXT,              -- Original broadcast time (21:30, etc.)

                        -- ==================== RUNTIME INFORMATION ====================
                        -- Duration and timing information in multiple formats
                        runtime_ticks             INTEGER,           -- Jellyfin duration in ticks (10,000 ticks = 1ms)
                        runtime_formatted         TEXT,              -- Human-readable runtime (1h 30m)

                        -- ==================== TIMESTAMPS ====================
                        -- Creation, modification, and processing timestamps
                        date_created              TEXT,              -- When item was added to Jellyfin (ISO format)
                        date_modified             TEXT,              -- When item was last modified (ISO format)
                        timestamp                 TEXT DEFAULT CURRENT_TIMESTAMP, -- Service processing timestamp

                        -- ==================== CONTENT METADATA ====================
                        -- Descriptive metadata and categorization
                        tagline                   TEXT,              -- Marketing tagline or promotional text
                        genres                    TEXT,              -- JSON array of genre names
                        studios                   TEXT,              -- JSON array of production companies/studios
                        tags                      TEXT,              -- JSON array of user-defined or imported tags
                        official_rating           TEXT,              -- MPAA rating (G, PG, R), TV rating (TV-MA), etc.

                        -- ==================== MUSIC-SPECIFIC METADATA ====================
                        -- Fields specific to audio content (songs, albums, artists)
                        album                     TEXT,              -- Album name (for music tracks)
                        artists                   TEXT,              -- JSON array of artist names
                        album_artist              TEXT,              -- Primary album artist

                        -- ==================== PHOTO-SPECIFIC METADATA ====================
                        -- Fields specific to image content
                        width                     INTEGER,           -- Image width in pixels
                        height                    INTEGER,           -- Image height in pixels

                        -- ==================== FILE SYSTEM INFORMATION ====================
                        -- File location and metadata from the storage system
                        file_path                 TEXT,              -- Full file path on server
                        file_size                 INTEGER,           -- File size in bytes
                        library_name              TEXT,              -- Jellyfin library name

                        -- ==================== INTERNAL TRACKING ====================
                        -- Fields used for service operations and change detection
                        content_hash              TEXT               -- MD5 hash for change detection
                    )
                """)

                # Create indexes for optimal query performance
                # These indexes support common query patterns used by the service
                await db.execute("CREATE INDEX IF NOT EXISTS idx_item_type ON media_items(item_type)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_series_name ON media_items(series_name)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON media_items(timestamp)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON media_items(content_hash)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_series_id ON media_items(series_id)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_season_id ON media_items(season_id)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_video_height ON media_items(video_height)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_video_codec ON media_items(video_codec)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_audio_codec ON media_items(audio_codec)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_tvdb_slug ON media_items(tvdb_slug)")

                # Create sync status tracking table for monitoring library synchronization
                await db.execute("""
                                 CREATE TABLE IF NOT EXISTS sync_status
                                 (
                                     id
                                     INTEGER
                                     PRIMARY
                                     KEY,
                                     last_sync_time
                                     TEXT,    -- Last successful sync timestamp
                                     sync_type
                                     TEXT,    -- Type of sync (initial, background, manual)
                                     items_processed
                                     INTEGER, -- Number of items processed in sync
                                     timestamp
                                     TEXT
                                     DEFAULT
                                     CURRENT_TIMESTAMP
                                 )
                                 """)

                await db.commit()
                self._connection_count -= 1

            self.logger.info("Database initialization completed successfully with complete webhook field schema")
            self.logger.debug("Schema includes all MediaItem fields for comprehensive webhook synchronization")

        except Exception as e:
            self.logger.error(f"Database initialization failed: {e}")
            raise

    async def save_item(self, item: MediaItem) -> bool:
        """
        Save or update a media item in the database.

        This method handles both new item insertion and existing item updates
        using SQLite's INSERT OR REPLACE functionality. It automatically
        serializes complex data types like lists to JSON format.

        **Understanding UPSERT Operations:**
        UPSERT (UPDATE or INSERT) is a database operation that:
        - Inserts a new record if the primary key doesn't exist
        - Updates the existing record if the primary key already exists
        - Provides atomic operation for concurrent safety

        Args:
            item (MediaItem): Media item to save or update

        Returns:
            bool: True if save was successful, False otherwise

        Example:
            ```python
            # Save a new movie
            movie = MediaItem(
                item_id="movie123",
                name="The Matrix",
                item_type="Movie",
                year=1999,
                video_height=1080
            )

            success = await db_manager.save_item(movie)
            if success:
                logger.info(f"Successfully saved {movie.name}")
            ```

        Note:
            This method handles JSON serialization automatically for
            list fields like genres, studios, tags, and artists.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1

                # Convert MediaItem to dictionary for database insertion
                item_dict = asdict(item)

                # Serialize list fields to JSON strings
                for field in ['genres', 'studios', 'tags', 'artists']:
                    if item_dict[field] is not None:
                        item_dict[field] = json.dumps(item_dict[field])

                # Insert or replace the item (UPSERT operation)
                placeholders = ', '.join(['?' for _ in item_dict])
                columns = ', '.join(item_dict.keys())

                await db.execute(
                    f"INSERT OR REPLACE INTO media_items ({columns}) VALUES ({placeholders})",
                    list(item_dict.values())
                )

                await db.commit()
                self._connection_count -= 1

                self.logger.debug(f"Successfully saved item: {item.name} ({item.item_id})")
                return True

        except Exception as e:
            self.logger.error(f"Failed to save item {item.item_id}: {e}")
            return False

    async def get_item(self, item_id: str) -> Optional[MediaItem]:
        """
        Retrieve a media item from the database by ID.

        This method fetches a single media item and handles JSON deserialization
        for complex fields. It returns None if the item is not found.

        Args:
            item_id (str): Unique identifier of the media item

        Returns:
            Optional[MediaItem]: MediaItem object if found, None otherwise

        Example:
            ```python
            # Retrieve an item by ID
            item = await db_manager.get_item("movie123")
            if item:
                logger.info(f"Found: {item.name} ({item.year})")
            else:
                logger.warning("Item not found")
            ```

        Note:
            This method automatically deserializes JSON fields back to
            Python lists for genres, studios, tags, and artists.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1
                db.row_factory = aiosqlite.Row  # Enable column access by name

                cursor = await db.execute(
                    "SELECT * FROM media_items WHERE item_id = ?",
                    (item_id,)
                )
                row = await cursor.fetchone()
                self._connection_count -= 1

                if row:
                    # Convert row to dictionary
                    item_dict = dict(row)

                    # Deserialize JSON fields back to lists
                    for field in ['genres', 'studios', 'tags', 'artists']:
                        if item_dict[field]:
                            try:
                                item_dict[field] = json.loads(item_dict[field])
                            except json.JSONDecodeError:
                                item_dict[field] = []
                        else:
                            item_dict[field] = None

                    self.logger.debug(f"Retrieved item: {item_dict['name']}")
                    return MediaItem(**item_dict)
                else:
                    self.logger.debug(f"Item not found: {item_id}")
                    return None

        except Exception as e:
            self.logger.error(f"Failed to retrieve item {item_id}: {e}")
            return None

    async def save_items_batch(self, items: List[MediaItem]) -> Dict[str, int]:
        """
        Save multiple media items in a single transaction for better performance.

        This method provides efficient batch processing for large numbers of
        items, such as during library synchronization. It uses a single
        database transaction to improve performance and ensure consistency.

        **Understanding Database Transactions:**
        A transaction is a group of database operations that are treated as
        a single unit. Either all operations succeed, or none of them do.
        This ensures data consistency and improves performance by reducing
        the number of individual database commits.

        Args:
            items (List[MediaItem]): List of media items to save

        Returns:
            Dict[str, int]: Statistics about the batch operation
            - 'successful': Number of items saved successfully
            - 'failed': Number of items that failed to save
            - 'total': Total number of items processed

        Example:
            ```python
            # Batch save multiple items
            items = [movie1, movie2, tv_episode1, music_track1]
            results = await db_manager.save_items_batch(items)

            logger.info(f"Batch save: {results['successful']}/{results['total']} succeeded")
            ```

        Note:
            This method is significantly faster than calling save_item()
            multiple times for large batches due to transaction overhead reduction.
        """
        if not items:
            return {'successful': 0, 'failed': 0, 'total': 0}

        successful = 0
        failed = 0

        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1

                # Begin transaction for all items
                await db.execute("BEGIN TRANSACTION")

                for item in items:
                    try:
                        # Convert to dictionary and serialize JSON fields
                        item_dict = asdict(item)
                        for field in ['genres', 'studios', 'tags', 'artists']:
                            if item_dict[field] is not None:
                                item_dict[field] = json.dumps(item_dict[field])

                        # Insert or replace the item
                        placeholders = ', '.join(['?' for _ in item_dict])
                        columns = ', '.join(item_dict.keys())

                        await db.execute(
                            f"INSERT OR REPLACE INTO media_items ({columns}) VALUES ({placeholders})",
                            list(item_dict.values())
                        )
                        successful += 1

                    except Exception as e:
                        self.logger.warning(f"Failed to save item {item.item_id} in batch: {e}")
                        failed += 1

                # Commit the entire transaction
                await db.commit()
                self._connection_count -= 1

            self.logger.info(f"Batch save completed: {successful} successful, {failed} failed")
            return {
                'successful': successful,
                'failed': failed,
                'total': len(items)
            }

        except Exception as e:
            self.logger.error(f"Batch save transaction failed: {e}")
            return {
                'successful': 0,
                'failed': len(items),
                'total': len(items)
            }

    async def get_items_by_type(self, item_type: str, limit: Optional[int] = None) -> List[MediaItem]:
        """
        Retrieve all media items of a specific type.

        This method fetches items filtered by type (Movie, Episode, Audio, etc.)
        and handles JSON deserialization. It supports optional result limiting
        for performance with large datasets.

        Args:
            item_type (str): Type of media items to retrieve (Movie, Episode, Audio, etc.)
            limit (Optional[int]): Maximum number of items to return (None for all)

        Returns:
            List[MediaItem]: List of matching media items, sorted by timestamp (newest first)

        Example:
            ```python
            # Get all movies
            movies = await db_manager.get_items_by_type("Movie")
            logger.info(f"Found {len(movies)} movies")

            # Get last 10 episodes
            recent_episodes = await db_manager.get_items_by_type("Episode", limit=10)
            ```

        Note:
            Results are ordered by timestamp in descending order (newest first)
            for consistent behavior across different query types.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1
                db.row_factory = aiosqlite.Row

                # Build query with optional limit
                sql = "SELECT * FROM media_items WHERE item_type = ? ORDER BY timestamp DESC"
                params = (item_type,)

                if limit:
                    sql += " LIMIT ?"
                    params = (item_type, limit)

                cursor = await db.execute(sql, params)
                rows = await cursor.fetchall()

                items = []
                for row in rows:
                    item_dict = dict(row)

                    # Deserialize JSON fields
                    for field in ['genres', 'studios', 'tags', 'artists']:
                        if item_dict.get(field):
                            try:
                                item_dict[field] = json.loads(item_dict[field])
                            except json.JSONDecodeError:
                                item_dict[field] = []
                        else:
                            item_dict[field] = None

                    items.append(MediaItem(**item_dict))

                self._connection_count -= 1
                return items

        except Exception as e:
            self.logger.error(f"Failed to query items by type {item_type}: {e}")
            return []

    async def delete_item(self, item_id: str) -> bool:
        """
        Delete a media item from the database.

        This method removes an item completely from the database. Use with caution
        as this operation cannot be undone.

        Args:
            item_id (str): Unique identifier of item to delete

        Returns:
            bool: True if deletion was successful, False otherwise

        Example:
            ```python
            # Remove an item that's no longer in Jellyfin
            success = await db_manager.delete_item("old_item_id")
            if success:
                logger.info("Item deleted successfully")
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1

                cursor = await db.execute(
                    "DELETE FROM media_items WHERE item_id = ?",
                    (item_id,)
                )
                await db.commit()

                deleted_count = cursor.rowcount
                self._connection_count -= 1

                if deleted_count > 0:
                    self.logger.debug(f"Deleted item: {item_id}")
                    return True
                else:
                    self.logger.warning(f"Item not found for deletion: {item_id}")
                    return False

        except Exception as e:
            self.logger.error(f"Failed to delete item {item_id}: {e}")
            return False

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get database statistics for monitoring and diagnostics.

        This method provides insights into database health, performance, and content
        distribution. Useful for monitoring and troubleshooting.

        Returns:
            Dict[str, Any]: Database statistics including counts, sizes, and health metrics

        Example:
            ```python
            stats = await db_manager.get_stats()
            logger.info(f"Total items: {stats['total_items']}")
            logger.info(f"Database size: {stats['db_size_mb']:.1f} MB")
            logger.info(f"Movies: {stats['item_counts']['Movie']}")
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1
                db.row_factory = aiosqlite.Row

                stats = {}

                # Get total item count
                cursor = await db.execute("SELECT COUNT(*) as count FROM media_items")
                row = await cursor.fetchone()
                stats['total_items'] = row['count'] if row else 0

                # Get item counts by type
                cursor = await db.execute("""
                                          SELECT item_type, COUNT(*) as count
                                          FROM media_items
                                          GROUP BY item_type
                                          ORDER BY count DESC
                                          """)
                rows = await cursor.fetchall()
                stats['item_counts'] = {row['item_type']: row['count'] for row in rows}

                # Get database file size
                if os.path.exists(self.db_path):
                    db_size_bytes = os.path.getsize(self.db_path)
                    stats['db_size_mb'] = db_size_bytes / (1024 * 1024)
                else:
                    stats['db_size_mb'] = 0

                # Get WAL mode status
                cursor = await db.execute("PRAGMA journal_mode")
                row = await cursor.fetchone()
                stats['wal_mode'] = row[0].upper() == 'WAL' if row else False

                # Get recent activity (items added in last 24 hours)
                cursor = await db.execute("""
                                          SELECT COUNT(*) as count
                                          FROM media_items
                                          WHERE datetime(timestamp) > datetime('now', '-1 day')
                                          """)
                row = await cursor.fetchone()
                stats['recent_additions'] = row['count'] if row else 0

                self._connection_count -= 1
                return stats

        except Exception as e:
            self.logger.error(f"Failed to get database stats: {e}")
            return {}

    async def update_last_sync_time(self) -> None:
        """
        Update the last sync time in the database.

        This method records when the last library synchronization occurred
        for tracking and scheduling purposes.

        Example:
            ```python
            # After completing a sync
            await db_manager.update_last_sync_time()
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1

                current_time = datetime.now(timezone.utc).isoformat()

                await db.execute("""
                    INSERT OR REPLACE INTO sync_status (id, last_sync_time, sync_type, timestamp)
                    VALUES (1, ?, 'library_sync', ?)
                """, (current_time, current_time))

                await db.commit()
                self._connection_count -= 1

                self.logger.debug("Updated last sync time")

        except Exception as e:
            self.logger.warning(f"Failed to update last sync time: {e}")

    async def vacuum_database(self) -> bool:
        """
        Perform database maintenance (VACUUM operation).

        The VACUUM command rebuilds the database, reclaiming space from deleted
        records and optimizing the internal structure. This should be run periodically,
        especially after large numbers of deletions or updates.

        **When to VACUUM:**
        - After bulk deletions
        - Periodically (weekly/monthly) for maintenance
        - When database file size seems larger than expected
        - As part of regular maintenance routines

        Returns:
            bool: True if VACUUM completed successfully, False otherwise

        Example:
            ```python
            # Perform maintenance during low-activity periods
            success = await db_manager.vacuum_database()
            if success:
                logger.info("Database maintenance completed")
            else:
                logger.warning("Database maintenance failed")
            ```

        Note:
            VACUUM can take significant time for large databases and requires
            temporary disk space equal to the database size. It's best run
            during maintenance windows.
        """
        try:
            self.logger.info("Starting database VACUUM operation...")

            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1

                # VACUUM rebuilds the database file
                await db.execute("VACUUM")

                # Update table statistics for query optimization
                await db.execute("ANALYZE")

                self._connection_count -= 1
                self.logger.info("Database VACUUM completed successfully")
                return True

        except Exception as e:
            self.logger.error(f"Database VACUUM failed: {e}")
            return False

    async def close(self) -> None:
        """
        Clean shutdown of database manager.

        This method performs any necessary cleanup operations when the
        application is shutting down.

        Example:
            ```python
            # During application shutdown
            await db_manager.close()
            ```
        """
        self.logger.info(f"Database manager shutdown. Final connection count: {self._connection_count}")
        if self._connection_count > 0:
            self.logger.warning(f"Shutdown with {self._connection_count} active connections")

        # WAL mode connections will be automatically closed by SQLite
        # No explicit cleanup needed for aiosqlite connections