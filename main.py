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
import json
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# Third-party imports for async web framework and HTTP operations
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import uvicorn
from pydantic import ValidationError

# Import our custom modules
from webhook_models import WebhookPayload
from webhook_service import WebhookService
from config_models import ConfigurationValidator
from utils import setup_logging, get_logger
from network_utils import log_jellynouncer_startup

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

    # Initialize logger early so it's available in exception handlers
    logger = None

    # === STARTUP PHASE ===
    # This code runs when the FastAPI application starts

    try:
        # Initialize logging first so we can log any errors during startup
        # The setup_logging function creates both console and file handlers

        # This ensures environment variables can override config file settings
        config_validator = ConfigurationValidator()
        config = None
        log_level = "INFO"  # Default fallback

        try:
            # Try to load full configuration
            config = config_validator.load_and_validate_config()
            log_level = config.server.log_level
        except Exception as config_error:
            # If config loading fails, fall back to environment variable or default
            log_level = os.getenv("LOG_LEVEL", "INFO")
            # Use print since logging isn't set up yet
            print(f"Warning: Could not load config for log level, using {log_level}: {config_error}")

        # Initialize logging with the determined log level
        # The setup_logging function creates both console and file handlers
        logger = setup_logging(
            log_level=log_level,
            log_dir=os.getenv("LOG_DIR", "/app/logs")
        )
        logger.info("Starting Jellynouncer service initialization...")
        # Log which source provided the log level for debugging
        logger.debug(f"Log level '{log_level}' determined from configuration system")

        # Create and initialize the main webhook service
        # This handles all the complex business logic and integrations
        webhook_service = WebhookService()
        await webhook_service.initialize()

        # Start background tasks for maintenance and monitoring
        # These tasks run continuously to sync with Jellyfin and maintain the database
        background_task = asyncio.create_task(webhook_service.background_tasks())

        # Get port from same config system used by uvicorn
        if config:
            port = int(os.getenv("PORT", str(config.server.port)))
        else:
            port = int(os.getenv("PORT", 8080))

        log_jellynouncer_startup(port=port, logger=logger)

        # Yield control back to FastAPI to start serving requests
        # Everything after this yield runs during shutdown
        yield

    except Exception as e:
        # Use logger if available, otherwise fall back to print
        if logger:
            logger.error(f"Failed to start Jellynouncer: {e}", exc_info=True)
        else:
            print(f"Failed to start Jellynouncer (logging not initialized): {e}")
            import traceback
            traceback.print_exc()
        raise SystemExit(f"Service startup failed: {e}")

    # === SHUTDOWN PHASE ===
    # This code runs when the FastAPI application stops

    try:
        if logger:
            logger.info("Starting graceful shutdown...")
        else:
            print("Starting graceful shutdown...")

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

        if logger:
            logger.info("Jellynouncer shutdown completed successfully")
        else:
            print("Jellynouncer shutdown completed successfully")

    except Exception as e:
        if logger:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
        else:
            print(f"Error during shutdown: {e}")
            import traceback
            traceback.print_exc()


# Create FastAPI application with lifespan management
app = FastAPI(
    title="Jellynouncer",
    description="Intermediary Discord webhook service for Jellyfin media notifications",
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
        `http://your-jellynouncer-server:PORT/webhook`

        Default port is 8080, but can be configured via PORT environment variable
        or config file server.port setting.

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


@app.post("/webhook/debug")
async def webhook_debug_endpoint(request: Request) -> Dict[str, Any]:
    """
    Enhanced debug webhook endpoint for troubleshooting webhook configuration issues.

    This endpoint accepts any JSON payload and provides comprehensive analysis
    of the request, including headers, body content, validation results, and
    field-by-field analysis with detailed logging.

    Args:
        request: FastAPI request object containing raw webhook data

    Returns:
        Dict[str, Any]: Comprehensive debug information including validation results

    Raises:
        HTTPException: If service is not ready or critical processing fails
    """
    # Check if webhook service is initialized
    if webhook_service is None:
        raise HTTPException(
            status_code=503,
            detail="Debug service not ready - still initializing"
        )

    client_info = getattr(request, "client", None)
    client_host = getattr(client_info, "host", "unknown") if client_info else "unknown"
    client_port = getattr(client_info, "port", "unknown") if client_info else "unknown"

    # Initialize response structure
    debug_response = {
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "client": {
            "host": client_host,
            "port": client_port
        },
        "request_analysis": {},
        "json_analysis": {},
        "validation_results": {},
        "webhook_payload": None,
        "recommendations": []
    }

    try:
        # ==================== REQUEST ANALYSIS ====================

        # Get raw request data for analysis
        raw_body = await request.body()
        content_type = request.headers.get("content-type", "")
        user_agent = request.headers.get("user-agent", "")

        # Store request metadata
        debug_response["request_analysis"] = {
            "method": request.method,
            "url": str(request.url),
            "content_type": content_type,
            "user_agent": user_agent,
            "body_length": len(raw_body),
            "headers": dict(request.headers),
            "query_params": dict(request.query_params) if request.query_params else {}
        }

        # Log comprehensive request details
        webhook_service.logger.info("=" * 80)
        webhook_service.logger.info("🔍 ENHANCED DEBUG WEBHOOK REQUEST RECEIVED")
        webhook_service.logger.info("=" * 80)
        webhook_service.logger.info(f"📡 Client: {client_host}:{client_port}")
        webhook_service.logger.info(f"🌐 Method: {request.method}")
        webhook_service.logger.info(f"📍 URL: {request.url}")
        webhook_service.logger.info(f"📋 Content-Type: {content_type}")
        webhook_service.logger.info(f"🤖 User-Agent: {user_agent}")
        webhook_service.logger.info(f"📏 Body Length: {len(raw_body)} bytes")

        # Log all headers (mask sensitive ones)
        webhook_service.logger.info("📨 REQUEST HEADERS:")
        for header_name, header_value in request.headers.items():
            if header_name.lower() in ['authorization', 'x-api-key', 'x-jellyfin-token']:
                masked_value = header_value[:8] + "***" if len(header_value) > 8 else "***"
                webhook_service.logger.info(f"    {header_name}: {masked_value}")
            else:
                webhook_service.logger.info(f"    {header_name}: {header_value}")

        # Log query parameters if any
        if request.query_params:
            webhook_service.logger.info("🔗 QUERY PARAMETERS:")
            for param_name, param_value in request.query_params.items():
                webhook_service.logger.info(f"    {param_name}: {param_value}")

        # Log raw body content (first 1000 chars for safety)
        webhook_service.logger.info("📦 RAW BODY CONTENT:")
        try:
            body_text = raw_body.decode('utf-8', errors='replace')
            if len(body_text) > 1000:
                webhook_service.logger.info(f"    {body_text[:1000]}... (truncated, total length: {len(body_text)})")
            else:
                webhook_service.logger.info(f"    {body_text}")

            debug_response["request_analysis"]["body_preview"] = body_text[:1000] if len(
                body_text) > 1000 else body_text

        except Exception as decode_error:
            webhook_service.logger.error(f"    Failed to decode body as UTF-8: {decode_error}")
            webhook_service.logger.info(f"    Raw bytes (first 200): {raw_body[:200]}")
            debug_response["request_analysis"]["decode_error"] = str(decode_error)

        # ==================== JSON PARSING ====================

        json_data = None
        json_parse_error = None

        # Attempt to parse JSON
        try:
            json_data = json.loads(raw_body)
            webhook_service.logger.info("✅ JSON PARSING SUCCESSFUL")

            if isinstance(json_data, dict):
                webhook_service.logger.info(f"📊 JSON Structure:")
                webhook_service.logger.info(f"    Top-level keys: {list(json_data.keys())}")
                webhook_service.logger.info(f"    Total keys: {len(json_data)}")

                # Store JSON analysis
                debug_response["json_analysis"] = {
                    "parse_success": True,
                    "is_dictionary": True,
                    "top_level_keys": list(json_data.keys()),
                    "total_keys": len(json_data),
                    "field_analysis": {}
                }

                # Log each field in detail
                webhook_service.logger.info("🔍 DETAILED FIELD ANALYSIS:")
                for key, value in json_data.items():
                    value_type = type(value).__name__
                    if value is None:
                        value_str = "null"
                    elif isinstance(value, str):
                        # Truncate long strings in logs
                        value_str = f'"{value[:100]}..."' if len(value) > 100 else f'"{value}"'
                    else:
                        value_str = str(value)

                    webhook_service.logger.info(f"    {key} ({value_type}): {value_str}")

                    # Store field analysis
                    debug_response["json_analysis"]["field_analysis"][key] = {
                        "type": value_type,
                        "value": value,
                        "is_null": value is None,
                        "is_empty": value == "" if isinstance(value, str) else False
                    }
            else:
                webhook_service.logger.warning(f"⚠️  JSON is not a dictionary, it's a {type(json_data).__name__}")
                debug_response["json_analysis"] = {
                    "parse_success": True,
                    "is_dictionary": False,
                    "actual_type": type(json_data).__name__,
                    "value": json_data
                }

        except json.JSONDecodeError as e:
            json_parse_error = str(e)
            webhook_service.logger.error(f"❌ JSON PARSING FAILED: {e}")
            debug_response["json_analysis"] = {
                "parse_success": False,
                "error": json_parse_error,
                "error_position": getattr(e, 'pos', None)
            }

        # ==================== WEBHOOK PAYLOAD VALIDATION ====================

        validation_results = {
            "webhook_model_validation": {},
            "required_fields_check": {},
            "field_type_validation": {},
            "recommendations": []
        }

        if json_data and isinstance(json_data, dict):
            debug_response["webhook_payload"] = json_data

            # Try to validate against WebhookPayload model
            try:
                webhook_payload = WebhookPayload(**json_data)
                validation_results["webhook_model_validation"] = {
                    "success": True,
                    "message": "Payload successfully validates against WebhookPayload model"
                }
                webhook_service.logger.info("✅ WEBHOOK PAYLOAD VALIDATION SUCCESSFUL")

            except ValidationError as ve:
                validation_errors = []
                for error in ve.errors():
                    field_path = " → ".join(str(loc) for loc in error["loc"])
                    validation_errors.append({
                        "field": field_path,
                        "message": error["msg"],
                        "input": error.get("input"),
                        "type": error["type"]
                    })

                validation_results["webhook_model_validation"] = {
                    "success": False,
                    "errors": validation_errors
                }
                webhook_service.logger.error(f"❌ WEBHOOK PAYLOAD VALIDATION FAILED: {len(validation_errors)} errors")
                for error in validation_errors:
                    webhook_service.logger.error(f"    {error['field']}: {error['message']}")

            except Exception as e:
                validation_results["webhook_model_validation"] = {
                    "success": False,
                    "error": f"Unexpected validation error: {str(e)}"
                }
                webhook_service.logger.error(f"❌ UNEXPECTED VALIDATION ERROR: {e}")

            # Check for common required fields
            required_fields = ["ItemId", "Name", "ItemType", "NotificationType"]
            missing_fields = [field for field in required_fields if field not in json_data]

            validation_results["required_fields_check"] = {
                "required_fields": required_fields,
                "missing_fields": missing_fields,
                "present_fields": [field for field in required_fields if field in json_data]
            }

            if missing_fields:
                webhook_service.logger.warning(f"⚠️  MISSING REQUIRED FIELDS: {missing_fields}")
                validation_results["recommendations"].append(
                    f"Add missing required fields to Jellyfin webhook: {', '.join(missing_fields)}"
                )
            else:
                webhook_service.logger.info("✅ ALL REQUIRED FIELDS PRESENT")

        # ==================== GENERATE RECOMMENDATIONS ====================

        if json_parse_error:
            validation_results["recommendations"].append(
                "Fix JSON syntax errors in webhook payload"
            )

        if not json_data:
            validation_results["recommendations"].append(
                "Ensure webhook sends valid JSON payload"
            )

        if content_type != "application/json":
            validation_results["recommendations"].append(
                f"Set Content-Type to 'application/json' (currently: '{content_type}')"
            )

        if not user_agent.startswith("Jellyfin"):
            validation_results["recommendations"].append(
                "Verify request is coming from Jellyfin server"
            )

        debug_response["validation_results"] = validation_results
        debug_response["recommendations"] = validation_results["recommendations"]

        # ==================== DISCORD NOTIFICATION TEST ====================

        notification_result = None

        # Only attempt Discord notification if webhook payload validation was successful
        if (json_data and isinstance(json_data, dict) and
                validation_results["webhook_model_validation"].get("success", False)):

            try:
                webhook_service.logger.info("🚀 ATTEMPTING DISCORD NOTIFICATION TEST...")

                # Create WebhookPayload from validated JSON data
                webhook_payload = WebhookPayload(**json_data)

                # Process through the same pipeline as main webhook endpoint
                notification_result = await webhook_service.process_webhook(webhook_payload)

                webhook_service.logger.info(
                    f"✅ DISCORD NOTIFICATION RESULT: {notification_result.get('status', 'unknown')}")

                debug_response["discord_notification"] = {
                    "attempted": True,
                    "success": notification_result.get("status") == "success",
                    "result": notification_result
                }

                if notification_result.get("status") == "success":
                    webhook_service.logger.info(f"🎉 DISCORD NOTIFICATION SENT SUCCESSFULLY!")
                    webhook_service.logger.info(f"    Action: {notification_result.get('action', 'unknown')}")
                    webhook_service.logger.info(f"    Item: {notification_result.get('item_name', 'unknown')}")
                else:
                    webhook_service.logger.warning(
                        f"⚠️  DISCORD NOTIFICATION FAILED: {notification_result.get('message', 'unknown error')}")

            except ValidationError as ve:
                # This shouldn't happen since we already validated, but just in case
                webhook_service.logger.error("❌ DISCORD NOTIFICATION VALIDATION ERROR (unexpected)")
                debug_response["discord_notification"] = {
                    "attempted": True,
                    "success": False,
                    "error": "Webhook payload validation failed during notification attempt",
                    "validation_errors": [{"field": " → ".join(str(loc) for loc in error["loc"]),
                                           "message": error["msg"]} for error in ve.errors()]
                }

            except Exception as notification_error:
                webhook_service.logger.error(f"❌ DISCORD NOTIFICATION ERROR: {notification_error}", exc_info=True)
                debug_response["discord_notification"] = {
                    "attempted": True,
                    "success": False,
                    "error": str(notification_error),
                    "error_type": type(notification_error).__name__
                }
        else:
            # Skip notification if validation failed
            webhook_service.logger.info("⏭️  SKIPPING DISCORD NOTIFICATION (validation failed)")
            debug_response["discord_notification"] = {
                "attempted": False,
                "skipped_reason": "Webhook payload validation failed",
                "success": False
            }

        # ==================== FINAL LOGGING ====================

        webhook_service.logger.info("=" * 80)
        webhook_service.logger.info("🎯 DEBUG ANALYSIS COMPLETE")
        webhook_service.logger.info("=" * 80)

        if validation_results["recommendations"]:
            webhook_service.logger.info("💡 RECOMMENDATIONS:")
            for i, recommendation in enumerate(validation_results["recommendations"], 1):
                webhook_service.logger.info(f"    {i}. {recommendation}")
        else:
            webhook_service.logger.info("✅ NO ISSUES DETECTED - WEBHOOK LOOKS GOOD!")

        # Add Discord notification summary to recommendations
        if debug_response["discord_notification"]["attempted"]:
            if debug_response["discord_notification"]["success"]:
                debug_response["recommendations"].append("✅ Discord notification sent successfully!")
            else:
                error_msg = debug_response["discord_notification"].get("error", "Unknown error")
                debug_response["recommendations"].append(f"❌ Discord notification failed: {error_msg}")

        return debug_response

    except Exception as e:
        # Handle any unexpected errors
        webhook_service.logger.error(f"❌ DEBUG ENDPOINT ERROR: {e}", exc_info=True)

        debug_response["status"] = "error"
        debug_response["error"] = {
            "type": type(e).__name__,
            "message": str(e),
            "occurred_at": datetime.now(timezone.utc).isoformat()
        }
        debug_response["recommendations"] = [
            "Check application logs for detailed error information",
            "Verify webhook service is properly initialized",
            "Contact administrator if error persists"
        ]

        # Return error response instead of raising exception
        return debug_response

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
        curl http://jellynouncer:PORT/health
        # Returns: {"status": "healthy", "timestamp": "2024-01-01T12:00:00Z"}
        ```

        Replace PORT with your configured port (default: 8080).

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


@app.get("/validate-templates")
async def validate_templates():
    """
    Validate all Discord notification templates for JSON syntax errors.
    
    This endpoint renders each template with sample data and checks for JSON validity.
    Useful for catching template errors during development or after template modifications.
    
    Returns:
        dict: Validation results for each template with error details if any
    
    Example:
        ```bash
        curl http://jellynouncer:8080/validate-templates
        ```
    """
    if webhook_service is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready - still initializing"
        )
    
    if not webhook_service.discord_notifier:
        raise HTTPException(
            status_code=503,
            detail="Discord notifier not initialized"
        )
    
    try:
        results = await webhook_service.discord_notifier.validate_all_templates()
        
        # Count valid/invalid templates
        valid_count = sum(1 for r in results.values() if r["status"].startswith("✅"))
        total_count = len(results)
        
        return {
            "status": "success" if valid_count == total_count else "errors_found",
            "summary": {
                "total_templates": total_count,
                "valid_templates": valid_count,
                "invalid_templates": total_count - valid_count
            },
            "templates": results,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        webhook_service.logger.error(f"Template validation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Template validation failed: {str(e)}"
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

    # This allows config file settings to be used with environment variable overrides
    config = None
    try:
        config_validator = ConfigurationValidator()
        config = config_validator.load_and_validate_config()
        print(f"Configuration loaded successfully from config file")
    except Exception as e:
        print(f"Warning: Could not load configuration file, using environment variables and defaults: {e}")

    # Get configuration from config file with environment variable overrides
    if config:
        # Use config values with environment variable overrides (environment takes priority)
        host = os.getenv("HOST", config.server.host)
        port = int(os.getenv("PORT", str(config.server.port)))
        # Convert log_level to lowercase for uvicorn
        log_level = os.getenv("LOG_LEVEL", config.server.log_level).lower()
        print(f"Using server config: {host}:{port}, log_level={log_level}")
    else:
        # Fallback to environment variables with hardcoded defaults
        host = os.getenv("HOST", "0.0.0.0")  # Listen on all interfaces
        port = int(os.getenv("PORT", 8080))  # Standard webhook port
        log_level = os.getenv("LOG_LEVEL", "info").lower()
        print(f"Using fallback config: {host}:{port}, log_level={log_level}")

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