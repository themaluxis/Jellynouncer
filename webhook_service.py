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
from typing import Dict, Any, Optional

import aiohttp
import aiosqlite

from config_models import AppConfig, ConfigurationValidator
from webhook_models import WebhookPayload
from database_manager import DatabaseManager
from jellyfin_api import JellyfinAPI
from discord_services import DiscordNotifier
from metadata_services import RatingService
from change_detector import ChangeDetector
from tvdb import TVDB, add_tvdb_metadata_to_item
from utils import get_logger


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
        logger.info(f"Processed {result['action']} for {result['item_name']}")

        # Check service health
        health = await service.health_check()
        logger.info(f"Service status: {health['status']}")

        # Clean up when shutting down
        await service.cleanup()
        ```

    Note:
        This class is designed to be a singleton - only one instance should
        exist per application to avoid conflicts with shared resources like
        the database and background tasks.
    """

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

        **Understanding Component Initialization:**
            We initialize all component references to None and populate them during
            the async initialize() method. This ensures clean error handling and
            allows for proper resource management.

        Raises:
            SystemExit: If configuration loading or validation fails critically
        """
        # Set up component-specific logger
        self.logger = get_logger("jellynouncer.webhook")

        # Initialize component references to None
        # We'll populate these with actual objects later
        self.config = None
        self.db = None
        self.jellyfin = None
        self.change_detector = None
        self.discord = None
        self.rating_service = None
        self.tvdb_client = None

        # Initialize service state tracking attributes
        # These keep track of what the service is currently doing
        self.last_vacuum: float = 0.0  # When we last cleaned up the database
        self.server_was_offline = False  # Was Jellyfin offline last time we checked?
        self.sync_in_progress = False  # Are we currently syncing the library?
        self.is_background_sync = False  # Is the sync running in the background?
        self.initial_sync_complete = False  # Have we done our first sync?
        self.shutdown_event = asyncio.Event()  # Graceful shutdown coordination

        # Record service startup time for uptime tracking
        self._start_time: float = time.time()
        self._last_sync_time: float = 0.0  # Initialize sync time

        self.logger.info("WebhookService created - ready for initialization")

    async def initialize(self) -> None:
        """
        Initialize all service components and establish connections.

        This async method handles the complex initialization process that requires
        network connections, database setup, and component coordination. It's
        designed to fail gracefully if any component cannot be initialized.

        **Initialization Steps:**
            1. Load and validate configuration from files and environment
            2. Initialize database manager and create/migrate tables
            3. Connect to Jellyfin API and verify authentication
            4. Set up Discord notification manager with webhooks
            5. Initialize external rating services (OMDb, TMDb, TVDb)
            6. Create change detector for upgrade notifications
            7. Perform initial library sync if needed
            8. Start background maintenance tasks

        **Error Handling Strategy:**
            Each initialization step is wrapped in try/catch blocks to provide
            detailed error information. Critical failures will stop the service,
            while non-critical failures (like rating services) will log warnings
            but allow the service to continue.

        Raises:
            SystemExit: If critical components (database, Jellyfin) cannot be initialized
            Various exceptions: If configuration is invalid or connections fail

        Example:
            ```python
            webhook_service = WebhookService()
            try:
                await webhook_service.initialize()
                logger.info("Service ready to process webhooks")
            except Exception as e:
                logger.error(f"Service initialization failed: {e}")
                sys.exit(1)
            ```
        """
        try:
            self.logger.info("Starting WebhookService initialization...")

            # Step 1: Load and validate configuration
            self.logger.debug("Loading application configuration...")
            try:
                config_validator = ConfigurationValidator()
                self.config = config_validator.load_and_validate_config()
                self.logger.info("Configuration loaded and validated successfully")
                self.logger.debug(f"Jellyfin server: {self.config.jellyfin.server_url}")
                self.logger.debug(f"Database path: {self.config.database.path}")
            except Exception as e:
                self.logger.error(f"Configuration loading failed: {e}")
                raise SystemExit(f"Cannot start without valid configuration: {e}")

            # Step 2: Initialize database manager
            self.logger.debug("Initializing database manager...")
            try:
                self.db = DatabaseManager(self.config.database)
                await self.db.initialize()
                self.logger.info("Database manager initialized successfully")

                # Load service state after database initialization
                await self._load_service_state()

            except Exception as e:
                self.logger.error(f"Database initialization failed: {e}")
                raise SystemExit(f"Cannot start without database: {e}")

            # Step 3: Initialize Jellyfin API client
            self.logger.debug("Connecting to Jellyfin API...")
            try:
                self.jellyfin = JellyfinAPI(self.config.jellyfin)
                if await self.jellyfin.connect():
                    self.logger.info("Connected to Jellyfin API successfully")
                else:
                    self.logger.error("Failed to connect to Jellyfin API")
                    raise SystemExit("Cannot start without Jellyfin connection")
            except Exception as e:
                self.logger.error(f"Jellyfin API initialization failed: {e}")
                raise SystemExit(f"Cannot connect to Jellyfin: {e}")

            # Step 4: Initialize Discord notification manager
            self.logger.debug("Setting up Discord notification manager...")
            try:
                # Create aiohttp session for Discord
                session = aiohttp.ClientSession()
                self.discord = DiscordNotifier(self.config.discord)
                await self.discord.initialize(session, self.config.jellyfin, self.config.templates, self.config.notifications)
                self.logger.info("Discord notification manager initialized")
            except Exception as e:
                self.logger.error(f"Discord manager initialization failed: {e}")
                raise SystemExit(f"Cannot start without Discord manager: {e}")

            # Step 5: Initialize rating service (non-critical)
            self.logger.debug("Initializing external rating services...")
            try:
                self.rating_service = RatingService(self.config.metadata_services)
                await self.rating_service.initialize(session, self.db)
                if self.rating_service.enabled:
                    self.logger.info("Rating services initialized and enabled")
                else:
                    self.logger.info("Rating services initialized but disabled")
            except Exception as e:
                self.logger.warning(f"Rating service initialization failed: {e}")
                self.logger.info("Service will continue without rating enhancements")

            # Step 5.5: Initialize TVDB client (non-critical)
            self.logger.debug("Initializing TVDB metadata client...")
            try:
                if (self.config.metadata_services.tvdb.enabled and
                        self.config.metadata_services.tvdb.api_key):

                    self.tvdb_client = TVDB(
                        api_key=self.config.metadata_services.tvdb.api_key,
                        pin=self.config.metadata_services.tvdb.subscriber_pin,
                        enable_caching=True,
                        cache_ttl=self.config.metadata_services.tvdb_cache_ttl_hours * 3600
                        # Convert hours to seconds
                    )
                    await self.tvdb_client.__aenter__()
                    self.logger.info("TVDB metadata client initialized successfully")
                else:
                    self.logger.info("TVDB metadata client disabled or no API key provided")
            except Exception as e:
                self.logger.warning(f"TVDB metadata client initialization failed: {e}")
                self.logger.info("Service will continue without TVDB metadata enhancement")
                self.tvdb_client = None

            # Step 6: Initialize change detector
            self.logger.debug("Setting up change detector...")
            try:
                self.change_detector = ChangeDetector(self.config.notifications)
                self.logger.info("Change detector initialized successfully")
            except Exception as e:
                self.logger.error(f"Change detector initialization failed: {e}")
                raise SystemExit(f"Cannot start without change detection: {e}")

            # Step 7: Perform initial library sync if needed
            await self._check_initial_sync()

            # Step 8: Log successful initialization
            self.logger.info("=" * 60)
            self.logger.info("🚀 WebhookService initialization completed successfully!")
            self.logger.info("Service is ready to process Jellyfin webhooks")
            self.logger.info("=" * 60)

        except SystemExit:
            # Re-raise SystemExit exceptions to stop the application
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during initialization: {e}", exc_info=True)
            raise SystemExit(f"Service initialization failed: {e}")

    async def _load_service_state(self) -> None:
        """
        Load service state from database.

        This method loads persistent service state like last vacuum time
        and last sync time from the database to prevent NoneType errors
        in background tasks.
        """
        try:
            if not self.db:
                self.logger.warning("Database not initialized, using default state values")
                return

            # Load last vacuum time from database
            vacuum_time = await self.db.get_vacuum_timestamp()
            if vacuum_time is not None:
                self.last_vacuum = vacuum_time
                self.logger.debug(f"Loaded last vacuum time: {vacuum_time}")
            else:
                # Set to current time to prevent immediate vacuum on startup
                self.last_vacuum = time.time()
                self.logger.debug("No vacuum history found, using current time")

            # Load last sync time from database
            sync_time = await self._get_last_sync_time()
            if sync_time is not None:
                self._last_sync_time = sync_time
                self.logger.debug(f"Loaded last sync time: {sync_time}")

        except Exception as e:
            self.logger.warning(f"Failed to load service state: {e}")
            # Ensure we have safe defaults even if loading fails
            self.last_vacuum = time.time()
            self._last_sync_time = 0.0

    async def _get_last_sync_time(self) -> Optional[float]:
        """
        Get the last sync time from database.

        Returns:
            Optional[float]: Unix timestamp of last sync, or None if not found
        """
        try:
            async with aiosqlite.connect(self.db.db_path) as db:
                cursor = await db.execute("""
                                          SELECT strftime('%s', last_sync_time)
                                          FROM sync_status
                                          WHERE id = 1
                                            AND sync_type = 'library_sync'
                                          """)
                row = await cursor.fetchone()
                if row and row[0]:
                    return float(row[0])
                return None
        except Exception as e:
            self.logger.debug(f"Could not retrieve last sync time: {e}")
            return None

    async def process_webhook(self, payload: WebhookPayload) -> Dict[str, Any]:
        """
        Process incoming webhook from Jellyfin media server.

        This is the main entry point for webhook processing. It handles the entire
        workflow from receiving a webhook payload to sending Discord notifications,
        including change detection, database updates, and error recovery.

        **Webhook Processing Pipeline:**
            1. Validate and extract media information from webhook payload
            2. Fetch additional metadata from Jellyfin API if needed
            3. Check database for existing item to detect changes vs new items
            4. Apply intelligent change detection for upgrade scenarios
            5. Update database with new/changed information
            6. Send appropriate Discord notifications based on content type
            7. Handle errors gracefully without breaking the service

        **Understanding the Workflow:**
            Not every webhook should result in a notification. The service needs
            to distinguish between:
            - New items that should always generate notifications
            - Updated items where only meaningful changes warrant notifications
            - Metadata-only updates that don't affect the user experience

        Args:
            payload (WebhookPayload): Validated webhook data from Jellyfin

        Returns:
            Dict[str, Any]: Processing result with details about the action taken

        Raises:
            HTTPException: If processing fails critically (rare - most errors are handled gracefully)

        Example:
            ```python
            # Webhook payload from Jellyfin
            payload = WebhookPayload(
                ItemId="abc123",
                Name="The Matrix",
                ItemType="Movie",
                NotificationType="library.new"
            )

            # Process the webhook
            result = await webhook_service.process_webhook(payload)

            logger.info(f"Action: {result['action']}")  # "new_item" or "item_updated"
            logger.info(f"Item: {result['item_name']}")  # "The Matrix"
            ```

        Note:
            This method is designed to be resilient and should never crash the
            entire service. All errors are logged and handled gracefully.
        """
        # Initialize timing variable
        start_time = time.time()

        try:
            self.logger.debug(f"Processing webhook for {payload.Name} ({payload.ItemType})")

            # Extract media item information from webhook payload
            try:
                # Get detailed item information from Jellyfin
                item_data = await self.jellyfin.get_item(payload.ItemId)
                if not item_data:
                    self.logger.warning(f"Could not fetch item data for {payload.ItemId}")
                    return {
                        "status": "error",
                        "action": "fetch_failed",
                        "item_id": payload.ItemId,
                        "item_name": payload.Name,
                        "message": "Could not fetch item data from Jellyfin"
                    }

                # Convert to our internal MediaItem format
                media_item = await self.jellyfin.convert_to_media_item(item_data)
                self.logger.debug(f"Converted to MediaItem: {media_item.name}")

            except Exception as e:
                self.logger.error(f"Error extracting media item from webhook: {e}")
                return {
                    "status": "error",
                    "action": "extraction_failed",
                    "item_id": payload.ItemId,
                    "item_name": payload.Name,
                    "message": str(e)
                }

            # Add TVDB metadata enhancement for TV content
            if (self.tvdb_client and
                    hasattr(media_item, 'item_type') and
                    media_item.item_type.lower() in ['series', 'season', 'episode'] and
                    hasattr(media_item, 'tvdb_id') and
                    media_item.tvdb_id):

                try:
                    self.logger.debug(f"Adding TVDB metadata to {media_item.item_type}: {media_item.name}")
                    await add_tvdb_metadata_to_item(media_item, self.tvdb_client)

                    if hasattr(media_item, 'has_tvdb_metadata') and media_item.has_tvdb_metadata:
                        self.logger.info(f"Successfully enhanced {media_item.name} with TVDB metadata")

                except Exception as e:
                    self.logger.warning(f"Failed to add TVDB metadata to {media_item.name}: {e}")

            # Check if this is a new item or an update to existing item
            existing_item = await self.db.get_item(media_item.item_id)

            if existing_item:
                # This is an update - check for meaningful changes
                self.logger.debug(f"Found existing item, checking for changes...")

                changes = self.change_detector.detect_changes(existing_item, media_item)

                if changes:
                    # Meaningful changes detected - update and notify
                    change_summary = self.change_detector.get_change_summary(changes)
                    self.logger.info(f"Changes detected for {media_item.name}: {change_summary}")

                    # Update database with new information
                    await self.db.save_item(media_item)

                    # Send upgrade notification
                    await self.discord.send_notification(media_item, "upgrade_item", changes)

                    processing_time = time.time() - start_time
                    return {
                        "status": "success",
                        "action": "item_updated",
                        "item_id": media_item.item_id,
                        "item_name": media_item.name,
                        "changes_detected": len(changes),
                        "change_summary": change_summary,
                        "processing_time": round(processing_time, 3)
                    }
                else:
                    # No meaningful changes - just update timestamp
                    self.logger.debug(f"No meaningful changes detected for {media_item.name}")
                    await self.db.save_item(media_item)

                    processing_time = time.time() - start_time
                    return {
                        "status": "success",
                        "action": "metadata_updated",
                        "item_id": media_item.item_id,
                        "item_name": media_item.name,
                        "message": "Metadata updated, no notification sent",
                        "processing_time": round(processing_time, 3)
                    }
            else:
                # This is a new item - save and notify
                self.logger.info(f"New item detected: {media_item.name} ({media_item.item_type})")

                # Save to database
                await self.db.save_item(media_item)

                # Send new item notification
                await self.discord.send_notification(media_item, "new_item")

                processing_time = time.time() - start_time
                return {
                    "status": "success",
                    "action": "new_item",
                    "item_id": media_item.item_id,
                    "item_name": media_item.name,
                    "item_type": media_item.item_type,
                    "processing_time": round(processing_time, 3)
                }

        except Exception as e:
            processing_time = time.time() - start_time
            self.logger.error(f"Error processing webhook for {payload.Name}: {e}", exc_info=True)
            return {
                "status": "error",
                "action": "processing_failed",
                "item_id": payload.ItemId,
                "item_name": payload.Name,
                "message": str(e),
                "processing_time": round(processing_time, 3)
            }

    async def trigger_manual_sync(self) -> Dict[str, Any]:
        """
        Trigger a manual library synchronization with Jellyfin.

        This method allows administrators to manually start a library sync
        without waiting for the scheduled background sync. It's useful when
        you know items have been added to Jellyfin but want immediate processing.

        **Manual vs Background Sync:**
            Manual syncs take priority over background syncs and provide immediate
            feedback about the sync status. They're designed for on-demand use
            when administrators need immediate results.

        Returns:
            Dict[str, Any]: Sync initiation status and result information

        Example:
            ```python
            result = await webhook_service.trigger_manual_sync()
            if result["status"] == "success":
                logger.info(f"Sync completed: {result['items_processed']} items")
            else:
                logger.warning(f"Sync issue: {result['message']}")
            ```

        Note:
            Multiple sync requests are queued to prevent resource conflicts.
            If a sync is already running, this method returns immediately
            with a warning status.
        """
        if self.sync_in_progress:
            self.logger.warning("Manual sync requested but sync already in progress")
            return {
                "status": "warning",
                "message": "Library sync already in progress",
                "sync_in_progress": True
            }

        try:
            self.logger.info("Manual library sync triggered by administrator")
            result = await self.sync_jellyfin_library(background=False)

            self.logger.info(f"Manual sync completed with status: {result.get('status', 'unknown')}")
            return result

        except Exception as e:
            self.logger.error(f"Manual sync failed: {e}")
            return {
                "status": "error",
                "message": str(e),
                "sync_in_progress": False
            }

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform comprehensive health check of all service components.

        This method tests the health and connectivity of all major service
        components to provide insight into service status. It's designed for
        monitoring and troubleshooting purposes.

        **Health Check Components:**
            - Jellyfin API connectivity and authentication
            - Database accessibility and performance
            - Discord webhook configuration and connectivity
            - Rating service availability (if enabled)
            - Service operational status (sync status, etc.)

        **Health Status Levels:**
            - healthy: All components functioning normally
            - degraded: Some non-critical components have issues
            - unhealthy: Critical components are failing

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
            logger.info(f"Service is {health['status']}")

            # Check individual components
            if health['components']['database']['status'] != 'healthy':
                logger.warning("Database issues detected!")
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
                    health_data["status"] = "unhealthy"
            except Exception as e:
                health_data["components"]["jellyfin"] = {
                    "status": "error",
                    "connected": False,
                    "error": str(e)
                }
                health_data["status"] = "unhealthy"

            # Check database health
            try:
                db_stats = await self.db.get_stats()
                health_data["components"]["database"] = {
                    "status": "healthy",
                    "total_items": db_stats.get("total_items", 0),
                    "db_size_mb": db_stats.get("db_size_mb", 0),
                    "wal_mode": db_stats.get("wal_mode", False)
                }
            except Exception as e:
                health_data["components"]["database"] = {
                    "status": "error",
                    "error": str(e)
                }
                if health_data["status"] == "healthy":
                    health_data["status"] = "degraded"

            # Check Discord webhook status
            try:
                webhook_status = self.discord.get_webhook_status()
                health_data["components"]["discord"] = {
                    "status": "healthy" if webhook_status.get("configured", False) else "warning",
                    "webhooks_configured": webhook_status.get("webhook_count", 0),
                    "enabled_webhooks": webhook_status.get("enabled_count", 0)
                }
            except Exception as e:
                health_data["components"]["discord"] = {
                    "status": "error",
                    "error": str(e)
                }
                if health_data["status"] == "healthy":
                    health_data["status"] = "degraded"

            # Check rating service status (non-critical)
            try:
                if self.rating_service and self.rating_service.enabled:
                    health_data["components"]["rating_services"] = {
                        "status": "healthy",
                        "enabled": True,
                        "cache_duration_hours": self.rating_service.cache_duration_hours
                    }
                else:
                    health_data["components"]["rating_services"] = {
                        "status": "disabled",
                        "enabled": False
                    }
            except Exception as e:
                health_data["components"]["rating_services"] = {
                    "status": "error",
                    "error": str(e)
                }
                # Rating service errors don't affect overall health

            # Add service operational status
            health_data["service_status"] = {
                "sync_in_progress": self.sync_in_progress,
                "initial_sync_complete": self.initial_sync_complete,
                "server_was_offline": self.server_was_offline,
                "uptime_seconds": round(time.time() - self._start_time, 2)
            }

            return health_data

        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return {
                "status": "error",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
                "message": "Health check system failure"
            }

    async def get_service_stats(self) -> Dict[str, Any]:
        """
        Get detailed service statistics for monitoring and diagnostics.

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

            logger.info(f"Service uptime: {stats['service']['uptime_seconds']} seconds")
            logger.info(f"Database items: {stats['database']['total_items']}")
            logger.info(f"Jellyfin connected: {stats['jellyfin']['connected']}")
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
        Run background maintenance tasks.

        This method runs indefinitely and performs periodic maintenance
        tasks to keep the service healthy and performant. It's designed
        to run as a background task alongside webhook processing.

        **Background Tasks:**
            - Periodic library synchronization with Jellyfin
            - Database maintenance (VACUUM, cleanup)
            - Connection health monitoring
            - Performance optimization tasks

        **Task Scheduling:**
            Tasks are scheduled based on time intervals and system load.
            Critical tasks run more frequently, while maintenance tasks
            run during low-activity periods.

        Note:
            This method runs indefinitely until the service is shut down.
            All errors are caught and logged to prevent the background
            tasks from crashing the main service.
        """
        self.logger.info("Starting background maintenance tasks")

        while not self.shutdown_event.is_set():
            try:
                # Task 1: Periodic library sync (every 6 hours)
                if not self.sync_in_progress and self.initial_sync_complete:
                    sync_interval = 6 * 3600  # 6 hours in seconds

                    # Ensure _last_sync_time is always a float
                    last_sync = getattr(self, '_last_sync_time', 0.0)
                    if not isinstance(last_sync, (int, float)):
                        last_sync = 0.0
                        self._last_sync_time = 0.0

                    time_since_last_sync = time.time() - last_sync

                    if time_since_last_sync > sync_interval:
                        self.logger.info("Starting scheduled background library sync")
                        try:
                            result = await self.sync_jellyfin_library(background=True)
                            self.logger.info(f"Background sync completed: {result.get('status', 'unknown')}")
                            self._last_sync_time = time.time()
                        except Exception as e:
                            self.logger.error(f"Background sync failed: {e}")

                # Task 2: Database maintenance (weekly) - WITH TYPE SAFETY
                vacuum_interval = 7 * 24 * 3600  # 1 week in seconds

                # Ensure last_vacuum is always a float
                if not isinstance(self.last_vacuum, (int, float)) or self.last_vacuum is None:
                    self.logger.warning("last_vacuum was None or invalid, resetting to current time")
                    self.last_vacuum = time.time()

                time_since_vacuum = time.time() - self.last_vacuum

                if time_since_vacuum > vacuum_interval:
                    self.logger.info("Starting scheduled database maintenance")
                    try:
                        if await self.db.vacuum_database():
                            await self.db.update_vacuum_timestamp()  # Update in database too
                            self.last_vacuum = time.time()
                            self.logger.info("Database maintenance completed successfully")
                        else:
                            self.logger.warning("Database maintenance completed with warnings")
                    except Exception as e:
                        self.logger.error(f"Database maintenance failed: {e}")

                # Task 3: Jellyfin connectivity monitoring
                try:
                    is_connected = await self.jellyfin.is_connected()
                    if not is_connected and not self.server_was_offline:
                        self.logger.warning("Jellyfin server appears to be offline")
                        self.server_was_offline = True
                    elif is_connected and self.server_was_offline:
                        self.logger.info("Jellyfin server is back online")
                        self.server_was_offline = False
                except Exception as e:
                    self.logger.debug(f"Connection check failed: {e}")

                # Wait before next iteration (5 minutes)
                await asyncio.sleep(300)

            except Exception as e:
                self.logger.error(f"Background task error: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait 1 minute before retrying

        self.logger.info("Background tasks stopped - service is shutting down")

    async def sync_jellyfin_library(self, background: bool = False) -> Dict[str, Any]:
        """
        Synchronize entire Jellyfin library to local database.

        This method performs a complete sync of the Jellyfin library, processing
        all items in configurable batches for optimal efficiency. It now uses the
        configured batch size and batch database operations for maximum performance.

        **Enhanced Batch Processing:**
            - Uses config.sync.sync_batch_size for both API retrieval and database operations
            - Batch database saves using save_items_batch() for significant performance improvement
            - Individual item error handling within batches maintains data integrity
            - Configurable batch sizes up to 1000 items for high-performance scenarios

        **Background vs Foreground Sync:**
            - Background: Doesn't block webhook processing (for periodic syncs)
            - Foreground: Blocks webhook processing (for initial setup)

        Args:
            background (bool): Whether to run sync in background mode.
                Background syncs don't block webhook processing.

        Returns:
            Dict[str, Any]: Sync results including status, items processed, and timing

        Example:
            ```python
            # Perform initial sync (blocks webhooks)
            result = await service.sync_jellyfin_library(background=False)

            # Periodic sync (non-blocking)
            result = await service.sync_jellyfin_library(background=True)

            logger.info(f"Sync status: {result['status']}")
            logger.info(f"Items processed: {result['items_processed']}")
            ```

        Note:
            Large libraries now process much faster due to batch operations.
            Progress is logged periodically to provide feedback during operations.
        """
        # Initialize timing variable
        sync_start_time = time.time()

        # Prevent multiple syncs from running simultaneously
        if self.sync_in_progress:
            self.logger.warning("Library sync already in progress - skipping")
            return {
                "status": "skipped",
                "message": "Sync already in progress",
                "items_processed": 0,
                "total_items": 0,
                "errors": 0,
                "processing_time": 0
            }

        try:
            # Set sync state flags
            self.sync_in_progress = True
            self.is_background_sync = background

            if background:
                self.logger.info("Starting background library sync...")
            else:
                self.logger.info("Starting foreground library sync...")

            # Check Jellyfin connectivity before starting
            if not await self.jellyfin.is_connected():
                self.logger.error("Cannot sync: Jellyfin server is not accessible")
                return {
                    "status": "error",
                    "message": "Jellyfin server not accessible",
                    "items_processed": 0,
                    "total_items": 0,
                    "errors": 0,
                    "processing_time": round(time.time() - sync_start_time, 2)
                }

            # Get configured batch size from sync configuration
            api_batch_size = self.config.sync.sync_batch_size
            self.logger.info(f"Using configured batch size: {api_batch_size}")

            # Retrieve all items from Jellyfin API using configured batch size
            self.logger.info("Retrieving all items from Jellyfin...")
            all_items = await self.jellyfin.get_all_items(batch_size=api_batch_size)

            if not all_items:
                self.logger.warning("No items retrieved from Jellyfin library")
                return {
                    "status": "warning",
                    "message": "No items found in library",
                    "items_processed": 0,
                    "total_items": 0,
                    "errors": 0,
                    "processing_time": round(time.time() - sync_start_time, 2)
                }

            self.logger.info(f"Retrieved {len(all_items)} items from Jellyfin - starting processing...")

            # Process items using the same batch size for database operations
            db_batch_size = api_batch_size  # Use same size for consistency
            items_processed = 0
            batch_errors = 0
            total_individual_errors = 0

            for i in range(0, len(all_items), db_batch_size):
                batch = all_items[i:i + db_batch_size]
                batch_start_time = time.time()

                try:
                    # Convert API data to MediaItem objects for this batch
                    media_items = []
                    conversion_errors = 0

                    for item_data in batch:
                        try:
                            media_item = await self.jellyfin.convert_to_media_item(item_data)
                            media_items.append(media_item)
                        except Exception as e:
                            self.logger.warning(f"Error converting item {item_data.get('Id', 'unknown')}: {e}")
                            conversion_errors += 1
                            total_individual_errors += 1

                    # Save batch to database using efficient batch operation
                    if media_items:
                        batch_results = await self.db.save_items_batch(media_items)

                        # Check if entire batch failed (likely schema issue)
                        if batch_results['failed'] == len(media_items) and batch_results['successful'] == 0:
                            # Entire batch failed - this is a batch error
                            batch_errors += 1
                            total_individual_errors += batch_results['failed']

                            self.logger.error(
                                f"Batch {i // db_batch_size + 1}: ENTIRE BATCH FAILED - "
                                f"likely schema mismatch or database issue. "
                                f"{batch_results['failed']} items could not be saved."
                            )
                        else:
                            # Partial success - some items saved, some failed
                            items_processed += batch_results['successful']
                            total_individual_errors += batch_results['failed']

                            batch_time = time.time() - batch_start_time
                            self.logger.debug(
                                f"Batch {i // db_batch_size + 1}: "
                                f"{batch_results['successful']}/{len(media_items)} saved, "
                                f"{batch_results['failed']} failed, "
                                f"{conversion_errors} conversion errors "
                                f"({batch_time:.2f}s)"
                            )
                    else:
                        # All items in this batch failed conversion
                        self.logger.warning(f"Batch {i // db_batch_size + 1}: All items failed conversion")

                    # Log progress periodically (every 10 batches)
                    if (i // db_batch_size) % 10 == 0:
                        progress_pct = round((i / len(all_items)) * 100, 1)
                        elapsed_time = time.time() - sync_start_time
                        estimated_total = elapsed_time / (i + db_batch_size) * len(all_items) if i > 0 else 0
                        remaining_time = max(0, estimated_total - elapsed_time)

                        self.logger.info(
                            f"Sync progress: {items_processed}/{len(all_items)} items processed "
                            f"({progress_pct}%) - "
                            f"Elapsed: {elapsed_time:.1f}s, "
                            f"ETA: {remaining_time:.1f}s"
                        )

                except Exception as e:
                    # Handle batch-level errors (connection issues, etc.)
                    self.logger.error(f"Error processing batch starting at index {i}: {e}")
                    batch_errors += 1
                    total_individual_errors += len(batch)

                # Add small delay between batches to prevent overwhelming the system
                if i + db_batch_size < len(all_items):  # Don't delay after the last batch
                    await asyncio.sleep(self.config.sync.api_request_delay)

                # Add small delay between batches to prevent overwhelming the system
                if i + db_batch_size < len(all_items):  # Don't delay after the last batch
                    await asyncio.sleep(self.config.sync.api_request_delay)

            # Update last sync time in database
            try:
                await self.db.update_last_sync_time()
            except Exception as e:
                self.logger.warning(f"Could not update last sync time: {e}")

            processing_time = time.time() - sync_start_time

            # Determine final status based on error thresholds
            total_items = len(all_items)
            success_rate = (items_processed / total_items) * 100 if total_items > 0 else 0

            if total_individual_errors == 0:
                status = "success"
            elif success_rate >= 50:  # More than half succeeded
                status = "partial"
            else:
                status = "error"

            # Log comprehensive completion summary
            self.logger.info("=" * 80)
            self.logger.info(f"Library sync completed with status: {status.upper()}")
            self.logger.info(f"  Items processed: {items_processed:,}/{total_items:,}")
            self.logger.info(f"  Success rate: {success_rate:.1f}%")
            self.logger.info(f"  Individual errors: {total_individual_errors:,}")
            self.logger.info(f"  Batch errors: {batch_errors:,}")
            self.logger.info(f"  Processing time: {processing_time:.2f}s")
            self.logger.info(f"  Throughput: {items_processed / processing_time:.1f} items/sec")
            self.logger.info(f"  Batch size used: {db_batch_size:,}")
            self.logger.info("=" * 80)

            return {
                "status": status,
                "items_processed": items_processed,
                "total_items": total_items,
                "errors": total_individual_errors,
                "batch_errors": batch_errors,
                "success_rate": round(success_rate, 1),
                "processing_time": round(processing_time, 2),
                "throughput": round(items_processed / processing_time, 1) if processing_time > 0 else 0,
                "batch_size_used": db_batch_size
            }

        except Exception as e:
            processing_time = time.time() - sync_start_time
            self.logger.error(f"Library sync failed after {processing_time:.2f}s: {e}")
            return {
                "status": "error",
                "message": str(e),
                "items_processed": 0,
                "total_items": 0,
                "errors": 0,
                "processing_time": round(processing_time, 2)
            }

        finally:
            # Always reset sync flags, even if an error occurred
            self.sync_in_progress = False
            self.is_background_sync = False

    async def _check_initial_sync(self) -> None:
        """
        Check if initial sync is needed and perform it.

        This private method determines whether the service needs to perform
        an initial library sync. It's called during service initialization
        to ensure the database is populated with existing library content.

        **Initial Sync Logic:**
            The service creates a marker file after successful initial sync.
            If this marker doesn't exist, we perform initial sync to populate
            the database with existing Jellyfin library content.

        **Why Initial Sync Matters:**
            Without initial sync, all existing library items would be treated
            as "new" when webhooks are received, resulting in duplicate
            notifications for content that already existed.

        Note:
            This method runs a complete library sync and blocks webhook processing
            until it completes. It's only used on the very first service startup
            to populate the database with existing library content.

        **Why Block During Initial Sync?**
            We need a complete picture of the library before processing webhooks,
            otherwise we might treat existing items as "new" items.
        """
        try:
            # Check for completion marker
            init_complete_path = Path("/app/data/init_complete")

            if not init_complete_path.exists():
                self.logger.info("No initial sync marker found - performing initial library sync")
                await self._perform_initial_sync()
            else:
                self.logger.info("Initial sync marker found - skipping initial sync")
                self.initial_sync_complete = True

        except Exception as e:
            self.logger.error(f"Error checking initial sync status: {e}")

    async def _perform_initial_sync(self) -> None:
        """
        Perform initial library synchronization.

        This private method runs a complete library sync and blocks webhook processing
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
                    self.initial_sync_complete = True
                    self._last_sync_time = time.time()
                    self.logger.debug(f"Set initial sync timestamp: {self._last_sync_time}")

                except Exception as e:
                    self.logger.warning(f"Could not create completion marker: {e}")
            else:
                self.logger.warning(f"Initial sync completed with status: {result.get('status', 'unknown')}")

        except Exception as e:
            self.logger.error(f"Initial sync failed: {e}")
            # Don't raise - service can continue without initial sync

    async def cleanup(self) -> None:
        """
        Clean up service resources during shutdown.

        This method performs graceful cleanup of all service components
        and resources. It's called during application shutdown to ensure
        proper resource cleanup and prevent data loss.

        **Cleanup Tasks:**
            - Signal background tasks to stop
            - Close database connections
            - Close HTTP sessions
            - Flush any pending operations
            - Log shutdown completion

        Example:
            ```python
            # During application shutdown
            await webhook_service.cleanup()
            logger.info("Service cleanup completed")
            ```

        Note:
            This method should be called during application shutdown
            to ensure proper resource cleanup. It's designed to be
            safe to call multiple times.
        """
        try:
            self.logger.info("Starting service cleanup...")

            # Signal background tasks to stop
            self.shutdown_event.set()

            # Close TVDB client
            if self.tvdb_client:
                try:
                    await self.tvdb_client.__aexit__(None, None, None)
                    self.logger.debug("TVDB client cleaned up")
                except Exception as e:
                    self.logger.error(f"Error cleaning up TVDB client: {e}")

            # Close database connections
            if self.db:
                await self.db.close()
                self.logger.debug("Database connections closed")

            # Close Discord notifier and its sessions
            if hasattr(self, 'discord') and self.discord:
                await self.discord.cleanup()
                self.logger.debug("Discord notifier cleaned up")

            self.logger.info("Service cleanup completed successfully")

        except Exception as e:
            self.logger.error(f"Error during service cleanup: {e}")