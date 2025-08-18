#!/usr/bin/env python3
"""
Jellynouncer Discord Services Module

This module contains the Discord notification services including webhook management,
thumbnail verification, and template-based message formatting. The module provides
Discord integration with intelligent routing, rate limiting, and rich embed generation.

The two main classes work together to provide reliable Discord notifications:
- ThumbnailManager: Handles thumbnail URL generation, verification, and fallback strategies
- DiscordNotifier: Manages webhook delivery, template rendering, and rate limiting

Both classes include debug logging capabilities that integrate with the application's
logging system, providing detailed operational insights during development
and troubleshooting.

Classes:
    ThumbnailManager: Manages thumbnail URL generation and verification
    DiscordNotifier: Discord webhook notifier with multi-webhook support

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import asyncio
import json
import time
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import asdict

import aiohttp
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError

from config_models import DiscordConfig
from media_models import MediaItem
from utils import get_logger


class ThumbnailManager:
    """
    Manages thumbnail URL generation and verification for Discord notifications.

    This class handles the complex process of generating reliable thumbnail URLs
    for different types of media content. It provides intelligent fallback
    strategies when primary thumbnails are unavailable or broken.

    **Why Thumbnail Management is Complex:**
    Jellyfin generates thumbnails dynamically, but they may not always be available:
    - New content might not have processed thumbnails yet
    - Network issues can prevent thumbnail loading
    - Some media types don't have suitable thumbnail sources
    - Different media types need different thumbnail approaches

    This manager provides a robust system that tries multiple sources and
    fallback strategies to ensure Discord embeds always have appropriate images.

    **Thumbnail Sources (in order of preference):**
    1. Primary image from Jellyfin (highest quality)
    2. Backdrop image (good for movies/shows)
    3. Logo image (for branding)
    4. Generic fallback based on media type

    Attributes:
        base_url (str): Base URL for Jellyfin server thumbnail requests
        session (aiohttp.ClientSession): HTTP session for thumbnail verification
        cache (Dict): In-memory cache of verified thumbnail URLs
        logger (logging.Logger): Component-specific logger

    Example:
        ```python
        manager = ThumbnailManager("http://jellyfin:8096", "api_key_here")

        # Get thumbnail for a movie
        thumbnail = await manager.get_thumbnail_url(
            item_id="123",
            media_type="Movie",
            primary_image_tag="abc123"
        )

        # Verify thumbnail is accessible
        is_valid = await manager.verify_thumbnail(thumbnail)
        ```
    """

    def __init__(self, jellyfin_url: str):
        """
        Initialize thumbnail manager with Jellyfin connection details.

        Args:
            jellyfin_url (str): Base URL of the Jellyfin server
        """
        self.base_url = jellyfin_url.rstrip('/')
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache: Dict[str, Optional[str]] = {}
        self.logger = get_logger("jellynouncer.discord.thumbnails")
        self._owns_session = False

    async def initialize(self) -> None:
        """
        Initialize HTTP session with connection pooling for thumbnail operations.

        This method sets up the aiohttp session with optimized connection pooling
        for better performance and resource usage. Connection pooling reuses TCP
        connections, reducing latency and improving throughput.
        """
        if self.session is None:
            # Configure connection pool for optimal performance
            connector = aiohttp.TCPConnector(
                limit=100,  # Total connection pool limit
                limit_per_host=30,  # Per-host connection limit
                ttl_dns_cache=300,  # DNS cache timeout in seconds
                enable_cleanup_closed=True  # Clean up closed connections
            )
            timeout = aiohttp.ClientTimeout(total=10)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector
            )
            self._owns_session = True  # Track if we created the session
            self.logger.debug("Thumbnail manager HTTP session created with connection pooling")
        else:
            self._owns_session = False  # We're using a shared session
            self.logger.debug("Thumbnail manager using shared HTTP session")

    async def cleanup(self) -> None:
        """
        Clean up HTTP session and resources.

        This method should be called during application shutdown to properly
        close the HTTP session and prevent resource leaks.
        """
        if self.session and self._owns_session:
            await self.session.close()
            self.session = None
            self.logger.debug("Thumbnail manager HTTP session closed")

    def _format_uuid_for_jellyfin(self, item_id: str) -> str:
        """
        Format a UUID string to the proper Jellyfin URL format with hyphens.

        Jellyfin stores UUIDs without hyphens but requires them with hyphens in URLs.
        This method converts from the database format to the URL format.

        Args:
            item_id (str): UUID string either with or without hyphens

        Returns:
            str: UUID formatted with hyphens for use in Jellyfin URLs

        Example:
            >>> ThumbnailManager._format_uuid_for_jellyfin("f549ba7fe88b2cbd7ac1794c029d5518")
            "f549ba7f-e88b-2cbd-7ac1-794c029d5518"

            >>> ThumbnailManager._format_uuid_for_jellyfin("f549ba7f-e88b-2cbd-7ac1-794c029d5518")
            "f549ba7f-e88b-2cbd-7ac1-794c029d5518"
        """
        # Remove any existing hyphens
        clean_uuid = item_id.replace('-', '')

        # Validate that we have a 32-character hex string
        if len(clean_uuid) != 32 or not re.match(r'^[a-fA-F0-9]{32}$', clean_uuid):
            # If it's not a valid UUID format, return as-is
            # This handles edge cases where item_id might not be a UUID
            return item_id

        # Format as UUID: 8-4-4-4-12
        formatted_uuid = f"{clean_uuid[:8]}-{clean_uuid[8:12]}-{clean_uuid[12:16]}-{clean_uuid[16:20]}-{clean_uuid[20:]}"

        return formatted_uuid

    async def get_thumbnail_url(
            self,
            item_id: str,
            media_type: str,
            primary_image_tag: Optional[str] = None,
            backdrop_image_tag: Optional[str] = None,
            logo_image_tag: Optional[str] = None
    ) -> Optional[str]:
        """
        Get the best available thumbnail URL for a media item.

        This method implements an intelligent fallback strategy to find the best
        available thumbnail for Discord embeds. It tries multiple image sources
        in order of preference and verifies that URLs are accessible.

        **Fallback Strategy:**
        1. Primary image (poster/cover art) - best quality and most appropriate
        2. Backdrop image - good for movies/shows, provides context
        3. Logo image - shows branding, better than no image
        4. Generic fallback - based on media type

        **Standardized Parameters:**
        - quality=90 (high quality for Discord)
        - maxWidth=500 (optimal for Discord embeds)
        - maxHeight=400 (prevents oversized images)

        Args:
            item_id (str): Jellyfin item ID
            media_type (str): Type of media (Movie, Series, Episode, Audio, etc.)
            primary_image_tag (Optional[str]): Primary image tag from Jellyfin
            backdrop_image_tag (Optional[str]): Backdrop image tag from Jellyfin
            logo_image_tag (Optional[str]): Logo image tag from Jellyfin

        Returns:
            Optional[str]: Best available thumbnail URL, or None if none available

        Example:
            ```python
            # Movie with all image types available
            url = await manager.get_thumbnail_url(
                item_id="123",
                media_type="Movie",
                primary_image_tag="abc123",
                backdrop_image_tag="def456",
                logo_image_tag="ghi789"
            )

            # Episode with only primary image
            url = await manager.get_thumbnail_url(
                item_id="456",
                media_type="Episode",
                primary_image_tag="jkl012"
            )
            ```
        """
        # Check cache first to avoid repeated verification
        cache_key = f"{item_id}:{primary_image_tag}:{backdrop_image_tag}:{logo_image_tag}"
        if cache_key in self.cache:
            cached_url = self.cache[cache_key]
            if cached_url is not None:
                self.logger.debug(f"Using cached thumbnail for item {item_id}")
                return cached_url
            else:
                self.logger.debug(f"Cached result shows no thumbnail available for item {item_id}")
                return None

        # DEBUG: Log incoming parameters
        self.logger.debug(f"get_thumbnail_url called with:")
        self.logger.debug(f"  item_id: {item_id}")
        self.logger.debug(f"  media_type: {media_type}")
        self.logger.debug(f"  primary_image_tag: {primary_image_tag} (type: {type(primary_image_tag)})")
        self.logger.debug(f"  backdrop_image_tag: {backdrop_image_tag} (type: {type(backdrop_image_tag)})")
        self.logger.debug(f"  logo_image_tag: {logo_image_tag} (type: {type(logo_image_tag)})")

        # Format item_id for Jellyfin URL (convert from unhyphenated to hyphenated UUID)
        formatted_item_id = self._format_uuid_for_jellyfin(item_id)
        self.logger.debug(f"Formatted item ID for URL: {item_id} -> {formatted_item_id}")

        # Standardized image parameters for consistency with templates
        # API key removed - images must be publicly accessible in Jellyfin
        image_params = "quality=90&maxWidth=500&maxHeight=400"

        # Try thumbnail sources in order of preference
        thumbnail_candidates = []

        # Primary image (poster/cover) - highest priority
        if primary_image_tag:
            primary_url = f"{self.base_url}/Items/{formatted_item_id}/Images/Primary?{image_params}&tag={primary_image_tag}"
            thumbnail_candidates.append(("Primary", primary_url))

        # Backdrop image - good fallback for movies/shows
        if backdrop_image_tag:
            backdrop_url = f"{self.base_url}/Items/{formatted_item_id}/Images/Backdrop?{image_params}&tag={backdrop_image_tag}"
            thumbnail_candidates.append(("Backdrop", backdrop_url))

        # Logo image - branding fallback
        if logo_image_tag:
            logo_url = f"{self.base_url}/Items/{formatted_item_id}/Images/Logo?{image_params}&tag={logo_image_tag}"
            thumbnail_candidates.append(("Logo", logo_url))

        self.logger.debug(f"Checking {len(thumbnail_candidates)} thumbnail candidates for item {formatted_item_id}")

        # Test each candidate URL
        for image_type, url in thumbnail_candidates:
            if await self.verify_thumbnail(url):
                self.logger.debug(f"Using {image_type} thumbnail for item {formatted_item_id}")
                self.cache[cache_key] = url
                return url
            else:
                self.logger.debug(f"{image_type} thumbnail not accessible for item {formatted_item_id}")

        # If no thumbnails work, log and return None
        self.logger.warning(f"No accessible thumbnails found for item {formatted_item_id} ({media_type})")
        self.cache[cache_key] = None
        return None

    async def verify_thumbnail(self, url: str) -> bool:
        """
        Verify that a thumbnail URL is accessible and returns valid image data.

        This method performs a GET request with a Range header to check if the
        thumbnail is accessible. This is more compatible than HEAD requests
        and still efficient as it only downloads a small portion of the image.

        Args:
            url (str): Thumbnail URL to verify

        Returns:
            bool: True if thumbnail is accessible, False otherwise

        Example:
            ```python
            url = "http://jellyfin:8096/Items/123/Images/Primary?..."
            is_valid = await manager.verify_thumbnail(url)
            if is_valid:
                # Use the thumbnail in Discord embed
                pass
            else:
                # Try fallback thumbnail
                pass
            ```
        """
        if not self.session:
            self.logger.warning("HTTP session not initialized, cannot verify thumbnail")
            return False

        try:
            # Use GET with Range header for better compatibility
            # Some servers/proxies don't properly support HEAD requests for images
            headers = {
                'Range': 'bytes=0-1024',  # Only download first 1KB to verify
                'Accept': 'image/*'
            }

            self.logger.debug(f"Verifying thumbnail URL: {url}")

            async with self.session.get(url, headers=headers, allow_redirects=True) as response:
                # Accept 200 (OK) and 206 (Partial Content) as success
                is_accessible = response.status in [200, 206]

                if is_accessible:
                    # Additional validation: check if content type is an image
                    content_type = response.headers.get('Content-Type', '').lower()
                    if content_type and not content_type.startswith('image/'):
                        self.logger.warning(f"URL returned non-image content type: {content_type}")
                        is_accessible = False
                    else:
                        self.logger.debug(f"Thumbnail verified successfully: {url}")
                else:
                    self.logger.debug(f"Thumbnail not accessible (HTTP {response.status}): {url}")
                    # Log response headers for debugging
                    self.logger.debug(f"Response headers: {dict(response.headers)}")

                return is_accessible

        except asyncio.TimeoutError:
            self.logger.warning(f"Thumbnail verification timeout: {url}")
            return False
        except aiohttp.ClientError as e:
            self.logger.warning(f"Thumbnail verification failed: {url} - {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error verifying thumbnail: {url} - {e}", exc_info=True)
            return False


class DiscordNotifier:
    """
    Discord webhook notifier with multi-webhook support and template rendering.

    This class handles all Discord notification functionality, including webhook
    management, template rendering, rate limiting, and error recovery. It supports
    multiple webhooks for different content types and provides rich embed formatting.

    **Multi-Webhook Strategy:**
    The notifier can route different types of content to different Discord channels:
    - Movies → Movies channel webhook
    - TV Shows → TV Shows channel webhook
    - Music → Music channel webhook
    - Fallback → General webhook for unmatched content

    This allows for organized notifications where users can subscribe to only
    the content types they're interested in.

    **Template System:**
    Uses Jinja2 templates to create rich Discord embeds with:
    - Dynamic content based on media type
    - Conditional fields (only show if data exists)
    - Custom formatting and styling
    - Fallback templates for different scenarios

    **Rate Limiting:**
    Implements intelligent rate limiting to respect Discord's API limits:
    - Tracks requests per webhook URL
    - Implements exponential backoff for failures
    - Queues notifications during rate limit periods

    Attributes:
        config (DiscordConfig): Discord configuration from application config
        thumbnail_manager (ThumbnailManager): Thumbnail URL management
        session (aiohttp.ClientSession): HTTP session for webhook requests
        rate_limits (Dict): Rate limiting state per webhook URL
        jinja_env (Environment): Jinja2 template environment
        logger (logging.Logger): Component-specific logger

    Example:
        ```python
        notifier = DiscordNotifier(discord_config, templates_config, thumbnail_manager)
        await notifier.initialize()

        # Send notification for a movie
        result = await notifier.send_notification(movie_item, "added")
        if result["success"]:
            logger.info("Notification sent successfully")
        ```
    """

    def __init__(self, config: DiscordConfig):
        """
        Initialize Discord notifier with configuration.

        Args:
            config (DiscordConfig): Discord webhook and notification configuration
        """
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.notifications_config = None
        self.rate_limits: Dict[str, Dict[str, Any]] = {}
        self.jinja_env: Optional[Environment] = None
        self.notification_queue: Dict[str, List[Any]] = {
            "new_items": [],
            "upgraded_items": [],
        }

        # Create thumbnail manager internally (needs Jellyfin config from main config)
        # This will need to be set during initialize() when full config is available
        self.thumbnail_manager = None

        self.logger = get_logger("jellynouncer.discord")

        # Template directory will be set during initialize() when templates_config is available
        self.template_dir = None
        self.webhooks = {}  # Make sure this is initialized

    async def initialize(self, session: aiohttp.ClientSession, jellyfin_config, templates_config, notifications_config=None) -> None:
        """Initialize Discord notifier with shared session and configuration dependencies."""
        self.session = session
        self.notifications_config = notifications_config

        # Set template directory from templates_config
        self.template_dir = templates_config.directory

        # Create thumbnail manager that will share the same session
        self.thumbnail_manager = ThumbnailManager(
            jellyfin_url=jellyfin_config.server_url
        )
        # Pass the shared session to thumbnail manager instead of letting it create its own
        self.thumbnail_manager.session = session
        # Don't call initialize() on thumbnail_manager since it would create another session

        # Initialize template environment
        if self.jinja_env is None:
            self.jinja_env = Environment(
                loader=FileSystemLoader(self.template_dir),
                trim_blocks=True,
                lstrip_blocks=True
            )
            self.logger.debug(f"Template environment initialized with directory: {self.template_dir}")

        self.logger.info("Discord notifier initialized successfully")

    async def cleanup(self) -> None:
        """
        Clean up HTTP session and resources.

        This method should be called during application shutdown to properly
        close the HTTP session and prevent resource leaks.
        """
        if self.session:
            await self.session.close()
            self.session = None
            self.logger.debug("Discord notifier HTTP session closed")

    def get_webhook_url(self, media_type: str) -> Optional[str]:
        """
        Get appropriate webhook URL based on media type and configuration.

        This method implements the routing logic that directs different types
        of media notifications to different Discord channels/webhooks.

        **Routing Logic:**
        1. Check for media-type-specific webhook (movies, tv, music)
        2. Fall back to general webhook if specific webhook not configured
        3. Return None if no applicable webhook is configured

        **Webhook Configuration Structure:**
        The method accesses webhooks through the nested configuration structure:
        - config.webhooks["movies"] for movie content
        - config.webhooks["tv"] for TV shows and episodes
        - config.webhooks["music"] for audio content
        - config.webhooks["default"] for fallback/general content

        Only enabled webhooks with valid URLs are considered for routing.

        Args:
            media_type (str): Type of media content (Movie, Series, Episode, Audio, etc.)

        Returns:
            Optional[str]: Webhook URL to use, or None if no webhook configured

        Example:
            ```python
            # Route a movie to movies webhook
            webhook_url = notifier.get_webhook_url("Movie")

            # Route a TV episode to TV webhook
            webhook_url = notifier.get_webhook_url("Episode")

            # Route music to music webhook
            webhook_url = notifier.get_webhook_url("Audio")
            ```
        """

        def _get_webhook_url_if_enabled(webhook_key: str) -> Optional[str]:
            """
            Helper function to safely get webhook URL if webhook exists, is enabled, and has URL.

            Args:
                webhook_key (str): Key name in the webhooks dictionary

            Returns:
                Optional[str]: Webhook URL if available and enabled, None otherwise
            """
            webhook_config = self.config.webhooks.get(webhook_key)
            if (webhook_config and
                    webhook_config.enabled and
                    webhook_config.url):
                return webhook_config.url
            return None

        # Map media types to webhook configuration keys
        webhook_type_mapping = {
            "Movie": "movies",
            "Series": "tv",
            "Season": "tv",
            "Episode": "tv",
            "Audio": "music",
            "MusicAlbum": "music",
            "MusicArtist": "music"
        }

        # Try to get specific webhook for this media type
        webhook_key = webhook_type_mapping.get(media_type)
        if webhook_key:
            specific_webhook_url = _get_webhook_url_if_enabled(webhook_key)
            if specific_webhook_url:
                self.logger.debug(f"Using specific {webhook_key} webhook for {media_type}")
                return specific_webhook_url

        # Fall back to general/default webhook
        default_webhook_url = _get_webhook_url_if_enabled("default")
        if default_webhook_url:
            self.logger.debug(f"Using default webhook for {media_type}")
            return default_webhook_url

        # No webhook configured
        self.logger.warning(f"No webhook configured for media type: {media_type}")
        return None

    async def send_notification(self, item: MediaItem, action: str, changes: Optional[List] = None) -> Dict[str, Any]:
        """
        Send Discord notification for a media item.

        This is the main entry point for sending Discord notifications. It handles
        the entire notification pipeline: webhook routing, template rendering,
        rate limiting, and error handling.

        **Notification Pipeline:**
        1. Determine appropriate webhook URL based on media type
        2. Generate thumbnail URL for the media item
        3. Render Discord embed using Jinja2 templates
        4. Check rate limits for the target webhook
        5. Send webhook request with retry logic
        6. Update rate limiting state
        7. Return detailed result information

        Args:
            item (MediaItem): Media item to create notification for
            action (str): Action that triggered the notification (added, updated, etc.)

        Returns:
            Dict[str, Any]: Detailed result information including:
                - success (bool): Whether notification was sent successfully
                - webhook_url (str): Webhook URL used (for debugging)
                - message (str): Human-readable result message
                - error (str): Error details if notification failed

        Example:
            ```python
            # Send notification for newly added movie
            result = await notifier.send_notification(movie_item, "added")

            if result["success"]:
                logger.info(f"Movie notification sent: {result['message']}")
            else:
                logger.error(f"Notification failed: {result['error']}")
            ```
        """
        # Initialize webhook_url early to avoid reference errors
        webhook_url = None

        try:
            # Get appropriate webhook URL for this media type
            webhook_url = self.get_webhook_url(item.item_type)
            if not webhook_url:
                return {
                    "success": False,
                    "error": f"No webhook configured for media type: {item.item_type}",
                    "webhook_url": None
                }

            self.logger.debug(f"Preparing notification for {item.name} ({item.item_type}) via {webhook_url}")

            # Generate thumbnail URL with fallback for episodes
            self.logger.debug(f"Checking MediaItem for image tags:")
            self.logger.debug(f"  hasattr primary_image_tag: {hasattr(item, 'primary_image_tag')}")
            self.logger.debug(f"  hasattr backdrop_image_tag: {hasattr(item, 'backdrop_image_tag')}")
            self.logger.debug(f"  hasattr logo_image_tag: {hasattr(item, 'logo_image_tag')}")

            primary_tag = getattr(item, 'primary_image_tag', None)
            backdrop_tag = getattr(item, 'backdrop_image_tag', None)
            logo_tag = getattr(item, 'logo_image_tag', None)

            self.logger.debug(f"Retrieved from MediaItem:")
            self.logger.debug(f"  primary_tag value: {primary_tag}")
            self.logger.debug(f"  backdrop_tag value: {backdrop_tag}")
            self.logger.debug(f"  logo_tag value: {logo_tag}")
            self.logger.debug(f"  primary_tag type: {type(primary_tag)}")

            # For episodes without their own images, try series images
            if item.item_type == 'Episode':
                if not primary_tag and hasattr(item, 'series_primary_image_tag'):
                    primary_tag = item.series_primary_image_tag
                if not backdrop_tag and hasattr(item, 'parent_backdrop_image_tag'):
                    backdrop_tag = item.parent_backdrop_image_tag
                if not logo_tag and hasattr(item, 'parent_logo_image_tag'):
                    logo_tag = item.parent_logo_image_tag

            # Generate thumbnail URL
            thumbnail_url = await self.thumbnail_manager.get_thumbnail_url(
                item_id=item.item_id if primary_tag == getattr(item, 'primary_image_tag', None) else getattr(item,
                                                                                                             'series_id',
                                                                                                             item.item_id),
                media_type=item.item_type,
                primary_image_tag=primary_tag,
                backdrop_image_tag=backdrop_tag,
                logo_image_tag=logo_tag
            )

            # Render Discord embed using templates
            embed_data = await self.render_embed(item, action, thumbnail_url, changes)
            
            # Log the embed data for debugging
            self.logger.debug(f"Embed data returned from render_embed:")
            self.logger.debug(f"  Type: {type(embed_data)}")
            self.logger.debug(f"  Keys: {list(embed_data.keys()) if isinstance(embed_data, dict) else 'Not a dict'}")
            if isinstance(embed_data, dict) and 'embeds' not in embed_data:
                self.logger.error(f"ERROR: embed_data missing 'embeds' key! Data: {embed_data}")

            # Check rate limits before sending
            if await self.is_rate_limited(webhook_url):
                self.logger.warning(f"Rate limited for webhook: {webhook_url}. Queuing notification.")
                if action == "new_item":
                    self.notification_queue["new_items"].append(item)
                elif action == "upgraded_item":
                    self.notification_queue["upgraded_items"].append(item)
                return {
                    "success": True,
                    "message": "Notification queued due to rate limiting",
                    "webhook_url": webhook_url
                }

            # Send the webhook
            webhook_data = {
                "embeds": embed_data.get("embeds", [embed_data]) if isinstance(embed_data, dict) else [],
                "username": "Jellynouncer"
            }

            success = await self.send_webhook(webhook_url, webhook_data)

            if success:
                self.logger.info(f"Discord notification sent for {item.name} ({item.item_type})")
                return {
                    "success": True,
                    "message": f"Notification sent for {item.name}",
                    "webhook_url": webhook_url
                }
            else:
                return {
                    "success": False,
                    "error": "Webhook request failed",
                    "webhook_url": webhook_url
                }

        except Exception as e:
            self.logger.error(f"Error sending Discord notification: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "webhook_url": webhook_url
            }

    async def send_server_status(self, template_data: Dict[str, Any]) -> bool:
        """
        Send server status notification using the template.

        This method sends server online/offline status notifications to Discord
        using the server_status.j2 template. It reuses the existing notification
        infrastructure for consistency.

        Args:
            template_data: Data for the server_status.j2 template including:
                - is_online: Boolean for server connectivity
                - jellyfin_url: Server URL
                - timestamp: ISO timestamp
                - health_data: Optional health check data
                - server_info: Optional server details
                - error_details: Optional error information

        Returns:
            bool: True if notification sent successfully
        """
        try:
            # Check if template exists
            template_name = "server_status.j2"

            if not self.jinja_env:
                self.logger.error("Template environment not initialized")
                return False

            # Render the template
            try:
                template = self.jinja_env.get_template(template_name)
                rendered = template.render(**template_data)
                embed_data = json.loads(rendered)
            except TemplateNotFound:
                self.logger.error(f"Server status template not found: {template_name}")
                return False
            except Exception as e:
                self.logger.error(f"Error rendering server status template: {e}")
                return False

            # Get the default webhook for server status notifications
            # Server status should always go to the default/general webhook
            webhook_url = None

            # Check for default webhook in the configuration
            default_webhook_config = self.config.webhooks.get("default")
            if (default_webhook_config and
                    default_webhook_config.enabled and
                    default_webhook_config.url):
                webhook_url = default_webhook_config.url

            if not webhook_url:
                self.logger.error("No default webhook configured for server status notification")
                return False

            # Check rate limiting
            if await self.is_rate_limited(webhook_url):
                self.logger.warning(f"Rate limited for server status notification")
                return False

            # Send the webhook using existing infrastructure
            success = await self.send_webhook(webhook_url, embed_data)

            if success:
                status_type = "online" if template_data.get("is_online") else "offline"
                self.logger.info(f"Server status notification sent: {status_type}")
            else:
                self.logger.error("Failed to send server status notification to Discord")

            return success

        except Exception as e:
            self.logger.error(f"Error sending server status notification: {e}", exc_info=True)
            return False

    def _get_notification_color(self, action: str, changes: Optional[List] = None) -> int:
        """Get the appropriate color for this notification type."""
        if not self.notifications_config or not hasattr(self.notifications_config, 'colors'):
            # Fallback colors if config not available
            return 65280 if action == "new_item" else 16766720

        colors = self.notifications_config.colors

        # For new items, use new_item color
        if action == "new_item":
            return colors.get("new_item", 65280)  # Default green

        # For upgrades, determine specific upgrade type
        elif action == "upgraded_item" and changes:
            # Check what type of upgrade this is
            for change in changes:
                change_type = change.get('type', '')
                if change_type == 'resolution':
                    return colors.get("resolution_upgrade", 16766720)  # Orange
                elif change_type == 'codec':
                    return colors.get("codec_upgrade", 16747520)  # Yellow
                elif change_type in ['audio_codec', 'audio_channels']:
                    return colors.get("audio_upgrade", 9662683)  # Purple
                elif change_type == 'hdr_status':
                    return colors.get("hdr_upgrade", 16716947)  # Gold
                elif change_type == 'provider_ids':
                    return colors.get("provider_update", 2003199)  # Blue

            # Default upgrade color if no specific type found
            return colors.get("resolution_upgrade", 16766720)

        # Default fallback
        return 65280  # Green

    def _log_json_error_context(self, rendered: str, position: int, line_no: int, col_no: int) -> None:
        """
        Log detailed context around a JSON parsing error.
        
        Args:
            rendered: The rendered template string
            position: Character position of the error
            line_no: Line number of the error
            col_no: Column number of the error
        """
        lines = rendered.splitlines()
        
        self.logger.error("📍 JSON Error Context:")
        
        # Show 3 lines before and after the error
        start_line = max(0, line_no - 4)
        end_line = min(len(lines), line_no + 3)
        
        for i in range(start_line, end_line):
            line_num = i + 1
            if line_num == line_no:
                # This is the error line
                self.logger.error(f"  >>> Line {line_num:3d}: {lines[i]}")
                # Add a pointer to the error column
                pointer = " " * (col_no + 13) + "^" + " ERROR HERE"
                self.logger.error(pointer)
            else:
                self.logger.error(f"      Line {line_num:3d}: {lines[i]}")
        
        # Try to identify the specific issue
        if position > 0 and position < len(rendered):
            char_at_error = rendered[position]
            self.logger.error(f"  - Character at error position: {repr(char_at_error)}")
            
            # Common JSON issues
            if char_at_error == "'":
                self.logger.error("  ⚠️  Possible issue: Single quotes used instead of double quotes")
            elif char_at_error == "\n":
                self.logger.error("  ⚠️  Possible issue: Unescaped newline in string value")
            elif char_at_error == "\\":
                self.logger.error("  ⚠️  Possible issue: Incorrectly escaped backslash")
            elif char_at_error == ",":
                self.logger.error("  ⚠️  Possible issue: Trailing comma or missing value")
            elif char_at_error == "}":
                self.logger.error("  ⚠️  Possible issue: Missing comma before closing brace")
    
    def _log_template_rendering_debug(self, template_name: str,
                                      template_vars: Dict[str, Any], rendered_output: str) -> None:
        """
        Debug logging for template rendering process.
        """
        self.logger.debug("=" * 60)
        self.logger.debug(f"🎨 TEMPLATE RENDERING DEBUG - {template_name}")
        self.logger.debug("=" * 60)

        # Log template variables (excluding sensitive data)
        self.logger.debug("📋 TEMPLATE VARIABLES:")
        for key, value in template_vars.items():
            if key in ['api_key']:  # Mask sensitive values
                self.logger.debug(f"  {key}: ***MASKED***")
            else:
                value_str = repr(value)
                if len(value_str) > 200:
                    value_str = value_str[:200] + "...TRUNCATED"
                self.logger.debug(f"  {key}: {value_str}")

        # Log rendered output
        self.logger.debug("\n📄 RENDERED TEMPLATE OUTPUT:")
        self.logger.debug(rendered_output)

        # Validate JSON structure
        self.logger.debug("\n✅ JSON VALIDATION:")
        try:
            parsed = json.loads(rendered_output)
            self.logger.debug("✅ Template output is valid JSON")
            self.logger.debug(f"  - Type: {type(parsed).__name__}")
            if isinstance(parsed, dict):
                self.logger.debug(f"  - Keys: {list(parsed.keys())}")
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Template output is invalid JSON: {e}")
            self.logger.error(f"  - Error at position: {e.pos}")

            # Show context around the error
            if hasattr(e, 'pos') and e.pos:
                start = max(0, e.pos - 50)
                end = min(len(rendered_output), e.pos + 50)
                context = rendered_output[start:end]
                self.logger.error(f"  - Context: ...{context}...")

        self.logger.debug("=" * 60)

    async def render_embed(self, item: MediaItem, action: str, thumbnail_url: Optional[str],
                           changes: Optional[List] = None) -> Dict[str, Any]:
        """
        Render Discord embed using Jinja2 templates.

        This method takes a media item and renders it into a rich Discord embed
        using the configured Jinja2 templates. It handles template selection based
        on the configured templates and grouping modes, variable preparation, and
        error recovery.

        **Template Selection Logic:**
        Templates are selected based on action type and grouping configuration:
        1. Check if grouping is enabled in webhook configuration
        2. Select appropriate template from TemplatesConfig based on:
           - Action type (new_item vs upgraded_item)
           - Grouping mode (individual, by_event, by_type, or grouped)
        3. Fall back to individual templates if grouping templates fail
        4. Use basic embed as final fallback

        **Supported Actions:**
        - "new_item": Uses new_item_template or grouped variants
        - "upgraded_item": Uses upgraded_item_template or grouped variants

        **Grouping Mode Support:**
        - Individual: Uses new_item_template or upgraded_item_template
        - by_event: Uses new_items_by_event_template or upgraded_items_by_event_template
        - by_type: Uses new_items_by_type_template or upgraded_items_by_type_template
        - grouped: Uses new_items_grouped_template or upgraded_items_grouped_template

        Args:
            item (MediaItem): Media item to render
            action (str): Action that triggered the notification ("new_item" or "upgraded_item")
            thumbnail_url (Optional[str]): Thumbnail URL for the embed image
            changes (Optional[List]): List of changes for upgraded items

        Returns:
            Dict[str, Any]: Discord embed data structure

        Raises:
            None: All exceptions are caught and logged, with fallback to basic embed

        Example:
            ```python
            embed = await notifier.render_embed(movie_item, "new_item", thumbnail_url)
            # Returns Discord embed structure ready for webhook
            ```
        """
        # Debug: Check what metadata is actually attached to the item
        self.logger.debug("=" * 60)
        self.logger.debug(f"🔍 METADATA DEBUGGING for item: {item.name}")
        self.logger.debug("=" * 60)

        # Check for attached metadata objects
        self.logger.debug("Checking for metadata attributes on MediaItem:")
        self.logger.debug(f"  - Has 'omdb' attribute: {hasattr(item, 'omdb')}")
        if hasattr(item, 'omdb'):
            omdb_data = getattr(item, 'omdb', None)
            if omdb_data:
                self.logger.debug(f"    - OMDb data present: {omdb_data is not None}")
                self.logger.debug(f"    - OMDb IMDb rating: {getattr(omdb_data, 'imdb_rating', 'N/A')}")
                self.logger.debug(f"    - OMDb Metascore: {getattr(omdb_data, 'metascore', 'N/A')}")
            else:
                self.logger.debug("    - OMDb data is None")

        self.logger.debug(f"  - Has 'tmdb' attribute: {hasattr(item, 'tmdb')}")
        if hasattr(item, 'tmdb'):
            tmdb_data = getattr(item, 'tmdb', None)
            if tmdb_data:
                self.logger.debug(f"    - TMDb data present: {tmdb_data is not None}")
                self.logger.debug(f"    - TMDb vote_average: {getattr(tmdb_data, 'vote_average', 'N/A')}")
                self.logger.debug(f"    - TMDb vote_count: {getattr(tmdb_data, 'vote_count', 'N/A')}")
            else:
                self.logger.debug("    - TMDb data is None")

        self.logger.debug(f"  - Has 'tvdb' attribute: {hasattr(item, 'tvdb')}")
        if hasattr(item, 'tvdb'):
            tvdb_data = getattr(item, 'tvdb', None)
            if tvdb_data:
                self.logger.debug(f"    - TVDb data present: {tvdb_data is not None}")
                self.logger.debug(f"    - TVDb rating: {getattr(tvdb_data, 'rating', 'N/A')}")
            else:
                self.logger.debug("    - TVDb data is None")

        self.logger.debug(f"  - Has 'ratings' attribute: {hasattr(item, 'ratings')}")
        if hasattr(item, 'ratings'):
            ratings_data = getattr(item, 'ratings', {})
            self.logger.debug(f"    - Ratings data: {ratings_data}")

        # Convert item to dictionary for template
        item_dict = asdict(item)

        # IMPORTANT: Check if metadata is preserved after asdict conversion
        self.logger.debug("After asdict() conversion:")
        self.logger.debug(f"  - 'omdb' in item_dict: {'omdb' in item_dict}")
        self.logger.debug(f"  - 'tmdb' in item_dict: {'tmdb' in item_dict}")
        self.logger.debug(f"  - 'tvdb' in item_dict: {'tvdb' in item_dict}")
        self.logger.debug(f"  - 'ratings' in item_dict: {'ratings' in item_dict}")

        # If metadata is not in dict, we need to manually add it
        if hasattr(item, 'omdb') and 'omdb' not in item_dict:
            self.logger.debug("⚠️ OMDb metadata lost in asdict() - manually adding")
            omdb_data = getattr(item, 'omdb', None)
            if omdb_data:
                item_dict['omdb'] = omdb_data.to_dict() if hasattr(omdb_data, 'to_dict') else omdb_data

        if hasattr(item, 'tmdb') and 'tmdb' not in item_dict:
            self.logger.debug("⚠️ TMDb metadata lost in asdict() - manually adding")
            tmdb_data = getattr(item, 'tmdb', None)
            if tmdb_data:
                item_dict['tmdb'] = tmdb_data.to_dict() if hasattr(tmdb_data, 'to_dict') else tmdb_data

        if hasattr(item, 'tvdb') and 'tvdb' not in item_dict:
            self.logger.debug("⚠️ TVDb metadata lost in asdict() - manually adding")
            tvdb_data = getattr(item, 'tvdb', None)
            if tvdb_data:
                item_dict['tvdb'] = tvdb_data.to_dict() if hasattr(tvdb_data, 'to_dict') else tvdb_data

        if hasattr(item, 'ratings') and 'ratings' not in item_dict:
            self.logger.debug("⚠️ Ratings data lost in asdict() - manually adding")
            item_dict['ratings'] = getattr(item, 'ratings', {})

        self.logger.debug(f"Final item_dict keys: {list(item_dict.keys())}")
        self.logger.debug("=" * 60)

        # Prepare template variables with standardized image parameters
        template_vars = {
            "item": item_dict,  # Use our enhanced dict
            "action": action,
            "thumbnail_url": thumbnail_url,
            "changes": changes or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server_url": self.thumbnail_manager.base_url,
            "jellyfin_url": self.thumbnail_manager.base_url,  # Keep both for backward compatibility
            "color": self._get_notification_color(action, changes),
            # Standardized image parameters
            "image_quality": 90,
            "image_max_width": 500,
            "image_max_height": 400,
            # Additional useful template variables
            "tvdb_attribution_needed": hasattr(item, 'has_tvdb_metadata') and getattr(item, 'has_tvdb_metadata', False),
            # Set based on whether tvdb metadata was fetched
        }

        def _get_webhook_grouping_config() -> Dict[str, Any]:
            """
            Get grouping configuration for the appropriate webhook.

            Returns:
                Dict[str, Any]: Grouping configuration from webhook config
            """
            # Determine which webhook would be used for this item
            webhook_key = None
            if item.item_type in ["Movie"]:
                webhook_key = "movies"
            elif item.item_type in ["Series", "Season", "Episode"]:
                webhook_key = "tv"
            elif item.item_type in ["Audio", "MusicAlbum", "MusicArtist"]:
                webhook_key = "music"
            else:
                webhook_key = "default"

            # Get the webhook configuration
            webhook_config = self.config.webhooks.get(webhook_key)
            if webhook_config and hasattr(webhook_config, 'grouping'):
                return webhook_config.grouping

            # Fall back to default webhook if specific one not found
            default_config = self.config.webhooks.get("default")
            if default_config and hasattr(default_config, 'grouping'):
                return default_config.grouping

            # Return empty config if no grouping found
            return {}

        def _get_template_for_action_and_grouping(action: str, grouping_config: Dict[str, Any]) -> List[str]:
            """
            Get template filename(s) to try based on action and grouping configuration.

            Args:
                action (str): Action type ("new_item" or "upgraded_item")
                grouping_config (Dict[str, Any]): Grouping configuration from webhook

            Returns:
                List[str]: List of template filenames to try in order of preference
            """
            # Get grouping mode from config
            grouping_mode = grouping_config.get("mode", "none")

            # Import templates_config from the webhook service
            # Note: This would need to be passed to the Discord service during initialization
            # For now, we'll use the default template configuration

            template_candidates = []

            if action == "new_item":
                if grouping_mode == "event_type" or grouping_mode == "by_event":
                    template_candidates.append("new_items_by_event.j2")
                elif grouping_mode == "content_type" or grouping_mode == "by_type":
                    template_candidates.append("new_items_by_type.j2")
                elif grouping_mode == "grouped" or grouping_mode == "both":
                    template_candidates.append("new_items_grouped.j2")

                # Always fall back to individual template
                template_candidates.append("new_item.j2")

            elif action == "upgraded_item":
                if grouping_mode == "event_type" or grouping_mode == "by_event":
                    template_candidates.append("upgraded_items_by_event.j2")
                elif grouping_mode == "content_type" or grouping_mode == "by_type":
                    template_candidates.append("upgraded_items_by_type.j2")
                elif grouping_mode == "grouped" or grouping_mode == "both":
                    template_candidates.append("upgraded_items_grouped.j2")

                # Always fall back to individual template
                template_candidates.append("upgraded_item.j2")

            else:
                # Unknown action, fall back to basic templates
                self.logger.warning(f"Unknown action type: {action}, falling back to new_item template")
                template_candidates.extend(["new_item.j2", "upgraded_item.j2"])

            return template_candidates

        # Get grouping configuration for this webhook
        grouping_config = _get_webhook_grouping_config()
        self.logger.debug(f"Grouping config for {item.item_type}: {grouping_config}")

        # Get template candidates based on action and grouping
        template_candidates = _get_template_for_action_and_grouping(action, grouping_config)
        self.logger.debug(f"Template candidates for {action}: {template_candidates}")

        rendered = None

        # Try to find and render appropriate template
        for template_name in template_candidates:
            try:
                template = self.jinja_env.get_template(template_name)
                rendered = template.render(**template_vars)

                # ADD: Template debugging
                self._log_template_rendering_debug(template_name, template_vars, rendered)

                embed_data = json.loads(rendered)

                self.logger.debug(f"Successfully using template {template_name} for {item.name}")
                return embed_data

            except TemplateNotFound:
                self.logger.debug(f"Template not found: {template_name}")
                continue
            except TemplateSyntaxError as e:
                self.logger.error(f"❌ Template Syntax Error in {template_name}:")
                self.logger.error(f"  - Error: {e.message}")
                if hasattr(e, 'lineno') and e.lineno:
                    self.logger.error(f"  - Line number: {e.lineno}")
                if hasattr(e, 'filename'):
                    self.logger.error(f"  - File: {e.filename}")
                self.logger.error(f"  - Full error: {str(e)}")
                continue
            except json.JSONDecodeError as e:
                self.logger.error(f"❌ JSON Decode Error in {template_name}:")
                self.logger.error(f"  - Error: {e.msg}")
                self.logger.error(f"  - Position: {e.pos}")
                self.logger.error(f"  - Line: {e.lineno}, Column: {e.colno}")
                
                # Enhanced context display with line numbers
                if rendered:
                    self._log_json_error_context(rendered, e.pos, e.lineno, e.colno)
                
                # Log the raw rendered output for debugging
                if self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug("Raw rendered template output:")
                    for i, line in enumerate(rendered.splitlines(), 1):
                        if max(1, e.lineno - 2) <= i <= e.lineno + 2:
                            marker = " >>> " if i == e.lineno else "     "
                            self.logger.debug(f"{marker}Line {i:3d}: {line}")
                continue
            except Exception as e:
                self.logger.error(f"❌ Unexpected error rendering template {template_name}:")
                self.logger.error(f"  - Error type: {type(e).__name__}")
                self.logger.error(f"  - Error: {str(e)}")
                import traceback
                self.logger.error(f"  - Traceback:\n{traceback.format_exc()}")
                continue

        # Fallback to basic embed if all templates fail
        self.logger.warning(f"All templates failed, using basic embed for {item.name}")

        # Create appropriate title based on action and item type
        if action == "upgraded_item":
            title_prefix = "📈 Upgraded"
        else:
            title_prefix = "✨ New"

        # Add emoji based on item type
        if item.item_type == "Episode":
            emoji = "📺"
            if hasattr(item, 'series_name') and item.series_name:
                title = f"{emoji} {title_prefix} Episode: {item.series_name}"
                description = f"S{getattr(item, 'season_number', 0):02d}E{getattr(item, 'episode_number', 0):02d} • {item.name}"
            else:
                title = f"{emoji} {title_prefix} Episode"
                description = item.name
        elif item.item_type == "Movie":
            emoji = "🎬"
            title = f"{emoji} {title_prefix} Movie"
            description = item.name
            if hasattr(item, 'year') and item.year:
                description += f" ({item.year})"
        elif item.item_type in ["Audio", "MusicAlbum"]:
            emoji = "🎵"
            title = f"{emoji} {title_prefix} Music"
            description = item.name
            if hasattr(item, 'album_artist') and item.album_artist:
                description += f" by {item.album_artist}"
        else:
            emoji = "📁"
            title = f"{emoji} {title_prefix} {item.item_type}"
            description = item.name

        # Determine embed color based on action
        if action == "upgraded_item":
            embed_color = 16766720  # Orange for upgrades
        else:
            embed_color = 65280  # Green for new items

        # Return in the same format as templates (with embeds array)
        return {
            "embeds": [{
                "title": title,
                "description": description,
                "color": embed_color,
                "image": {"url": thumbnail_url} if thumbnail_url else {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {
                    "text": f"{item.server_name or 'Jellyfin'} • {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
                }
            }]
        }

    def _make_serializable(self, obj):
        """
        Convert objects to JSON-serializable format.
        
        Handles common non-serializable types like dataclasses, datetime objects, etc.
        """
        import dataclasses
        from datetime import datetime, date
        
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        elif isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: self._make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._make_serializable(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self._make_serializable(item) for item in obj)
        elif hasattr(obj, '__dict__'):
            return self._make_serializable(obj.__dict__)
        else:
            return obj
    
    def _log_discord_payload_debug(self, webhook_url: str, payload: Dict[str, Any],
                                   item_name: str = "Unknown") -> None:
        """
        Comprehensive debug logging for Discord webhook payloads.

        This function logs the complete webhook payload structure to help debug
        Discord API 400 errors by showing exactly what data is being sent.

        Args:
            webhook_url: Discord webhook URL (will be masked for security)
            payload: Complete webhook payload being sent to Discord
            item_name: Name of the media item for context
        """
        self.logger.debug("=" * 80)
        self.logger.debug(f"🚀 DISCORD WEBHOOK DEBUG - {item_name}")
        self.logger.debug("=" * 80)

        # Mask webhook URL for security
        masked_url = webhook_url[:50] + "***" if len(webhook_url) > 50 else webhook_url
        self.logger.debug(f"📡 Webhook URL: {masked_url}")
        self.logger.debug(f"⏰ Timestamp: {datetime.now(timezone.utc).isoformat()}")

        # Log payload structure overview
        self.logger.debug("\n📋 PAYLOAD STRUCTURE OVERVIEW:")
        self.logger.debug(f"  - Type: {type(payload).__name__}")
        self.logger.debug(f"  - Top-level keys: {list(payload.keys()) if isinstance(payload, dict) else 'Not a dict'}")

        if isinstance(payload, dict):
            self.logger.debug(f"  - Has 'embeds': {'embeds' in payload}")
            self.logger.debug(f"  - Has 'username': {'username' in payload}")
            self.logger.debug(f"  - Has 'content': {'content' in payload}")

            if 'embeds' in payload:
                embeds = payload['embeds']
                self.logger.debug(f"  - Embeds type: {type(embeds).__name__}")
                self.logger.debug(f"  - Embeds count: {len(embeds) if isinstance(embeds, list) else 'Not a list'}")

        # Log complete JSON payload with pretty formatting
        self.logger.debug("\n📦 COMPLETE JSON PAYLOAD:")
        try:
            # Create a serializable copy of the payload
            serializable_payload = self._make_serializable(payload)
            formatted_json = json.dumps(serializable_payload, indent=2, ensure_ascii=False)
            self.logger.debug(formatted_json)
        except Exception as e:
            self.logger.error(f"❌ Failed to serialize payload to JSON: {e}")
            self.logger.debug(f"Raw payload: {payload}")
            # Try to at least show the structure
            try:
                self.logger.debug(f"Payload structure: {repr(payload)[:1000]}")
            except:
                self.logger.debug("Could not even repr() the payload")

        # Detailed embed analysis if embeds exist
        if isinstance(payload, dict) and 'embeds' in payload and isinstance(payload['embeds'], list):
            self.logger.debug("\n🔍 DETAILED EMBED ANALYSIS:")

            for i, embed in enumerate(payload['embeds']):
                self.logger.debug(f"\n  📄 EMBED {i}:")
                self.logger.debug(f"    - Type: {type(embed).__name__}")

                if isinstance(embed, dict):
                    self.logger.debug(f"    - Keys: {list(embed.keys())}")

                    # Check required and important fields
                    important_fields = ['title', 'description', 'color', 'fields', 'footer', 'timestamp']
                    for field in important_fields:
                        if field in embed:
                            value = embed[field]
                            self.logger.debug(
                                f"    - {field}: {type(value).__name__} = {repr(value)[:100]}{'...' if len(repr(value)) > 100 else ''}")
                        else:
                            self.logger.debug(f"    - {field}: ❌ MISSING")

                    # Detailed fields analysis
                    if 'fields' in embed and isinstance(embed['fields'], list):
                        self.logger.debug(f"\n    🏷️  FIELDS ANALYSIS ({len(embed['fields'])} fields):")

                        for j, field in enumerate(embed['fields']):
                            self.logger.debug(f"      Field {j}:")
                            self.logger.debug(f"        - Type: {type(field).__name__}")

                            if isinstance(field, dict):
                                self.logger.debug(f"        - Keys: {list(field.keys())}")

                                # Check required field properties
                                if 'name' in field:
                                    name_value = field['name']
                                    self.logger.debug(
                                        f"        - name: {type(name_value).__name__} = {repr(name_value)}")
                                    if not isinstance(name_value, str) or not name_value.strip():
                                        self.logger.error(f"        - ❌ INVALID NAME: Must be non-empty string")
                                else:
                                    self.logger.error(f"        - ❌ MISSING 'name' property")

                                if 'value' in field:
                                    value_value = field['value']
                                    self.logger.debug(
                                        f"        - value: {type(value_value).__name__} = {repr(value_value)[:100]}{'...' if len(repr(value_value)) > 100 else ''}")
                                    if not isinstance(value_value, str) or not value_value.strip():
                                        self.logger.error(f"        - ❌ INVALID VALUE: Must be non-empty string")
                                else:
                                    self.logger.error(f"        - ❌ MISSING 'value' property")

                                if 'inline' in field:
                                    inline_value = field['inline']
                                    self.logger.debug(
                                        f"        - inline: {type(inline_value).__name__} = {repr(inline_value)}")
                                    if not isinstance(inline_value, bool):
                                        self.logger.error(f"        - ❌ INVALID INLINE: Must be boolean")
                            else:
                                self.logger.error(f"        - ❌ FIELD IS NOT A DICT: {type(field).__name__}")

                    # Check embed limits
                    if 'title' in embed and isinstance(embed['title'], str):
                        if len(embed['title']) > 256:
                            self.logger.error(f"    - ❌ TITLE TOO LONG: {len(embed['title'])} chars (max 256)")

                    if 'description' in embed and isinstance(embed['description'], str):
                        if len(embed['description']) > 4096:
                            self.logger.error(
                                f"    - ❌ DESCRIPTION TOO LONG: {len(embed['description'])} chars (max 4096)")

                    if 'color' in embed:
                        color_value = embed['color']
                        if not isinstance(color_value, int) or color_value < 0 or color_value > 16777215:
                            self.logger.error(f"    - ❌ INVALID COLOR: {color_value} (must be integer 0-16777215)")
                else:
                    self.logger.error(f"    - ❌ EMBED IS NOT A DICT: {type(embed).__name__}")

        # Final validation summary
        self.logger.debug("\n✅ VALIDATION SUMMARY:")
        validation_errors = []

        if 'embeds' not in payload and 'content' not in payload:
            validation_errors.append("Payload missing both 'embeds' and 'content'")

        if 'embeds' in payload:
            if not isinstance(payload['embeds'], list):
                validation_errors.append("'embeds' is not a list")
            elif len(payload['embeds']) == 0:
                validation_errors.append("'embeds' array is empty")
            elif len(payload['embeds']) > 10:
                validation_errors.append(f"Too many embeds: {len(payload['embeds'])} (max 10)")

        if validation_errors:
            self.logger.error("❌ VALIDATION ERRORS FOUND:")
            for error in validation_errors:
                self.logger.error(f"  - {error}")
        else:
            self.logger.debug("✅ Basic payload structure appears valid")

        self.logger.debug("=" * 80)

    async def is_rate_limited(self, webhook_url: str) -> bool:
        """
        Check if webhook URL is currently rate limited.

        Discord has rate limits for webhooks (typically 30 requests per minute).
        This method tracks request timing and implements intelligent rate limiting
        to prevent hitting Discord's limits.

        Args:
            webhook_url (str): Webhook URL to check

        Returns:
            bool: True if rate limited, False if safe to send

        Example:
            ```python
            if await notifier.is_rate_limited(webhook_url):
                # Wait or queue the notification
                await asyncio.sleep(60)
            else:
                # Safe to send notification
                await send_webhook(webhook_url, data)
            ```
        """
        now = time.time()

        # Initialize rate limit tracking for this webhook if needed
        if webhook_url not in self.rate_limits:
            self.rate_limits[webhook_url] = {
                "requests": [],
                "blocked_until": 0
            }

        rate_limit_info = self.rate_limits[webhook_url]

        # Check if we're in a rate limit cooldown period
        if now < rate_limit_info["blocked_until"]:
            return True

        # Clean old requests (older than 1 minute)
        rate_limit_info["requests"] = [
            req_time for req_time in rate_limit_info["requests"]
            if now - req_time < 60
        ]

        # Check if we've exceeded the rate limit (30 requests per minute)
        if len(rate_limit_info["requests"]) >= 30:
            self.logger.warning(f"Rate limit reached for webhook: {webhook_url}")
            return True

        return False

    async def send_webhook(self, webhook_url: str, data: Dict[str, Any]) -> bool:
        """
        Send webhook request to Discord with comprehensive debug logging and error handling.

        This method handles the actual HTTP request to Discord's webhook API,
        including error handling, rate limit detection, retry logic, and detailed
        debug logging to help troubleshoot webhook issues.

        Args:
            webhook_url (str): Discord webhook URL
            data (Dict[str, Any]): Webhook payload data

        Returns:
            bool: True if webhook sent successfully, False otherwise
        """
        if not self.session:
            self.logger.error("HTTP session not initialized")
            return False

        item_name = "Unknown"

        try:
            # Extract item name for logging context
            if 'embeds' in data and isinstance(data['embeds'], list) and len(data['embeds']) > 0:
                embed = data['embeds'][0]
                if isinstance(embed, dict) and 'description' in embed:
                    # Try to extract item name from description
                    desc = embed['description']
                    if isinstance(desc, str) and '**' in desc:
                        # Extract text between ** markers
                        parts = desc.split('**')
                        if len(parts) >= 3:
                            item_name = parts[1][:50]  # Limit length for logging

            # Payload debugging
            self._log_discord_payload_debug(webhook_url, data, item_name)

            self.logger.debug(f"Sending webhook to Discord: {webhook_url}")

            async with self.session.post(webhook_url, json=data) as response:
                # Track this request for rate limiting
                now = time.time()
                if webhook_url not in self.rate_limits:
                    self.rate_limits[webhook_url] = {"requests": [], "blocked_until": 0}

                self.rate_limits[webhook_url]["requests"].append(now)

                # Enhanced response logging
                self.logger.debug(f"📥 Discord API Response:")
                self.logger.debug(f"  - Status Code: {response.status}")
                self.logger.debug(f"  - Status Text: {response.reason}")
                self.logger.debug(f"  - Content Type: {response.headers.get('Content-Type', 'Unknown')}")

                # Log response headers (excluding sensitive ones)
                self.logger.debug("  - Response Headers:")
                for header_name, header_value in response.headers.items():
                    if header_name.lower() not in ['set-cookie', 'authorization']:
                        self.logger.debug(f"    {header_name}: {header_value}")

                if response.status == 200 or response.status == 204:
                    self.logger.info(f"✅ Discord webhook sent successfully for: {item_name}")
                    self.logger.debug("✅ Webhook sent successfully")
                    return True
                elif response.status == 429:  # Rate limited
                    # Parse rate limit headers if available
                    retry_after = response.headers.get('Retry-After', '60')
                    try:
                        retry_seconds = int(retry_after)
                    except ValueError:
                        retry_seconds = 60

                    self.rate_limits[webhook_url]["blocked_until"] = now + retry_seconds
                    self.logger.warning(
                        f"⏳ Discord rate limit hit for {item_name}, blocked for {retry_seconds} seconds")
                    return False
                else:
                    # Enhanced error response handling
                    error_text = await response.text()
                    self.logger.error(f"❌ Discord webhook failed for {item_name}:")
                    self.logger.error(f"  - Status: {response.status} {response.reason}")
                    self.logger.error(f"  - Error Response: {error_text}")

                    # Try to parse error as JSON for better analysis
                    try:
                        error_json = json.loads(error_text)
                        self.logger.error(f"  - Parsed Error: {json.dumps(error_json, indent=2)}")

                        # Specific analysis for 400 errors
                        if response.status == 400 and isinstance(error_json, dict):
                            if 'embeds' in error_json:
                                self.logger.error(f"  - 🎯 EMBED ERROR DETECTED: {error_json['embeds']}")
                                if isinstance(error_json['embeds'], list):
                                    for i, embed_error in enumerate(error_json['embeds']):
                                        self.logger.error(f"    - Embed {i} error: {embed_error}")

                    except Exception as parse_error:
                        self.logger.debug(f"  - Could not parse error as JSON: {parse_error}")

                    return False

        except asyncio.TimeoutError:
            self.logger.error(f"⏰ Webhook request timed out for: {item_name}")
            return False
        except aiohttp.ClientError as e:
            self.logger.error(f"🌐 Webhook request failed for {item_name}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"💥 Unexpected error sending webhook for {item_name}: {e}", exc_info=True)
            return False

    async def validate_all_templates(self) -> Dict[str, Any]:
        """
        Validate all available templates for JSON syntax errors.
        
        This method renders each template with sample data and checks for JSON validity.
        Uses real movie data (Demon Slayer: Infinity Castle) with full metadata.
        
        Returns:
            Dict with validation results for each template
        """
        import os
        from media_models import MediaItem
        from metadata_omdb import OMDbMetadata, OMDbRating
        from metadata_tmdb import TMDbMetadata
        
        results = {}
        
        # Create sample OMDb metadata based on actual API response
        sample_omdb = OMDbMetadata(
            imdb_id="tt32820897",
            type="movie",
            title="Demon Slayer -Kimetsu no Yaiba- The Movie: Infinity Castle",
            year="2025",
            rated=None,
            released="12 Sep 2025",
            runtime="150 min",
            genre="Animation, Action, Adventure",
            director="Haruo Sotozaki, Hikaru Kondô",
            writer="Koyoharu Gotouge, Hikaru Kondô",
            actors="Natsuki Hanae, Saori Hayami, Akari Kitô",
            plot="The Demon Slayer Corps are drawn into the Infinity Castle, where Tanjiro, Nezuko, and the Hashira face terrifying Upper Rank demons in a desperate fight as the final battle against Muzan Kibutsuji begins.",
            language="Japanese",
            country="Japan, United States",
            poster="https://m.media-amazon.com/images/M/MV5BOGQ3YWUzYjEtMTJiYy00ZjQ0LWI0YjktYjhiNGVhNGExYTM3XkEyXkFqcGc@._V1_SX300.jpg",
            metascore=None,
            imdb_rating="9.0",
            imdb_votes="5,044",
            ratings=[
                OMDbRating(source="Internet Movie Database", value="9.0/10", normalized_value=9.0)
            ],
            runtime_minutes=150,
            genres_list=["Animation", "Action", "Adventure"],
            actors_list=["Natsuki Hanae", "Saori Hayami", "Akari Kitô"],
            languages_list=["Japanese"],
            countries_list=["Japan", "United States"]
        )
        # Set up ratings_dict for OMDb
        sample_omdb.ratings_dict = {
            "imdb": sample_omdb.ratings[0] if sample_omdb.ratings else None
        }
        
        # Create sample TMDb metadata based on actual API response
        sample_tmdb = TMDbMetadata(
            tmdb_id=1311031,
            imdb_id="tt32820897",
            media_type="movie",
            title="Demon Slayer: Kimetsu no Yaiba Infinity Castle",
            original_title="劇場版「鬼滅の刃」無限城編 第一章 猗窩座再来",
            tagline="It's time to have some fun.",
            overview="The Demon Slayer Corps are drawn into the Infinity Castle, where Tanjiro, Nezuko, and the Hashira face terrifying Upper Rank demons in a desperate fight as the final battle against Muzan Kibutsuji begins.",
            status="Released",
            release_date="2025-07-18",
            vote_average=7.2,
            vote_count=73,
            popularity=309.9512,
            runtime=155,
            budget=68000000,
            revenue=148000000,
            original_language="ja",
            genres_list=["Animation", "Action", "Fantasy", "Thriller"],
            production_companies_list=["ufotable", "Aniplex", "Shueisha"],
            production_countries_list=["Japan"],
            spoken_languages_list=["Japanese"],
            poster_path="/aFRDH3P7TX61FVGpaLhKr6QiOC1.jpg",
            backdrop_path="/1RgPyOhN4DRs225BGTlHJqCudII.jpg",
            poster_url="https://image.tmdb.org/t/p/w500/aFRDH3P7TX61FVGpaLhKr6QiOC1.jpg",
            backdrop_url="https://image.tmdb.org/t/p/w1280/1RgPyOhN4DRs225BGTlHJqCudII.jpg"
        )
        
        # Create Demon Slayer movie MediaItem with all fields from the webhook
        sample_item = MediaItem(
            item_id="fd94413771cbe2b735da55e82d613f26",
            name="Demon Slayer: Infinity Castle",
            item_type="Movie",
            year=2025,
            overview="Based on an action fantasy shounen manga by Gotouge Koyoharu.",
            tagline="It's time to have some fun.",
            runtime_ticks=77628390000,
            runtime_formatted="02:09:22",
            premiere_date="2025-07-18",
            
            # Video specs
            video_height=1008,
            video_width=1920,
            video_codec="h264",
            video_profile="High",
            video_level="40",
            video_range="SDR",
            video_framerate=23.976025,
            aspect_ratio="40:21",
            video_interlaced=False,
            video_bitrate=None,
            video_bitdepth=None,
            video_colorspace="bt709",
            video_colortransfer="bt709",
            video_colorprimaries="bt709",
            video_pixelformat="yuv420p",
            video_refframes=1,
            video_title="1080p H264 SDR",
            video_type="Video",
            
            # Audio specs
            audio_codec="eac3",
            audio_channels=6,
            audio_language="eng",
            audio_bitrate=768000,
            audio_samplerate=48000,
            audio_default=True,
            audio_title="English - Dolby Digital Plus + Dolby Atmos - 5.1 - Default",
            audio_type="Audio",
            
            # Subtitles (just first one for testing)
            subtitle_title="English (forced) - Default - SUBRIP",
            subtitle_type="Subtitle",
            subtitle_language="eng",
            subtitle_codec="subrip",
            subtitle_default=True,
            subtitle_forced=True,
            subtitle_external=False,
            
            # External IDs
            imdb_id="tt32820897",
            tmdb_id="1311031",
            tvdb_id="357928",
            tvdb_slug="ju-chang-ban-gui-mie-noren-wu-xian-cheng-bian",
            
            # Server info
            server_id="example_server_id_12345",
            server_name="Example Jellyfin Server",
            server_version="10.10.7",
            server_url="http://jellyfin.example.com",
            notification_type="ItemAdded",
            
            # File info
            file_size=10737418240,  # 10GB for testing
            library_name="Movies",
            
            # Timestamps
            timestamp="2025-08-16T14:42:44.9384606-04:00",
            utc_timestamp="2025-08-16T18:42:44.9384614Z",
            timestamp_created=datetime.now(timezone.utc).isoformat(),
            
            # Lists
            genres=["Anime", "Fantasy"],
            studios=["ufotable", "Aniplex", "Shueisha"],
            tags=[],
            
            # Image tags
            primary_image_tag="ef43347430d568667404c5ee65376c6b",
            logo_image_tag="a0bf9d1784cab4a4b125b543b3c7e77b",
            thumb_image_tag="008b18a341d9a4483100823dbb50a2fc",
            
            # Content hash for change detection
            content_hash="test_hash_12345"
        )
        
        # Attach metadata to the item
        sample_item.omdb = sample_omdb
        sample_item.tmdb = sample_tmdb
        sample_item.tvdb = None  # No TVDb data for this movie
        sample_item.ratings = {
            'imdb': {'value': '9.0/10', 'normalized': 9.0, 'source': 'Internet Movie Database'},
            'imdb_score': '9.0',
            'imdb_votes': '5,044',
            'metascore': None,
            'tmdb': {'value': '7.2/10', 'normalized': 7.2, 'count': 73}
        }
        
        # Convert item to dict and manually add metadata (since it's not a dataclass field)
        item_dict = asdict(sample_item)
        
        # Add metadata to dict (matching what happens in render_embed)
        if sample_item.omdb:
            item_dict['omdb'] = sample_omdb.to_dict() if hasattr(sample_omdb, 'to_dict') else asdict(sample_omdb)
        if sample_item.tmdb:
            item_dict['tmdb'] = sample_tmdb.to_dict() if hasattr(sample_tmdb, 'to_dict') else asdict(sample_tmdb)
        if hasattr(sample_item, 'ratings'):
            item_dict['ratings'] = sample_item.ratings
        
        # Sample template variables matching actual webhook processing
        sample_vars = {
            "item": item_dict,
            "action": "new_item",
            "thumbnail_url": "http://jellyfin.example.com/Items/fd944137-71cb-e2b7-35da-55e82d613f26/Images/Primary?quality=90&maxWidth=500&maxHeight=400&tag=ef43347430d568667404c5ee65376c6b",
            "changes": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server_url": "http://jellyfin.example.com",
            "jellyfin_url": "http://jellyfin.example.com",
            "color": 65280,
            "image_quality": 90,
            "image_max_width": 500,
            "image_max_height": 400,
            "tvdb_attribution_needed": False
        }
        
        # Get all template files
        template_dir = self.template_dir
        if os.path.exists(template_dir):
            for filename in os.listdir(template_dir):
                if filename.endswith('.j2'):
                    try:
                        template = self.jinja_env.get_template(filename)
                        rendered = template.render(**sample_vars)
                        
                        # Try to parse as JSON
                        json.loads(rendered)
                        results[filename] = {
                            "status": "✅ Valid",
                            "error": None
                        }
                        
                    except TemplateSyntaxError as e:
                        results[filename] = {
                            "status": "❌ Template Syntax Error",
                            "error": str(e),
                            "line": getattr(e, 'lineno', None)
                        }
                    except json.JSONDecodeError as e:
                        results[filename] = {
                            "status": "❌ JSON Invalid",
                            "error": e.msg,
                            "line": e.lineno,
                            "column": e.colno
                        }
                    except Exception as e:
                        results[filename] = {
                            "status": "❌ Render Error",
                            "error": str(e)
                        }
        
        # Log summary
        self.logger.info("=" * 60)
        self.logger.info("📋 TEMPLATE VALIDATION RESULTS")
        self.logger.info("=" * 60)
        
        valid_count = sum(1 for r in results.values() if r["status"].startswith("✅"))
        total_count = len(results)
        
        for template_name, result in sorted(results.items()):
            if result["status"].startswith("✅"):
                self.logger.info(f"  {result['status']} {template_name}")
            else:
                self.logger.error(f"  {result['status']} {template_name}")
                if result.get("line"):
                    self.logger.error(f"      Line: {result['line']}")
                if result.get("column"):
                    self.logger.error(f"      Column: {result['column']}")
                self.logger.error(f"      Error: {result['error']}")
        
        self.logger.info("=" * 60)
        self.logger.info(f"Summary: {valid_count}/{total_count} templates valid")
        
        return results
    
    def get_webhook_status(self) -> Dict[str, Any]:
        """
        Get current status of Discord webhook configuration and rate limiting.

        This method provides diagnostic information about the Discord notifier's
        current state, including configured webhooks and rate limiting status.

        **Status Information Provided:**
        - Total number of enabled webhooks with valid URLs
        - Breakdown by webhook type (default, movies, tv, music)
        - Current rate limiting state for active webhooks
        - Service initialization status

        Returns:
            Dict[str, Any]: Status information including:
                - configured_webhooks: Count of enabled webhooks with URLs
                - webhook_types: Dictionary showing which webhook types are configured
                - rate_limits: Current rate limiting state count
                - session_initialized: Whether HTTP session is ready
                - templates_initialized: Whether Jinja2 templates are loaded

        Example:
            ```python
            status = notifier.get_webhook_status()
            logger.info(f"Configured webhooks: {status['configured_webhooks']}")
            logger.info(f"Available webhook types: {list(status['webhook_types'].keys())}")
            ```
        """
        configured_webhooks = 0
        webhook_info = {}

        # Check each webhook type in the configuration
        webhook_types = ["default", "movies", "tv", "music"]

        for webhook_type in webhook_types:
            webhook_config = self.config.webhooks.get(webhook_type)
            if (webhook_config and
                    webhook_config.enabled and
                    webhook_config.url):
                configured_webhooks += 1
                webhook_info[webhook_type] = {
                    "enabled": True,
                    "name": webhook_config.name,
                    "has_url": bool(webhook_config.url)
                }
            else:
                webhook_info[webhook_type] = {
                    "enabled": False,
                    "name": webhook_config.name if webhook_config else f"{webhook_type.title()} Webhook",
                    "has_url": False
                }

        return {
            "configured_webhooks": configured_webhooks,
            "webhook_types": webhook_info,
            "rate_limits": len(self.rate_limits),
            "session_initialized": self.session is not None,
            "templates_initialized": self.jinja_env is not None
        }