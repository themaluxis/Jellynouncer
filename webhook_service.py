#!/usr/bin/env python3
"""
Webhook Service Module

This module contains the main WebhookService class that orchestrates all components
to provide the complete JellyNotify functionality. It manages service initialization,
background maintenance tasks, webhook processing, and graceful shutdown procedures.

Classes:
    WebhookService: Main service orchestrator that coordinates all other components
"""

import asyncio
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

import aiohttp
from fastapi import HTTPException

from config_models import AppConfig, ConfigurationValidator
from webhook_models import WebhookPayload
from media_models import MediaItem
from database_manager import DatabaseManager
from jellyfin_api import JellyfinAPI
from discord_services import DiscordNotifier
from rating_services import RatingService
from change_detector import ChangeDetector
from utils import setup_logging


class WebhookService:
    """
    Main webhook service that orchestrates all components.

    This is the central service class that coordinates all the other components
    to provide the complete JellyNotify functionality. It manages:

    - Service initialization and configuration
    - Background maintenance tasks
    - Webhook processing from Jellyfin
    - Library synchronization
    - Health monitoring and diagnostics
    - Graceful shutdown procedures

    The service follows an async-first design and includes comprehensive error
    handling to ensure reliable operation in production environments.
    """

    def __init__(self):
        """
        Initialize webhook service with logging and configuration loading.

        This constructor handles the early initialization that must happen
        synchronously, including logging setup and configuration validation.
        The actual async initialization happens in the initialize() method.

        Raises:
            SystemExit: If configuration loading/validation fails
        """
        # Set up logging - consolidated to use utils module
        self.logger = setup_logging()

        # Initialize component references
        self.config = None
        self.db = None
        self.jellyfin = None
        self.change_detector = None
        self.discord = None
        self.rating_service = None

        # Initialize service state tracking attributes
        self.last_vacuum = 0
        self.server_was_offline = False
        self.sync_in_progress = False
        self.is_background_sync = False
        self.initial_sync_complete = False
        self.shutdown_event = asyncio.Event()

        # Session for HTTP requests
        self.session = None

        # Load and validate configuration
        try:
            validator = ConfigurationValidator(self.logger)
            self.config = validator.load_and_validate_config()
            self.logger.info("Configuration loaded and validated successfully")
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise SystemExit(1)

        # Initialize components with validated configuration
        try:
            self.db = DatabaseManager(self.config.database, self.logger)
            self.jellyfin = JellyfinAPI(self.config.jellyfin, self.logger)
            self.change_detector = ChangeDetector(self.config.notifications, self.logger)
            self.discord = DiscordNotifier(self.config.discord, self.config.jellyfin.server_url, self.logger)
            self.rating_service = RatingService(self.config.rating_services, self.logger)

        except Exception as e:
            self.logger.error(f"Failed to initialize service components: {e}")
            raise SystemExit(1)

    async def initialize(self) -> None:
        """
        Perform async initialization of all service components.

        This method handles the initialization tasks that require async operations,
        including database setup, API connections, and initial library sync.

        The initialization process includes smart sync behavior:
        - If this is the first run, perform a blocking initial sync
        - If an init_complete marker exists, perform background sync
        - Handle connection failures gracefully

        Raises:
            Exception: If critical initialization steps fail
        """
        try:
            self.logger.info("Initializing JellyNotify Discord Webhook Service...")

            # Step 1: Initialize HTTP session
            try:
                timeout = aiohttp.ClientTimeout(total=30, connect=10)
                connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
                self.session = aiohttp.ClientSession(
                    timeout=timeout,
                    connector=connector,
                    headers={'User-Agent': 'JellyNotify/2.0.0'}
                )
                self.logger.info("HTTP session initialized successfully")
            except Exception as e:
                self.logger.error(f"HTTP session initialization failed: {e}")
                raise

            # Step 2: Initialize database
            try:
                await self.db.initialize()
                self.logger.info("Database initialized successfully")
            except Exception as e:
                self.logger.error(f"Database initialization failed: {e}")
                raise

            # Step 3: Initialize Discord notifier
            try:
                await self.discord.initialize(self.config.templates, self.session)
                self.logger.info("Discord notifier initialized successfully")
            except Exception as e:
                self.logger.error(f"Discord notifier initialization failed: {e}")
                raise

            # Step 4: Initialize rating service with shared session and database
            try:
                await self.rating_service.initialize(self.session, self.db)
                self.logger.info("Rating service initialized successfully")
            except Exception as e:
                self.logger.error(f"Rating service initialization failed: {e}")
                # Don't raise - rating service is optional

            # Step 5: Link services for cross-component access
            self.discord._webhook_service = self

            # Step 6: Connect to Jellyfin and handle initial sync
            try:
                if await self.jellyfin.connect():
                    self.logger.info("Successfully connected to Jellyfin")

                    # Check for init_complete marker to determine sync strategy
                    init_complete_path = Path("/app/data/init_complete")

                    if self.config.sync.startup_sync:
                        if init_complete_path.exists():
                            # Subsequent startup - run background sync
                            self.logger.info("Init complete marker found - performing full background sync")
                            await self._perform_background_startup_sync()
                            self.initial_sync_complete = True
                        else:
                            # First startup - run blocking sync
                            self.logger.info("No init complete marker found - performing blocking initial sync")
                            await self._perform_startup_sync()
                            self.initial_sync_complete = True
                    else:
                        self.logger.info("Startup sync disabled in configuration")
                        self.initial_sync_complete = True
                else:
                    self.logger.error("Failed to connect to Jellyfin server")
                    self.server_was_offline = True

            except Exception as e:
                self.logger.error(f"Jellyfin connection failed: {e}")
                self.server_was_offline = True

            self.logger.info("JellyNotify service initialization completed")

        except Exception as e:
            self.logger.error(f"Service initialization failed: {e}")
            raise

    async def _perform_background_startup_sync(self) -> None:
        """
        Perform full background startup sync for subsequent service starts.

        This method runs a full library sync in the background without blocking
        webhook processing. It's used on subsequent service starts when we
        already have a populated database.
        """
        try:
            self.logger.info("Starting full background startup sync (init_complete marker found)...")

            # Create background task for sync
            sync_task = asyncio.create_task(self.sync_jellyfin_library(background=True))

            # Set up completion callback for logging
            def log_completion(task):
                try:
                    result = task.result()
                    if result.get("status") == "success":
                        self.logger.info("Background startup sync completed successfully")
                    else:
                        self.logger.warning(
                            f"Background startup sync completed with status: {result.get('status', 'unknown')} - keeping init_complete marker")
                except Exception as e:
                    self.logger.error(f"Background startup sync failed: {e} - keeping init_complete marker")

            sync_task.add_done_callback(log_completion)

        except Exception as e:
            self.logger.error(f"Failed to start background startup sync: {e}")

    async def _perform_startup_sync(self) -> None:
        """
        Perform blocking startup sync for first-time service initialization.

        This method runs a complete library sync and blocks webhook processing
        until it completes. It's only used on the first service startup to
        populate the database.
        """
        try:
            self.logger.info("Starting initial Jellyfin library sync...")
            result = await self.sync_jellyfin_library()

            # Create init_complete marker only if sync was successful
            if result.get("status") == "success":
                init_complete_path = Path("/app/data/init_complete")
                try:
                    init_complete_path.touch(exist_ok=True)
                    self.logger.info("Initial sync completed successfully - created init_complete marker")
                except Exception as e:
                    self.logger.warning(f"Could not create init_complete marker: {e}")
            else:
                self.logger.warning(f"Initial sync completed with status: {result.get('status', 'unknown')}")

        except Exception as e:
            self.logger.error(f"Initial sync failed: {e}")
            # Don't raise - service can continue without initial sync

    async def sync_jellyfin_library(self, background: bool = False) -> Dict[str, Any]:
        """
        Synchronize entire Jellyfin library to local database.

        This method performs a complete sync of the Jellyfin library, processing
        all items in batches for efficiency. It includes:
        - Concurrent sync prevention
        - Progress monitoring and logging
        - Error handling and recovery
        - Performance metrics

        Args:
            background: Whether this is a background sync (non-blocking)

        Returns:
            Dictionary with sync results including status, item counts, and timing
        """
        # Prevent concurrent syncs which could cause database conflicts
        if self.sync_in_progress:
            message = "Library sync already in progress, skipping new request"
            self.logger.warning(message)
            return {"status": "warning", "message": message}

        self.sync_in_progress = True
        self.is_background_sync = background
        sync_start_time = time.time()

        try:
            sync_type = "background" if background else "initial"
            self.logger.info(f"Starting {sync_type} Jellyfin library sync...")

            # Verify Jellyfin connectivity before starting
            if not await self.jellyfin.is_connected():
                if not await self.jellyfin.connect():
                    raise ConnectionError("Cannot connect to Jellyfin server for sync")

            batch_size = self.config.sync.sync_batch_size
            items_processed = 0

            # Use callback-based processing for memory efficiency
            async def count_processed_items(items):
                nonlocal items_processed
                items_processed += len(items)
                await self.process_batch(items)

            await self.jellyfin.get_all_items(
                batch_size=batch_size,
                process_batch_callback=count_processed_items
            )

            sync_duration = time.time() - sync_start_time
            self.logger.info(
                f"Jellyfin library sync completed successfully. "
                f"Processed {items_processed} items in {sync_duration:.1f} seconds"
            )

            return {
                "status": "success",
                "message": f"Library sync completed. Processed {items_processed} items",
                "items_processed": items_processed,
                "duration_seconds": round(sync_duration, 1)
            }

        except Exception as e:
            sync_duration = time.time() - sync_start_time
            error_msg = f"Library sync failed after {sync_duration:.1f} seconds: {e}"
            self.logger.error(error_msg)
            return {"status": "error", "message": error_msg}

        finally:
            self.sync_in_progress = False
            self.is_background_sync = False

    async def process_batch(self, jellyfin_items: List[Dict[str, Any]]) -> None:
        """
        Process a batch of items from Jellyfin API efficiently.

        This method handles batch processing during library sync, including:
        - Converting Jellyfin data to MediaItem objects
        - Change detection using content hashes
        - Batch database operations for performance
        - Error handling for individual items

        Args:
            jellyfin_items: List of item dictionaries from Jellyfin API
        """
        if not jellyfin_items:
            return

        processed_count = 0
        error_count = 0
        media_items = []

        for jellyfin_item in jellyfin_items:
            try:
                # Convert Jellyfin data to MediaItem
                media_item = self.jellyfin.extract_media_item(jellyfin_item)

                # Check if item has changed using hash comparison
                existing_hash = await self.db.get_item_hash(media_item.item_id)

                if existing_hash and existing_hash == media_item.content_hash:
                    # Item unchanged - skip to save processing time
                    continue

                media_items.append(media_item)
                processed_count += 1

            except Exception as e:
                error_count += 1
                item_id = jellyfin_item.get('Id', 'unknown') if isinstance(jellyfin_item, dict) else 'unknown'
                self.logger.warning(f"Error processing item {item_id}: {e}")
                continue

        # Save all changed items in a single database transaction
        if media_items:
            try:
                saved_count = await self.db.save_items_batch(media_items)
                self.logger.debug(f"Saved {saved_count}/{len(media_items)} changed items in batch")
            except Exception as e:
                self.logger.error(f"Error saving batch of {len(media_items)} items: {e}")

        if error_count > 0:
            self.logger.warning(
                f"Batch processing completed with {error_count} errors out of {len(jellyfin_items)} items")

    async def process_webhook(self, payload: WebhookPayload) -> Dict[str, Any]:
        """
        Process incoming webhook from Jellyfin with comprehensive handling.

        This is the main entry point for webhook processing. It handles:
        - Payload validation and extraction
        - Waiting for initial sync completion
        - Change detection and notification sending
        - Performance monitoring and logging
        - Error handling and reporting

        Args:
            payload: Validated webhook payload from Jellyfin

        Returns:
            Dictionary with processing results and metrics

        Raises:
            HTTPException: For client errors (400) or server errors (500)
        """
        request_start_time = time.time()

        try:
            # Wait for initial sync if needed (with smart timeout handling)
            if not self.initial_sync_complete and self.sync_in_progress:
                await self._wait_for_initial_sync()

            # Extract and validate media item from webhook payload
            try:
                media_item = self._extract_from_webhook(payload)
            except Exception as e:
                self.logger.error(f"Error extracting media item from webhook payload: {e}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid webhook payload: {str(e)}"
                )

            # Process the media item (change detection, notifications, database updates)
            try:
                result = await self._process_media_item(media_item)

                processing_time = (time.time() - request_start_time) * 1000
                self.logger.info(
                    f"Webhook processed successfully for {media_item.name} "
                    f"(ID: {media_item.item_id}) in {processing_time:.2f}ms"
                )

                return {
                    "status": "success",
                    "item_id": media_item.item_id,
                    "item_name": media_item.name,
                    "processing_time_ms": round(processing_time, 2),
                    **result
                }

            except Exception as e:
                self.logger.error(f"Error processing media item {media_item.item_id}: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error processing media item: {str(e)}"
                )

        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise
        except Exception as e:
            processing_time = (time.time() - request_start_time) * 1000
            self.logger.error(f"Webhook processing failed after {processing_time:.2f}ms: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error during webhook processing"
            )

    async def _wait_for_initial_sync(self) -> None:
        """
        Wait for initial sync to complete with smart timeout handling.

        This method implements different waiting strategies based on sync type:
        - Background sync: Don't wait (process webhooks immediately)
        - Foreground sync: Wait indefinitely until completion

        This ensures optimal user experience in both scenarios.
        """
        # Don't wait for background syncs - process webhooks immediately
        if getattr(self, 'is_background_sync', False):
            self.logger.debug("Background sync in progress, proceeding with webhook processing immediately")
            return

        # For blocking initial sync, wait indefinitely (no timeout)
        check_interval = 2  # Check every 2 seconds
        wait_time = 0

        while self.sync_in_progress:
            self.logger.debug(f"Waiting for initial full sync to complete... ({wait_time}s)")
            await asyncio.sleep(check_interval)
            wait_time += check_interval

    async def _process_media_item(self, media_item: MediaItem) -> Dict[str, Any]:
        """
        Process a media item for changes and send appropriate notifications.

        This method implements the core business logic for handling media items:
        1. Check if item exists in database
        2. Detect changes using content hash comparison
        3. Perform detailed change analysis if hash differs
        4. Send appropriate notification (new vs upgrade)
        5. Update database with current item state

        Args:
            media_item: MediaItem to process

        Returns:
            Dictionary with processing results
        """
        # Check if item exists and get its current hash for comparison
        existing_hash = await self.db.get_item_hash(media_item.item_id)

        if existing_hash:
            # Item exists - check if it has changed
            if existing_hash != media_item.content_hash:
                # Hash changed - need to detect specific changes
                existing_item = await self.db.get_item(media_item.item_id)

                if existing_item:
                    # Perform detailed change detection
                    changes = self.change_detector.detect_changes(existing_item, media_item)

                    if changes:
                        # Meaningful changes detected - send upgrade notification
                        notification_sent = await self.discord.send_notification(
                            media_item, changes, is_new=False
                        )

                        self.logger.info(f"Processed upgrade for {media_item.name} with {len(changes)} changes")

                        result = {
                            "action": "upgraded",
                            "changes_count": len(changes),
                            "notification_sent": notification_sent,
                            "changes": [change['type'] for change in changes]
                        }
                    else:
                        # Hash changed but no significant changes detected
                        self.logger.debug(f"Hash changed but no significant changes detected for {media_item.name}")
                        result = {
                            "action": "hash_updated",
                            "changes_count": 0,
                            "notification_sent": False
                        }
                else:
                    # Couldn't retrieve existing item for comparison
                    self.logger.warning(
                        f"Could not retrieve existing item {media_item.item_id} for change detection")
                    result = {
                        "action": "error_retrieving_existing",
                        "changes_count": 0,
                        "notification_sent": False
                    }
            else:
                # No changes detected (hash matches)
                self.logger.debug(f"No changes detected for {media_item.name} (hash match)")
                result = {
                    "action": "no_changes",
                    "changes_count": 0,
                    "notification_sent": False
                }
        else:
            # New item - send new item notification
            notification_sent = await self.discord.send_notification(media_item, is_new=True)
            self.logger.info(f"Processed new item: {media_item.name}")

            result = {
                "action": "new_item",
                "changes_count": 0,
                "notification_sent": notification_sent
            }

        # Save/update item in database (only if new or changed)
        if not existing_hash or existing_hash != media_item.content_hash:
            save_success = await self.db.save_item(media_item)
            if not save_success:
                self.logger.warning(f"Failed to save item {media_item.item_id} to database")

        return result

    def _extract_from_webhook(self, payload: WebhookPayload) -> MediaItem:
        """
        Extract MediaItem from enhanced Jellyfin webhook payload with comprehensive field mapping.

        Args:
            payload: Enhanced webhook payload from Jellyfin

        Returns:
            MediaItem instance with comprehensive normalized data

        Raises:
            ValueError: If required fields are missing or invalid
        """
        try:
            # Validate required fields
            if not payload.ItemId:
                raise ValueError("ItemId is required")
            if not payload.Name:
                raise ValueError("Name is required")
            if not payload.ItemType:
                raise ValueError("ItemType is required")

            # Extract and validate season/episode numbers from multiple sources
            season_number = None
            episode_number = None

            # Try integer fields first (more reliable)
            if payload.SeasonNumber is not None:
                season_number = payload.SeasonNumber
            elif payload.SeasonNumber00:
                try:
                    season_number = int(payload.SeasonNumber00)
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Invalid season number '{payload.SeasonNumber00}': {e}")

            if payload.EpisodeNumber is not None:
                episode_number = payload.EpisodeNumber
            elif payload.EpisodeNumber00:
                try:
                    episode_number = int(payload.EpisodeNumber00)
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Invalid episode number '{payload.EpisodeNumber00}': {e}")

            # Parse genres from comma-separated string to list
            genres_list = []
            if payload.Genres:
                try:
                    genres_list = [genre.strip() for genre in payload.Genres.split(',') if genre.strip()]
                except Exception as e:
                    self.logger.warning(f"Error parsing genres '{payload.Genres}': {e}")

            # Create MediaItem with comprehensive webhook data
            return MediaItem(
                # Core identification
                item_id=payload.ItemId,
                name=payload.Name,
                item_type=payload.ItemType,
                year=payload.Year,
                series_name=payload.SeriesName,
                season_number=season_number,
                episode_number=episode_number,
                overview=payload.Overview,

                # Enhanced metadata
                series_id=payload.SeriesId,
                parent_id=payload.SeasonId,
                premiere_date=payload.PremiereDate,
                runtime_ticks=payload.RunTimeTicks,
                genres=genres_list,

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
                tvdb_id=payload.Provider_tvdb,

                # Additional fields
                date_created=payload.UtcTimestamp,
                date_modified=payload.UtcTimestamp,
                official_rating=None,
                studios=[],
                tags=[],

                # Music-specific
                album=None,
                artists=[],
                album_artist=None,

                # Photo-specific
                width=payload.Video_0_Width,
                height=payload.Video_0_Height,

                # Internal tracking
                timestamp=datetime.now(timezone.utc).isoformat(),
                file_path=None,
                file_size=None,
                last_modified=payload.UtcTimestamp,

                # Initialize external rating fields as None
                omdb_imdb_rating=None,
                omdb_rt_rating=None,
                omdb_metacritic_rating=None,
                tmdb_rating=None,
                tmdb_vote_count=None,
                tvdb_rating=None,
                ratings_last_updated=None,
                ratings_fetch_failed=None
            )

        except Exception as e:
            self.logger.error(f"Error extracting MediaItem from enhanced webhook payload: {e}")
            self.logger.error(f"Payload data: ItemId={getattr(payload, 'ItemId', 'N/A')}, "
                              f"Name={getattr(payload, 'Name', 'N/A')}, "
                              f"ItemType={getattr(payload, 'ItemType', 'N/A')}")
            raise

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform comprehensive health check of all service components.

        Returns:
            Dictionary with comprehensive health information
        """
        try:
            health_data = {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "2.0.0",
                "components": {}
            }

            # Check Jellyfin connection status
            try:
                jellyfin_connected = await self.jellyfin.is_connected()
                health_data["components"]["jellyfin"] = {
                    "status": "healthy" if jellyfin_connected else "unhealthy",
                    "connected": jellyfin_connected,
                    "server_url": self.config.jellyfin.server_url
                }
            except Exception as e:
                health_data["components"]["jellyfin"] = {
                    "status": "error",
                    "connected": False,
                    "error": str(e)
                }

            # Check database status
            try:
                db_stats = await self.db.get_stats()
                health_data["components"]["database"] = {
                    "status": "healthy" if "error" not in db_stats else "unhealthy",
                    "path": self.config.database.path,
                    "wal_mode": self.config.database.wal_mode,
                    **db_stats
                }
            except Exception as e:
                health_data["components"]["database"] = {
                    "status": "error",
                    "error": str(e)
                }

            # Check Discord webhook status
            try:
                webhook_status = self.discord.get_webhook_status()
                enabled_webhooks = sum(1 for wh in webhook_status["webhooks"].values() if wh.get("enabled", False))
                health_data["components"]["discord"] = {
                    "status": "healthy" if enabled_webhooks > 0 else "unhealthy",
                    "enabled_webhooks": enabled_webhooks,
                    "routing_enabled": webhook_status["routing_enabled"]
                }
            except Exception as e:
                health_data["components"]["discord"] = {
                    "status": "error",
                    "error": str(e)
                }

            # Add service operational status
            health_data.update({
                "sync_in_progress": self.sync_in_progress,
                "initial_sync_complete": self.initial_sync_complete,
                "server_was_offline": self.server_was_offline
            })

            # Determine overall status based on component health
            component_statuses = [comp.get("status") for comp in health_data["components"].values()]
            if "error" in component_statuses:
                health_data["status"] = "error"
            elif "unhealthy" in component_statuses:
                health_data["status"] = "degraded"

            return health_data

        except Exception as e:
            self.logger.error(f"Error performing health check: {e}")
            return {
                "status": "error",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e)
            }

    async def manual_sync(self) -> Dict[str, Any]:
        """
        Trigger manual library synchronization.

        Returns:
            Dictionary with sync initiation status
        """
        try:
            if self.sync_in_progress:
                return {
                    "status": "warning",
                    "message": "Library sync already in progress"
                }

            # Verify Jellyfin connectivity before starting
            if not await self.jellyfin.is_connected():
                return {
                    "status": "error",
                    "message": "Cannot start sync: Jellyfin server is not connected"
                }

            # Start sync in background (don't await)
            sync_task = asyncio.create_task(self.sync_jellyfin_library(background=True))

            return {
                "status": "success",
                "message": "Library sync started in background"
            }

        except Exception as e:
            self.logger.error(f"Error starting manual sync: {e}")
            return {
                "status": "error",
                "message": f"Failed to start sync: {str(e)}"
            }

    async def get_service_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive service statistics for monitoring and debugging.

        Returns:
            Dictionary with detailed service statistics
        """
        try:
            stats = {
                "service": {
                    "version": "2.0.0",
                    "uptime_seconds": time.time() - getattr(self, '_start_time', time.time()),
                    "sync_in_progress": self.sync_in_progress,
                    "initial_sync_complete": self.initial_sync_complete
                }
            }

            # Get database statistics
            try:
                db_stats = await self.db.get_stats()
                stats["database"] = db_stats
            except Exception as e:
                stats["database"] = {"error": str(e)}

            # Get webhook status
            try:
                webhook_status = self.discord.get_webhook_status()
                stats["webhooks"] = webhook_status
            except Exception as e:
                stats["webhooks"] = {"error": str(e)}

            # Get Jellyfin connection status
            try:
                jellyfin_connected = await self.jellyfin.is_connected()
                stats["jellyfin"] = {
                    "connected": jellyfin_connected,
                    "server_url": self.config.jellyfin.server_url,
                    "server_was_offline": self.server_was_offline
                }
            except Exception as e:
                stats["jellyfin"] = {"error": str(e)}

            return stats

        except Exception as e:
            self.logger.error(f"Error getting service stats: {e}")
            return {"error": str(e)}

    async def background_tasks(self) -> None:
        """
        Run background maintenance tasks for the service.

        This method runs continuously in the background, performing:
        - Database maintenance (VACUUM operations)
        - Jellyfin connection monitoring
        - Periodic library syncs
        - Health monitoring and alerting
        """
        self._start_time = time.time()

        # Initial delay to allow service startup to complete
        await asyncio.sleep(60)

        self.logger.info("Background maintenance tasks started")

        while not self.shutdown_event.is_set():
            try:
                await self._run_maintenance_cycle()

                # Sleep for 60 seconds or until shutdown signal
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=60.0)
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    continue  # Normal timeout, continue loop

            except Exception as e:
                self.logger.error(f"Error in background maintenance cycle: {e}")
                # Sleep before retrying to avoid rapid error loops
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=60.0)
                    break
                except asyncio.TimeoutError:
                    continue

        self.logger.info("Background maintenance tasks stopped")

    async def _run_maintenance_cycle(self) -> None:
        """
        Execute a single maintenance cycle with all background tasks.
        """
        current_time = time.time()

        # Database maintenance
        await self._perform_database_maintenance(current_time)

        # Jellyfin connection monitoring
        await self._monitor_jellyfin_connection()

        # Periodic sync checking
        await self._check_periodic_sync()

    async def _perform_database_maintenance(self, current_time: float) -> None:
        """
        Perform database maintenance tasks including VACUUM operations.

        Args:
            current_time: Current timestamp for interval checking
        """
        try:
            vacuum_interval = self.config.database.vacuum_interval_hours * 3600
            if current_time - self.last_vacuum > vacuum_interval:
                self.logger.info("Starting database vacuum...")
                await self.db.vacuum_database()
                self.last_vacuum = current_time
                self.logger.info("Database vacuum completed")
        except Exception as e:
            self.logger.error(f"Database maintenance error: {e}")

    async def _monitor_jellyfin_connection(self) -> None:
        """
        Monitor Jellyfin server connectivity and send status notifications.
        """
        try:
            jellyfin_connected = await self.jellyfin.is_connected()

            if not jellyfin_connected and not self.server_was_offline:
                # Server went offline - notify users
                await self.discord.send_server_status(False)
                self.server_was_offline = True
                self.logger.warning("Jellyfin server went offline")

            elif jellyfin_connected and self.server_was_offline:
                # Server came back online - notify users and sync
                await self.discord.send_server_status(True)
                self.server_was_offline = False
                self.logger.info("Jellyfin server is back online")

                # Trigger recovery sync to catch up on any missed changes
                if not self.sync_in_progress:
                    self.logger.info("Starting recovery sync after server came back online")
                    asyncio.create_task(self.sync_jellyfin_library(background=True))

        except Exception as e:
            self.logger.error(f"Error monitoring Jellyfin connection: {e}")

    async def _check_periodic_sync(self) -> None:
        """
        Check if periodic library sync is needed and trigger if necessary.
        """
        try:
            if self.sync_in_progress:
                return

            sync_interval = 24 * 3600  # 24 hours in seconds

            # Get last sync time from database
            last_sync_time_str = await self.db.get_last_sync_time()

            if last_sync_time_str:
                try:
                    # Handle various timestamp formats
                    if last_sync_time_str.endswith('Z'):
                        last_sync_time_str = last_sync_time_str[:-1] + '+00:00'
                    elif '+' not in last_sync_time_str and last_sync_time_str.count(':') == 2:
                        last_sync_time_str += '+00:00'

                    last_sync = datetime.fromisoformat(last_sync_time_str)
                    now = datetime.now(timezone.utc)
                    seconds_since_sync = (now - last_sync).total_seconds()

                    if seconds_since_sync > sync_interval:
                        self.logger.info(
                            f"Starting periodic background sync "
                            f"({seconds_since_sync / 3600:.1f} hours since last sync)"
                        )
                        asyncio.create_task(self.sync_jellyfin_library(background=True))

                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Error parsing last sync time '{last_sync_time_str}': {e}")

        except Exception as e:
            self.logger.error(f"Error checking periodic sync: {e}")

    async def cleanup(self) -> None:
        """
        Clean up service resources during shutdown.

        This method handles graceful shutdown of all service components:
        - Signal background tasks to stop
        - Close network connections
        - Close database connections
        - Release other resources
        """
        self.logger.info("Starting service cleanup...")

        try:
            # Signal shutdown to background tasks
            self.shutdown_event.set()

            # Close HTTP session
            if self.session:
                try:
                    await self.session.close()
                    self.logger.debug("HTTP session closed")
                except Exception as e:
                    self.logger.warning(f"Error closing HTTP session: {e}")

            # Note: Database connections are closed automatically by aiosqlite
            self.logger.info("Service cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during service cleanup: {e}")