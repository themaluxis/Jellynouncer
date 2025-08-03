#!/usr/bin/env python3
"""
Jellynouncer Webhook Service Module

This module contains the main WebhookService class that serves as the central orchestrator
for all Jellynouncer functionality. It coordinates between Jellyfin webhooks, change
detection, database management, and Discord notifications to provide a complete media
notification service.

The WebhookService acts as the "conductor" of the application, managing the flow of data
between all other components and ensuring they work together harmoniously.

Classes:
    WebhookService: Main service orchestrator that coordinates all other components

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
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
    Main webhook service orchestrator that coordinates all application components.

    Think of this class as the "conductor" of an orchestra - it doesn't play any
    instruments itself, but it coordinates all the other "musicians" (components)
    to create a harmonious performance. The WebhookService manages the entire
    flow of data from Jellyfin webhooks through to Discord notifications.

    The service handles several key responsibilities:
    - Service initialization and configuration management
    - Background maintenance tasks (database cleanup, health monitoring)
    - Webhook processing from Jellyfin (the main event loop)
    - Library synchronization with Jellyfin
    - Health monitoring and diagnostics
    - Graceful shutdown procedures

    The service follows an async-first design pattern, meaning most operations
    are asynchronous (non-blocking) to handle multiple webhook requests
    simultaneously without slowing down.

    **Understanding Async/Await:**
        The `async` and `await` keywords are Python's way of handling concurrent
        operations. When a method is marked `async`, it means it can be paused
        and resumed, allowing other operations to run while waiting for slow
        operations (like network requests or database queries) to complete.

    **The Orchestrator Pattern:**
        This class implements the "orchestrator" or "service coordinator" pattern.
        Instead of having components directly communicate with each other, they
        all report to this central coordinator which manages their interactions.
        This makes the system easier to maintain and debug.

    Attributes:
        logger (logging.Logger): Main application logger for tracking operations
        config (AppConfig): Validated application configuration from files/environment
        db (DatabaseManager): Database manager for storing media item information
        jellyfin (JellyfinAPI): Jellyfin API client for communicating with media server
        change_detector (ChangeDetector): Logic for detecting meaningful media changes
        discord (DiscordNotifier): Discord notification manager for sending messages
        rating_service (RatingService): Service for fetching external rating information

        Service State Tracking:
            last_vacuum (float): Timestamp of last database maintenance operation
            server_was_offline (bool): Whether Jellyfin server was previously offline
            sync_in_progress (bool): Whether library sync is currently running
            is_background_sync (bool): Whether current sync is running in background
            initial_sync_complete (bool): Whether initial startup sync finished
            shutdown_event (asyncio.Event): Coordination signal for graceful shutdown

    Example:
        Basic service lifecycle:
        ```python
        # Create and initialize the service
        service = WebhookService()
        await service.initialize()

        # Process a webhook from Jellyfin
        webhook_payload = WebhookPayload(ItemId="123", Name="Movie", ...)
        result = await service.process_webhook(webhook_payload)
        print(f"Processed {result['action']} for {result['item_name']}")

        # Check service health
        health = await service.health_check()
        print(f"Service status: {health['status']}")

        # Clean up when shutting down
        await service.cleanup()
        ```

    Note:
        This class is designed to be a singleton - only one instance should
        exist per application to avoid conflicts with shared resources like
        the database and background tasks.
    """

    # Class-level logger to prevent multiple logging setups
    # This is a "class variable" shared by all instances
    _logger = None

    def __init__(self):
        """
        Initialize webhook service with logging and configuration loading.

        This constructor handles the early initialization steps that must happen
        synchronously (without async/await). The heavy lifting of async operations
        happens later in the initialize() method.

        **Why Two-Phase Initialization?**
            Python constructors (__init__) cannot be async, but we need to perform
            async operations like database connections and API calls. So we split
            initialization into two phases:
            1. __init__: Sync operations (logging, config loading, object creation)
            2. initialize(): Async operations (database setup, API connections)

        **Understanding Class Variables vs Instance Variables:**
            - _logger is a class variable (shared by all instances)
            - config, db, etc. are instance variables (unique to each instance)

        Raises:
            SystemExit: If configuration loading or validation fails critically
        """
        # Set up logging only once at the class level to avoid duplicate loggers
        # The 'if' statement ensures we don't create multiple loggers
        if WebhookService._logger is None:
            WebhookService._logger = setup_logging()

        self.logger = WebhookService._logger

        # Initialize component references to None
        # We'll populate these with actual objects later
        self.config = None
        self.db = None
        self.jellyfin = None
        self.change_detector = None
        self.discord = None
        self.rating_service = None

        # Initialize service state tracking attributes
        # These keep track of what the service is currently doing
        self.last_vacuum = 0  # When we last cleaned up the database
        self.server_was_offline = False  # Was Jellyfin offline last time we checked?
        self.sync_in_progress = False  # Are we currently syncing the library?
        self.is_background_sync = False  # Is the sync running in the background?
        self.initial_sync_complete = False  # Have we done our first sync?
        self.shutdown_event = asyncio.Event()  # Signal for coordinated shutdown

        # Load and validate configuration
        # This is critical - if config fails, the service can't start
        try:
            validator = ConfigurationValidator(self.logger)
            self.config = validator.load_and_validate_config()
            self.logger.info("Configuration loaded and validated successfully")
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise SystemExit(1)  # Exit immediately if config fails

        # Initialize component objects with validated configuration
        # Note: These are just object creation, not connection establishment
        try:
            self.db = DatabaseManager(self.config.database, self.logger)
            self.jellyfin = JellyfinAPI(self.config.jellyfin, self.logger)
            self.change_detector = ChangeDetector(self.config.notifications, self.logger)
            self.discord = DiscordNotifier(self.config.discord, self.config.jellyfin.server_url, self.logger)
            self.rating_service = RatingService(self.config.rating_services, self.logger)

            self.logger.info("Service components initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize service components: {e}")
            raise SystemExit(1)

    async def initialize(self) -> None:
        """
        Perform async initialization of all service components.

        This method handles the initialization tasks that require async operations,
        such as establishing database connections, testing API connectivity, and
        performing the initial library synchronization.

        **Why This is Async:**
            Database connections, network requests to Jellyfin, and file system
            operations all involve waiting for I/O operations. By making this
            async, we can handle these operations efficiently without blocking
            the entire application.

        **The Initialization Flow:**
            1. Initialize database (create tables, set up connections)
            2. Test Jellyfin connectivity
            3. Check if this is first run or if we need to sync
            4. Perform initial sync if needed
            5. Start background maintenance tasks

        Raises:
            Exception: Any initialization failure will be logged and re-raised
        """
        self.logger.info("Starting Jellynouncer service initialization...")

        # Track when we started for uptime calculations
        self._start_time = time.time()

        try:
            # Initialize database connection and create tables if needed
            # This is async because it involves file I/O and potentially slow operations
            await self.db.initialize()
            self.logger.info("Database initialized successfully")

            # Test Jellyfin connectivity before proceeding
            # No point in continuing if we can't talk to Jellyfin
            jellyfin_connected = await self.jellyfin.is_connected()
            if jellyfin_connected:
                self.logger.info("Jellyfin connectivity verified")
            else:
                self.logger.warning(
                    "Jellyfin server not accessible - service will continue but functionality may be limited")

            # Check if this is the first run by looking for our completion marker
            # This helps us decide whether to do a full sync or just start processing webhooks
            init_complete_path = Path("/app/data/init_complete")

            if not init_complete_path.exists() and jellyfin_connected:
                # First run - do a complete sync to populate our database
                self.logger.info("First run detected - performing initial library sync")
                await self._perform_initial_sync()
                self.initial_sync_complete = True
            else:
                # Not first run - mark initial sync as complete so webhooks can be processed
                self.initial_sync_complete = True
                self.logger.info("Initialization complete - ready to process webhooks")

            # Start background maintenance tasks
            # These run continuously to keep the service healthy
            asyncio.create_task(self.background_tasks())
            self.logger.info("Background maintenance tasks started")

        except Exception as e:
            self.logger.error(f"Service initialization failed: {e}")
            raise

    async def process_webhook(self, payload: WebhookPayload) -> Dict[str, Any]:
        """
        Process an incoming webhook from Jellyfin and send appropriate notifications.

        This is the main entry point for webhook processing and the heart of the
        entire application. When Jellyfin adds or updates a media item, it sends
        a webhook to this method, which then orchestrates the entire response.

        **The Process Flow:**
            1. Wait for initial sync to complete (if needed)
            2. Extract and validate media item from webhook payload
            3. Check if item already exists in our database
            4. Detect what changes occurred (new item vs. upgrade)
            5. Send appropriate Discord notification
            6. Update our database with the latest information

        **Understanding Context Managers and Performance Monitoring:**
            The method tracks processing time to help identify performance
            bottlenecks. This is important for a webhook service because
            Jellyfin expects quick responses.

        **Error Handling Strategy:**
            The method uses a multi-layered error handling approach:
            - Specific exceptions are caught and converted to appropriate HTTP responses
            - Generic exceptions are caught and logged with full context
            - HTTP exceptions are preserved and re-raised for FastAPI

        Args:
            payload (WebhookPayload): Validated webhook payload from Jellyfin containing
                all the information about the media item that was added or changed.

        Returns:
            Dict[str, Any]: Processing results and metrics including:
                - status: "success" if processing completed
                - item_id: Jellyfin ID of the processed item
                - item_name: Human-readable name of the item
                - processing_time_ms: How long processing took
                - action: What action was taken (new_item, upgraded, no_changes, etc.)
                - changes_count: Number of changes detected (for upgrades)
                - notification_sent: Whether a Discord notification was sent

        Raises:
            HTTPException:
                - 400 (Bad Request): For invalid webhook payloads
                - 500 (Internal Server Error): For processing failures

        Example:
            ```python
            # This is typically called by FastAPI when a webhook is received
            payload = WebhookPayload(
                ItemId="12345",
                Name="The Matrix",
                ItemType="Movie",
                Year=1999
            )

            result = await service.process_webhook(payload)

            # Example successful response:
            # {
            #     "status": "success",
            #     "item_id": "12345",
            #     "item_name": "The Matrix",
            #     "processing_time_ms": 45.23,
            #     "action": "new_item",
            #     "changes_count": 0,
            #     "notification_sent": True
            # }
            ```
        """
        # Start timing for performance monitoring
        request_start_time = time.time()

        try:
            # Wait for initial sync if needed (with smart timeout handling)
            # We don't want to process webhooks before we know what's already in the library
            if not self.initial_sync_complete and self.sync_in_progress:
                await self._wait_for_initial_sync()

            # Extract and validate media item from webhook payload
            # This converts Jellyfin's webhook format into our internal MediaItem format
            try:
                media_item = self._extract_from_webhook(payload)
                self.logger.debug(f"Successfully extracted media item: {media_item.name} (ID: {media_item.item_id})")
            except Exception as e:
                self.logger.error(f"Error extracting media item from webhook payload: {e}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid webhook payload: {str(e)}"
                )

            # Process the media item (change detection, notifications, database updates)
            # This is where the main business logic happens
            try:
                result = await self._process_media_item(media_item)

                # Calculate and log processing time for performance monitoring
                processing_time = (time.time() - request_start_time) * 1000
                self.logger.info(
                    f"Webhook processed successfully for {media_item.name} "
                    f"(ID: {media_item.item_id}) in {processing_time:.2f}ms"
                )

                # Return comprehensive results
                return {
                    "status": "success",
                    "item_id": media_item.item_id,
                    "item_name": media_item.name,
                    "processing_time_ms": round(processing_time, 2),
                    **result  # Include all the processing results (action, changes, etc.)
                }

            except Exception as e:
                self.logger.error(f"Error processing media item {media_item.item_id}: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error processing media item: {str(e)}"
                )

        except HTTPException:
            # Re-raise HTTP exceptions as-is so FastAPI can handle them properly
            raise
        except Exception as e:
            # Catch any other unexpected errors and convert to 500 error
            processing_time = (time.time() - request_start_time) * 1000
            self.logger.error(f"Webhook processing failed after {processing_time:.2f}ms: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error during webhook processing"
            )

    async def _wait_for_initial_sync(self) -> None:
        """
        Wait for initial sync to complete with smart timeout handling.

        This method implements different waiting strategies based on the type of sync:
        - Background sync: Don't wait (process webhooks immediately for better UX)
        - Foreground/initial sync: Wait indefinitely until completion

        **Why Smart Timeout Handling?**
            We want to balance user experience with data consistency:
            - For background syncs, users expect webhooks to work immediately
            - For initial syncs, we need complete data before processing webhooks

        **Understanding Async Sleep:**
            `await asyncio.sleep()` is non-blocking - it pauses this specific
            operation but allows other operations to continue. This is much
            better than `time.sleep()` which would block the entire application.
        """
        # Don't wait for background syncs - process webhooks immediately
        if getattr(self, 'is_background_sync', False):
            self.logger.debug("Background sync in progress, proceeding with webhook processing immediately")
            return

        # For blocking initial sync, wait indefinitely (no timeout)
        check_interval = 2  # Check every 2 seconds
        wait_time = 0

        while self.sync_in_progress:
            self.logger.debug(f"Waiting for initial full sync to complete... ({wait_time}s elapsed)")
            await asyncio.sleep(check_interval)  # Non-blocking wait
            wait_time += check_interval

    async def _process_media_item(self, media_item: MediaItem) -> Dict[str, Any]:
        """
        Process a media item for changes and send appropriate notifications.

        This method implements the core business logic for handling media items.
        It's where we decide whether something is new or an upgrade, and what
        kind of notification to send.

        **The Processing Logic:**
            1. Check if item exists in database using content hash
            2. If exists and hash changed: detect specific changes
            3. If meaningful changes found: send upgrade notification
            4. If new item: send new item notification
            5. Update database with current item state

        **Understanding Content Hashes:**
            A content hash is like a "fingerprint" of the media item's important
            properties. If any significant property changes, the hash changes too.
            This lets us quickly detect if something meaningful changed without
            comparing every field individually.

        Args:
            media_item (MediaItem): The media item to process, already extracted
                from the webhook payload and validated.

        Returns:
            Dict[str, Any]: Processing results including:
                - action: Type of action taken (new_item, upgraded, no_changes, etc.)
                - changes_count: Number of meaningful changes detected
                - notification_sent: Whether a Discord notification was sent
                - changes: List of change types (for upgrades)

        Example:
            ```python
            # For a new movie
            result = await self._process_media_item(movie_item)
            # Returns: {"action": "new_item", "changes_count": 0, "notification_sent": True}

            # For a resolution upgrade
            result = await self._process_media_item(upgraded_movie)
            # Returns: {
            #     "action": "upgraded",
            #     "changes_count": 1,
            #     "notification_sent": True,
            #     "changes": ["resolution_upgrade"]
            # }
            ```
        """
        # Check if item already exists by looking up its content hash
        # This is much faster than retrieving the full item
        existing_hash = await self.db.get_item_hash(media_item.item_id)

        if existing_hash:
            # Item exists - check if it has changed by comparing hashes
            if existing_hash != media_item.content_hash:
                # Hash changed - need to detect what specifically changed
                existing_item = await self.db.get_item(media_item.item_id)

                if existing_item:
                    # Perform detailed change detection
                    # This compares the old and new versions to find meaningful changes
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
                        # This can happen with minor metadata updates we don't care about
                        self.logger.debug(f"Hash changed but no significant changes detected for {media_item.name}")
                        result = {
                            "action": "hash_updated",
                            "changes_count": 0,
                            "notification_sent": False
                        }
                else:
                    # Couldn't retrieve existing item for comparison
                    # This is unusual and might indicate a database issue
                    self.logger.warning(
                        f"Could not retrieve existing item {media_item.item_id} for change detection")
                    result = {
                        "action": "error_retrieving_existing",
                        "changes_count": 0,
                        "notification_sent": False
                    }
            else:
                # No changes detected (hash matches exactly)
                self.logger.debug(f"No changes detected for {media_item.name} (hash unchanged)")
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
        # No point saving if nothing changed
        if not existing_hash or existing_hash != media_item.content_hash:
            save_success = await self.db.save_item(media_item)
            if not save_success:
                self.logger.warning(f"Failed to save item {media_item.item_id} to database")

        return result

    def _extract_from_webhook(self, payload: WebhookPayload) -> MediaItem:
        """
        Extract and normalize a MediaItem from Jellyfin webhook payload.

        This method converts Jellyfin's webhook format into our internal MediaItem
        format. Jellyfin's webhook contains lots of fields with different naming
        conventions, so we need to map them to our standardized format.

        **Understanding Data Transformation:**
            Raw webhook data from Jellyfin isn't always in the format we want:
            - Field names use different conventions
            - Some data needs parsing (like comma-separated genres)
            - Numbers might be strings that need conversion
            - We need to handle missing or invalid data gracefully

        **Validation Strategy:**
            We validate the most critical fields (ItemId, Name, ItemType) and
            warn about problems with optional fields rather than failing completely.

        Args:
            payload (WebhookPayload): The validated webhook payload from Jellyfin
                containing raw data about the media item.

        Returns:
            MediaItem: Normalized media item with all fields properly formatted
                and validated for internal use.

        Raises:
            ValueError: If required fields are missing or invalid

        Example:
            ```python
            # Jellyfin webhook payload
            payload = WebhookPayload(
                ItemId="12345",
                Name="Breaking Bad",
                ItemType="Episode",
                SeriesName="Breaking Bad",
                SeasonNumber="1",
                EpisodeNumber="1"
            )

            # Extract to our internal format
            media_item = service._extract_from_webhook(payload)

            # Now we have a MediaItem with normalized data
            print(f"Item: {media_item.name}")
            print(f"Season: {media_item.season_number}")  # Now an integer
            ```
        """
        try:
            # Validate the most critical fields first
            # Without these, we can't process the item at all
            if not payload.ItemId:
                raise ValueError("ItemId is required but was not provided")
            if not payload.Name:
                raise ValueError("Name is required but was not provided")
            if not payload.ItemType:
                raise ValueError("ItemType is required but was not provided")

            # Extract and validate season/episode numbers from multiple possible sources
            # Jellyfin sometimes provides these in different fields
            season_number = None
            episode_number = None

            # Try integer fields first (more reliable than string fields)
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
            # Jellyfin sends genres as "Action, Drama, Thriller" but we want a list
            genres_list = []
            if payload.Genres:
                try:
                    # Split on commas and strip whitespace from each genre
                    genres_list = [genre.strip() for genre in payload.Genres.split(',') if genre.strip()]
                except Exception as e:
                    self.logger.warning(f"Error parsing genres '{payload.Genres}': {e}")

            # Create MediaItem with comprehensive webhook data mapping
            # We map from Jellyfin's field names to our internal field names
            return MediaItem(
                # ==================== CORE IDENTIFICATION ====================
                item_id=payload.ItemId,
                name=payload.Name,
                item_type=payload.ItemType,
                year=payload.Year,
                series_name=payload.SeriesName,
                season_number=season_number,
                episode_number=episode_number,
                overview=payload.Overview,

                # ==================== ENHANCED METADATA ====================
                series_id=payload.SeriesId,  # Critical for episode thumbnails
                parent_id=payload.SeasonId,  # Season ID for episodes
                premiere_date=payload.PremiereDate,
                runtime_ticks=payload.RunTimeTicks,
                genres=genres_list,

                # ==================== VIDEO PROPERTIES ====================
                video_height=payload.Video_0_Height,
                video_width=payload.Video_0_Width,
                video_codec=payload.Video_0_Codec,
                video_profile=payload.Video_0_Profile,
                video_range=payload.Video_0_VideoRange,
                video_framerate=payload.Video_0_FrameRate,
                aspect_ratio=payload.Video_0_AspectRatio,

                # ==================== AUDIO PROPERTIES ====================
                audio_codec=payload.Audio_0_Codec,
                audio_channels=payload.Audio_0_Channels,
                audio_language=payload.Audio_0_Language,
                audio_bitrate=payload.Audio_0_Bitrate,

                # ==================== EXTERNAL PROVIDER IDS ====================
                imdb_id=payload.Provider_imdb,
                tmdb_id=payload.Provider_tmdb,
                tvdb_id=payload.Provider_tvdb,

                # ==================== FILE INFORMATION ====================
                path=payload.Path,
                file_size=payload.Size
            )

        except Exception as e:
            self.logger.error(f"Failed to extract MediaItem from webhook payload: {e}")
            raise ValueError(f"Invalid webhook payload: {str(e)}")

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform comprehensive health check of all service components.

        This method checks the status of all major components to determine
        overall service health. It's used both internally for monitoring
        and externally by load balancers or monitoring systems.

        **Health Check Strategy:**
            We check each component individually and then determine overall
            health based on the results. Some components are more critical
            than others - for example, we can operate without rating services
            but not without database access.

        **Understanding Error Isolation:**
            Each component check is wrapped in its own try/catch block so
            that a failure in one component doesn't prevent checking others.
            This gives us better visibility into what's working and what isn't.

        Returns:
            Dict[str, Any]: Comprehensive health status including:
                - status: Overall health (healthy, degraded, error)
                - timestamp: When the check was performed
                - components: Individual component health details
                - Service operational status (sync status, etc.)

        Example:
            ```python
            health = await service.health_check()
            print(f"Service is {health['status']}")

            # Check individual components
            if health['components']['database']['status'] != 'healthy':
                print("Database issues detected!")
            ```
        """
        try:
            health_data = {
                "status": "healthy",  # Assume healthy until proven otherwise
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "components": {}
            }

            # Check Jellyfin connectivity
            try:
                jellyfin_connected = await self.jellyfin.is_connected()
                if jellyfin_connected:
                    # Get additional Jellyfin info if connected
                    jellyfin_info = await self.jellyfin.get_system_info()
                    health_data["components"]["jellyfin"] = {
                        "status": "healthy",
                        "connected": True,
                        "server_name": jellyfin_info.get("ServerName", "Unknown"),
                        "version": jellyfin_info.get("Version", "Unknown")
                    }
                else:
                    health_data["components"]["jellyfin"] = {
                        "status": "unhealthy",
                        "connected": False,
                        "error": "Cannot connect to Jellyfin server"
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
                    **db_stats  # Include all database statistics
                }
            except Exception as e:
                health_data["components"]["database"] = {
                    "status": "error",
                    "error": str(e)
                }

            # Check Discord webhook status
            try:
                webhook_status = self.discord.get_webhook_status()
                enabled_webhooks = sum(1 for wh in webhook_status["webhooks"].values()
                                       if wh.get("enabled", False))
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
        Trigger manual library synchronization through the API.

        This method allows administrators to manually trigger a library sync
        without waiting for the scheduled periodic sync. The sync runs in the
        background to avoid blocking webhook processing.

        **Background Sync Benefits:**
            By running the sync in the background (using asyncio.create_task),
            the API call returns immediately while the sync continues. This
            provides better user experience and doesn't block webhook processing.

        Returns:
            Dict[str, Any]: Sync initiation status including:
                - status: success, warning, or error
                - message: Human-readable description of what happened

        Example:
            ```python
            # Trigger manual sync via API
            result = await service.manual_sync()

            if result['status'] == 'success':
                print("Manual sync started successfully")
            elif result['status'] == 'warning':
                print(f"Warning: {result['message']}")
            else:
                print(f"Error: {result['message']}")
            ```
        """
        try:
            # Check if a sync is already running
            if self.sync_in_progress:
                return {
                    "status": "warning",
                    "message": "Library sync already in progress - please wait for completion"
                }

            # Verify Jellyfin connectivity before starting
            # No point starting a sync if we can't talk to Jellyfin
            if not await self.jellyfin.is_connected():
                return {
                    "status": "error",
                    "message": "Cannot start sync: Jellyfin server is not connected"
                }

            # Start sync in background (don't await - let it run independently)
            # This allows the API to return immediately while sync continues
            sync_task = asyncio.create_task(self.sync_jellyfin_library(background=True))

            self.logger.info("Manual library sync initiated via API")

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

        This method collects detailed statistics from all components to provide
        a complete picture of service operation. It's useful for monitoring,
        debugging, and capacity planning.

        **Statistics Collection Strategy:**
            We collect stats from each component separately and handle errors
            gracefully. If one component fails to provide stats, we still
            return statistics from the others with an error indicator.

        Returns:
            Dict[str, Any]: Detailed service statistics including:
                - service: Version, uptime, and operational status
                - database: Item counts, performance metrics
                - webhooks: Configuration and status information
                - jellyfin: Connection status and server information

        Example:
            ```python
            stats = await service.get_service_stats()

            print(f"Service uptime: {stats['service']['uptime_seconds']} seconds")
            print(f"Database items: {stats['database']['total_items']}")
            print(f"Jellyfin connected: {stats['jellyfin']['connected']}")
            ```
        """
        try:
            # Calculate uptime since service started
            uptime_seconds = time.time() - getattr(self, '_start_time', time.time())

            stats = {
                "service": {
                    "version": "2.0.0",
                    "uptime_seconds": round(uptime_seconds, 2),
                    "sync_in_progress": self.sync_in_progress,
                    "initial_sync_complete": self.initial_sync_complete
                }
            }

            # Get database statistics
            try:
                db_stats = await self.db.get_stats()
                stats["database"] = db_stats
            except Exception as e:
                self.logger.warning(f"Could not get database stats: {e}")
                stats["database"] = {"error": str(e)}

            # Get webhook configuration and status
            try:
                webhook_status = self.discord.get_webhook_status()
                stats["webhooks"] = webhook_status
            except Exception as e:
                self.logger.warning(f"Could not get webhook status: {e}")
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
                self.logger.warning(f"Could not get Jellyfin status: {e}")
                stats["jellyfin"] = {"error": str(e)}

            return stats

        except Exception as e:
            self.logger.error(f"Error getting service stats: {e}")
            return {"error": str(e)}

    async def background_tasks(self) -> None:
        """
        Run continuous background maintenance tasks for service health.

        This method runs an infinite loop that performs periodic maintenance
        tasks to keep the service running smoothly. It handles database
        cleanup, connection monitoring, and periodic syncs.

        **The Background Task Pattern:**
            Background tasks run continuously in a separate async task, allowing
            the main service to handle webhooks while maintenance happens in
            the background. This is similar to having a janitor who cleans up
            while the office workers continue their normal work.

        **Task Schedule:**
            - Database maintenance: Every hour
            - Connection monitoring: Every 60 seconds
            - Periodic sync check: Every 60 seconds
            - Error recovery: Continuous

        **Understanding Exception Handling in Loops:**
            Each task is wrapped in try/catch to prevent one failing task from
            breaking the entire maintenance loop. This ensures the service
            stays healthy even if one maintenance task encounters problems.
        """
        self.logger.info("Background maintenance tasks started")

        # Run forever until shutdown is requested
        while not self.shutdown_event.is_set():
            try:
                # Database maintenance - vacuum and cleanup
                await self._perform_database_maintenance()

                # Monitor Jellyfin connection status
                await self._monitor_jellyfin_connection()

                # Check if periodic sync is needed
                await self._check_periodic_sync()

                # Wait 60 seconds before next maintenance cycle
                # Use wait_for to allow interruption by shutdown event
                try:
                    await asyncio.wait_for(
                        self.shutdown_event.wait(),
                        timeout=60.0
                    )
                    # If we get here, shutdown was requested
                    break
                except asyncio.TimeoutError:
                    # Timeout is expected - continue with next cycle
                    continue

            except Exception as e:
                self.logger.error(f"Error in background tasks: {e}")
                # Wait a bit before retrying to avoid tight error loops
                await asyncio.sleep(30)

        self.logger.info("Background maintenance tasks stopped")

    async def _perform_database_maintenance(self) -> None:
        """
        Perform periodic database maintenance operations.

        Database maintenance includes operations like VACUUM (which optimizes
        the database file) and other cleanup tasks. These operations can be
        slow, so we only do them periodically.

        **Understanding Database Maintenance:**
            SQLite databases can become fragmented over time as data is added,
            updated, and deleted. The VACUUM operation reorganizes the database
            file to reclaim space and improve performance.
        """
        try:
            current_time = time.time()
            # Perform vacuum every hour (3600 seconds)
            if current_time - self.last_vacuum > 3600:
                self.logger.debug("Starting database maintenance (VACUUM)")
                await self.db.vacuum()
                self.last_vacuum = current_time
                self.logger.debug("Database maintenance completed")
        except Exception as e:
            self.logger.error(f"Error during database maintenance: {e}")

    async def _monitor_jellyfin_connection(self) -> None:
        """
        Monitor Jellyfin server connection and handle status changes.

        This method tracks whether Jellyfin is online or offline and sends
        notifications when the status changes. It also triggers recovery
        syncs when the server comes back online.

        **Connection Monitoring Strategy:**
            We track the previous connection state so we can detect transitions
            (online->offline or offline->online) and react appropriately.
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

        This method implements periodic background syncs to ensure the local
        database stays in sync with Jellyfin even if webhooks are missed.
        The default interval is 24 hours.

        **Why Periodic Syncs?**
            Webhooks can sometimes be missed due to network issues, service
            restarts, or Jellyfin problems. Periodic syncs act as a safety net
            to catch any missed changes.
        """
        try:
            if self.sync_in_progress:
                return  # Don't start another sync if one is already running

            sync_interval = 24 * 3600  # 24 hours in seconds

            # Get last sync time from database
            last_sync_time_str = await self.db.get_last_sync_time()

            if last_sync_time_str:
                try:
                    # Handle various timestamp formats from the database
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

    async def _perform_initial_sync(self) -> None:
        """
        Perform initial library sync on first startup.

        This method runs a complete library sync and blocks webhook processing
        until it completes. It's only used on the very first service startup
        to populate the database with existing library content.

        **Why Block During Initial Sync?**
            We need a complete picture of the library before processing webhooks,
            otherwise we might treat existing items as "new" items.
        """
        try:
            self.logger.info("Starting initial Jellyfin library sync...")
            result = await self.sync_jellyfin_library()

            # Create completion marker only if sync was successful
            if result.get("status") == "success":
                init_complete_path = Path("/app/data/init_complete")
                try:
                    init_complete_path.touch(exist_ok=True)
                    self.logger.info("Initial sync completed successfully - created completion marker")
                except Exception as e:
                    self.logger.warning(f"Could not create completion marker: {e}")
            else:
                self.logger.warning(f"Initial sync completed with status: {result.get('status', 'unknown')}")

        except Exception as e:
            self.logger.error(f"Initial sync failed: {e}")
            # Don't raise - service can continue without initial sync

    async def sync_jellyfin_library(self, background: bool = False) -> Dict[str, Any]:
        """
        Synchronize entire Jellyfin library to local database.

        This method performs a complete sync of the Jellyfin library, processing
        all items in batches for efficiency. It's used for initial setup and
        periodic maintenance to ensure our database matches Jellyfin's library.

        **Understanding Batch Processing:**
            Instead of processing items one by one, we process them in batches
            (groups). This is much more efficient for large libraries because
            it reduces the number of database transactions and API calls.

        **Background vs Foreground Sync:**
            - Background: Doesn't block webhook processing (for periodic syncs)
            - Foreground: Blocks webhook processing (for initial setup)

        Args:
            background (bool): Whether to run sync in background mode.
                Background syncs don't block webhook processing.

        Returns:
            Dict[str, Any]: Sync results including:
                - status: success, error, or partial
                - items_processed: Number of items synchronized
                - processing_time: How long the sync took
                - errors: Any errors encountered during sync

        Example:
            ```python
            # Initial sync (blocking)
            result = await service.sync_jellyfin_library(background=False)

            # Periodic sync (non-blocking)
            result = await service.sync_jellyfin_library(background=True)

            print(f"Synced {result['items_processed']} items in {result['processing_time']}s")
            ```
        """
        if self.sync_in_progress:
            return {
                "status": "error",
                "message": "Library sync already in progress"
            }

        sync_start_time = time.time()
        self.sync_in_progress = True
        self.is_background_sync = background

        try:
            self.logger.info(f"Starting {'background' if background else 'foreground'} library sync")

            # Get all library items from Jellyfin
            all_items = await self.jellyfin.get_all_library_items()

            if not all_items:
                self.logger.warning("No items retrieved from Jellyfin library")
                return {
                    "status": "error",
                    "message": "No items retrieved from Jellyfin library"
                }

            # Process items in batches for efficiency
            batch_size = 50
            items_processed = 0
            errors = 0

            for i in range(0, len(all_items), batch_size):
                batch = all_items[i:i + batch_size]

                try:
                    # Process each item in the batch
                    for item_data in batch:
                        try:
                            # Convert Jellyfin API data to MediaItem
                            media_item = await self.jellyfin.convert_to_media_item(item_data)

                            # Save to database
                            await self.db.save_item(media_item)
                            items_processed += 1

                        except Exception as e:
                            self.logger.warning(f"Error processing item {item_data.get('Id', 'unknown')}: {e}")
                            errors += 1

                    # Log progress periodically
                    if i % (batch_size * 10) == 0:  # Every 10 batches
                        self.logger.info(f"Sync progress: {items_processed}/{len(all_items)} items processed")

                except Exception as e:
                    self.logger.error(f"Error processing batch starting at index {i}: {e}")
                    errors += len(batch)

            # Update last sync time in database
            await self.db.update_last_sync_time()

            processing_time = time.time() - sync_start_time

            # Determine final status
            if errors == 0:
                status = "success"
            elif errors < len(all_items) / 2:  # Less than half failed
                status = "partial"
            else:
                status = "error"

            self.logger.info(
                f"Library sync completed: {items_processed} items processed, "
                f"{errors} errors in {processing_time:.2f}s"
            )

            return {
                "status": status,
                "items_processed": items_processed,
                "total_items": len(all_items),
                "errors": errors,
                "processing_time": round(processing_time, 2)
            }

        except Exception as e:
            processing_time = time.time() - sync_start_time
            self.logger.error(f"Library sync failed after {processing_time:.2f}s: {e}")
            return {
                "status": "error",
                "message": str(e),
                "processing_time": round(processing_time, 2)
            }

        finally:
            # Always reset sync flags, even if an error occurred
            self.sync_in_progress = False
            self.is_background_sync = False

    async def cleanup(self) -> None:
        """
        Clean up service resources during shutdown.

        This method handles graceful shutdown of all service components,
        ensuring that connections are closed properly and resources are
        released. It's called automatically by FastAPI during shutdown.

        **Graceful Shutdown Strategy:**
            1. Signal background tasks to stop
            2. Wait for current operations to complete
            3. Close database connections
            4. Close HTTP client sessions
            5. Release other resources

        **Understanding Resource Management:**
            Proper cleanup prevents resource leaks and ensures that the
            service can restart cleanly. This is especially important in
            containerized environments.
        """
        self.logger.info("Starting service cleanup...")

        try:
            # Signal background tasks to stop
            self.shutdown_event.set()

            # Wait a moment for background tasks to notice the shutdown signal
            await asyncio.sleep(2)

            # Close database connections
            if self.db:
                await self.db.close()
                self.logger.info("Database connections closed")

            # Close HTTP client sessions
            if self.jellyfin:
                await self.jellyfin.close()
                self.logger.info("Jellyfin API client closed")

            if self.discord:
                await self.discord.close()
                self.logger.info("Discord client closed")

            if self.rating_service:
                await self.rating_service.close()
                self.logger.info("Rating service closed")

            self.logger.info("Service cleanup completed successfully")

        except Exception as e:
            self.logger.error(f"Error during service cleanup: {e}")