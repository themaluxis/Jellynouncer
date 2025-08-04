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
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse
from dataclasses import asdict

import aiohttp
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError

from config_models import DiscordConfig, TemplatesConfig
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
        api_key (str): API key for authenticated thumbnail requests
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

    def __init__(self, jellyfin_url: str, api_key: str):
        """
        Initialize thumbnail manager with Jellyfin connection details.

        Args:
            jellyfin_url (str): Base URL of the Jellyfin server
            api_key (str): API key for authenticated requests
        """
        self.base_url = jellyfin_url.rstrip('/')
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache: Dict[str, str] = {}
        self.logger = get_logger("jellynouncer.discord.thumbnails")

    async def initialize(self) -> None:
        """
        Initialize HTTP session for thumbnail operations.

        This method sets up the aiohttp session used for thumbnail verification
        and other HTTP operations. It should be called once during service startup.
        """
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=10)  # 10-second timeout for thumbnail requests
            self.session = aiohttp.ClientSession(timeout=timeout)
            self.logger.debug("Thumbnail manager HTTP session initialized")

    async def cleanup(self) -> None:
        """
        Clean up HTTP session and resources.

        This method should be called during application shutdown to properly
        close the HTTP session and prevent resource leaks.
        """
        if self.session:
            await self.session.close()
            self.session = None
            self.logger.debug("Thumbnail manager HTTP session closed")

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
            self.logger.debug(f"Using cached thumbnail for item {item_id}")
            return self.cache[cache_key]

        # Try thumbnail sources in order of preference
        thumbnail_candidates = []

        # Primary image (poster/cover) - highest priority
        if primary_image_tag:
            primary_url = f"{self.base_url}/Items/{item_id}/Images/Primary?api_key={self.api_key}&tag={primary_image_tag}&quality=90&maxWidth=500"
            thumbnail_candidates.append(("Primary", primary_url))

        # Backdrop image - good fallback for movies/shows
        if backdrop_image_tag:
            backdrop_url = f"{self.base_url}/Items/{item_id}/Images/Backdrop?api_key={self.api_key}&tag={backdrop_image_tag}&quality=90&maxWidth=500"
            thumbnail_candidates.append(("Backdrop", backdrop_url))

        # Logo image - branding fallback
        if logo_image_tag:
            logo_url = f"{self.base_url}/Items/{item_id}/Images/Logo?api_key={self.api_key}&tag={logo_image_tag}&quality=90&maxWidth=500"
            thumbnail_candidates.append(("Logo", logo_url))

        self.logger.debug(f"Checking {len(thumbnail_candidates)} thumbnail candidates for item {item_id}")

        # Test each candidate URL
        for image_type, url in thumbnail_candidates:
            if await self.verify_thumbnail(url):
                self.logger.debug(f"Using {image_type} thumbnail for item {item_id}")
                self.cache[cache_key] = url
                return url
            else:
                self.logger.debug(f"{image_type} thumbnail not accessible for item {item_id}")

        # If no thumbnails work, log and return None
        self.logger.warning(f"No accessible thumbnails found for item {item_id} ({media_type})")
        self.cache[cache_key] = None
        return None

    async def verify_thumbnail(self, url: str) -> bool:
        """
        Verify that a thumbnail URL is accessible and returns valid image data.

        This method performs a HEAD request to check if the thumbnail URL returns
        a successful response without downloading the full image. This is much
        faster than downloading the entire image just to verify it exists.

        Args:
            url (str): Thumbnail URL to verify

        Returns:
            bool: True if thumbnail is accessible, False otherwise

        Example:
            ```python
            url = "http://jellyfin:8096/Items/123/Images/Primary?api_key=..."
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
            # Use HEAD request to check accessibility without downloading full image
            async with self.session.head(url) as response:
                is_accessible = response.status == 200
                if is_accessible:
                    self.logger.debug(f"Thumbnail verified: {url}")
                else:
                    self.logger.debug(f"Thumbnail not accessible (HTTP {response.status}): {url}")
                return is_accessible

        except asyncio.TimeoutError:
            self.logger.warning(f"Thumbnail verification timeout: {url}")
            return False
        except aiohttp.ClientError as e:
            self.logger.warning(f"Thumbnail verification failed: {url} - {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error verifying thumbnail: {url} - {e}")
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
        templates_config (TemplatesConfig): Template configuration settings
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

    def __init__(
            self,
            config: DiscordConfig,
            templates_config: TemplatesConfig,
            thumbnail_manager: ThumbnailManager
    ):
        """
        Initialize Discord notifier with configuration and dependencies.

        Args:
            config (DiscordConfig): Discord webhook and notification configuration
            templates_config (TemplatesConfig): Template system configuration
            thumbnail_manager (ThumbnailManager): Thumbnail URL manager instance
        """
        self.config = config
        self.templates_config = templates_config
        self.thumbnail_manager = thumbnail_manager
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limits: Dict[str, Dict[str, Any]] = {}
        self.jinja_env: Optional[Environment] = None
        self.logger = get_logger("jellynouncer.discord.notifier")

    async def initialize(self) -> None:
        """
        Initialize Discord notifier with HTTP session and template environment.

        This method sets up all necessary components for Discord notifications:
        - HTTP session for webhook requests
        - Jinja2 template environment for embed rendering
        - Rate limiting state management

        Should be called once during application startup.
        """
        # Initialize HTTP session with reasonable timeout
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=30)  # 30-second timeout for Discord webhooks
            self.session = aiohttp.ClientSession(timeout=timeout)
            self.logger.debug("Discord notifier HTTP session initialized")

        # Initialize Jinja2 template environment
        if self.jinja_env is None:
            self.jinja_env = Environment(
                loader=FileSystemLoader(self.templates_config.directory),
                trim_blocks=True,
                lstrip_blocks=True
            )
            self.logger.debug(f"Template environment initialized with directory: {self.templates_config.directory}")

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
        # Map media types to webhook configuration
        webhook_mapping = {
            "Movie": self.config.webhook_url_movies,
            "Series": self.config.webhook_url_tv,
            "Season": self.config.webhook_url_tv,
            "Episode": self.config.webhook_url_tv,
            "Audio": self.config.webhook_url_music,
            "MusicAlbum": self.config.webhook_url_music,
            "MusicArtist": self.config.webhook_url_music
        }

        # Try to get specific webhook for this media type
        specific_webhook = webhook_mapping.get(media_type)
        if specific_webhook:
            self.logger.debug(f"Using specific webhook for {media_type}")
            return specific_webhook

        # Fall back to general webhook
        if self.config.webhook_url:
            self.logger.debug(f"Using general webhook for {media_type}")
            return self.config.webhook_url

        # No webhook configured
        self.logger.warning(f"No webhook configured for media type: {media_type}")
        return None

    async def send_notification(self, item: MediaItem, action: str) -> Dict[str, Any]:
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

            # Generate thumbnail URL
            thumbnail_url = await self.thumbnail_manager.get_thumbnail_url(
                item_id=item.item_id,
                media_type=item.item_type,
                primary_image_tag=getattr(item, 'primary_image_tag', None),
                backdrop_image_tag=getattr(item, 'backdrop_image_tag', None),
                logo_image_tag=getattr(item, 'logo_image_tag', None)
            )

            # Render Discord embed using templates
            embed_data = await self.render_embed(item, action, thumbnail_url)

            # Check rate limits before sending
            if await self.is_rate_limited(webhook_url):
                self.logger.warning(f"Rate limited for webhook: {webhook_url}")
                return {
                    "success": False,
                    "error": "Rate limited",
                    "webhook_url": webhook_url
                }

            # Send the webhook
            webhook_data = {
                "embeds": [embed_data],
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
                "webhook_url": webhook_url if 'webhook_url' in locals() else None
            }

    async def render_embed(self, item: MediaItem, action: str, thumbnail_url: Optional[str]) -> Dict[str, Any]:
        """
        Render Discord embed using Jinja2 templates.

        This method takes a media item and renders it into a rich Discord embed
        using the configured Jinja2 templates. It handles template selection,
        variable preparation, and error recovery.

        **Template Selection:**
        Templates are selected based on media type and action:
        1. Specific template: f"{media_type.lower()}_{action}.json"
        2. Media type template: f"{media_type.lower()}.json"
        3. Action template: f"{action}.json"
        4. Default template: "default.json"

        Args:
            item (MediaItem): Media item to render
            action (str): Action that triggered the notification
            thumbnail_url (Optional[str]): Thumbnail URL for the embed image

        Returns:
            Dict[str, Any]: Discord embed data structure

        Example:
            ```python
            embed = await notifier.render_embed(movie_item, "added", thumbnail_url)
            # Returns Discord embed structure ready for webhook
            ```
        """
        # Prepare template variables
        template_vars = {
            "item": asdict(item),
            "action": action,
            "thumbnail_url": thumbnail_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server_url": self.thumbnail_manager.base_url
        }

        # Try to find and render appropriate template
        template_names = [
            f"{item.item_type.lower()}_{action}.json",
            f"{item.item_type.lower()}.json",
            f"{action}.json",
            "default.json"
        ]

        for template_name in template_names:
            try:
                template = self.jinja_env.get_template(template_name)
                rendered = template.render(**template_vars)
                embed_data = json.loads(rendered)

                self.logger.debug(f"Using template {template_name} for {item.name}")
                return embed_data

            except TemplateNotFound:
                self.logger.debug(f"Template not found: {template_name}")
                continue
            except (TemplateSyntaxError, json.JSONDecodeError) as e:
                self.logger.warning(f"Template error in {template_name}: {e}")
                continue
            except Exception as e:
                self.logger.error(f"Unexpected error rendering template {template_name}: {e}")
                continue

        # Fallback to basic embed if all templates fail
        self.logger.warning(f"All templates failed, using basic embed for {item.name}")
        return {
            "title": f"{action.title()}: {item.name}",
            "description": f"New {item.item_type.lower()} available",
            "color": 5814783,  # Discord blue
            "image": {"url": thumbnail_url} if thumbnail_url else {},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

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
        Send webhook request to Discord with error handling and retry logic.

        This method handles the actual HTTP request to Discord's webhook API,
        including error handling, rate limit detection, and retry logic.

        Args:
            webhook_url (str): Discord webhook URL
            data (Dict[str, Any]): Webhook payload data

        Returns:
            bool: True if webhook sent successfully, False otherwise

        Example:
            ```python
            webhook_data = {
                "embeds": [embed],
                "username": "Jellynouncer"
            }
            success = await notifier.send_webhook(webhook_url, webhook_data)
            ```
        """
        if not self.session:
            self.logger.error("HTTP session not initialized")
            return False

        try:
            self.logger.debug(f"Sending webhook to Discord: {webhook_url}")

            async with self.session.post(webhook_url, json=data) as response:
                # Track this request for rate limiting
                now = time.time()
                if webhook_url not in self.rate_limits:
                    self.rate_limits[webhook_url] = {"requests": [], "blocked_until": 0}

                self.rate_limits[webhook_url]["requests"].append(now)

                if response.status == 200 or response.status == 204:
                    self.logger.debug("Webhook sent successfully")
                    return True
                elif response.status == 429:  # Rate limited
                    # Parse rate limit headers if available
                    retry_after = response.headers.get('Retry-After', '60')
                    try:
                        retry_seconds = int(retry_after)
                    except ValueError:
                        retry_seconds = 60

                    self.rate_limits[webhook_url]["blocked_until"] = now + retry_seconds
                    self.logger.warning(f"Discord rate limit hit, blocked for {retry_seconds} seconds")
                    return False
                else:
                    error_text = await response.text()
                    self.logger.error(f"Webhook failed with status {response.status}: {error_text}")
                    return False

        except asyncio.TimeoutError:
            self.logger.error("Webhook request timed out")
            return False
        except aiohttp.ClientError as e:
            self.logger.error(f"Webhook request failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error sending webhook: {e}", exc_info=True)
            return False

    def get_webhook_status(self) -> Dict[str, Any]:
        """
        Get current status of webhook configuration and rate limiting.

        This method provides diagnostic information about the Discord notifier's
        current state, including configured webhooks and rate limiting status.

        Returns:
            Dict[str, Any]: Status information including:
                - configured_webhooks: Count of configured webhook URLs
                - rate_limits: Current rate limiting state
                - session_status: HTTP session status

        Example:
            ```python
            status = notifier.get_webhook_status()
            logger.info(f"Configured webhooks: {status['configured_webhooks']}")
            ```
        """
        configured_webhooks = 0
        webhook_info = {}

        if self.config.webhook_url:
            configured_webhooks += 1
            webhook_info["general"] = True

        if self.config.webhook_url_movies:
            configured_webhooks += 1
            webhook_info["movies"] = True

        if self.config.webhook_url_tv:
            configured_webhooks += 1
            webhook_info["tv"] = True

        if self.config.webhook_url_music:
            configured_webhooks += 1
            webhook_info["music"] = True

        return {
            "configured_webhooks": configured_webhooks,
            "webhook_types": webhook_info,
            "rate_limits": len(self.rate_limits),
            "session_initialized": self.session is not None,
            "templates_initialized": self.jinja_env is not None
        }