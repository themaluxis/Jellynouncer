#!/usr/bin/env python3
"""
Jellynouncer Discord Services Module

This module contains the Discord notification services including webhook management,
thumbnail verification, and template-based message formatting. The module provides
comprehensive Discord integration with intelligent routing, rate limiting, and
rich embed generation.

The two main classes work together to provide reliable Discord notifications:
- ThumbnailManager: Handles thumbnail URL generation, verification, and fallback strategies
- DiscordNotifier: Manages webhook delivery, template rendering, and rate limiting

Both classes include optional debug logging capabilities when the DEBUG environment
variable is set to 'true', providing detailed operational insights during development
and troubleshooting.

Classes:
    ThumbnailManager: Manages thumbnail URL generation and verification
    DiscordNotifier: Enhanced Discord webhook notifier with multi-webhook support

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
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
    """
    Global debug logging function for development and troubleshooting.

    This utility function provides enhanced debugging output when the DEBUG
    environment variable is set to 'true'. It logs detailed information about
    Discord service operations with formatted JSON data.

    Args:
        message (str): Human-readable description of the event
        data (Any, optional): Structured data to include in debug output
        component (str): Component name for log organization (default: "Discord")

    Example:
        ```python
        _debug_log("Sending webhook", {
            "webhook_url": "https://discord.com/api/webhooks/...",
            "item_name": "The Matrix",
            "embed_color": 65280
        }, "DiscordNotifier")
        ```

    Note:
        This function only produces output when DEBUG=true in environment variables.
        In production, these calls have minimal performance impact as they return
        immediately when debugging is disabled.
    """
    if os.getenv('DEBUG', 'false').lower() == 'true':
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [DEBUG] [{component}] {message}")
        if data is not None:
            print(f"[{timestamp}] [DEBUG] [{component}] Data: {json.dumps(data, indent=2, default=str)}")


class ThumbnailManager:
    """
    Manages thumbnail URL generation and verification for Discord notifications.

    This class implements a comprehensive thumbnail system that ensures Discord
    embeds always display appropriate images for media items. It handles the
    complexity of Jellyfin's image API while providing intelligent fallback
    strategies when images aren't available.

    **Understanding Thumbnails in Discord:**
    Discord embeds can display thumbnail images that make notifications much more
    visually appealing. However, Discord requires that thumbnail URLs:
    - Are publicly accessible (no authentication required)
    - Return valid image content (proper MIME types)
    - Respond quickly (Discord has timeout limits)

    **Thumbnail Fallback Strategy:**
    Different media types have different optimal thumbnail sources:

    - **Episodes**: Episode image → Season image → Series image → Default
    - **Seasons**: Season image → Series image → Default
    - **Series**: Series image → Default
    - **Movies**: Movie poster → Default
    - **Music**: Album art → Artist image → Default
    - **Other**: Item image → Default

    **Verification Caching:**
    To avoid repeatedly checking the same URLs, this manager caches verification
    results for a configurable duration. This improves performance and reduces
    load on the Jellyfin server.

    Attributes:
        jellyfin_url (str): Base Jellyfin server URL for image generation
        session (aiohttp.ClientSession): HTTP session for URL verification
        logger (logging.Logger): Logger instance for thumbnail operations
        verification_cache (Dict): Cache of URL verification results
        cache_duration (int): How long to cache verification results (seconds)
        debug_enabled (bool): Whether debug logging is enabled

    Example:
        ```python
        # Initialize thumbnail manager
        async with aiohttp.ClientSession() as session:
            thumbnail_mgr = ThumbnailManager("http://jellyfin:8096", session, logger)

            # Get verified thumbnail for a movie
            movie_item = MediaItem(item_id="abc123", name="The Matrix", item_type="Movie")
            thumbnail_url = await thumbnail_mgr.get_verified_thumbnail_url(movie_item)

            if thumbnail_url:
                print(f"Using thumbnail: {thumbnail_url}")
            else:
                print("No valid thumbnail available")
        ```

    Note:
        This class is designed to work with aiohttp ClientSession for efficient
        HTTP connection reuse. It should be initialized once and reused across
        multiple thumbnail operations.
    """

    def __init__(self, jellyfin_url: str, session: aiohttp.ClientSession, logger: logging.Logger):
        """
        Initialize thumbnail manager with Jellyfin connection and HTTP session.

        Sets up the thumbnail manager with the necessary components for URL
        generation and verification. The HTTP session should be provided by
        the parent component for efficient connection reuse.

        Args:
            jellyfin_url (str): Base Jellyfin server URL (e.g., "http://jellyfin:8096")
            session (aiohttp.ClientSession): HTTP session for verification requests
            logger (logging.Logger): Logger instance for thumbnail operations

        Example:
            ```python
            async with aiohttp.ClientSession() as session:
                thumbnail_manager = ThumbnailManager(
                    jellyfin_url="http://jellyfin:8096",
                    session=session,
                    logger=logger
                )
            ```
        """
        self.jellyfin_url = jellyfin_url.rstrip('/')  # Remove trailing slash
        self.session = session
        self.logger = logger

        # Cache for URL verification results (URL -> (is_valid, timestamp))
        self.verification_cache = {}
        self.cache_duration = 300  # 5 minutes cache duration

        # Enable debug logging based on environment variable
        self.debug_enabled = os.getenv('DEBUG', 'false').lower() == 'true'

        if self.debug_enabled:
            _debug_log("ThumbnailManager initialized", {
                "jellyfin_url": self.jellyfin_url,
                "cache_duration": self.cache_duration,
                "debug_enabled": self.debug_enabled
            }, "ThumbnailManager")

    async def get_verified_thumbnail_url(self, item: MediaItem) -> Optional[str]:
        """
        Get verified thumbnail URL for a media item with intelligent fallback strategy.

        This method implements the core thumbnail selection and verification logic.
        It generates multiple candidate URLs based on the media type and tests each
        one until a working thumbnail is found.

        **Verification Process:**
        1. Generate candidate URLs based on media type and fallback strategy
        2. Test each candidate URL to ensure it's accessible and returns valid image data
        3. Return the first working URL, or None if no thumbnails are available
        4. Cache results to avoid repeated verification of the same URLs

        **Performance Optimization:**
        The method uses cached results when available to minimize HTTP requests.
        This is especially important during batch operations like library syncing.

        Args:
            item (MediaItem): Media item to generate thumbnail for

        Returns:
            Optional[str]: Verified thumbnail URL if available, None otherwise

        Example:
            ```python
            # Get thumbnail for a TV episode
            episode = MediaItem(
                item_id="episode123",
                name="Pilot",
                item_type="Episode",
                series_id="series456",
                parent_id="season789"
            )

            thumbnail_url = await thumbnail_manager.get_verified_thumbnail_url(episode)
            if thumbnail_url:
                # Use in Discord embed
                embed = {"thumbnail": {"url": thumbnail_url}}
            ```

        Note:
            This method performs network requests and should be awaited. It may
            take several seconds if multiple URLs need verification, but results
            are cached to speed up subsequent requests.
        """
        if self.debug_enabled:
            _debug_log("Getting verified thumbnail URL", {
                "item_id": item.item_id,
                "item_name": item.name,
                "item_type": item.item_type
            }, "ThumbnailManager")

        # Generate thumbnail URL candidates based on item type and fallback strategy
        thumbnail_candidates = self._generate_thumbnail_candidates(item)

        if self.debug_enabled:
            _debug_log("Generated thumbnail candidates", {
                "item_id": item.item_id,
                "candidates": thumbnail_candidates,
                "candidate_count": len(thumbnail_candidates)
            }, "ThumbnailManager")

        # Test each candidate URL until we find one that works
        for i, candidate in enumerate(thumbnail_candidates):
            if await self._verify_thumbnail_url(candidate):
                if self.debug_enabled:
                    _debug_log("✅ Found verified thumbnail", {
                        "item_id": item.item_id,
                        "selected_url": candidate,
                        "candidate_index": i,
                        "total_candidates": len(thumbnail_candidates)
                    }, "ThumbnailManager")

                self.logger.debug(f"Using verified thumbnail: {candidate}")
                return candidate

        # No valid thumbnails found
        if self.debug_enabled:
            _debug_log("❌ No valid thumbnails found", {
                "item_id": item.item_id,
                "item_name": item.name,
                "tested_candidates": len(thumbnail_candidates)
            }, "ThumbnailManager")

        self.logger.warning(f"No valid thumbnail found for {item.name} (ID: {item.item_id})")
        return None

    def _generate_thumbnail_candidates(self, item: MediaItem) -> List[str]:
        """
        Generate list of thumbnail URL candidates based on media type and fallback strategy.

        This private method implements the intelligent fallback logic that ensures
        we always try the most appropriate thumbnail sources first, falling back
        to more generic options when specific images aren't available.

        **Fallback Strategy by Media Type:**

        **TV Episodes:**
        1. Episode-specific thumbnail (if available)
        2. Season poster/thumbnail
        3. Series poster/logo
        4. Generic item thumbnail

        **TV Seasons:**
        1. Season poster/thumbnail
        2. Series poster/logo
        3. Generic item thumbnail

        **Movies:**
        1. Movie poster (Primary image)
        2. Movie backdrop/fanart
        3. Generic item thumbnail

        **Music Albums:**
        1. Album cover art
        2. Artist image (if available)
        3. Generic item thumbnail

        **Music Tracks:**
        1. Album cover art (from parent album)
        2. Artist image
        3. Generic item thumbnail

        Args:
            item (MediaItem): Media item to generate candidates for

        Returns:
            List[str]: Ordered list of thumbnail URL candidates to test

        Example:
            For a TV episode, this might return:
            ```python
            [
                "http://jellyfin:8096/Items/episode123/Images/Primary?maxHeight=400",
                "http://jellyfin:8096/Items/season456/Images/Primary?maxHeight=400",
                "http://jellyfin:8096/Items/series789/Images/Primary?maxHeight=400",
                "http://jellyfin:8096/Items/episode123/Images/Thumb?maxHeight=400"
            ]
            ```
        """
        candidates = []
        base_params = "maxHeight=400&quality=90"  # Standard thumbnail parameters

        try:
            if item.item_type == "Episode":
                # Episode fallback strategy
                candidates.extend([
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}",
                ])

                # Try season thumbnail if we have parent_id (season)
                if item.parent_id:
                    candidates.append(
                        f"{self.jellyfin_url}/Items/{item.parent_id}/Images/Primary?{base_params}"
                    )

                # Try series thumbnail if we have series_id
                if item.series_id:
                    candidates.extend([
                        f"{self.jellyfin_url}/Items/{item.series_id}/Images/Primary?{base_params}",
                        f"{self.jellyfin_url}/Items/{item.series_id}/Images/Logo?{base_params}",
                    ])

            elif item.item_type == "Season":
                # Season fallback strategy
                candidates.extend([
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}",
                ])

                # Try series thumbnail if we have series_id
                if item.series_id:
                    candidates.extend([
                        f"{self.jellyfin_url}/Items/{item.series_id}/Images/Primary?{base_params}",
                        f"{self.jellyfin_url}/Items/{item.series_id}/Images/Logo?{base_params}",
                    ])

            elif item.item_type == "Series":
                # Series fallback strategy
                candidates.extend([
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}",
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Logo?{base_params}",
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Backdrop?{base_params}",
                ])

            elif item.item_type == "Movie":
                # Movie fallback strategy
                candidates.extend([
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}",
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Backdrop?{base_params}",
                ])

            elif item.item_type in ["Audio", "MusicAlbum"]:
                # Music fallback strategy
                candidates.extend([
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}",
                ])

                # For music tracks, try parent album artwork
                if item.item_type == "Audio" and item.parent_id:
                    candidates.append(
                        f"{self.jellyfin_url}/Items/{item.parent_id}/Images/Primary?{base_params}"
                    )

            else:
                # Generic fallback for other media types
                candidates.extend([
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}",
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Thumb?{base_params}",
                ])

            # Always add generic item thumbnail as final fallback
            if f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}" not in candidates:
                candidates.append(
                    f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}"
                )

        except Exception as e:
            self.logger.error(f"Error generating thumbnail candidates for {item.item_id}: {e}")
            # Provide minimal fallback on error
            candidates = [f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?{base_params}"]

        return candidates

    async def _verify_thumbnail_url(self, url: str) -> bool:
        """
        Verify that a thumbnail URL is accessible and returns valid image content.

        This private method performs HTTP HEAD requests to check if thumbnail URLs
        are accessible without downloading the full image content. It implements
        caching to avoid repeated verification of the same URLs.

        **Verification Process:**
        1. Check cache for recent verification result
        2. If not cached, perform HTTP HEAD request
        3. Verify response status (200) and content type (image/*)
        4. Cache result for future use
        5. Return verification status

        **Performance Considerations:**
        - Uses HEAD requests (metadata only) to minimize bandwidth
        - Implements timeout to avoid hanging on slow servers
        - Caches results to reduce repeated network requests
        - Handles network errors gracefully

        Args:
            url (str): Thumbnail URL to verify

        Returns:
            bool: True if URL returns valid image content, False otherwise

        Example:
            ```python
            # Internal verification call
            is_valid = await self._verify_thumbnail_url(
                "http://jellyfin:8096/Items/abc123/Images/Primary?maxHeight=400"
            )
            ```

        Note:
            This is a private method called internally by get_verified_thumbnail_url().
            It includes comprehensive error handling to ensure network issues don't
            crash the thumbnail verification process.
        """
        # Check cache first to avoid redundant network requests
        current_time = time.time()
        if url in self.verification_cache:
            is_valid, timestamp = self.verification_cache[url]
            if current_time - timestamp < self.cache_duration:
                if self.debug_enabled:
                    _debug_log("Using cached thumbnail verification result", {
                        "url": url,
                        "is_valid": is_valid,
                        "cache_age_seconds": current_time - timestamp
                    }, "ThumbnailManager")
                return is_valid

        # Perform URL verification with comprehensive error handling
        try:
            if self.debug_enabled:
                _debug_log("Verifying thumbnail URL", {"url": url}, "ThumbnailManager")

            # Use HEAD request to check URL without downloading image data
            async with self.session.head(url, timeout=5) as response:
                content_type = response.headers.get('content-type', '')
                is_valid = (
                        response.status == 200 and
                        content_type.startswith('image/')
                )

                # Cache the verification result for future use
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
        """
        Clear the thumbnail verification cache for testing or maintenance.

        This method removes all cached verification results, forcing fresh
        verification on the next requests. Useful for testing, debugging,
        or periodic cache maintenance.

        Example:
            ```python
            # Clear cache during maintenance
            thumbnail_manager.clear_cache()
            ```
        """
        if self.debug_enabled:
            cache_size = len(self.verification_cache)
            _debug_log("Thumbnail verification cache cleared", {
                "previous_cache_size": cache_size
            }, "ThumbnailManager")

        self.verification_cache.clear()
        self.logger.debug("Thumbnail verification cache cleared")


class DiscordNotifier:
    """
    Enhanced Discord webhook notifier with multi-webhook support and rate limiting.

    This class manages Discord webhook notifications with advanced features designed
    for production reliability and rich user experience. It handles the complexity
    of Discord's webhook API while providing intelligent routing and comprehensive
    error handling.

    **Advanced Features:**

    **Multi-Webhook Support:**
    The service can route different content types to different Discord channels:
    - Movies → #movies-channel
    - TV Shows → #tv-shows-channel
    - Music → #music-channel
    - General → #announcements-channel

    **Rate Limiting:**
    Discord has strict rate limits (typically 30 requests per minute per webhook).
    This class implements intelligent rate limiting to avoid being blocked.

    **Template-Based Formatting:**
    Uses Jinja2 templates to create rich, customizable Discord embeds. Templates
    can be modified without changing code, allowing easy customization of
    notification appearance.

    **Retry Logic:**
    Network operations can fail. This class implements exponential backoff
    retry logic to handle temporary Discord API issues gracefully.

    Attributes:
        config (DiscordConfig): Discord webhook configuration settings
        jellyfin_url (str): Jellyfin server URL for thumbnail generation
        logger (logging.Logger): Logger instance for Discord operations
        session (aiohttp.ClientSession): HTTP session for webhook requests
        thumbnail_manager (ThumbnailManager): Thumbnail verification manager
        template_env (Environment): Jinja2 template environment
        rate_limiter (Dict): Rate limiting state for each webhook
        debug_enabled (bool): Whether debug logging is enabled

    Example:
        ```python
        # Initialize Discord notifier
        discord_config = DiscordConfig(
            webhooks={
                "movies": WebhookConfig(
                    url="https://discord.com/api/webhooks/...",
                    name="Movies",
                    enabled=True
                )
            }
        )

        async with aiohttp.ClientSession() as session:
            notifier = DiscordNotifier(discord_config, "http://jellyfin:8096", logger, session)

            # Send notification for new movie
            movie = MediaItem(item_id="abc123", name="The Matrix", item_type="Movie")
            success = await notifier.send_notification(movie, is_new=True)

            if success:
                print("Notification sent successfully")
        ```

    Note:
        This class requires an active aiohttp ClientSession for HTTP operations.
        It's designed to be long-lived and reused across multiple notifications
        for optimal performance.
    """

    def __init__(self, config: DiscordConfig, jellyfin_url: str, logger: logging.Logger,
                 session: Optional[aiohttp.ClientSession] = None,
                 templates_config: Optional[TemplatesConfig] = None):
        """
        Initialize Discord notifier with configuration and dependencies.

        Sets up the Discord notifier with all necessary components for webhook
        delivery, template rendering, and rate limiting. Creates HTTP session
        if not provided and initializes template environment.

        **Dependency Injection Pattern:**
        This constructor accepts optional dependencies (session, templates_config)
        to support both standalone usage and integration with larger service
        architectures. When dependencies aren't provided, sensible defaults
        are created.

        Args:
            config (DiscordConfig): Discord webhook configuration settings
            jellyfin_url (str): Jellyfin server URL for image/thumbnail generation
            logger (logging.Logger): Logger instance for Discord operations
            session (Optional[aiohttp.ClientSession]): HTTP session for requests.
                If None, a new session will be created.
            templates_config (Optional[TemplatesConfig]): Template configuration.
                If None, default template settings will be used.

        Raises:
            Exception: If template environment initialization fails

        Example:
            ```python
            # With provided session (recommended for service integration)
            async with aiohttp.ClientSession() as session:
                notifier = DiscordNotifier(config, jellyfin_url, logger, session)

            # Standalone usage (creates own session)
            notifier = DiscordNotifier(config, jellyfin_url, logger)
            ```
        """
        self.config = config
        self.jellyfin_url = jellyfin_url.rstrip('/')
        self.logger = logger

        # Use provided templates config or create default
        if templates_config is None:
            templates_config = TemplatesConfig()  # Uses default template directory

        # Enable debug logging based on environment variable
        self.debug_enabled = os.getenv('DEBUG', 'false').lower() == 'true'

        if self.debug_enabled:
            _debug_log("Initializing Discord notifier components", {
                "templates_directory": templates_config.directory,
                "session_provided": session is not None,
                "webhook_count": len(config.webhooks),
                "routing_enabled": config.routing.enabled
            }, "DiscordNotifier")

        # Create HTTP session if not provided
        if session is None:
            self.session = aiohttp.ClientSession()
            self._owns_session = True  # Track ownership for cleanup
        else:
            self.session = session
            self._owns_session = False

        # Initialize thumbnail manager for image verification
        self.thumbnail_manager = ThumbnailManager(self.jellyfin_url, self.session, self.logger)

        # Initialize Jinja2 template environment
        try:
            self.template_env = Environment(
                loader=FileSystemLoader(templates_config.directory),
                auto_reload=True  # Enable template reloading for development
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

        # Initialize rate limiting state for each webhook
        self.rate_limiter = {}
        for webhook_name in config.webhooks.keys():
            self.rate_limiter[webhook_name] = {
                'requests': [],  # Timestamps of recent requests
                'limit': 30,  # Requests per minute (Discord's typical limit)
                'window': 60  # Time window in seconds
            }

    async def send_notification(self, item: MediaItem, changes: Optional[List[Dict[str, Any]]] = None,
                                is_new: bool = True) -> bool:
        """
        Send Discord notification for a media item with intelligent routing and formatting.

        This is the main entry point for sending Discord notifications. It handles
        the complete workflow from webhook routing to template rendering to actual
        delivery, with comprehensive error handling and debugging support.

        **Notification Workflow:**
        1. Determine appropriate webhook(s) based on content type and routing rules
        2. Generate verified thumbnail URL for rich embed display
        3. Select and render appropriate Jinja2 template (new vs. upgraded)
        4. Apply rate limiting to respect Discord's API limits
        5. Send webhook request with retry logic
        6. Handle errors gracefully with detailed logging

        **Content Routing:**
        When routing is enabled, notifications are sent to content-specific webhooks:
        - Movies → movies webhook
        - TV content (Episodes, Seasons, Series) → tv webhook
        - Music content → music webhook
        - Other content → default webhook

        **Template Selection:**
        - New items: Uses 'new_item' template for announcements
        - Upgraded items: Uses 'upgraded_item' template showing changes

        Args:
            item (MediaItem): Media item to send notification for
            changes (Optional[List[Dict[str, Any]]]): List of detected changes for upgrades
            is_new (bool): Whether this is a new item (True) or upgrade (False)

        Returns:
            bool: True if notification was sent successfully, False otherwise

        Example:
            ```python
            # Send notification for new movie
            movie = MediaItem(
                item_id="abc123",
                name="The Matrix",
                item_type="Movie",
                year=1999,
                video_height=1080
            )

            success = await discord_notifier.send_notification(movie, is_new=True)

            # Send notification for upgraded content
            changes = [
                {
                    "type": "resolution",
                    "old_value": 720,
                    "new_value": 1080,
                    "description": "Resolution upgraded from 720p to 1080p"
                }
            ]

            success = await discord_notifier.send_notification(
                movie,
                changes=changes,
                is_new=False
            )
            ```

        Note:
            This method handles all error cases gracefully and will always return
            a boolean result. Detailed error information is logged for debugging.
            Network timeouts, Discord API errors, and template issues are all
            handled without raising exceptions.
        """
        if self.debug_enabled:
            _debug_log("Starting notification send process", {
                "item_id": item.item_id,
                "item_name": item.name,
                "item_type": item.item_type,
                "is_new": is_new,
                "changes_count": len(changes) if changes else 0
            }, "DiscordNotifier")

        try:
            # Determine which webhook(s) to use based on routing configuration
            target_webhooks = self._determine_target_webhooks(item)

            if not target_webhooks:
                if self.debug_enabled:
                    _debug_log("❌ No target webhooks found", {
                        "item_type": item.item_type,
                        "routing_enabled": self.config.routing.enabled
                    }, "DiscordNotifier")
                self.logger.warning(f"No enabled webhooks found for {item.item_type}")
                return False

            # Get verified thumbnail URL for rich embed display
            thumbnail_url = await self.thumbnail_manager.get_verified_thumbnail_url(item)

            # Render Discord embed using appropriate template
            embed_data = await self._render_notification_template(item, changes, is_new, thumbnail_url)

            if not embed_data:
                self.logger.error("Failed to render notification template")
                return False

            # Send to all target webhooks
            success_count = 0
            for webhook_name, webhook_config in target_webhooks.items():
                if await self._send_webhook(webhook_name, webhook_config, embed_data):
                    success_count += 1

            success = success_count > 0

            if self.debug_enabled:
                _debug_log("Notification send process completed", {
                    "success": success,
                    "successful_webhooks": success_count,
                    "total_webhooks": len(target_webhooks)
                }, "DiscordNotifier")

            return success

        except Exception as e:
            if self.debug_enabled:
                _debug_log("❌ Notification send process failed", {
                    "item_id": item.item_id,
                    "error": str(e),
                    "error_type": type(e).__name__
                }, "DiscordNotifier")
            self.logger.error(f"Failed to send notification for {item.name}: {e}")
            return False

    def _determine_target_webhooks(self, item: MediaItem) -> Dict[str, Any]:
        """
        Determine which webhook(s) should receive notification based on content type and routing.

        This private method implements the intelligent routing logic that directs
        different content types to appropriate Discord channels when routing is enabled.

        **Routing Logic:**
        - If routing is disabled: Use default webhook only
        - If routing is enabled: Route by content type with fallback to default

        **Content Type Mapping:**
        - Movies → movies webhook
        - Episodes, Seasons, Series → tv webhook
        - Audio, MusicAlbum → music webhook
        - Other types → default webhook

        Args:
            item (MediaItem): Media item to determine routing for

        Returns:
            Dict[str, Any]: Dictionary of webhook names to webhook configurations

        Example:
            For a Movie with routing enabled:
            ```python
            {
                "movies": WebhookConfig(url="...", name="Movies", enabled=True)
            }
            ```
        """
        target_webhooks = {}

        if self.config.routing.enabled:
            # Route based on content type
            if item.item_type == "Movie":
                webhook_name = "movies"
            elif item.item_type in ["Episode", "Season", "Series"]:
                webhook_name = "tv"
            elif item.item_type in ["Audio", "MusicAlbum"]:
                webhook_name = "music"
            else:
                webhook_name = "default"

            # Use specific webhook if available and enabled
            if (webhook_name in self.config.webhooks and
                    self.config.webhooks[webhook_name].enabled and
                    self.config.webhooks[webhook_name].url):
                target_webhooks[webhook_name] = self.config.webhooks[webhook_name]
            # Fallback to default webhook
            elif ("default" in self.config.webhooks and
                  self.config.webhooks["default"].enabled and
                  self.config.webhooks["default"].url):
                target_webhooks["default"] = self.config.webhooks["default"]
        else:
            # No routing - use default webhook only
            if ("default" in self.config.webhooks and
                    self.config.webhooks["default"].enabled and
                    self.config.webhooks["default"].url):
                target_webhooks["default"] = self.config.webhooks["default"]

        return target_webhooks

    async def _render_notification_template(self, item: MediaItem, changes: Optional[List[Dict[str, Any]]],
                                            is_new: bool, thumbnail_url: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Render Discord notification template with media item data and formatting.

        This private method handles the complex task of rendering Jinja2 templates
        with media item data, changes information, and additional context needed
        for rich Discord embeds.

        **Template Context:**
        The method provides templates with comprehensive context including:
        - item: Complete MediaItem data
        - changes: List of detected changes (for upgrades)
        - is_new: Boolean flag for template logic
        - thumbnail_url: Verified thumbnail URL
        - jellyfin_url: Server URL for additional links
        - color: Embed color based on content type

        Args:
            item (MediaItem): Media item data for template rendering
            changes (Optional[List[Dict[str, Any]]]): Changes for upgrade notifications
            is_new (bool): Whether this is a new item notification
            thumbnail_url (Optional[str]): Verified thumbnail URL

        Returns:
            Optional[Dict[str, Any]]: Rendered template data or None if rendering failed

        Note:
            This method handles template errors gracefully, logging issues and
            returning None rather than raising exceptions that would break
            the notification workflow.
        """
        try:
            # Select appropriate template based on notification type
            template_name = "new_item.j2" if is_new else "upgraded_item.j2"
            template = self.template_env.get_template(template_name)

            # Prepare template context with comprehensive data
            context = {
                'item': item,
                'changes': changes or [],
                'is_new': is_new,
                'thumbnail_url': thumbnail_url,
                'jellyfin_url': self.jellyfin_url,
                'color': self._get_embed_color(item.item_type),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

            # Render template with context data
            rendered_content = template.render(**context)

            # Parse rendered JSON template
            embed_data = json.loads(rendered_content)

            if self.debug_enabled:
                _debug_log("✅ Template rendered successfully", {
                    "template_name": template_name,
                    "item_id": item.item_id,
                    "context_keys": list(context.keys()),
                    "embed_title": embed_data.get('embeds', [{}])[0].get('title', 'No title')
                }, "DiscordNotifier")

            return embed_data

        except TemplateNotFound as e:
            self.logger.error(f"Template not found: {e}")
            if self.debug_enabled:
                _debug_log("❌ Template not found", {
                    "template_name": template_name,
                    "error": str(e)
                }, "DiscordNotifier")
            return None

        except TemplateSyntaxError as e:
            self.logger.error(f"Template syntax error: {e}")
            if self.debug_enabled:
                _debug_log("❌ Template syntax error", {
                    "template_name": template_name,
                    "error": str(e),
                    "line_number": e.lineno
                }, "DiscordNotifier")
            return None

        except json.JSONDecodeError as e:
            self.logger.error(f"Template rendered invalid JSON: {e}")
            if self.debug_enabled:
                _debug_log("❌ Template rendered invalid JSON", {
                    "template_name": template_name,
                    "json_error": str(e),
                    "rendered_content_preview": rendered_content[:200] if 'rendered_content' in locals() else "N/A"
                }, "DiscordNotifier")
            return None

        except Exception as e:
            self.logger.error(f"Template rendering failed: {e}")
            if self.debug_enabled:
                _debug_log("❌ Template rendering failed", {
                    "template_name": template_name,
                    "error": str(e),
                    "error_type": type(e).__name__
                }, "DiscordNotifier")
            return None

    def _get_embed_color(self, item_type: str) -> int:
        """
        Get Discord embed color based on media type.

        This private method returns appropriate embed colors for different
        content types, providing visual distinction in Discord channels.

        **Color Scheme:**
        - Movies: Blue (#0099ff)
        - TV Content: Green (#00ff00)
        - Music: Purple (#9900ff)
        - Other: Orange (#ff9900)

        Args:
            item_type (str): Media item type

        Returns:
            int: RGB color value as integer for Discord embeds

        Example:
            ```python
            color = self._get_embed_color("Movie")  # Returns 39423 (blue)
            ```
        """
        color_map = {
            "Movie": 0x0099ff,  # Blue
            "Episode": 0x00ff00,  # Green
            "Season": 0x00ff00,  # Green
            "Series": 0x00ff00,  # Green
            "Audio": 0x9900ff,  # Purple
            "MusicAlbum": 0x9900ff,  # Purple
        }
        return color_map.get(item_type, 0xff9900)  # Default orange

    async def _send_webhook(self, webhook_name: str, webhook_config: Any, embed_data: Dict[str, Any]) -> bool:
        """
        Send webhook request to Discord with rate limiting and retry logic.

        This private method handles the actual HTTP request to Discord's webhook API
        with comprehensive error handling, rate limiting, and retry logic for
        production reliability.

        **Rate Limiting Strategy:**
        Discord enforces rate limits (typically 30 requests per minute per webhook).
        This method tracks request timestamps and delays sending when limits are
        approached to avoid being blocked by Discord.

        **Retry Logic:**
        Network requests can fail for various reasons. This method implements
        exponential backoff retry logic to handle temporary failures:
        - First retry: 1 second delay
        - Second retry: 2 seconds delay
        - Third retry: 4 seconds delay

        **Error Handling:**
        Handles various failure scenarios gracefully:
        - Network timeouts
        - Discord API errors (rate limits, server errors)
        - Invalid webhook URLs
        - JSON serialization issues

        Args:
            webhook_name (str): Name of webhook for logging and rate limiting
            webhook_config (Any): Webhook configuration with URL and settings
            embed_data (Dict[str, Any]): Rendered embed data to send

        Returns:
            bool: True if webhook was sent successfully, False otherwise

        Example:
            ```python
            success = await self._send_webhook(
                "movies",
                webhook_config,
                {"embeds": [{"title": "New Movie", "description": "The Matrix"}]}
            )
            ```

        Note:
            This method never raises exceptions - all errors are handled gracefully
            and logged appropriately. Rate limiting is applied automatically.
        """
        if not webhook_config.url:
            self.logger.warning(f"Webhook {webhook_name} has no URL configured")
            return False

        # Apply rate limiting before sending
        if not await self._check_rate_limit(webhook_name):
            self.logger.warning(f"Rate limit exceeded for webhook {webhook_name}, skipping")
            return False

        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                if self.debug_enabled:
                    _debug_log(f"Sending webhook (attempt {attempt + 1}/{max_retries})", {
                        "webhook_name": webhook_name,
                        "webhook_url": webhook_config.url[:50] + "..." if len(
                            webhook_config.url) > 50 else webhook_config.url,
                        "embed_title": embed_data.get('embeds', [{}])[0].get('title', 'No title')
                    }, "DiscordNotifier")

                # Send webhook request with timeout
                async with self.session.post(
                        webhook_config.url,
                        json=embed_data,
                        timeout=aiohttp.ClientTimeout(total=10)
                ) as response:

                    if response.status == 204:  # Discord success response
                        self._record_successful_request(webhook_name)

                        if self.debug_enabled:
                            _debug_log("✅ Webhook sent successfully", {
                                "webhook_name": webhook_name,
                                "status_code": response.status,
                                "attempt": attempt + 1
                            }, "DiscordNotifier")

                        self.logger.info(f"Notification sent successfully via {webhook_name}")
                        return True

                    elif response.status == 429:  # Rate limited
                        retry_after = response.headers.get('Retry-After', '60')
                        self.logger.warning(f"Rate limited by Discord, retry after {retry_after}s")

                        if self.debug_enabled:
                            _debug_log("❌ Discord rate limit hit", {
                                "webhook_name": webhook_name,
                                "retry_after": retry_after,
                                "attempt": attempt + 1
                            }, "DiscordNotifier")

                        if attempt < max_retries - 1:
                            await asyncio.sleep(int(retry_after))

                    else:  # Other HTTP error
                        error_text = await response.text()
                        self.logger.error(f"Webhook failed with status {response.status}: {error_text}")

                        if self.debug_enabled:
                            _debug_log("❌ Webhook HTTP error", {
                                "webhook_name": webhook_name,
                                "status_code": response.status,
                                "error_text": error_text,
                                "attempt": attempt + 1
                            }, "DiscordNotifier")

                        # Don't retry on client errors (4xx)
                        if 400 <= response.status < 500:
                            return False

            except asyncio.TimeoutError:
                self.logger.warning(f"Webhook timeout for {webhook_name} (attempt {attempt + 1})")
                if self.debug_enabled:
                    _debug_log("❌ Webhook timeout", {
                        "webhook_name": webhook_name,
                        "attempt": attempt + 1,
                        "timeout_seconds": 10
                    }, "DiscordNotifier")

            except Exception as e:
                self.logger.error(f"Webhook error for {webhook_name}: {e}")
                if self.debug_enabled:
                    _debug_log("❌ Webhook exception", {
                        "webhook_name": webhook_name,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "attempt": attempt + 1
                    }, "DiscordNotifier")

            # Wait before retry (exponential backoff)
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                if self.debug_enabled:
                    _debug_log(f"Retrying webhook in {delay} seconds", {
                        "webhook_name": webhook_name,
                        "delay": delay,
                        "next_attempt": attempt + 2
                    }, "DiscordNotifier")
                await asyncio.sleep(delay)

        self.logger.error(f"Webhook failed after {max_retries} attempts: {webhook_name}")
        return False

    async def _check_rate_limit(self, webhook_name: str) -> bool:
        """
        Check if webhook can send without exceeding Discord's rate limits.

        This private method implements a sliding window rate limiter to track
        request frequency and prevent exceeding Discord's API limits. It
        automatically cleans up old request timestamps and determines if
        a new request can be sent.

        **Rate Limiting Algorithm:**
        1. Clean up request timestamps older than the time window
        2. Check if current request count is under the limit
        3. Return True if request can proceed, False if rate limited

        Args:
            webhook_name (str): Name of webhook to check rate limit for

        Returns:
            bool: True if request can proceed, False if rate limited

        Example:
            ```python
            if await self._check_rate_limit("movies"):
                # Safe to send webhook
                await self._send_webhook_request()
            else:
                # Rate limited, skip or delay
                pass
            ```
        """
        current_time = time.time()
        rate_info = self.rate_limiter[webhook_name]

        # Clean up old requests outside the time window
        rate_info['requests'] = [
            req_time for req_time in rate_info['requests']
            if current_time - req_time < rate_info['window']
        ]

        # Check if we're under the rate limit
        can_send = len(rate_info['requests']) < rate_info['limit']

        if self.debug_enabled and not can_send:
            _debug_log("Rate limit check failed", {
                "webhook_name": webhook_name,
                "current_requests": len(rate_info['requests']),
                "limit": rate_info['limit'],
                "window_seconds": rate_info['window']
            }, "DiscordNotifier")

        return can_send

    def _record_successful_request(self, webhook_name: str):
        """
        Record successful webhook request for rate limiting tracking.

        This private method adds the current timestamp to the rate limiting
        tracker when a webhook request succeeds. This ensures accurate
        rate limit calculations for future requests.

        Args:
            webhook_name (str): Name of webhook that succeeded

        Example:
            ```python
            # Called automatically after successful webhook
            self._record_successful_request("movies")
            ```
        """
        current_time = time.time()
        self.rate_limiter[webhook_name]['requests'].append(current_time)

        if self.debug_enabled:
            _debug_log("Recorded successful webhook request", {
                "webhook_name": webhook_name,
                "timestamp": current_time,
                "total_recent_requests": len(self.rate_limiter[webhook_name]['requests'])
            }, "DiscordNotifier")

    async def send_server_status(self, status: str, message: str) -> bool:
        """
        Send server status notification to Discord (connection issues, maintenance, etc.).

        This method provides a way to send administrative notifications about
        service status, server connectivity, or maintenance events. It uses
        a simple embed format optimized for status updates.

        **Status Types:**
        - "online": Server connection restored
        - "offline": Server connection lost
        - "maintenance": Scheduled maintenance
        - "error": Service errors or issues

        Args:
            status (str): Status type (online, offline, maintenance, error)
            message (str): Detailed status message

        Returns:
            bool: True if status notification was sent successfully

        Example:
            ```python
            # Send connection restored notification
            await discord_notifier.send_server_status(
                "online",
                "Connection to Jellyfin server restored"
            )

            # Send maintenance notification
            await discord_notifier.send_server_status(
                "maintenance",
                "Starting scheduled database maintenance"
            )
            ```

        Note:
            Status notifications are sent to the default webhook only,
            regardless of routing configuration. This ensures important
            administrative messages are always delivered.
        """
        try:
            # Only send to default webhook for status messages
            if ("default" not in self.config.webhooks or
                    not self.config.webhooks["default"].enabled or
                    not self.config.webhooks["default"].url):
                self.logger.warning("No default webhook configured for status notifications")
                return False

            # Create simple status embed
            status_colors = {
                "online": 0x00ff00,  # Green
                "offline": 0xff0000,  # Red
                "maintenance": 0xffff00,  # Yellow
                "error": 0xff0000  # Red
            }

            embed_data = {
                "embeds": [{
                    "title": f"🤖 Jellynouncer Status: {status.title()}",
                    "description": message,
                    "color": status_colors.get(status, 0x999999),  # Default gray
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {
                        "text": "Jellynouncer Service"
                    }
                }]
            }

            # Send via default webhook
            success = await self._send_webhook("default", self.config.webhooks["default"], embed_data)

            if self.debug_enabled:
                _debug_log("Server status notification sent", {
                    "status": status,
                    "message": message,
                    "success": success
                }, "DiscordNotifier")

            return success

        except Exception as e:
            self.logger.error(f"Failed to send server status notification: {e}")
            if self.debug_enabled:
                _debug_log("❌ Server status notification failed", {
                    "status": status,
                    "error": str(e),
                    "error_type": type(e).__name__
                }, "DiscordNotifier")
            return False

    async def close(self):
        """
        Clean up Discord notifier resources including HTTP sessions and caches.

        This method performs proper cleanup of resources when the Discord
        notifier is no longer needed. It closes HTTP sessions (if owned by
        this instance) and clears caches.

        **Resource Management:**
        The notifier only closes HTTP sessions that it created itself.
        Sessions provided during initialization are left open for the
        parent component to manage.

        Example:
            ```python
            # During application shutdown
            await discord_notifier.close()
            ```

        Note:
            This method should be called during application shutdown to
            ensure proper resource cleanup and avoid resource leaks.
        """
        if self.debug_enabled:
            _debug_log("Closing Discord notifier", {
                "owns_session": self._owns_session,
                "session_closed": self.session.closed if hasattr(self.session, 'closed') else 'unknown'
            }, "DiscordNotifier")

        try:
            # Clear thumbnail verification cache
            self.thumbnail_manager.clear_cache()

            # Close HTTP session if we created it
            if self._owns_session and self.session and not self.session.closed:
                await self.session.close()
                if self.debug_enabled:
                    _debug_log("✅ HTTP session closed", {}, "DiscordNotifier")

        except Exception as e:
            self.logger.error(f"Error during Discord notifier cleanup: {e}")
            if self.debug_enabled:
                _debug_log("❌ Error during cleanup", {
                    "error": str(e),
                    "error_type": type(e).__name__
                }, "DiscordNotifier")

        if self.debug_enabled:
            _debug_log("Discord notifier cleanup completed", {}, "DiscordNotifier")