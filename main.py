#!/usr/bin/env python3
"""
Jellynouncer Discord Webhook Service

This module provides a comprehensive intermediate webhook service that sits between Jellyfin
media server and Discord, enabling intelligent notifications for new media additions and
quality upgrades.

Architecture Overview:
    The service follows a modular, async-first design with clear separation of concerns:

    1. Configuration Layer (Pydantic Models):
       - Type-safe configuration with validation using Pydantic v2
       - Environment variable overrides for Docker deployments
       - Nested configuration structures for organized settings

    2. Data Layer (DatabaseManager):
       - SQLite with WAL mode for concurrent access (multiple readers, single writer)
       - Content hashing for change detection to avoid duplicate notifications
       - Batch operations for performance during library syncs

    3. Integration Layer (JellyfinAPI, DiscordNotifier):
       - Jellyfin API client with retry logic for network resilience
       - Discord webhook with rate limiting to respect Discord's API limits
       - Template-based message formatting using Jinja2 templates

    4. Logic Layer (ChangeDetector, WebhookService):
       - Intelligent change detection (resolution upgrades, codec improvements, audio enhancements, HDR)
       - Webhook routing based on content type (movies vs TV shows vs music)
       - Background sync and maintenance tasks using asyncio

    5. API Layer (FastAPI):
       - RESTful endpoints for webhook processing from Jellyfin
       - Health checks and diagnostics for monitoring
       - Debug endpoints for troubleshooting and development

Key Features:
    - Smart change detection (new vs. upgraded content)
    - Multi-webhook routing (separate channels for movies, TV, music)
    - Rate limiting and error recovery to handle network issues
    - Background library synchronization to catch missed webhooks
    - Template-based Discord embeds for rich media information
    - Comprehensive logging and monitoring for production deployment

Example Usage:
    This service is designed to run as a Docker container with environment variables
    for configuration:

    ```bash
    # Set required environment variables
    export JELLYFIN_SERVER_URL="http://jellyfin:8096"
    export JELLYFIN_API_KEY="your_api_key_here"
    export JELLYFIN_USER_ID="your_user_id_here"
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

    # Run the service
    python main.py
    ```

Dependencies:
    - FastAPI: Modern async web framework for the API layer
    - aiohttp: Async HTTP client for Discord webhooks and external API calls
    - aiosqlite: Async SQLite database operations to avoid blocking the event loop
    - Pydantic: Data validation and configuration management with Python type hints
    - Jinja2: Template engine for Discord embed formatting
    - jellyfin-apiclient-python: Official Jellyfin API client

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import os
import asyncio
import time
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

# Third-party imports for async web framework and HTTP operations
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import uvicorn

# Import our custom modules
from webhook_models import WebhookPayload
from webhook_service import WebhookService
from utils import setup_logging, get_logger

# Global service instance - shared across the FastAPI application
# This pattern allows us to initialize the service once during startup
# and reuse it across all request handlers
webhook_service: Optional[WebhookService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Async context manager for FastAPI application lifespan management.

    This function handles the complete lifecycle of the Jellynouncer application,
    including startup initialization and graceful shutdown procedures. It uses
    Python's async context manager pattern to ensure proper resource management.

    The lifespan pattern is the modern FastAPI way to handle startup/shutdown,
    replacing the deprecated @app.on_event("startup") decorators.

    Args:
        app: The FastAPI application instance

    Yields:
        None: Control is yielded back to FastAPI to handle requests

    Raises:
        SystemExit: If critical initialization fails, the application will exit

    Example:
        This function is automatically called by FastAPI when the application
        starts and stops:

        ```python
        app = FastAPI(lifespan=lifespan)
        # FastAPI automatically calls lifespan on startup/shutdown
        ```

    Note:
        All code before the `yield` statement runs during application startup.
        All code after the `yield` statement runs during application shutdown.
        This ensures proper resource cleanup even if the application crashes.
    """
    global webhook_service

    # === STARTUP PHASE ===
    # This code runs when the FastAPI application starts

    try:
        # Initialize logging first so we can log any errors during startup
        # The setup_logging function creates both console and file handlers
        logger = setup_logging(
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_dir=os.getenv("LOG_DIR", "/app/logs")
        )
        logger.info("Starting Jellynouncer service initialization...")

        # Create and initialize the main webhook service
        # This handles all the complex business logic and integrations
        webhook_service = WebhookService()
        await webhook_service.initialize()

        # Start background tasks for maintenance and monitoring
        # These tasks run continuously to sync with Jellyfin and maintain the database
        background_task = asyncio.create_task(webhook_service.background_tasks())

        logger.info("Jellynouncer service started successfully")
        logger.info("=" * 60)
        logger.info("🎬 Jellynouncer is ready to receive webhooks!")
        logger.info("Send webhooks to: http://your-server:8080/webhook")
        logger.info("Health check: http://your-server:8080/health")
        logger.info("=" * 60)

        # Yield control back to FastAPI to start serving requests
        # Everything after this yield runs during shutdown
        yield

    except Exception as e:
        logger.error(f"Failed to start Jellynouncer: {e}", exc_info=True)
        raise SystemExit(f"Service startup failed: {e}")

    # === SHUTDOWN PHASE ===
    # This code runs when the FastAPI application stops

    try:
        logger.info("Starting graceful shutdown...")

        if webhook_service:
            # Cancel background tasks
            if 'background_task' in locals():
                background_task.cancel()
                try:
                    await background_task
                except asyncio.CancelledError:
                    pass

            # Clean up service resources
            await webhook_service.cleanup()

        logger.info("Jellynouncer shutdown completed successfully")

    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)


# Create FastAPI application with lifespan management
app = FastAPI(
    title="Jellynouncer",
    description="Advanced Discord webhook service for Jellyfin media notifications",
    version="2.0.0",
    lifespan=lifespan
)


@app.post("/webhook")
async def receive_webhook(payload: WebhookPayload):
    """
    Main webhook endpoint for receiving notifications from Jellyfin.

    This endpoint processes incoming webhooks from Jellyfin's webhook plugin
    and triggers the appropriate notification workflow. It validates the
    payload format and handles the complete notification pipeline.

    Args:
        payload (WebhookPayload): Validated webhook data from Jellyfin

    Returns:
        dict: Processing result with status and details

    Raises:
        HTTPException: If service is not ready or processing fails

    Example:
        Jellyfin webhook plugin should be configured to send POST requests to:
        `http://your-jellynouncer-server:8080/webhook`

        Example webhook payload:
        ```json
        {
            "ItemId": "abc123",
            "Name": "The Matrix",
            "ItemType": "Movie",
            "NotificationType": "library.new"
        }
        ```
    """
    if webhook_service is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready - still initializing"
        )

    try:
        # Process the webhook through our service layer
        result = await webhook_service.process_webhook(payload)
        return result

    except Exception as e:
        # Log the error but don't expose internal details to the client
        webhook_service.logger.error(f"Webhook processing failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal error processing webhook"
        )


@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring and load balancers.

    This endpoint provides a quick health check for external monitoring
    systems, Docker health checks, and load balancers. It returns basic
    service status without detailed component information.

    Returns:
        dict: Simple health status

    Example:
        ```bash
        curl http://jellynouncer:8080/health
        # Returns: {"status": "healthy", "timestamp": "2024-01-01T12:00:00Z"}
        ```

    Note:
        This endpoint is lightweight and designed for frequent polling.
        For detailed service information, use the /stats endpoint instead.
    """
    if webhook_service is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready - still initializing"
        )

    try:
        # Perform comprehensive health check
        health_data = await webhook_service.health_check()

        # Return appropriate HTTP status based on health
        if health_data["status"] == "healthy":
            return health_data
        elif health_data["status"] == "degraded":
            return JSONResponse(status_code=200, content=health_data)
        else:
            return JSONResponse(status_code=503, content=health_data)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Health check failed: {str(e)}"
        )


@app.get("/stats")
async def service_statistics():
    """
    Detailed service statistics endpoint for monitoring and diagnostics.

    This endpoint provides comprehensive statistics about the service
    including database metrics, processing counts, and component status.
    It's designed for administrative monitoring and troubleshooting.

    Returns:
        dict: Detailed service statistics and metrics

    Example:
        ```bash
        curl http://jellynouncer:8080/stats
        ```

    Note:
        This endpoint provides more detailed information than /health.
        It should not be called as frequently as the health check endpoint.
    """
    if webhook_service is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready - still initializing"
        )

    try:
        # Gather comprehensive statistics from the service
        stats = await webhook_service.get_service_stats()

        # Add timestamp and metadata
        stats["timestamp"] = datetime.now(timezone.utc).isoformat()
        stats["service_info"] = {
            "name": "Jellynouncer",
            "version": "2.0.0",
            "author": "Mark Newton"
        }

        return stats

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving service statistics: {str(e)}"
        )


@app.post("/sync")
async def trigger_manual_sync():
    """
    Trigger a manual library synchronization with Jellyfin.

    This endpoint allows administrators to manually start a full library sync
    without waiting for the scheduled background sync. This is useful when
    you know items have been added to Jellyfin but want to ensure they're
    processed immediately.

    Returns:
        dict: Sync initiation status with details about the operation

    Raises:
        HTTPException:
            - 503 if service is not ready
            - 409 if a sync is already in progress
            - 500 if sync initiation fails

    Example:
        ```bash
        curl -X POST http://jellynouncer:8080/sync
        # Returns: {"status": "success", "message": "Library sync started"}
        ```

    Note:
        The sync runs in the background, so this endpoint returns immediately.
        Use the /stats endpoint to monitor sync progress and completion.
        Multiple sync requests will be queued to prevent resource conflicts.
    """
    if webhook_service is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready - still initializing"
        )

    try:
        # Initiate manual library sync
        result = await webhook_service.trigger_manual_sync()

        # Return appropriate HTTP status based on result
        if result.get("status") == "success":
            return result
        elif result.get("status") == "warning":
            # Sync already in progress
            raise HTTPException(status_code=409, detail=result.get("message"))
        else:
            # Error starting sync
            raise HTTPException(status_code=500, detail=result.get("message"))

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error triggering manual sync: {str(e)}"
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom exception handler for Pydantic validation errors.

    This handler provides more user-friendly error messages when webhook
    payloads don't match the expected structure. It's particularly useful
    for debugging webhook configuration issues with Jellyfin.

    Args:
        request: The FastAPI request that caused the validation error
        exc: The Pydantic validation exception with detailed error information

    Returns:
        JSONResponse: Formatted error response with detailed validation errors

    Example:
        When Jellyfin sends an invalid webhook payload, this handler returns:
        ```json
        {
            "error": "Validation Error",
            "details": [
                {
                    "field": "ItemId",
                    "message": "field required",
                    "input": null
                }
            ],
            "help": "Check your Jellyfin webhook configuration..."
        }
        ```

    Note:
        This handler helps administrators diagnose webhook configuration
        problems by providing clear, actionable error messages rather than
        generic HTTP 422 responses.
    """
    # Extract detailed error information from the Pydantic exception
    errors = []
    for error in exc.errors():
        field_path = " → ".join(str(loc) for loc in error["loc"])
        errors.append({
            "field": field_path,
            "message": error["msg"],
            "input": error.get("input", "unknown")
        })

    # Log the validation error for debugging
    if webhook_service and webhook_service.logger:
        webhook_service.logger.warning(
            f"Webhook validation error from {request.client.host if request.client else 'unknown'}: "
            f"{len(errors)} field errors"
        )

    # Return user-friendly error response
    return JSONResponse(
        status_code=422,
        content={
            "error": "Webhook Validation Error",
            "message": "The webhook payload from Jellyfin doesn't match the expected format",
            "details": errors,
            "help": (
                "This usually indicates a configuration issue with the Jellyfin webhook plugin. "
                "Please check that your webhook is configured to send the required fields: "
                "ItemId, Name, ItemType, NotificationType"
            )
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler for unexpected errors.

    This handler catches any unhandled exceptions in the application and
    provides a consistent error response format. It also ensures that
    sensitive error details are not exposed to clients while still
    logging the full error for debugging.

    Args:
        request: The FastAPI request that caused the exception
        exc: The unhandled exception

    Returns:
        JSONResponse: Generic error response without sensitive details

    Note:
        This handler serves as a safety net to prevent the application
        from crashing on unexpected errors while ensuring that detailed
        error information is logged for debugging purposes.
    """
    # Log the full error with stack trace for debugging
    if webhook_service and webhook_service.logger:
        webhook_service.logger.error(
            f"Unhandled exception in {request.method} {request.url}: {exc}",
            exc_info=True
        )

    # Return generic error response without sensitive details
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred while processing your request",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": str(hash(f"{request.method}{request.url}{time.time()}"))
        }
    )


def setup_signal_handlers():
    """
    Set up signal handlers for graceful shutdown.

    This function configures the application to handle system signals
    (like SIGTERM from Docker or SIGINT from Ctrl+C) gracefully,
    ensuring that connections are closed and data is saved properly.

    The signal handlers work with the FastAPI lifespan manager to
    ensure clean shutdown procedures are followed.

    Note:
        Signal handlers only work on Unix-based systems (Linux, macOS).
        On Windows, the application will still shut down cleanly when
        the process is terminated normally.
    """
    # Get logger for signal handling
    logger = get_logger("main")

    def signal_handler(signum, frame):
        """Handle shutdown signals gracefully."""
        signal_name = signal.Signals(signum).name
        logger.info(f"Received {signal_name} signal - initiating graceful shutdown...")

        # Exit with success code to trigger the lifespan shutdown
        sys.exit(0)

    # Register handlers for common shutdown signals
    signal.signal(signal.SIGTERM, signal_handler)  # Docker stop
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C


if __name__ == "__main__":
    """
    Main entry point when running the application directly.

    This block only executes when the script is run directly (python main.py)
    rather than being imported as a module. It sets up signal handlers and
    starts the Uvicorn ASGI server with production-ready configuration.

    Environment Variables:
        HOST: Server host address (default: 0.0.0.0)
        PORT: Server port number (default: 8080)
        WORKERS: Number of worker processes (default: 1)
        LOG_LEVEL: Uvicorn log level (default: info)

    Example:
        ```bash
        # Run with default settings
        python main.py

        # Run with custom configuration
        HOST=127.0.0.1 PORT=3000 python main.py
        ```

    Note:
        In production, you may want to use a process manager like
        systemd or Docker to manage the application lifecycle rather
        than running it directly with python.
    """
    # Set up signal handlers for graceful shutdown
    setup_signal_handlers()

    # Get configuration from environment variables with sensible defaults
    host = os.getenv("HOST", "0.0.0.0")  # Listen on all interfaces
    port = int(os.getenv("PORT", 8080))  # Standard webhook port
    workers = int(os.getenv("WORKERS", 1))  # Single worker for SQLite compatibility
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    # Start the Uvicorn ASGI server
    # Uvicorn is a lightning-fast ASGI server implementation for Python
    uvicorn.run(
        "main:app",  # Module and app variable
        host=host,
        port=port,
        log_level=log_level,
        # Use only 1 worker with SQLite to avoid database locking issues
        # Multiple workers would require PostgreSQL or MySQL
        workers=1,
        # Enable auto-reload in development
        reload=os.getenv("ENVIRONMENT", "production") == "development",
        # Server configuration for production
        access_log=True,  # Enable access logging
        use_colors=True,  # Colorized logs for better readability
    )