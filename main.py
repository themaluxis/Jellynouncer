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
import json
import asyncio
import logging
import logging.handlers
import time
import hashlib
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

# Third-party imports for async web framework and HTTP operations
import aiohttp
import aiosqlite
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from jellyfin_apiclient_python import JellyfinClient
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError
from pydantic import BaseModel, Field, ConfigDict, ValidationError, field_validator
import uvicorn

# Import our custom modules
from config_models import AppConfig, ConfigurationValidator
from webhook_models import WebhookPayload
from webhook_service import WebhookService
from utils import setup_logging

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
        logger.info("📡 Listening for Jellyfin notifications...")
        logger.info("💬 Ready to send Discord notifications")
        logger.info("=" * 60)

    except Exception as e:
        # If initialization fails, log the error and exit
        # This prevents the application from starting in a broken state
        logger.error(f"Failed to initialize Jellynouncer service: {e}")
        logger.error("Application startup failed - exiting")
        raise SystemExit(1)

    # === YIELD CONTROL TO FASTAPI ===
    # At this point, startup is complete and FastAPI takes over to handle requests
    # The application will run normally until it receives a shutdown signal
    yield

    # === SHUTDOWN PHASE ===
    # This code runs when the FastAPI application is shutting down

    logger.info("Shutting down Jellynouncer service...")

    try:
        # Cancel background tasks gracefully
        if 'background_task' in locals() and not background_task.done():
            background_task.cancel()
            try:
                # Wait a bit for the task to finish cleanly
                await asyncio.wait_for(background_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning("Background task took too long to cancel")

        # Shutdown the webhook service and close all connections
        if webhook_service:
            await webhook_service.shutdown()

        logger.info("Jellynouncer service shutdown completed")

    except Exception as e:
        # Log shutdown errors but don't prevent the application from stopping
        logger.error(f"Error during shutdown: {e}")


# Create the FastAPI application with the lifespan manager
# The lifespan parameter tells FastAPI to use our custom startup/shutdown logic
app = FastAPI(
    title="Jellynouncer Discord Webhook Service",
    description="Intelligent Discord notifications for Jellyfin media server",
    version="2.0.0",
    lifespan=lifespan,
    # Disable automatic OpenAPI documentation in production for security
    docs_url="/docs" if os.getenv("ENVIRONMENT", "production") == "development" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT", "production") == "development" else None,
)


@app.post("/webhook")
async def process_webhook(payload: WebhookPayload, request: Request):
    """
    Process incoming webhooks from Jellyfin and trigger Discord notifications.

    This is the main endpoint that Jellyfin calls when media items are added
    or updated. It validates the webhook payload using Pydantic models,
    then delegates processing to the WebhookService for business logic.

    Args:
        payload: Validated webhook payload from Jellyfin containing media information
        request: FastAPI request object for accessing headers and client info

    Returns:
        dict: Processing result with status and details

    Raises:
        HTTPException:
            - 503 if the service is not ready or initializing
            - 400 if the webhook payload is invalid
            - 500 if processing fails due to internal errors

    Example:
        This endpoint is called automatically by Jellyfin's webhook plugin:

        ```bash
        curl -X POST http://jellynouncer:8080/webhook \
             -H "Content-Type: application/json" \
             -d '{"ItemId": "123", "Name": "Movie Title", "ItemType": "Movie", ...}'
        ```

    Note:
        The endpoint uses Pydantic's automatic validation to ensure the payload
        matches the expected WebhookPayload model. Invalid payloads are automatically
        rejected with detailed error messages.
    """
    # Check if the service is ready to process webhooks
    # During startup, the service might not be fully initialized
    if webhook_service is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready - still initializing"
        )

    try:
        # Get client information for logging and debugging
        client_host = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")

        # Log the incoming webhook for debugging and monitoring
        webhook_service.logger.info(
            f"Received webhook from {client_host} for item: {payload.Name} "
            f"(ID: {payload.ItemId}, Type: {payload.ItemType})"
        )

        # Process the webhook through our business logic
        # This handles change detection, Discord notifications, and database updates
        result = await webhook_service.process_webhook(payload)

        # Log the result for monitoring and debugging
        if result.get("status") == "success":
            webhook_service.logger.info(f"Successfully processed webhook for {payload.Name}")
        else:
            webhook_service.logger.warning(f"Webhook processing completed with issues: {result}")

        return result

    except ValidationError as e:
        # Handle Pydantic validation errors (invalid payload structure)
        webhook_service.logger.error(f"Invalid webhook payload: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {str(e)}")

    except Exception as e:
        # Handle unexpected errors during processing
        webhook_service.logger.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while processing webhook"
        )


@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring and load balancer probes.

    This endpoint provides a quick way to verify that the Jellynouncer service
    is running and healthy. It's designed to be called frequently by monitoring
    systems, Docker health checks, and Kubernetes probes.

    Returns:
        dict: Health status information including:
            - status: Overall health status ("healthy" or "unhealthy")
            - timestamp: Current timestamp in ISO format
            - service: Service name and version
            - components: Status of individual service components

    Example:
        ```bash
        curl http://jellynouncer:8080/health
        # Returns: {"status": "healthy", "timestamp": "2024-01-15T10:30:00Z", ...}
        ```

    Note:
        This endpoint is intentionally lightweight and fast to respond.
        It performs minimal checks to avoid impacting performance when
        called frequently by monitoring systems.
    """
    # Check if the service is initialized
    if webhook_service is None:
        return {
            "status": "unhealthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "Jellynouncer",
            "version": "2.0.0",
            "error": "Service not initialized"
        }

    try:
        # Perform a quick health check of the service
        health_status = await webhook_service.health_check()

        return {
            "status": "healthy" if health_status.get("healthy", False) else "unhealthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "Jellynouncer",
            "version": "2.0.0",
            "details": health_status
        }

    except Exception as e:
        # If health check itself fails, return unhealthy status
        return {
            "status": "unhealthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "Jellynouncer",
            "version": "2.0.0",
            "error": str(e)
        }


@app.get("/stats")
async def get_service_statistics():
    """
    Get comprehensive service statistics for monitoring and debugging.

    This endpoint provides detailed information about the service's current
    state, performance metrics, and component status. It's useful for
    administrators and developers to understand how the service is performing.

    Returns:
        dict: Detailed service statistics including:
            - Service uptime and version information
            - Database statistics (item counts, cache hit rates)
            - Webhook processing metrics
            - Jellyfin connection status
            - Discord webhook configurations
            - Memory and performance metrics

    Raises:
        HTTPException: 503 if service is not ready

    Example:
        ```bash
        curl http://jellynouncer:8080/stats
        # Returns detailed JSON with service metrics
        ```

    Note:
        This endpoint may take slightly longer to respond than /health
        as it gathers comprehensive statistics from all service components.
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

    def signal_handler(signum, frame):
        """Handle shutdown signals gracefully."""
        signal_name = signal.Signals(signum).name
        print(f"\nReceived {signal_name} signal - initiating graceful shutdown...")

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