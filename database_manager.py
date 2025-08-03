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
            print(f"Found: {retrieved.name} ({retrieved.video_height}p)")

        # Batch operations for efficiency
        items = [item1, item2, item3]
        results = await db_manager.save_items_batch(items)
        ```

    Note:
        WAL mode creates additional files (.wal, .shm) alongside the main database.
        These are automatically managed by SQLite and improve concurrent performance.
        The class handles all WAL-specific configuration and maintenance automatically.
    """

    def __init__(self, config: DatabaseConfig, logger: logging.Logger):
        """
        Initialize database manager with configuration and logging.

        Sets up the database manager with the provided configuration, ensuring
        the parent directory exists and initializing tracking variables.

        Args:
            config (DatabaseConfig): Database configuration with path and WAL settings
            logger (logging.Logger): Logger instance for database operations

        Note:
            This constructor only sets up the initial state. Actual database
            initialization (creating tables, enabling WAL) happens in the
            async initialize() method.
        """
        self.config = config
        self.logger = logger
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

        **Configuration Steps:**
        1. Enable WAL mode for concurrent access
        2. Set performance optimization PRAGMAs
        3. Create the media_items table if it doesn't exist
        4. Create indexes for query performance
        5. Run initial maintenance (ANALYZE)

        Raises:
            Exception: If database initialization fails

        Example:
            ```python
            db_manager = DatabaseManager(config, logger)
            try:
                await db_manager.initialize()
                logger.info("Database ready for operations")
            except Exception as e:
                logger.error(f"Database initialization failed: {e}")
                raise
            ```
        """
        try:
            self.logger.info(f"Initializing database at {self.db_path}")
            
            # Use context manager for automatic connection handling
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1
                
                # Enable WAL mode for better concurrent access
                if self.wal_mode:
                    await db.execute("PRAGMA journal_mode=WAL")
                    self.logger.debug("Enabled WAL mode for concurrent access")

                # Set performance optimization settings
                await db.execute("PRAGMA synchronous=NORMAL")  # Balance safety/performance
                await db.execute("PRAGMA cache_size=10000")     # 40MB cache (10000 pages * 4KB)
                await db.execute("PRAGMA temp_store=MEMORY")    # Store temp tables in memory
                await db.execute("PRAGMA mmap_size=268435456")  # 256MB memory-mapped I/O

                # Create the media_items table with comprehensive schema
                await self._create_tables(db)
                
                # Create indexes for query performance
                await self._create_indexes(db)
                
                # Run initial database analysis for query optimization
                await db.execute("ANALYZE")
                await db.commit()

                self._connection_count -= 1
                self.logger.info("Database initialization completed successfully")

        except Exception as e:
            self.logger.error(f"Database initialization failed: {e}")
            raise

    async def _create_tables(self, db) -> None:
        """
        Create the media_items table with comprehensive schema.

        This private method creates the main table that stores all media metadata.
        The schema is designed to handle all media types (movies, TV, music, photos)
        while maintaining good performance for queries and updates.

        **Schema Design Principles:**
        - Single table for all media types (reduces joins)
        - TEXT fields for flexibility with different data formats
        - JSON fields for arrays/lists (genres, artists, etc.) 
        - Proper field sizing for SQLite optimization
        - Content hash for efficient change detection

        Args:
            db: Active database connection from initialize()
        """
        # Create comprehensive media_items table
        create_sql = """
        CREATE TABLE IF NOT EXISTS media_items (
            -- ==================== CORE IDENTIFICATION ====================
            item_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            item_type TEXT NOT NULL,
            
            -- ==================== CONTENT METADATA ====================
            year INTEGER,
            series_name TEXT,
            season_number INTEGER,
            episode_number INTEGER,
            overview TEXT,
            
            -- ==================== VIDEO TECHNICAL SPECIFICATIONS ====================
            video_height INTEGER,
            video_width INTEGER,
            video_codec TEXT,
            video_profile TEXT,
            video_range TEXT,
            video_framerate REAL,
            aspect_ratio TEXT,
            
            -- ==================== AUDIO TECHNICAL SPECIFICATIONS ====================
            audio_codec TEXT,
            audio_channels INTEGER,
            audio_language TEXT,
            audio_bitrate INTEGER,
            
            -- ==================== EXTERNAL PROVIDER IDS ====================
            imdb_id TEXT,
            tmdb_id TEXT,
            tvdb_id TEXT,
            
            -- ==================== EXTENDED METADATA FROM API ====================
            date_created TEXT,
            date_modified TEXT,
            runtime_ticks INTEGER,
            official_rating TEXT,
            genres TEXT, -- JSON string array
            studios TEXT, -- JSON string array
            tags TEXT, -- JSON string array
            community_rating REAL,
            critic_rating REAL,
            premiere_date TEXT,
            
            -- ==================== MUSIC-SPECIFIC METADATA ====================
            album TEXT,
            artists TEXT, -- JSON string array
            album_artist TEXT,
            
            -- ==================== PHOTO-SPECIFIC METADATA ====================
            width INTEGER,
            height INTEGER,
            
            -- ==================== INTERNAL TRACKING ====================
            content_hash TEXT,
            timestamp TEXT,
            file_path TEXT,
            file_size INTEGER,
            last_modified TEXT,
            
            -- ==================== RELATIONSHIPS ====================
            series_id TEXT,
            parent_id TEXT
        )
        """
        
        await db.execute(create_sql)
        self.logger.debug("Created media_items table with comprehensive schema")

    async def _create_indexes(self, db) -> None:
        """
        Create database indexes for query performance optimization.

        Indexes speed up common queries by creating sorted lookup structures.
        These indexes are carefully chosen based on the service's query patterns.

        **Index Strategy:**
        - Primary access is by item_id (automatic primary key index)
        - Content hash for change detection queries
        - Item type for filtering different media types
        - Series relationships for TV show queries
        - Combined indexes for complex filtering

        Args:
            db: Active database connection from initialize()
        """
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_content_hash ON media_items(content_hash)",
            "CREATE INDEX IF NOT EXISTS idx_item_type ON media_items(item_type)",
            "CREATE INDEX IF NOT EXISTS idx_series_id ON media_items(series_id)",
            "CREATE INDEX IF NOT EXISTS idx_timestamp ON media_items(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_series_season ON media_items(series_name, season_number)",
        ]
        
        for index_sql in indexes:
            await db.execute(index_sql)
        
        self.logger.debug(f"Created {len(indexes)} database indexes for performance")

    async def save_item(self, item: MediaItem) -> bool:
        """
        Save a single media item to the database.

        This method handles the conversion of a MediaItem dataclass to database
        storage, including JSON serialization of list fields and proper error handling.

        **Data Serialization:**
        Some MediaItem fields contain lists (genres, artists, etc.) that need to be
        converted to JSON strings for SQLite storage. This method handles that
        conversion automatically.

        **Upsert Operation:**
        Uses INSERT OR REPLACE to handle both new items and updates to existing items.
        This is more efficient than separate SELECT/INSERT/UPDATE operations.

        Args:
            item (MediaItem): Media item to save to database

        Returns:
            bool: True if save was successful, False otherwise

        Example:
            ```python
            movie = MediaItem(
                item_id="abc123",
                name="The Matrix",
                item_type="Movie",
                genres=["Action", "Sci-Fi"],  # Will be JSON serialized
                video_height=1080
            )
            
            success = await db_manager.save_item(movie)
            if success:
                print("Movie saved successfully")
            else:
                print("Failed to save movie")
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1
                
                # Convert MediaItem to dictionary for database storage
                item_dict = asdict(item)
                
                # Serialize list fields to JSON strings for SQLite storage
                for field in ['genres', 'studios', 'tags', 'artists']:
                    if item_dict.get(field) is not None:
                        item_dict[field] = json.dumps(item_dict[field])

                # Build dynamic INSERT OR REPLACE query
                # This handles both new items and updates to existing items
                fields = ', '.join(item_dict.keys())
                placeholders = ', '.join(['?' for _ in item_dict.keys()])
                values = list(item_dict.values())

                sql = f"INSERT OR REPLACE INTO media_items ({fields}) VALUES ({placeholders})"
                
                await db.execute(sql, values)
                await db.commit()
                
                self._connection_count -= 1
                self.logger.debug(f"Saved item: {item.name} ({item.item_id})")
                return True

        except Exception as e:
            self.logger.error(f"Failed to save item {item.item_id}: {e}")
            return False

    async def save_items_batch(self, items: List[MediaItem]) -> List[bool]:
        """
        Save multiple media items in a single database transaction.

        Batch operations are much more efficient than individual saves when
        processing multiple items (like during library synchronization).
        All items are processed in a single transaction for consistency.

        **Transaction Benefits:**
        - Atomic operation: all items succeed or all fail
        - Better performance: reduced connection overhead
        - Consistency: no partial updates if something goes wrong
        - Reduced lock contention in WAL mode

        Args:
            items (List[MediaItem]): List of media items to save

        Returns:
            List[bool]: Success status for each item (True/False)

        Example:
            ```python
            # Process multiple items from library sync
            items = [movie1, movie2, tv_episode1, music_track1]
            results = await db_manager.save_items_batch(items)
            
            success_count = sum(results)
            print(f"Saved {success_count}/{len(items)} items successfully")
            ```
        """
        if not items:
            return []

        results = []
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1
                
                # Process all items in a single transaction
                async with db.execute("BEGIN TRANSACTION"):
                    for item in items:
                        try:
                            # Convert item to dict and serialize JSON fields
                            item_dict = asdict(item)
                            for field in ['genres', 'studios', 'tags', 'artists']:
                                if item_dict.get(field) is not None:
                                    item_dict[field] = json.dumps(item_dict[field])

                            # Build and execute INSERT OR REPLACE
                            fields = ', '.join(item_dict.keys())
                            placeholders = ', '.join(['?' for _ in item_dict.keys()])
                            values = list(item_dict.values())

                            sql = f"INSERT OR REPLACE INTO media_items ({fields}) VALUES ({placeholders})"
                            await db.execute(sql, values)
                            results.append(True)

                        except Exception as e:
                            self.logger.error(f"Failed to save batch item {item.item_id}: {e}")
                            results.append(False)
                    
                    # Commit the entire transaction
                    await db.execute("COMMIT")

                self._connection_count -= 1
                success_count = sum(results)
                self.logger.info(f"Batch save completed: {success_count}/{len(items)} items successful")
                return results

        except Exception as e:
            self.logger.error(f"Batch save failed: {e}")
            return [False] * len(items)

    async def get_item(self, item_id: str) -> Optional[MediaItem]:
        """
        Retrieve a media item by its Jellyfin ID.

        This method fetches an item from the database and converts it back to
        a MediaItem dataclass, handling JSON deserialization of list fields.

        **Data Deserialization:**
        List fields (genres, artists, etc.) are stored as JSON strings and need
        to be converted back to Python lists when loading from the database.

        Args:
            item_id (str): Unique Jellyfin item identifier

        Returns:
            Optional[MediaItem]: Media item if found, None otherwise

        Example:
            ```python
            # Retrieve an item for comparison
            existing_item = await db_manager.get_item("abc123")
            if existing_item:
                print(f"Found: {existing_item.name}")
                print(f"Resolution: {existing_item.video_height}p")
                print(f"Genres: {existing_item.genres}")
            else:
                print("Item not found in database")
            ```
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
                
                if not row:
                    return None

                # Convert database row to dictionary
                item_dict = dict(row)
                
                # Deserialize JSON fields back to Python lists
                for field in ['genres', 'studios', 'tags', 'artists']:
                    if item_dict.get(field):
                        try:
                            item_dict[field] = json.loads(item_dict[field])
                        except json.JSONDecodeError:
                            # Handle corrupted JSON by setting to empty list
                            item_dict[field] = []
                    else:
                        item_dict[field] = None

                self._connection_count -= 1
                return MediaItem(**item_dict)

        except Exception as e:
            self.logger.error(f"Failed to retrieve item {item_id}: {e}")
            return None

    async def get_items_by_content_hash(self, content_hash: str) -> List[MediaItem]:
        """
        Find items with matching content hash for change detection.

        This method is crucial for detecting upgraded versions of existing items.
        Items with the same content hash have identical technical specifications,
        indicating they're the same content at the same quality level.

        **Change Detection Logic:**
        When a webhook arrives, we check if items with the same content hash
        already exist. If not, this might be a quality upgrade of existing content.

        Args:
            content_hash (str): MD5 hash of technical specifications

        Returns:
            List[MediaItem]: Items matching the content hash (usually 0 or 1)

        Example:
            ```python
            # Check for existing items with same quality specs
            matches = await db_manager.get_items_by_content_hash(new_item.content_hash)
            if matches:
                print("Found existing item with same specifications")
            else:
                print("This appears to be new or upgraded content")
            ```
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                self._connection_count += 1
                db.row_factory = aiosqlite.Row
                
                cursor = await db.execute(
                    "SELECT * FROM media_items WHERE content_hash = ?",
                    (content_hash,)
                )
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
            self.logger.error(f"Failed to query content hash {content_hash}: {e}")
            return []

    async def get_items_by_type(self, item_type: str, limit: Optional[int] = None) -> List[MediaItem]:
        """
        Retrieve items by media type with optional limit.

        This method is useful for filtering content by type (movies, episodes, etc.)
        and supports pagination through the limit parameter.

        Args:
            item_type (str): Media type to filter by (Movie, Episode, Audio, etc.)
            limit (Optional[int]): Maximum number of items to return

        Returns:
            List[MediaItem]: Items matching the specified type

        Example:
            ```python
            # Get recent movies for testing
            recent_movies = await db_manager.get_items_by_type("Movie", limit=10)
            print(f"Found {len(recent_movies)} recent movies")
            
            # Get all TV episodes
            all_episodes = await db_manager.get_items_by_type("Episode")
            ```
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
                print("Item deleted successfully")
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

    async def get_database_stats(self) -> Dict[str, Any]:
        """
        Get database statistics for monitoring and diagnostics.

        This method provides insights into database health, performance, and content
        distribution. Useful for monitoring and troubleshooting.

        Returns:
            Dict[str, Any]: Database statistics including counts, sizes, and health metrics

        Example:
            ```python
            stats = await db_manager.get_database_stats()
            print(f"Total items: {stats['total_items']}")
            print(f"Database size: {stats['db_size_mb']:.1f} MB")
            print(f"Movies: {stats['item_counts']['Movie']}")
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
                print("Database maintenance completed")
            else:
                print("Database maintenance failed")
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

        This method performs any necessary cleanup operations before the
        database manager is destroyed. Currently serves as a placeholder
        for future cleanup needs.

        Example:
            ```python
            # During application shutdown
            await db_manager.close()
            ```
        """
        self.logger.debug("Database manager shutdown completed")
        # aiosqlite connections are automatically closed by context managers
        # No explicit cleanup needed currently