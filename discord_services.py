#!/usr/bin/env python3
"""
Discord Services Module

This module contains the Discord-related services including notification management
and thumbnail verification. These services are tightly coupled and work together
to provide rich Discord notifications with verified media thumbnails.

Classes:
    ThumbnailManager: Manages thumbnail URL generation and verification
    DiscordNotifier: Handles Discord webhook notifications with multi-webhook support
"""

import asyncio
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse
from dataclasses import asdict

import aiohttp
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError

from config_models import DiscordConfig, TemplatesConfig
from media_models import MediaItem


class ThumbnailManager:
    """
    Manages thumbnail URL generation and verification for Discord notifications.

    This class implements a comprehensive thumbnail fallback system that:
    1. Generates appropriate thumbnail URLs based on media type
    2. Verifies thumbnail URLs are accessible before sending to Discord
    3. Implements fallback strategies when primary thumbnails fail
    4. Caches verification results for performance

    Fallback Strategy:
    - Episodes: Episode image → Season image → Series image → Default
    - Series: Series image → Default
    - Movies: Movie image → Default
    - Other: Item image → Default
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

    async def get_verified_thumbnail_url(self, item: MediaItem) -> Optional[str]:
        """
        Get verified thumbnail URL for a media item with fallback strategy.

        Args:
            item: MediaItem to get thumbnail for

        Returns:
            Verified thumbnail URL or None if no valid thumbnail found
        """
        # Generate thumbnail URL candidates based on item type
        thumbnail_candidates = self._generate_thumbnail_candidates(item)

        # Test each candidate URL
        for candidate in thumbnail_candidates:
            if await self._verify_thumbnail_url(candidate):
                self.logger.debug(f"Using verified thumbnail: {candidate}")
                return candidate

        self.logger.warning(f"No valid thumbnail found for {item.name} (ID: {item.item_id})")
        return None

    def _generate_thumbnail_candidates(self, item: MediaItem) -> List[str]:
        """
        Generate list of thumbnail URL candidates based on media type and fallback strategy.

        Args:
            item: MediaItem to generate URLs for

        Returns:
            List of thumbnail URLs to try, in order of preference
        """
        candidates = []

        if item.item_type == "Episode":
            # Episode fallback: Episode → Season → Series

            # 1. Episode primary image
            candidates.append(
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?maxHeight=400&maxWidth=300"
            )

            # 2. Episode thumb image (alternative)
            candidates.append(
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Thumb?maxHeight=300&maxWidth=500"
            )

            # 3. Season primary image (if season info available)
            if item.parent_id:  # parent_id is season_id for episodes
                candidates.append(
                    f"{self.jellyfin_url}/Items/{item.parent_id}/Images/Primary?maxHeight=400&maxWidth=300"
                )
                candidates.append(
                    f"{self.jellyfin_url}/Items/{item.parent_id}/Images/Thumb?maxHeight=300&maxWidth=500"
                )

            # 4. Series primary image (if series info available)
            if item.series_id:
                candidates.append(
                    f"{self.jellyfin_url}/Items/{item.series_id}/Images/Primary?maxHeight=400&maxWidth=300"
                )
                candidates.append(
                    f"{self.jellyfin_url}/Items/{item.series_id}/Images/Backdrop?maxHeight=200&maxWidth=400"
                )

        elif item.item_type == "Series":
            # Series fallback: Series primary → Series backdrop
            candidates.extend([
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?maxHeight=400&maxWidth=300",
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Backdrop?maxHeight=200&maxWidth=400",
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Banner?maxHeight=150&maxWidth=500"
            ])

        elif item.item_type == "Movie":
            # Movie fallback: Movie primary → Movie backdrop
            candidates.extend([
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?maxHeight=400&maxWidth=300",
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Backdrop?maxHeight=200&maxWidth=400"
            ])

        elif item.item_type == "Season":
            # Season fallback: Season primary → Series primary
            candidates.append(
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?maxHeight=400&maxWidth=300"
            )
            if item.series_id:
                candidates.append(
                    f"{self.jellyfin_url}/Items/{item.series_id}/Images/Primary?maxHeight=400&maxWidth=300"
                )

        else:
            # Generic fallback for other item types
            candidates.extend([
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Primary?maxHeight=400&maxWidth=300",
                f"{self.jellyfin_url}/Items/{item.item_id}/Images/Thumb?maxHeight=300&maxWidth=500"
            ])

        # Add default Jellyfin logo as final fallback
        candidates.append(f"{self.jellyfin_url}/web/assets/img/banner-light.png")

        return candidates

    async def _verify_thumbnail_url(self, url: str) -> bool:
        """
        Verify that a thumbnail URL is accessible and returns a valid image.

        Args:
            url: Thumbnail URL to verify

        Returns:
            True if URL is valid and accessible, False otherwise
        """
        # Check cache first
        current_time = time.time()
        if url in self.verification_cache:
            is_valid, timestamp = self.verification_cache[url]
            if current_time - timestamp < self.cache_duration:
                self.logger.debug(f"Using cached verification result for {url}: {is_valid}")
                return is_valid

        try:
            # Make HEAD request to check if image exists without downloading full content
            async with self.session.head(url, timeout=5) as response:
                is_valid = (
                        response.status == 200 and
                        response.headers.get('content-type', '').startswith('image/')
                )

                # Cache the result
                self.verification_cache[url] = (is_valid, current_time)

                if is_valid:
                    self.logger.debug(f"Thumbnail URL verified: {url}")
                else:
                    self.logger.debug(f"Thumbnail URL invalid (status: {response.status}, "
                                      f"content-type: {response.headers.get('content-type')}): {url}")

                return is_valid

        except asyncio.TimeoutError:
            self.logger.debug(f"Thumbnail URL verification timeout: {url}")
            # Cache negative result for failed verifications
            self.verification_cache[url] = (False, current_time)
            return False
        except Exception as e:
            self.logger.debug(f"Thumbnail URL verification error for {url}: {e}")
            # Cache negative result for failed verifications
            self.verification_cache[url] = (False, current_time)
            return False

    def clear_cache(self):
        """Clear the verification cache."""
        self.verification_cache.clear()
        self.logger.debug("Thumbnail verification cache cleared")


class DiscordNotifier:
    """
    Enhanced Discord webhook notifier with multi-webhook support and rate limiting.

    This class manages Discord webhook notifications with advanced features:
    - Multiple webhook support for different content types
    - Intelligent routing based on media type
    - Rate limiting to respect Discord's API limits
    - Template-based message formatting using Jinja2
    - Retry logic with exponential backoff
    - Server status notifications

    The notifier uses Jinja2 templates to create rich Discord embeds that provide
    detailed information about media additions and upgrades in an attractive format.
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

        # Thumbnail manager (initialized later)
        self.thumbnail_manager = None

        # Reference to webhook service for cross-component access
        self._webhook_service = None

    async def initialize(self, templates_config: TemplatesConfig, session: aiohttp.ClientSession) -> None:
        """Initialize HTTP session, Jinja2 templates, and thumbnail manager."""
        try:
            # Store the provided session
            self.session = session

            # Initialize Jinja2 template environment
            self.template_env = Environment(
                loader=FileSystemLoader(templates_config.directory),
                autoescape=True,
                enable_async=False
            )

            # Validate required templates exist
            required_templates = [
                templates_config.new_item_template,
                templates_config.upgraded_item_template
            ]

            for template_name in required_templates:
                try:
                    self.template_env.get_template(template_name)
                except TemplateNotFound:
                    self.logger.error(f"Required template not found: {template_name}")
                    raise
                except TemplateSyntaxError as e:
                    self.logger.error(f"Template syntax error in {template_name}: {e}")
                    raise

            # Initialize thumbnail manager
            self.thumbnail_manager = ThumbnailManager(
                jellyfin_url=self.jellyfin_url,
                session=self.session,
                logger=self.logger
            )

            self.logger.info("Discord notifier initialized successfully with thumbnail verification")

        except Exception as e:
            self.logger.error(f"Failed to initialize Discord notifier: {e}")
            raise

    def _get_webhook_for_item(self, item: MediaItem) -> Optional[Dict[str, Any]]:
        """
        Determine which webhook to use for a specific media item.

        This method implements the routing logic that directs different types
        of content to appropriate Discord channels/webhooks.

        Args:
            item: Media item to find webhook for

        Returns:
            Dictionary with webhook name and config, or None if no webhook available
        """
        if not self.routing_enabled:
            # Simple mode: use first enabled webhook
            for webhook_name, webhook_config in self.webhooks.items():
                if webhook_config.enabled and webhook_config.url:
                    return {
                        'name': webhook_name,
                        'config': webhook_config
                    }
            return None

        # Smart routing mode: route based on content type
        item_type = item.item_type
        movie_types = self.routing_config.get('movie_types', ['Movie'])
        tv_types = self.routing_config.get('tv_types', ['Episode', 'Season', 'Series'])
        music_types = self.routing_config.get('music_types', ['Audio', 'MusicAlbum', 'MusicArtist'])
        fallback_webhook = self.routing_config.get('fallback_webhook', 'default')

        # Determine target webhook based on content type
        target_webhook = None
        if item_type in movie_types:
            target_webhook = 'movies'
        elif item_type in tv_types:
            target_webhook = 'tv'
        elif item_type in music_types:
            target_webhook = 'music'
        else:
            target_webhook = fallback_webhook

        # Check if target webhook is available
        if (target_webhook in self.webhooks and
                self.webhooks[target_webhook].enabled and
                self.webhooks[target_webhook].url):
            return {
                'name': target_webhook,
                'config': self.webhooks[target_webhook]
            }

        # Fall back to configured fallback webhook
        if (fallback_webhook in self.webhooks and
                self.webhooks[fallback_webhook].enabled and
                self.webhooks[fallback_webhook].url):
            self.logger.debug(f"Target webhook '{target_webhook}' not available, using fallback '{fallback_webhook}'")
            return {
                'name': fallback_webhook,
                'config': self.webhooks[fallback_webhook]
            }

        # Last resort: use any enabled webhook
        for webhook_name, webhook_config in self.webhooks.items():
            if webhook_config.enabled and webhook_config.url:
                self.logger.debug(f"Using '{webhook_name}' as last resort webhook")
                return {
                    'name': webhook_name,
                    'config': webhook_config
                }

        return None

    async def _wait_for_rate_limit(self, webhook_name: str) -> None:
        """
        Implement rate limiting to respect Discord's API limits.

        Discord has strict rate limits on webhook calls. This method ensures
        we don't exceed those limits by tracking request frequency per webhook
        and introducing delays when necessary.

        Args:
            webhook_name: Name of webhook to check rate limit for
        """
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
                self.logger.debug(f"Rate limiting webhook '{webhook_name}', waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                rate_limit_info['request_count'] = 0

        # Update rate limit tracking
        rate_limit_info['last_request_time'] = time.time()
        rate_limit_info['request_count'] += 1

    async def send_notification(self, item: MediaItem, changes: Optional[List[Dict[str, Any]]] = None,
                                is_new: bool = True) -> bool:
        """Enhanced notification sending with verified thumbnails."""
        try:
            # Find webhook
            webhook_info = self._get_webhook_for_item(item)
            if not webhook_info:
                self.logger.warning("No suitable Discord webhook found")
                return False

            webhook_name = webhook_info['name']
            webhook_config = webhook_info['config']
            webhook_url = webhook_config.url

            # Rate limiting
            await self._wait_for_rate_limit(webhook_name)

            # Get verified thumbnail URL
            thumbnail_url = await self.thumbnail_manager.get_verified_thumbnail_url(item)

            # Template selection
            if is_new:
                template_name = 'new_item.j2'
                color = 0x00FF00
            else:
                template_name = 'upgraded_item.j2'
                color = self._get_change_color(changes)

            # Load template
            try:
                template = self.template_env.get_template(template_name)
            except (TemplateNotFound, TemplateSyntaxError) as e:
                self.logger.error(f"Template error {template_name}: {e}")
                return False

            # Get ratings with TVDB URL information
            ratings = {}
            if self._webhook_service and self._webhook_service.rating_service:
                try:
                    ratings = await self._webhook_service.rating_service.get_ratings_for_item(item)
                    if ratings:
                        self.logger.debug(f"Retrieved {len(ratings)} rating sources for {item.name}")
                except Exception as e:
                    self.logger.warning(f"Failed to fetch ratings for {item.name}: {e}")

            # Extract TVDB URL information
            tvdb_url_info = {}
            if 'tvdb' in ratings and 'proper_url' in ratings['tvdb']:
                tvdb_url_info = {
                    'has_proper_url': True,
                    'proper_url': ratings['tvdb']['proper_url'],
                    'series_slug': ratings['tvdb'].get('series_slug'),
                    'episode_id': ratings['tvdb'].get('episode_id')
                }
                self.logger.debug(f"Using proper TVDB URL for {item.name}: {ratings['tvdb']['proper_url']}")

            # Check if TVDB ratings are included and add attribution flag
            tvdb_attribution_needed = False
            if ratings:
                for rating_key, rating_data in ratings.items():
                    if rating_key == 'tvdb' and rating_data.get('attribution_required'):
                        tvdb_attribution_needed = True
                        break

            # Template data (with all the information we've gathered)
            template_data = {
                'item': asdict(item),
                'changes': changes or [],
                'is_new': is_new,
                'color': color,
                'jellyfin_url': self.jellyfin_url,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'webhook_name': webhook_config.name,
                'webhook_target': webhook_name,
                'ratings': ratings,
                'tvdb_url_info': tvdb_url_info,
                'tvdb_attribution_needed': tvdb_attribution_needed,
                'verified_thumbnail_url': thumbnail_url,
                'has_thumbnail': thumbnail_url is not None
            }

            # Render template
            try:
                rendered = template.render(**template_data)
                payload = json.loads(rendered)
            except Exception as e:
                self.logger.error(f"Template rendering error {template_name}: {e}")
                return False

            # Send to Discord with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    async with self.session.post(webhook_url, json=payload) as response:
                        if response.status == 204:
                            self.logger.info(
                                f"Successfully sent notification for {item.name} to '{webhook_name}' webhook"
                                f"{' with thumbnail' if thumbnail_url else ' (no thumbnail)'}")
                            return True
                        elif response.status == 429:
                            retry_after = int(response.headers.get('Retry-After', '60'))
                            self.logger.warning(f"Rate limited, retry after {retry_after}s")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            error_text = await response.text()
                            self.logger.error(f"Discord error {response.status}: {error_text}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2 ** attempt)
                                continue
                            return False

                except aiohttp.ClientError as e:
                    self.logger.error(f"Network error: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return False

            return False

        except Exception as e:
            self.logger.error(f"Critical error in send_notification: {e}")
            return False

    def _get_change_color(self, changes: List[Dict[str, Any]]) -> int:
        """
        Determine Discord embed color based on the types of changes detected.

        Different types of upgrades get different colors to provide visual
        distinction in Discord. Colors are prioritized by significance.

        Args:
            changes: List of change dictionaries

        Returns:
            Integer color value for Discord embed
        """
        try:
            colors = self.config.notifications.colors

            if not changes:
                return colors.get('new_item', 0x00FF00)

            # Extract change types for priority determination
            change_types = [change['type'] for change in changes if isinstance(change, dict) and 'type' in change]

            # Priority order: resolution > codec > HDR > audio > provider IDs
            if 'resolution' in change_types:
                return colors.get('resolution_upgrade', 0xFFD700)  # Gold
            elif 'codec' in change_types:
                return colors.get('codec_upgrade', 0xFF8C00)  # Dark orange
            elif 'hdr_status' in change_types:
                return colors.get('hdr_upgrade', 0xFF1493)  # Deep pink
            elif any(t in change_types for t in ['audio_codec', 'audio_channels']):
                return colors.get('audio_upgrade', 0x9370DB)  # Medium purple
            elif 'provider_ids' in change_types:
                return colors.get('provider_update', 0x1E90FF)  # Dodger blue
            else:
                return colors.get('new_item', 0x00FF00)  # Green fallback

        except Exception as e:
            self.logger.error(f"Error determining change color: {e}")
            return 0x00FF00  # Default to green

    async def send_server_status(self, is_online: bool) -> bool:
        """
        Send server status notification to all enabled webhooks.

        This method sends notifications when the Jellyfin server goes offline
        or comes back online, helping administrators monitor service health.

        Args:
            is_online: True if server is online, False if offline

        Returns:
            True if at least one notification was sent successfully
        """
        if not self.template_env:
            self.logger.error("Template environment not initialized")
            return False

        try:
            # Load server status template
            try:
                template = self.template_env.get_template('server_status.j2')
            except TemplateNotFound:
                self.logger.error("Server status template not found: server_status.j2")
                return False
            except TemplateSyntaxError as e:
                self.logger.error(f"Server status template syntax error: {e}")
                return False

            # Prepare template data
            template_data = {
                'is_online': is_online,
                'jellyfin_url': self.jellyfin_url,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

            # Render template
            try:
                rendered = template.render(**template_data)
                payload = json.loads(rendered)
            except Exception as e:
                self.logger.error(f"Error rendering server status template: {e}")
                return False

            # Send to all enabled webhooks
            success_count = 0
            total_webhooks = 0

            for webhook_name, webhook_config in self.webhooks.items():
                if not webhook_config.enabled or not webhook_config.url:
                    continue

                total_webhooks += 1

                try:
                    await self._wait_for_rate_limit(webhook_name)

                    async with self.session.post(webhook_config.url, json=payload) as response:
                        if response.status == 204:
                            success_count += 1
                            self.logger.debug(f"Server status sent to '{webhook_name}' webhook")
                        elif response.status == 429:
                            # Handle Discord rate limiting
                            retry_after = int(response.headers.get('Retry-After', '60'))
                            self.logger.warning(
                                f"Server status rate limited for '{webhook_name}': retry after {retry_after}s")
                            await asyncio.sleep(retry_after)
                            # Retry once
                            async with self.session.post(webhook_config.url, json=payload) as retry_response:
                                if retry_response.status == 204:
                                    success_count += 1
                                    self.logger.debug(f"Server status sent to '{webhook_name}' webhook (retry)")
                                else:
                                    self.logger.warning(
                                        f"Server status retry failed for '{webhook_name}': {retry_response.status}")
                        else:
                            error_text = await response.text()
                            self.logger.warning(
                                f"Server status failed for '{webhook_name}' webhook: {response.status} - {error_text}")

                except aiohttp.ClientError as e:
                    self.logger.error(f"Network error sending server status to '{webhook_name}': {e}")
                except Exception as e:
                    self.logger.error(f"Unexpected error sending server status to '{webhook_name}': {e}")

            self.logger.info(f"Server status notification sent to {success_count}/{total_webhooks} webhooks")
            return success_count > 0

        except Exception as e:
            self.logger.error(f"Critical error in send_server_status: {e}")
            return False

    def get_webhook_status(self) -> Dict[str, Any]:
        """
        Get status information for all configured webhooks.

        This method provides diagnostic information about webhook configuration
        and current state, useful for monitoring and troubleshooting.

        Returns:
            Dictionary containing webhook status information
        """
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

                    status["webhooks"][webhook_name] = {
                        "name": webhook_config.name,
                        "enabled": webhook_config.enabled,
                        "has_url": bool(webhook_config.url),
                        "url_preview": url_preview,
                        "grouping": webhook_config.grouping,
                        "rate_limit_info": self.webhook_rate_limits.get(webhook_name, {})
                    }

                except Exception as e:
                    self.logger.warning(f"Error processing webhook '{webhook_name}' status: {e}")
                    status["webhooks"][webhook_name] = {
                        "name": webhook_name,
                        "enabled": False,
                        "has_url": False,
                        "url_preview": None,
                        "grouping": {},
                        "error": str(e)
                    }

            return status

        except Exception as e:
            self.logger.error(f"Error getting webhook status: {e}")
            return {
                "error": "Failed to get webhook status",
                "routing_enabled": False,
                "webhooks": {}
            }