#!/usr/bin/env python3
"""
JellyNotify Discord Webhook Service - Main Entry Point

This module provides the FastAPI application entry point and coordinates all service components.
Maintains Docker compatibility by keeping the main execution logic in main.py.
"""

import os
import signal
import sys
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from webhook_service import WebhookService
from webhook_models import WebhookPayload
from utils import setup_logging


# ==================== FASTAPI LIFESPAN MANAGEMENT ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage FastAPI application lifespan with proper startup and shutdown handling.
    """
    # Startup sequence
    service = app.state.service
    try:
        # Initialize service components
        await service.initialize()

        # Start background maintenance tasks
        background_task = asyncio.create_task(service.background_tasks())
        app.state.background_task = background_task

        app.state.logger.info("FastAPI application started successfully")

        # Yield control to FastAPI for normal operation
        yield

    except Exception as e:
        app.state.logger.error(f"Application startup failed: {e}")
        raise
    finally:
        # Shutdown sequence
        try:
            app.state.logger.info("Shutting down FastAPI application...")

            # Cancel background tasks gracefully
            if hasattr(app.state, 'background_task'):
                app.state.background_task.cancel()
                try:
                    await app.state.background_task
                except asyncio.CancelledError:
                    pass  # Expected when cancelling

            # Clean up service resources
            await service.cleanup()
            app.state.logger.info("FastAPI application shutdown completed")

        except Exception as e:
            app.state.logger.error(f"Error during application shutdown: {e}")


# ==================== FASTAPI APP INITIALIZATION ====================

# Create FastAPI application with metadata and lifespan management
app = FastAPI(
    title="JellyNotify Discord Webhook Service",
    version="2.0.0",
    description="Enhanced webhook service for Jellyfin to Discord notifications",
    lifespan=lifespan
)

# Initialize service and attach to application state
service = WebhookService()
app.state.service = service
app.state.logger = service.logger


# ==================== GLOBAL EXCEPTION HANDLERS ====================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle all unhandled exceptions with comprehensive logging and safe error responses."""
    logger = app.state.logger

    # Extract request context for logging
    client_host = getattr(getattr(request, "client", None), "host", "unknown")
    client_port = getattr(getattr(request, "client", None), "port", "unknown")
    method = request.method
    url = str(request.url)

    # Log the error with full context and stack trace
    logger.error(
        f"Unhandled exception in {method} {url} from {client_host}:{client_port}: {exc}",
        exc_info=True  # Include full stack trace
    )

    # Return appropriate response based on exception type
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail}
        )
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"}
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic validation errors with detailed error information."""
    logger = app.state.logger
    client_host = getattr(getattr(request, "client", None), "host", "unknown")

    # Format validation errors for better readability
    formatted_errors = []
    for error in exc.errors():
        formatted_errors.append({
            "field": " -> ".join(str(x) for x in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
            "input": error.get("input")
        })

    logger.warning(f"Validation error from {client_host}: {formatted_errors}")

    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation failed",
            "details": formatted_errors
        }
    )


# ==================== API ENDPOINTS ====================

@app.post("/webhook")
async def webhook_endpoint(payload: WebhookPayload, request: Request) -> Dict[str, Any]:
    """Main webhook endpoint for receiving notifications from Jellyfin."""
    client_host = getattr(getattr(request, "client", None), "host", "unknown")
    app.state.logger.debug(f"Webhook received from {client_host} for item: {payload.ItemId}")

    try:
        result = await app.state.service.process_webhook(payload)
        return result
    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        app.state.logger.error(f"Webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")


@app.post("/webhook/debug")
async def webhook_debug_endpoint(request: Request) -> Dict[str, Any]:
    """Enhanced debug webhook endpoint for troubleshooting webhook configuration issues."""
    # Import here to avoid circular imports
    import json
    from pydantic import ValidationError

    logger = app.state.logger
    client_info = getattr(request, "client", None)
    client_host = getattr(client_info, "host", "unknown") if client_info else "unknown"
    client_port = getattr(client_info, "port", "unknown") if client_info else "unknown"

    try:
        # Get raw request data for analysis
        raw_body = await request.body()
        content_type = request.headers.get("content-type", "")
        user_agent = request.headers.get("user-agent", "")

        # Log comprehensive request details
        logger.info("=" * 80)
        logger.info("🔍 ENHANCED DEBUG WEBHOOK REQUEST RECEIVED")
        logger.info("=" * 80)
        logger.info(f"📡 Client: {client_host}:{client_port}")
        logger.info(f"🌐 Method: {request.method}")
        logger.info(f"📍 URL: {request.url}")
        logger.info(f"📋 Content-Type: {content_type}")
        logger.info(f"🤖 User-Agent: {user_agent}")
        logger.info(f"📏 Body Length: {len(raw_body)} bytes")

        # Log all headers
        logger.info("📨 REQUEST HEADERS:")
        for header_name, header_value in request.headers.items():
            if header_name.lower() in ['authorization', 'x-api-key', 'x-jellyfin-token']:
                masked_value = header_value[:8] + "***" if len(header_value) > 8 else "***"
                logger.info(f"    {header_name}: {masked_value}")
            else:
                logger.info(f"    {header_name}: {header_value}")

        # Log query parameters if any
        if request.query_params:
            logger.info("🔗 QUERY PARAMETERS:")
            for param_name, param_value in request.query_params.items():
                logger.info(f"    {param_name}: {param_value}")

        # Log raw body content (first 1000 chars for safety)
        logger.info("📦 RAW BODY CONTENT:")
        try:
            body_text = raw_body.decode('utf-8', errors='replace')
            if len(body_text) > 1000:
                logger.info(f"    {body_text[:1000]}... (truncated, total length: {len(body_text)})")
            else:
                logger.info(f"    {body_text}")
        except Exception as decode_error:
            logger.error(f"    Failed to decode body as UTF-8: {decode_error}")
            logger.info(f"    Raw bytes (first 200): {raw_body[:200]}")

        # JSON Parsing
        json_data = None
        json_parse_error = None

        try:
            json_data = json.loads(raw_body)
            logger.info("✅ JSON PARSING SUCCESSFUL")
            logger.info(f"📊 JSON Structure:")
            logger.info(
                f"    Top-level keys: {list(json_data.keys()) if isinstance(json_data, dict) else 'Not a dictionary'}")
            logger.info(f"    Total keys: {len(json_data) if isinstance(json_data, dict) else 'N/A'}")

            # Log each field in detail
            if isinstance(json_data, dict):
                logger.info("🔍 DETAILED FIELD ANALYSIS:")
                for key, value in json_data.items():
                    value_type = type(value).__name__
                    if value is None:
                        value_str = "null"
                    elif isinstance(value, str):
                        value_str = f'"{value}"' if len(str(value)) <= 100 else f'"{str(value)[:100]}..." (truncated)'
                    else:
                        value_str = str(value) if len(str(value)) <= 100 else f"{str(value)[:100]}... (truncated)"

                    logger.info(f"    {key} ({value_type}): {value_str}")

        except json.JSONDecodeError as e:
            json_parse_error = e
            logger.error("❌ JSON PARSING FAILED")
            logger.error(f"    Error: {e}")
            logger.error(f"    Error position: line {e.lineno}, column {e.colno}")
            logger.error(f"    Error message: {e.msg}")

            return {
                "status": "json_parse_error",
                "request_details": {
                    "client": f"{client_host}:{client_port}",
                    "method": request.method,
                    "url": str(request.url),
                    "content_type": content_type,
                    "user_agent": user_agent,
                    "body_length": len(raw_body),
                    "headers": dict(request.headers)
                },
                "error": "Invalid JSON",
                "json_error": {
                    "message": str(e),
                    "line": e.lineno,
                    "column": e.colno,
                    "error_type": e.msg
                },
                "raw_body_preview": raw_body.decode('utf-8', errors='replace')[:500]
            }

        # Pydantic Validation
        validation_success = False
        validation_errors = []
        payload = None

        try:
            payload = WebhookPayload(**json_data)
            validation_success = True

            logger.info("✅ PYDANTIC VALIDATION SUCCESSFUL")
            logger.info(f"📋 Validated Payload Details:")
            logger.info(f"    ItemId: {payload.ItemId}")
            logger.info(f"    Name: {payload.Name}")
            logger.info(f"    ItemType: {payload.ItemType}")

            # Process normally if validation passes
            logger.info("🚀 PROCEEDING WITH NORMAL WEBHOOK PROCESSING")
            result = await app.state.service.process_webhook(payload)

            logger.info("✅ WEBHOOK PROCESSING COMPLETED SUCCESSFULLY")
            logger.info(f"    Processing result: {result}")
            logger.info("=" * 80)

            return {
                "status": "success",
                "validation": "passed",
                "request_details": {
                    "client": f"{client_host}:{client_port}",
                    "method": request.method,
                    "url": str(request.url),
                    "content_type": content_type,
                    "user_agent": user_agent,
                    "body_length": len(raw_body),
                    "headers": dict(request.headers)
                },
                "parsed_payload": {
                    "ItemId": payload.ItemId,
                    "Name": payload.Name,
                    "ItemType": payload.ItemType,
                    "Year": payload.Year,
                    "SeriesName": payload.SeriesName,
                },
                "processing_result": result
            }

        except ValidationError as e:
            logger.error("❌ PYDANTIC VALIDATION FAILED")
            logger.error(f"    Validation errors: {len(e.errors())}")

            for i, error in enumerate(e.errors(), 1):
                field_path = " -> ".join(str(x) for x in error['loc'])
                logger.error(f"    Error {i}:")
                logger.error(f"        Field: {field_path}")
                logger.error(f"        Error: {error['msg']}")
                logger.error(f"        Error Type: {error['type']}")

                validation_errors.append({
                    "field": field_path,
                    "error": error['msg'],
                    "type": error['type'],
                    "input": error.get('input')
                })

        # Field Analysis
        expected_fields = set(WebhookPayload.model_fields.keys())
        received_fields = set(json_data.keys()) if isinstance(json_data, dict) else set()

        logger.info("🔬 COMPREHENSIVE FIELD ANALYSIS:")
        logger.info(f"    Expected fields: {len(expected_fields)}")
        logger.info(f"    Received fields: {len(received_fields)}")
        logger.info(f"    Missing fields: {expected_fields - received_fields}")
        logger.info(f"    Extra fields: {received_fields - expected_fields}")

        logger.info("=" * 80)

        return {
            "status": "validation_failed" if not validation_success else "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_details": {
                "client": f"{client_host}:{client_port}",
                "method": request.method,
                "url": str(request.url),
                "content_type": content_type,
                "user_agent": user_agent,
                "body_length": len(raw_body),
                "headers": dict(request.headers),
                "query_params": dict(request.query_params)
            },
            "json_parsing": {
                "success": json_parse_error is None,
                "error": str(json_parse_error) if json_parse_error else None
            },
            "validation": {
                "success": validation_success,
                "error_count": len(validation_errors),
                "errors": validation_errors
            },
            "field_analysis": {
                "expected_field_count": len(expected_fields),
                "received_field_count": len(received_fields),
                "missing_fields": list(expected_fields - received_fields),
                "extra_fields": list(received_fields - expected_fields),
            },
            "raw_data": {
                "json_payload": json_data,
                "raw_body_preview": raw_body.decode('utf-8', errors='replace')[:1000]
            }
        }

    except Exception as e:
        logger.error("💥 CRITICAL ERROR IN DEBUG WEBHOOK")
        logger.error(f"    Exception type: {type(e).__name__}")
        logger.error(f"    Exception message: {str(e)}")
        logger.error("    Full traceback:", exc_info=True)
        logger.info("=" * 80)

        return {
            "status": "critical_error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": {
                "type": type(e).__name__,
                "message": str(e)
            },
            "request_details": {
                "client": f"{client_host}:{client_port}",
                "method": getattr(request, 'method', 'unknown'),
                "url": str(getattr(request, 'url', 'unknown'))
            }
        }


@app.get("/health")
async def health_endpoint() -> Dict[str, Any]:
    """Comprehensive health check endpoint for monitoring service status."""
    return await app.state.service.health_check()


@app.post("/sync")
async def sync_endpoint() -> Dict[str, Any]:
    """Manually trigger Jellyfin library synchronization."""
    return await app.state.service.manual_sync()


@app.get("/stats")
async def stats_endpoint() -> Dict[str, Any]:
    """Get comprehensive service statistics for monitoring and debugging."""
    return await app.state.service.get_service_stats()


@app.get("/webhooks")
async def webhooks_endpoint() -> Dict[str, Any]:
    """Get Discord webhook configuration and status information."""
    return app.state.service.discord.get_webhook_status()


@app.post("/test-webhook")
async def test_webhook_endpoint(webhook_name: str = "default") -> Dict[str, Any]:
    """Test a specific Discord webhook by sending a test notification."""
    try:
        # Import here to avoid circular imports
        import time
        from media_models import MediaItem

        # Create a test media item
        test_item = MediaItem(
            item_id="test-item-" + str(int(time.time())),
            name="Test Movie",
            item_type="Movie"
        )

        webhook_info = app.state.service.discord._get_webhook_for_item(test_item)

        if not webhook_info:
            return {
                "status": "error",
                "message": "No webhook available for testing"
            }

        # Create test notification payload
        test_payload = {
            "embeds": [{
                "title": "🧪 Webhook Test",
                "description": f"Test notification from {webhook_info['config'].name} webhook",
                "color": 65280,  # Green
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {
                    "text": "JellyNotify Test",
                    "icon_url": app.state.service.config.jellyfin.server_url + "/web/favicon.ico"
                }
            }]
        }

        webhook_url = webhook_info['config'].url

        # Send test notification
        async with app.state.service.discord.session.post(webhook_url, json=test_payload) as response:
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
        app.state.logger.error(f"Webhook test error: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


@app.get("/config")
async def config_endpoint() -> Dict[str, Any]:
    """Get sanitized configuration information for debugging and verification."""
    try:
        config = app.state.service.config

        # Return sanitized configuration (remove sensitive data)
        sanitized_config = {
            "jellyfin": {
                "server_url": config.jellyfin.server_url,
                "client_name": config.jellyfin.client_name,
                "client_version": config.jellyfin.client_version
            },
            "database": {
                "path": config.database.path,
                "wal_mode": config.database.wal_mode,
                "vacuum_interval_hours": config.database.vacuum_interval_hours
            },
            "discord": {
                "routing": config.discord.routing,
                "rate_limit": config.discord.rate_limit,
                "webhooks": {
                    name: {
                        "name": webhook.name,
                        "enabled": webhook.enabled,
                        "has_url": bool(webhook.url),
                        "grouping": webhook.grouping
                    }
                    for name, webhook in config.discord.webhooks.items()
                }
            },
            "templates": {
                "directory": config.templates.directory
            },
            "notifications": config.notifications.model_dump(),
            "server": config.server.model_dump(),
            "sync": config.sync.model_dump()
        }

        return sanitized_config

    except Exception as e:
        app.state.logger.error(f"Config endpoint error: {e}")
        return {"error": str(e)}


# ==================== MAIN ENTRY POINT ====================

if __name__ == "__main__":
    """Main entry point for running the JellyNotify service."""


    def signal_handler(signum, frame):
        """Handle shutdown signals gracefully."""
        logger = service.logger
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        sys.exit(0)


    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination request

    # Run the service with Uvicorn
    try:
        uvicorn.run(
            "main:app",  # Application module and variable
            host=service.config.server.host,  # Bind address
            port=service.config.server.port,  # Port number
            log_level=service.config.server.log_level.lower(),  # Logging level
            reload=False,  # Disable auto-reload in production
            access_log=False,  # We handle our own access logging
            server_header=False,  # Don't expose server type
            date_header=False  # Don't expose server date
        )
    except Exception as e:
        service.logger.error(f"Failed to start server: {e}")
        sys.exit(1)