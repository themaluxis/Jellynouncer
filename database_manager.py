"""
JellyNotify Database Manager

This module handles all SQLite database operations including table creation,
item storage/retrieval, and maintenance tasks.
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


class DatabaseManager:
    """
    Enhanced SQLite database manager with WAL mode and comprehensive error handling.

    This class manages all database operations for the JellyNotify service,
    including table creation, item storage/retrieval, and maintenance tasks.

    Key features:
    - WAL (Write-Ahead Logging) mode for better concurrent access
    - Batch operations for improved performance
    - Content hash-based change detection
    - Automatic database maintenance (VACUUM)
    - Comprehensive error handling and logging

    Attributes:
        config: Database configuration settings
        logger: Logger instance for database operations
        db_path: Full path to SQLite database file
        wal_mode: Whether WAL mode is enabled

    Example:
        ```python
        db_manager = DatabaseManager(config.database, logger)
        await db_manager.initialize()

        # Save a media item
        item = MediaItem(item_id="123", name="Movie", item_type="Movie")
        success = await db_manager.save_item(item)

        # Retrieve it later
        retrieved = await db_manager.get_item("123")
        ```

    Note:
        WAL mode allows multiple readers to access the database while a writer
        is active, which improves performance in concurrent scenarios like
        webhook processing during library syncs.
    """

    def __init__(self, config: DatabaseConfig, logger: logging.Logger):
        """
        Initialize database manager with configuration and logging.

        Args:
            config: Database configuration settings
            logger: Logger instance for database operations
        """
        self.config = config
        self.logger = logger
        self.db_path = config.path
        self.wal_mode = config.wal_mode

        # Ensure the parent directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def initialize(self) -> None:
        """
        Initialize database tables and configure SQLite settings.

        This method sets up the database schema and configures SQLite for
        optimal performance and reliability. It includes:
        - Enabling WAL mode for concurrent access
        - Setting performance-oriented PRAGMA settings
        - Creating tables and indexes

        Raises:
            aiosqlite.Error: Database operation errors
            Exception: Unexpected initialization errors
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Configure SQLite for performance and reliability
                if self.wal_mode:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA synchronous=NORMAL")
                    await db.execute("PRAGMA temp_store=memory")
                    await db.execute("PRAGMA mmap_size=268435456")
                    await db.execute("PRAGMA cache_size=-32000")
                    await db.execute("PRAGMA busy_timeout=30000")

                # Create the main media items table with complete schema
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS media_items (
                        -- Core identification fields
                        item_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        item_type TEXT NOT NULL,

                        -- Basic metadata
                        year INTEGER,
                        series_name TEXT,
                        season_number INTEGER,
                        episode_number INTEGER,
                        overview TEXT,

                        -- Video technical specifications
                        video_height INTEGER,
                        video_width INTEGER,
                        video_codec TEXT,
                        video_profile TEXT,
                        video_range TEXT,
                        video_framerate REAL,
                        aspect_ratio TEXT,

                        -- Audio technical specifications
                        audio_codec TEXT,
                        audio_channels INTEGER,
                        audio_language TEXT,
                        audio_bitrate INTEGER,

                        -- External provider IDs
                        imdb_id TEXT,
                        tmdb_id TEXT,
                        tvdb_id TEXT,

                        -- Enhanced metadata from API
                        date_created TEXT,
                        date_modified TEXT,
                        runtime_ticks INTEGER,
                        official_rating TEXT,
                        genres TEXT, -- JSON string
                        studios TEXT, -- JSON string
                        tags TEXT, -- JSON string

                        -- Music-specific metadata
                        album TEXT,
                        artists TEXT, -- JSON string
                        album_artist TEXT,

                        -- Photo-specific metadata
                        width INTEGER,
                        height INTEGER,

                        -- Internal tracking
                        timestamp TEXT,
                        file_path TEXT,
                        file_size INTEGER,
                        content_hash TEXT,
                        last_modified TEXT,

                        -- Enhanced metadata for rich notifications
                        series_id TEXT,
                        parent_id TEXT,
                        community_rating REAL,
                        critic_rating REAL,
                        premiere_date TEXT,
                        end_date TEXT,

                        -- External rating data
                        omdb_imdb_rating TEXT,
                        omdb_rt_rating TEXT,
                        omdb_metacritic_rating TEXT,
                        tmdb_rating REAL,
                        tmdb_vote_count INTEGER,
                        tvdb_rating REAL,

                        -- Rating fetch metadata
                        ratings_last_updated TEXT,
                        ratings_fetch_failed BOOLEAN DEFAULT 0,

                        -- Timestamps
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create ratings cache table for efficient rating storage and caching
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS ratings_cache (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,

                        -- Item identification
                        imdb_id TEXT,
                        tmdb_id TEXT,
                        tvdb_id TEXT,

                        -- Rating data from various services
                        omdb_imdb_rating TEXT,
                        omdb_rt_rating TEXT,
                        omdb_metacritic_rating TEXT,
                        omdb_plot TEXT,
                        omdb_awards TEXT,

                        tmdb_rating REAL,
                        tmdb_vote_count INTEGER,
                        tmdb_popularity REAL,

                        tvdb_rating REAL,
                        tvdb_vote_count INTEGER,

                        -- Cache management
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        expires_at DATETIME,

                        -- Ensure we don't duplicate entries for the same external IDs
                        UNIQUE(imdb_id, tmdb_id, tvdb_id)
                    )
                """)

                # Create indexes for efficient lookups
                indexes = [
                    # Core indexes for media_items
                    "CREATE INDEX IF NOT EXISTS idx_item_type ON media_items(item_type)",
                    "CREATE INDEX IF NOT EXISTS idx_series_name ON media_items(series_name)",
                    "CREATE INDEX IF NOT EXISTS idx_updated_at ON media_items(updated_at)",
                    "CREATE INDEX IF NOT EXISTS idx_content_hash ON media_items(content_hash)",

                    # Enhanced indexes for rating functionality
                    "CREATE INDEX IF NOT EXISTS idx_ratings_last_updated ON media_items(ratings_last_updated)",
                    "CREATE INDEX IF NOT EXISTS idx_series_id ON media_items(series_id)",
                    "CREATE INDEX IF NOT EXISTS idx_parent_id ON media_items(parent_id)",

                    # Ratings cache indexes
                    "CREATE INDEX IF NOT EXISTS idx_ratings_imdb ON ratings_cache(imdb_id)",
                    "CREATE INDEX IF NOT EXISTS idx_ratings_tmdb ON ratings_cache(tmdb_id)",
                    "CREATE INDEX IF NOT EXISTS idx_ratings_tvdb ON ratings_cache(tvdb_id)",
                    "CREATE INDEX IF NOT EXISTS idx_ratings_expires ON ratings_cache(expires_at)",
                ]

                for index_sql in indexes:
                    await db.execute(index_sql)

                await db.commit()
                self.logger.info("Database initialized successfully with ratings support")

        except aiosqlite.Error as e:
            self.logger.error(f"Database initialization failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during database initialization: {e}")
            raise

    async def get_last_sync_time(self) -> Optional[str]:
        """Get the timestamp of the last database update for sync scheduling."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT MAX(updated_at) FROM media_items")
                row = await cursor.fetchone()
                return row[0] if row and row[0] else None

        except aiosqlite.Error as e:
            self.logger.error(f"Database error retrieving last sync time: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error retrieving last sync time: {e}")
            return None

    async def get_item(self, item_id: str) -> Optional[MediaItem]:
        """
        Retrieve a media item by its Jellyfin ID.

        Args:
            item_id: Jellyfin item identifier

        Returns:
            MediaItem instance if found, None otherwise
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Use Row factory to get column names with values
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM media_items WHERE item_id = ?", (item_id,)
                )
                row = await cursor.fetchone()

                if row:
                    # Convert database row to dictionary
                    item_dict = dict(row)

                    # Deserialize JSON fields back to Python lists
                    for field in ['genres', 'studios', 'tags', 'artists']:
                        if field in item_dict and isinstance(item_dict[field], str):
                            try:
                                item_dict[field] = json.loads(item_dict[field])
                            except (json.JSONDecodeError, TypeError):
                                # If JSON parsing fails, use empty list as fallback
                                item_dict[field] = []

                    return MediaItem(**item_dict)
                return None

        except aiosqlite.Error as e:
            self.logger.error(f"Database error retrieving item {item_id}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error retrieving item {item_id}: {e}")
            raise

    async def get_item_hash(self, item_id: str) -> Optional[str]:
        """
        Get only the content hash of an item (performance optimization).

        This method is used for change detection when we only need to know
        if an item has changed, not retrieve all its data.

        Args:
            item_id: Jellyfin item identifier

        Returns:
            Content hash string if item exists, None otherwise
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT content_hash FROM media_items WHERE item_id = ?", (item_id,)
                )
                row = await cursor.fetchone()
                return row[0] if row else None

        except aiosqlite.Error as e:
            self.logger.error(f"Database error retrieving hash for {item_id}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error retrieving hash for {item_id}: {e}")
            raise

    async def save_item(self, item: MediaItem) -> bool:
        """
        Save or update a single media item in the database.

        This method uses INSERT OR REPLACE to handle both new items and updates
        to existing items. It automatically sets the updated_at timestamp.

        Args:
            item: MediaItem instance to save

        Returns:
            True if save successful, False otherwise
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Convert MediaItem to dictionary for database storage
                item_dict = asdict(item)
                item_dict['updated_at'] = datetime.now(timezone.utc).isoformat()

                # Serialize list fields to JSON strings for storage
                for field in ['genres', 'studios', 'tags', 'artists']:
                    if field in item_dict and isinstance(item_dict[field], list):
                        item_dict[field] = json.dumps(item_dict[field])

                # Build dynamic SQL based on available fields
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

        except aiosqlite.Error as e:
            self.logger.error(f"Database error saving item {item.item_id}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error saving item {item.item_id}: {e}")
            return False

    async def save_items_batch(self, items: List[MediaItem]) -> int:
        """
        Save multiple items in a single transaction for better performance.

        Args:
            items: List of MediaItem instances to save

        Returns:
            Number of items successfully saved
        """
        if not items:
            return 0

        saved_count = 0
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Use explicit transaction for atomic batch operation
                await db.execute("BEGIN TRANSACTION")

                for item in items:
                    try:
                        item_dict = asdict(item)
                        item_dict['updated_at'] = datetime.now(timezone.utc).isoformat()

                        # Serialize list fields to JSON
                        for field in ['genres', 'studios', 'tags', 'artists']:
                            if field in item_dict and isinstance(item_dict[field], list):
                                item_dict[field] = json.dumps(item_dict[field])

                        # Build dynamic SQL
                        columns = list(item_dict.keys())
                        placeholders = ['?' for _ in columns]
                        values = list(item_dict.values())

                        sql = f"""
                            INSERT OR REPLACE INTO media_items 
                            ({', '.join(columns)}) 
                            VALUES ({', '.join(placeholders)})
                        """

                        await db.execute(sql, values)
                        saved_count += 1

                    except Exception as e:
                        self.logger.warning(f"Failed to save item {item.item_id} in batch: {e}")
                        # Continue with other items rather than failing the entire batch

                await db.commit()
                self.logger.debug(f"Successfully saved {saved_count}/{len(items)} items in batch")

        except aiosqlite.Error as e:
            self.logger.error(f"Database error during batch save: {e}")
            try:
                await db.rollback()
            except:
                pass  # Rollback might fail if connection is closed
        except Exception as e:
            self.logger.error(f"Unexpected error during batch save: {e}")

        return saved_count

    async def vacuum_database(self) -> None:
        """
        Perform VACUUM operation to reclaim space and optimize database.

        The VACUUM command rebuilds the database file, reclaiming unused space
        and optimizing the database structure. This is important for long-running
        applications that frequently update data.

        Note:
            VACUUM can be time-consuming on large databases and requires
            exclusive access, so it should be run during maintenance windows.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("VACUUM")
                await db.commit()
                self.logger.info("Database vacuum completed successfully")
        except aiosqlite.Error as e:
            self.logger.error(f"Database vacuum failed: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error during database vacuum: {e}")

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive database statistics for monitoring and debugging.

        Returns:
            Dictionary containing database statistics including:
            - total_items: Total number of media items
            - item_types: Breakdown by media type
            - last_updated: Timestamp of most recent update
            - database_path: Path to database file
            - wal_mode: Whether WAL mode is enabled
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Get total item count
                cursor = await db.execute("SELECT COUNT(*) FROM media_items")
                total_items = (await cursor.fetchone())[0]

                # Get breakdown by item type
                cursor = await db.execute(
                    "SELECT item_type, COUNT(*) FROM media_items GROUP BY item_type ORDER BY COUNT(*) DESC"
                )
                item_types = dict(await cursor.fetchall())

                # Get last update timestamp
                cursor = await db.execute("SELECT MAX(updated_at) FROM media_items")
                last_updated = (await cursor.fetchone())[0]

                return {
                    "total_items": total_items,
                    "item_types": item_types,
                    "last_updated": last_updated,
                    "database_path": self.db_path,
                    "wal_mode": self.wal_mode
                }

        except aiosqlite.Error as e:
            self.logger.error(f"Database error retrieving stats: {e}")
            return {"error": str(e)}
        except Exception as e:
            self.logger.error(f"Unexpected error retrieving stats: {e}")
            return {"error": str(e)}