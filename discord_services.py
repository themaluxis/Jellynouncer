#!/usr/bin/env python3
"""
Debug Enhanced Discord Services Module

This module contains debug-enhanced versions of the original Discord services
with comprehensive debug logging when DEBUG environment variable is set to true.
All original function and class names are preserved.
"""

import asyncio
import json
import time
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse
from dataclasses import asdict

import aiohttp
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError

from config_models import DiscordConfig, TemplatesConfig
from media_models import MediaItem


def _debug_log(message: str, data: Any = None, component: str = "Discord"):
    """Global debug logging function when DEBUG=true."""
    if os.getenv('DEBUG', 'false').lower() == 'true':
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [DEBUG] [{component}] {message}")
        if data is not None:
            print(f"[{timestamp}] [DEBUG] [{component}] Data: {json.dumps(data, indent=2, default=str)}")


class ThumbnailManager:
    """
    Enhanced thumbnail manager with debug logging capabilities.

    Original class with added debug functionality when DEBUG=true.
    """

    def __init__(self, jellyfin_url: str, session: aiohttp.ClientSession, logger: logging.Logger):
        """
        Initialize thumbnail manager.

        Args:
            jellyfin_url: Base Jellyfin server URL
            session: HTTP session for verification requests
            logger: Logger instance
        """
        self.jellyfin_url = jellyfin_url.rstrip('/')
        self.session = session
        self.logger = logger

        # Cache for URL verification results (URL -> (is_valid, timestamp))
        self.verification_cache = {}
        self.cache_duration = 300  # 5 minutes cache

        # Debug flag from environment
        self.debug_enabled = os.getenv('DEBUG', 'false').lower() == 'true'

    async def get_verified_thumbnail_url(self, item: MediaItem) -> Optional[str]:
        """
        Get verified thumbnail URL for a media item with fallback strategy.
        Enhanced with debug logging when DEBUG=true.
        """
        if self.debug_enabled:
            _debug_log("Starting thumbnail URL resolution", {
                "item_id": item.item_id,
                "item_name": item.name,
                "item_type": item.item_type,
                "series_id": getattr(item, 'series_id', None),
                "parent_id": getattr(item, 'parent_id', None)
            }, "ThumbnailManager")

        # Define fallback URLs based on item type
        fallback_urls = []

        if item.item_type == "Episode":
            # Episode → Season → Series → Default
            if item.item_id:
                fallback_urls.append(f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary")
            if getattr(item, 'parent_id', None):
                fallback_urls.append(f"{self.jellyfin_url}/Items/{item.parent_id}/Images/Primary")
            if getattr(item, 'series_id', None):
                fallback_urls.append(f"{self.jellyfin_url}/Items/{item.series_id}/Images/Primary")
        elif item.item_type == "Series":
            # Series → Default
            if item.item_id:
                fallback_urls.append(f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary")
        elif item.item_type == "Movie":
            # Movie → Default
            if item.item_id:
                fallback_urls.append(f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary")
        else:
            # Generic item → Default
            if item.item_id:
                fallback_urls.append(f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary")

        if self.debug_enabled:
            _debug_log("Generated fallback URLs", {
                "url_count": len(fallback_urls),
                "urls": fallback_urls
            }, "ThumbnailManager")

        # Try each URL in order
        for i, url in enumerate(fallback_urls):
            if self.debug_enabled:
                _debug_log(f"Attempting URL {i + 1}/{len(fallback_urls)}", {
                    "url": url,
                    "attempt_number": i + 1
                }, "ThumbnailManager")

            is_valid = await self._verify_thumbnail_url(url)

            if is_valid:
                if self.debug_enabled:
                    _debug_log("✅ Thumbnail URL validated successfully", {
                        "final_url": url,
                        "attempt_number": i + 1,
                        "total_attempts": len(fallback_urls)
                    }, "ThumbnailManager")
                return url
            else:
                if self.debug_enabled:
                    _debug_log(f"❌ Thumbnail URL validation failed for attempt {i + 1}", {
                        "failed_url": url,
                        "remaining_attempts": len(fallback_urls) - (i + 1)
                    }, "ThumbnailManager")

        if self.debug_enabled:
            _debug_log("❌ All thumbnail URLs failed validation", {
                "total_attempts": len(fallback_urls),
                "item_id": item.item_id,
                "item_name": item.name
            }, "ThumbnailManager")
        return None

    async def _verify_thumbnail_url(self, url: str) -> bool:
        """
        Verify if a thumbnail URL is accessible and returns a valid image.
        Enhanced with debug logging when DEBUG=true.

        Args:
            url: Thumbnail URL to verify

        Returns:
            True if URL is valid and accessible, False otherwise
        """
        if self.debug_enabled:
            _debug_log("Starting thumbnail URL verification", {"url": url}, "ThumbnailManager")

        # Check cache first
        current_time = time.time()
        if url in self.verification_cache:
            is_valid, timestamp = self.verification_cache[url]
            if current_time - timestamp < self.cache_duration:
                if self.debug_enabled:
                    _debug_log("Using cached verification result", {
                        "url": url,
                        "cached_result": is_valid,
                        "cache_age_seconds": current_time - timestamp
                    }, "ThumbnailManager")
                return is_valid

        try:
            # Make HEAD request to check if image exists without downloading full content
            if self.debug_enabled:
                _debug_log("Making HEAD request to verify thumbnail", {"url": url}, "ThumbnailManager")

            async with self.session.head(url, timeout=5) as response:
                content_type = response.headers.get('content-type', '')
                is_valid = (
                        response.status == 200 and
                        content_type.startswith('image/')
                )

                # Cache the result
                self.verification_cache[url] = (is_valid, current_time)

                if self.debug_enabled:
                    verification_result = {
                        "url": url,
                        "status_code": response.status,
                        "content_type": content_type,
                        "is_valid": is_valid,
                        "response_headers": dict(response.headers)
                    }

                    if is_valid:
                        _debug_log("✅ Thumbnail URL verification successful", verification_result, "ThumbnailManager")
                    else:
                        _debug_log("❌ Thumbnail URL verification failed", verification_result, "ThumbnailManager")

                return is_valid

        except asyncio.TimeoutError:
            if self.debug_enabled:
                _debug_log("❌ Thumbnail URL verification timeout", {
                    "url": url,
                    "timeout_seconds": 5
                }, "ThumbnailManager")
            # Cache negative result for failed verifications
            self.verification_cache[url] = (False, current_time)
            return False
        except Exception as e:
            if self.debug_enabled:
                _debug_log("❌ Thumbnail URL verification error", {
                    "url": url,
                    "error": str(e),
                    "error_type": type(e).__name__
                }, "ThumbnailManager")
            # Cache negative result for failed verifications
            self.verification_cache[url] = (False, current_time)
            return False

    def clear_cache(self):
        """Clear the verification cache with debug logging."""
        if self.debug_enabled:
            cache_size = len(self.verification_cache)
            _debug_log("Thumbnail verification cache cleared", {
                "previous_cache_size": cache_size
            }, "ThumbnailManager")

        self.verification_cache.clear()
        self.logger.debug("Thumbnail verification cache cleared")


class DiscordNotifier:
    """
    Enhanced Discord webhook notifier with debug logging capabilities.

    Original class with added debug functionality when DEBUG=true.
    """

    def __init__(self, config: DiscordConfig, jellyfin_url: str, logger: logging.Logger):
        """
        Initialize Discord notifier with configuration and logging.

        Args:
            config: Discord configuration including webhooks and routing
            jellyfin_url: Base URL of Jellyfin server for generating links
            logger: Logger instance for notification operations
        """
        self.config = config
        self.jellyfin_url = jellyfin_url
        self.logger = logger
        self.routing_enabled = config.routing.get('enabled', False)
        self.webhooks = config.webhooks
        self.routing_config = config.routing
        self.rate_limit = config.rate_limit

        # Rate limiting state tracking per webhook
        self.webhook_rate_limits = {}
        self.session = None

        # Jinja2 template environment (initialized later)
        self.template_env = None

        # Reference to webhook service for cross-component access
        self._webhook_service = None

        # Debug flag from environment
        self.debug_enabled = os.getenv('DEBUG', 'false').lower() == 'true'

        if self.debug_enabled:
            _debug_log("Discord notifier initialized", {
                "jellyfin_url": jellyfin_url,
                "routing_enabled": self.routing_enabled,
                "webhook_count": len(self.webhooks),
                "webhook_names": list(self.webhooks.keys())
            }, "DiscordNotifier")

    async def initialize(self, templates_config: TemplatesConfig, session: aiohttp.ClientSession) -> None:
        """Initialize HTTP session, Jinja2 templates, and thumbnail manager with debug logging."""
        if self.debug_enabled:
            _debug_log("Initializing Discord notifier components", {
                "templates_directory": templates_config.directory,
                "session_provided": session is not None
            }, "DiscordNotifier")

        # Store the provided HTTP session
        self.session = session

        # Initialize thumbnail manager
        self.thumbnail_manager = ThumbnailManager(self.jellyfin_url, self.session, self.logger)

        # Initialize Jinja2 environment
        try:
            self.template_env = Environment(
                loader=FileSystemLoader(templates_config.directory),
                auto_reload=True  # Enable auto-reload for development
            )
            if self.debug_enabled:
                _debug_log("✅ Jinja2 template environment initialized successfully", {
                    "template_directory": templates_config.directory,
                    "auto_reload": True
                }, "DiscordNotifier")
        except Exception as e:
            error_info = {
                "error": str(e),
                "error_type": type(e).__name__,
                "template_directory": templates_config.directory
            }
            if self.debug_enabled:
                _debug_log("❌ Failed to initialize Jinja2 template environment", error_info, "DiscordNotifier")
            self.logger.error(f"Failed to initialize template environment: {e}")
            raise

    async def send_notification(self, item: MediaItem, changes: Optional[List[Dict[str, Any]]] = None,
                                is_new: bool = True) -> bool:
        """Enhanced notification sending with debug logging for all webhook data."""
        if self.debug_enabled:
            _debug_log("Starting notification send process", {
                "item_id": item.item_id,
                "item_name": item.name,
                "item_type": item.item_type,
                "is_new": is_new,
                "changes_count": len(changes) if changes else 0,
                "changes": changes if changes else []
            }, "DiscordNotifier")

        try:
            # Find webhook
            webhook_info = self._get_webhook_for_item(item)
            if not webhook_info:
                if self.debug_enabled:
                    _debug_log("❌ No suitable Discord webhook found", {
                        "item_type": item.item_type,
                        "available_webhooks": list(self.webhooks.keys()),
                        "routing_enabled": self.routing_enabled
                    }, "DiscordNotifier")
                self.logger.warning("No suitable Discord webhook found")
                return False

            webhook_name = webhook_info['name']
            webhook_config = webhook_info['config']
            webhook_url = webhook_config.url

            if self.debug_enabled:
                # Sanitize webhook URL for logging (hide token)
                parsed_url = urlparse(webhook_url)
                safe_webhook_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path[:-20]}...HIDDEN"

                _debug_log("Selected webhook for notification", {
                    "webhook_name": webhook_name,
                    "webhook_url": safe_webhook_url,
                    "webhook_enabled": webhook_config.enabled,
                    "webhook_grouping": webhook_config.grouping
                }, "DiscordNotifier")

            # Rate limiting
            await self._wait_for_rate_limit(webhook_name)

            # Get verified thumbnail URL
            thumbnail_url = await self.thumbnail_manager.get_verified_thumbnail_url(item)

            if self.debug_enabled:
                _debug_log("Thumbnail resolution completed", {
                    "thumbnail_url": thumbnail_url,
                    "has_thumbnail": bool(thumbnail_url)
                }, "DiscordNotifier")

            # Template selection
            if is_new:
                template_name = 'new_item.j2'
                color = 0x00FF00
            else:
                template_name = 'upgraded_item.j2'
                color = self._get_change_color(changes)

            if self.debug_enabled:
                _debug_log("Template and color selection", {
                    "template_name": template_name,
                    "color_hex": f"0x{color:06X}",
                    "color_decimal": color
                }, "DiscordNotifier")

            # Load template
            try:
                template = self.template_env.get_template(template_name)
                if self.debug_enabled:
                    _debug_log("✅ Template loaded successfully", {
                        "template_name": template_name
                    }, "DiscordNotifier")
            except (TemplateNotFound, TemplateSyntaxError) as e:
                if self.debug_enabled:
                    _debug_log("❌ Template loading failed", {
                        "template_name": template_name,
                        "error": str(e),
                        "error_type": type(e).__name__
                    }, "DiscordNotifier")
                self.logger.error(f"Template error {template_name}: {e}")
                return False

            # Get ratings with TVDB URL information
            ratings = {}
            if self._webhook_service and self._webhook_service.rating_service:
                try:
                    ratings = await self._webhook_service.rating_service.get_ratings_for_item(item)
                    if self.debug_enabled:
                        _debug_log("Ratings retrieved", {
                            "rating_count": len(ratings) if ratings else 0,
                            "rating_sources": list(ratings.keys()) if ratings else []
                        }, "DiscordNotifier")
                except Exception as e:
                    if self.debug_enabled:
                        _debug_log("❌ Failed to fetch ratings", {
                            "error": str(e),
                            "error_type": type(e).__name__
                        }, "DiscordNotifier")
                    self.logger.warning(f"Failed to fetch ratings for {item.name}: {e}")

            # Extract TVDB URL information
            tvdb_url_info = {}
            if 'tvdb' in ratings and 'proper_url' in ratings['tvdb']:
                tvdb_url_info = {
                    'url': ratings['tvdb']['proper_url'],
                    'display_text': ratings['tvdb'].get('display_text', 'TVDB')
                }
                if self.debug_enabled:
                    _debug_log("TVDB URL information extracted", tvdb_url_info, "DiscordNotifier")

            # Render template with all available data
            template_data = {
                'item': item,
                'changes': changes or [],
                'is_new': is_new,
                'color': color,
                'thumbnail_url': thumbnail_url,
                'jellyfin_url': self.jellyfin_url,
                'ratings': ratings,
                'tvdb_url_info': tvdb_url_info,
                'timestamp': datetime.now(timezone.utc).isoformat()  # Add missing timestamp
            }

            if self.debug_enabled:
                # Create a safe version of template data for logging
                safe_template_data = template_data.copy()
                safe_template_data['item'] = asdict(item) if hasattr(item, '__dict__') else str(item)
                _debug_log("Template rendering data prepared", {
                    "template_data_keys": list(template_data.keys()),
                    "item_fields": list(safe_template_data['item'].keys()) if isinstance(safe_template_data['item'],
                                                                                         dict) else "N/A",
                    "changes_count": len(changes) if changes else 0,
                    "ratings_available": bool(ratings),
                    "thumbnail_available": bool(thumbnail_url)
                }, "DiscordNotifier")

            try:
                rendered = template.render(**template_data)
                payload = json.loads(rendered)

                if self.debug_enabled:
                    _debug_log("✅ Template rendered successfully", {
                        "payload_keys": list(payload.keys()),
                        "payload_size_bytes": len(rendered),
                        "embed_count": len(payload.get('embeds', [])) if payload.get('embeds') else 0
                    }, "DiscordNotifier")

                    # Log the complete JSON payload being sent to Discord
                    _debug_log("📤 DISCORD WEBHOOK JSON PAYLOAD", payload, "DiscordNotifier")

            except Exception as e:
                if self.debug_enabled:
                    _debug_log("❌ Template rendering failed", {
                        "template_name": template_name,
                        "error": str(e),
                        "error_type": type(e).__name__
                    }, "DiscordNotifier")
                self.logger.error(f"Template rendering error: {e}")
                return False

            # Send to Discord with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    if self.debug_enabled:
                        _debug_log(f"Sending Discord webhook request (attempt {attempt + 1}/{max_retries})", {
                            "webhook_name": webhook_name,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "payload_size": len(json.dumps(payload))
                        }, "DiscordNotifier")

                    async with self.session.post(webhook_url, json=payload) as response:
                        if self.debug_enabled:
                            response_headers = dict(response.headers)
                            _debug_log("Discord API response received", {
                                "status_code": response.status,
                                "response_headers": response_headers,
                                "content_length": response_headers.get('content-length', 'N/A')
                            }, "DiscordNotifier")

                        if response.status == 204:
                            if self.debug_enabled:
                                _debug_log("✅ Discord notification sent successfully", {
                                    "webhook_name": webhook_name,
                                    "item_name": item.name,
                                    "attempt": attempt + 1,
                                    "has_thumbnail": bool(thumbnail_url)
                                }, "DiscordNotifier")

                            self.logger.info(
                                f"Successfully sent notification for {item.name} to '{webhook_name}' webhook"
                                f"{' with thumbnail' if thumbnail_url else ' (no thumbnail)'}")
                            return True

                        elif response.status == 429:
                            retry_after = int(response.headers.get('Retry-After', '60'))
                            if self.debug_enabled:
                                _debug_log("⏳ Discord rate limit encountered", {
                                    "retry_after_seconds": retry_after,
                                    "attempt": attempt + 1
                                }, "DiscordNotifier")
                            self.logger.warning(f"Rate limited, retry after {retry_after}s")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            error_text = await response.text()
                            if self.debug_enabled:
                                _debug_log("❌ Discord API error response", {
                                    "status_code": response.status,
                                    "error_text": error_text,
                                    "attempt": attempt + 1,
                                    "will_retry": attempt < max_retries - 1
                                }, "DiscordNotifier")
                            self.logger.error(f"Discord error {response.status}: {error_text}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2 ** attempt)
                                continue
                            return False

                except aiohttp.ClientError as e:
                    if self.debug_enabled:
                        _debug_log("❌ Network error during Discord request", {
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "attempt": attempt + 1,
                            "will_retry": attempt < max_retries - 1
                        }, "DiscordNotifier")
                    self.logger.error(f"Network error: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return False

            return False

        except Exception as e:
            if self.debug_enabled:
                _debug_log("❌ Critical error in send_notification", {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "item_id": item.item_id,
                    "item_name": item.name
                }, "DiscordNotifier")
            self.logger.error(f"Critical error in send_notification: {e}")
            return False

    async def _wait_for_rate_limit(self, webhook_name: str) -> None:
        """
        Wait if necessary to respect Discord's rate limits.
        Enhanced with debug logging when DEBUG=true.
        """
        if self.debug_enabled:
            _debug_log("Checking rate limits", {
                "webhook_name": webhook_name,
                "rate_limit_config": self.rate_limit
            }, "DiscordNotifier")

        # Initialize rate limit tracking for this webhook
        if webhook_name not in self.webhook_rate_limits:
            self.webhook_rate_limits[webhook_name] = {
                'last_request_time': 0,
                'request_count': 0
            }

        rate_limit_info = self.webhook_rate_limits[webhook_name]
        current_time = time.time()

        # Reset counter if enough time has passed
        period_seconds = self.rate_limit.get('period_seconds', 2)
        if current_time - rate_limit_info['last_request_time'] >= period_seconds:
            rate_limit_info['request_count'] = 0

        # Check if we need to wait to avoid rate limiting
        max_requests = self.rate_limit.get('requests_per_period', 5)
        if rate_limit_info['request_count'] >= max_requests:
            wait_time = period_seconds - (current_time - rate_limit_info['last_request_time'])
            if wait_time > 0:
                if self.debug_enabled:
                    _debug_log("⏳ Rate limiting active - waiting before request", {
                        "webhook_name": webhook_name,
                        "wait_time_seconds": wait_time,
                        "current_request_count": rate_limit_info['request_count'],
                        "max_requests_per_period": max_requests
                    }, "DiscordNotifier")
                self.logger.debug(f"Rate limiting webhook '{webhook_name}', waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                rate_limit_info['request_count'] = 0

        # Update rate limit tracking
        rate_limit_info['last_request_time'] = time.time()
        rate_limit_info['request_count'] += 1

        if self.debug_enabled:
            _debug_log("Rate limit updated", {
                "webhook_name": webhook_name,
                "new_request_count": rate_limit_info['request_count'],
                "last_request_time": rate_limit_info['last_request_time']
            }, "DiscordNotifier")

    def _get_webhook_for_item(self, item: MediaItem) -> Optional[Dict[str, Any]]:
        """
        Get the appropriate webhook configuration for a media item.
        Enhanced with debug logging when DEBUG=true.
        """
        if self.debug_enabled:
            _debug_log("Finding webhook for item", {
                "item_type": item.item_type,
                "routing_enabled": self.routing_enabled,
                "available_webhooks": list(self.webhooks.keys())
            }, "DiscordNotifier")

        # If routing is disabled, use default webhook
        if not self.routing_enabled:
            default_webhook = self.webhooks.get('default')
            if default_webhook and default_webhook.enabled:
                result = {'name': 'default', 'config': default_webhook}
                if self.debug_enabled:
                    _debug_log("Using default webhook (routing disabled)", {
                        "webhook_name": "default",
                        "webhook_enabled": default_webhook.enabled
                    }, "DiscordNotifier")
                return result

        # Try routing based on item type
        routing_map = {
            'Movie': 'movies',
            'Episode': 'tv',
            'Series': 'tv',
            'Audio': 'music',
            'MusicAlbum': 'music'
        }

        webhook_key = routing_map.get(item.item_type)
        if webhook_key:
            webhook_config = self.webhooks.get(webhook_key)
            if webhook_config and webhook_config.enabled:
                result = {'name': webhook_key, 'config': webhook_config}
                if self.debug_enabled:
                    _debug_log("Found specific webhook via routing", {
                        "item_type": item.item_type,
                        "webhook_name": webhook_key,
                        "webhook_enabled": webhook_config.enabled
                    }, "DiscordNotifier")
                return result

        # Fallback to default webhook
        default_webhook = self.webhooks.get('default')
        if default_webhook and default_webhook.enabled:
            result = {'name': 'default', 'config': default_webhook}
            if self.debug_enabled:
                _debug_log("Using default webhook as fallback", {
                    "item_type": item.item_type,
                    "webhook_name": "default",
                    "reason": "no specific webhook found or enabled"
                }, "DiscordNotifier")
            return result

        if self.debug_enabled:
            _debug_log("❌ No suitable webhook found", {
                "item_type": item.item_type,
                "checked_webhooks": [webhook_key, 'default'] if webhook_key else ['default'],
                "available_webhooks": list(self.webhooks.keys())
            }, "DiscordNotifier")

        return None

    def _get_change_color(self, changes: List[Dict[str, Any]]) -> int:
        """
        Determine Discord embed color based on the types of changes detected.
        Enhanced with debug logging when DEBUG=true.
        """
        if self.debug_enabled:
            _debug_log("Determining change color", {
                "changes_count": len(changes) if changes else 0,
                "change_types": [change.get('type') for change in changes] if changes else []
            }, "DiscordNotifier")

        try:
            colors = self.config.notifications.colors

            if not changes:
                color = colors.get('new_item', 0x00FF00)
                if self.debug_enabled:
                    _debug_log("Using new_item color (no changes)", {
                        "color_hex": f"0x{color:06X}",
                        "color_decimal": color
                    }, "DiscordNotifier")
                return color

            # Extract change types for priority determination
            change_types = [change['type'] for change in changes if isinstance(change, dict) and 'type' in change]

            # Priority order: resolution > codec > HDR > audio > provider IDs
            if 'resolution' in change_types:
                color = colors.get('resolution_upgrade', 0xFFD700)  # Gold
                color_type = "resolution_upgrade"
            elif 'codec' in change_types:
                color = colors.get('codec_upgrade', 0xFF8C00)  # Dark orange
                color_type = "codec_upgrade"
            elif 'hdr_status' in change_types:
                color = colors.get('hdr_upgrade', 0xFF1493)  # Deep pink
                color_type = "hdr_upgrade"
            elif any(t in change_types for t in ['audio_codec', 'audio_channels']):
                color = colors.get('audio_upgrade', 0x9370DB)  # Medium purple
                color_type = "audio_upgrade"
            elif 'provider_ids' in change_types:
                color = colors.get('provider_update', 0x1E90FF)  # Dodger blue
                color_type = "provider_update"
            else:
                color = colors.get('new_item', 0x00FF00)  # Green fallback
                color_type = "new_item (fallback)"

            if self.debug_enabled:
                _debug_log("Color determined based on change priority", {
                    "color_type": color_type,
                    "color_hex": f"0x{color:06X}",
                    "color_decimal": color,
                    "matching_change_types": change_types
                }, "DiscordNotifier")

            return color

        except Exception as e:
            if self.debug_enabled:
                _debug_log("❌ Error determining change color, using fallback", {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "fallback_color": "0x00FF00"
                }, "DiscordNotifier")
            self.logger.error(f"Error determining change color: {e}")
            return 0x00FF00  # Default to green

    async def send_server_status(self, is_online: bool) -> bool:
        """
        Send server status notification to all enabled webhooks.
        Enhanced with debug logging when DEBUG=true.
        """
        if self.debug_enabled:
            _debug_log("Sending server status notification", {
                "server_online": is_online,
                "enabled_webhooks": [name for name, config in self.webhooks.items() if config.enabled]
            }, "DiscordNotifier")

        success_count = 0
        total_webhooks = 0

        for webhook_name, webhook_config in self.webhooks.items():
            if not webhook_config.enabled:
                if self.debug_enabled:
                    _debug_log(f"Skipping disabled webhook: {webhook_name}", {
                        "webhook_name": webhook_name,
                        "enabled": False
                    }, "DiscordNotifier")
                continue

            total_webhooks += 1

            try:
                status_text = "🟢 Online" if is_online else "🔴 Offline"
                color = 0x00FF00 if is_online else 0xFF0000

                payload = {
                    "embeds": [{
                        "title": f"Jellyfin Server Status: {status_text}",
                        "description": f"Server is now {'online' if is_online else 'offline'}",
                        "color": color,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "footer": {
                            "text": "JellyNotify Server Monitor"
                        }
                    }]
                }

                if self.debug_enabled:
                    _debug_log(f"Sending server status to {webhook_name}", {
                        "webhook_name": webhook_name,
                        "payload": payload
                    }, "DiscordNotifier")

                async with self.session.post(webhook_config.url, json=payload) as response:
                    if response.status == 204:
                        success_count += 1
                        if self.debug_enabled:
                            _debug_log(f"✅ Server status sent successfully to {webhook_name}", {
                                "webhook_name": webhook_name,
                                "status_code": response.status
                            }, "DiscordNotifier")
                    else:
                        error_text = await response.text()
                        if self.debug_enabled:
                            _debug_log(f"❌ Failed to send server status to {webhook_name}", {
                                "webhook_name": webhook_name,
                                "status_code": response.status,
                                "error_text": error_text
                            }, "DiscordNotifier")

            except Exception as e:
                if self.debug_enabled:
                    _debug_log(f"❌ Exception sending server status to {webhook_name}", {
                        "webhook_name": webhook_name,
                        "error": str(e),
                        "error_type": type(e).__name__
                    }, "DiscordNotifier")

        success_rate = success_count / total_webhooks if total_webhooks > 0 else 0
        if self.debug_enabled:
            _debug_log("Server status notification summary", {
                "total_webhooks": total_webhooks,
                "successful_sends": success_count,
                "success_rate": f"{success_rate:.2%}"
            }, "DiscordNotifier")

        return success_count > 0

    def get_webhook_status(self) -> Dict[str, Any]:
        """
        Get comprehensive webhook status information.
        Enhanced with debug logging when DEBUG=true.
        """
        if self.debug_enabled:
            _debug_log("Generating webhook status report", {
                "webhook_count": len(self.webhooks),
                "routing_enabled": self.routing_enabled
            }, "DiscordNotifier")

        try:
            status = {
                "routing_enabled": self.routing_enabled,
                "webhooks": {},
                "routing_config": self.routing_config if self.routing_enabled else None
            }

            for webhook_name, webhook_config in self.webhooks.items():
                try:
                    webhook_url = webhook_config.url
                    url_preview = None

                    if webhook_url:
                        # Create safe URL preview (hide token for security)
                        parsed_url = urlparse(webhook_url)
                        if parsed_url.path:
                            path_parts = parsed_url.path.split('/')
                            if len(path_parts) >= 4:
                                webhook_id = path_parts[-2]
                                token_preview = path_parts[-1][:8] + "..." if len(path_parts[-1]) > 8 else path_parts[
                                    -1]
                                url_preview = f"https://discord.com/api/webhooks/{webhook_id}/{token_preview}"
                            else:
                                url_preview = webhook_url[:50] + "..." if len(webhook_url) > 50 else webhook_url
                        else:
                            url_preview = webhook_url[:50] + "..." if len(webhook_url) > 50 else webhook_url

                    webhook_status = {
                        "name": webhook_config.name,
                        "enabled": webhook_config.enabled,
                        "has_url": bool(webhook_config.url),
                        "url_preview": url_preview,
                        "grouping": webhook_config.grouping,
                        "rate_limit_info": self.webhook_rate_limits.get(webhook_name, {})
                    }

                    status["webhooks"][webhook_name] = webhook_status

                    if self.debug_enabled:
                        _debug_log(f"Webhook status for {webhook_name}", webhook_status, "DiscordNotifier")

                except Exception as e:
                    error_status = {
                        "name": webhook_name,
                        "enabled": False,
                        "has_url": False,
                        "url_preview": None,
                        "grouping": {},
                        "error": str(e)
                    }
                    status["webhooks"][webhook_name] = error_status

                    if self.debug_enabled:
                        _debug_log(f"❌ Error processing webhook status for {webhook_name}", {
                            "webhook_name": webhook_name,
                            "error": str(e),
                            "error_type": type(e).__name__
                        }, "DiscordNotifier")

            if self.debug_enabled:
                _debug_log("Webhook status report completed", {
                    "total_webhooks": len(status["webhooks"]),
                    "enabled_webhooks": sum(1 for w in status["webhooks"].values() if w.get("enabled", False)),
                    "webhooks_with_errors": sum(1 for w in status["webhooks"].values() if "error" in w)
                }, "DiscordNotifier")

            return status

        except Exception as e:
            if self.debug_enabled:
                _debug_log("❌ Error generating webhook status report", {
                    "error": str(e),
                    "error_type": type(e).__name__
                }, "DiscordNotifier")
            self.logger.error(f"Error getting webhook status: {e}")
            return {
                "error": "Failed to get webhook status",
                "routing_enabled": False,
                "webhooks": {}
            }

    async def close(self) -> None:
        """Close the HTTP session with debug logging."""
        if self.debug_enabled:
            _debug_log("Closing Discord notifier session", {
                "session_exists": self.session is not None
            }, "DiscordNotifier")

        if self.session:
            await self.session.close()
            if self.debug_enabled:
                _debug_log("✅ Discord notifier session closed", {}, "DiscordNotifier")