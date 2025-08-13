#!/usr/bin/env python3
"""
Jellynouncer Jellyfin API Client

This module provides a comprehensive async interface to the Jellyfin media server API.
It handles authentication, connection management, item retrieval, and data conversion
with robust error handling and retry logic for production reliability.

The JellyfinAPI class serves as the primary interface between Jellynouncer and Jellyfin,
abstracting away the complexities of the Jellyfin API while providing efficient access
to media metadata and library information.

Classes:
    JellyfinAPI: Async Jellyfin API client with retry logic and connection management

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import asyncio
import time
import ssl
from datetime import datetime, timezone
import logging
from typing import Dict, Any, Optional, List, Callable, Union

from jellyfin_apiclient_python import JellyfinClient

from config_models import JellyfinConfig
from media_models import MediaItem
from utils import get_logger


class JellyfinAPI:
    """
    Async Jellyfin API client with comprehensive error handling and retry logic.

    This class provides a high-level interface to the Jellyfin media server API,
    handling authentication, connection management, and data retrieval. It's designed
    for production use with robust error handling and automatic retry capabilities.

    **Understanding Jellyfin API Authentication:**

    Jellyfin supports multiple authentication methods, but this client uses API keys
    for service-to-service communication. API keys provide several advantages:

    **API Key Benefits:**
    - No password exposure in configuration files
    - Can be easily revoked and regenerated
    - Limited scope and permissions for security
    - Perfect for automated service authentication

    **Connection Management:**
    The client maintains connection state and automatically reconnects when needed.
    Connection health is checked periodically to ensure reliability during long
    running operations like library synchronization.

    **Retry Logic:**
    Network operations can fail for many reasons (temporary network issues,
    server restarts, high load). This client implements intelligent retry
    with exponential backoff to handle temporary failures gracefully.

    Attributes:
        config (JellyfinConfig): Jellyfin server configuration settings
        logger (logging.Logger): Logger instance for API operations
        client (JellyfinClient): Official Jellyfin API client instance
        last_connection_check (float): Timestamp of last connection verification
        connection_check_interval (int): Seconds between connection health checks
        max_retries (int): Maximum connection attempts before giving up
        retry_delay (int): Base delay between connection retry attempts

    Example:
        ```python
        # Initialize and connect to Jellyfin
        jellyfin_config = JellyfinConfig(
            server_url="http://jellyfin:8096",
            api_key="your_api_key_here",
            user_id="your_user_id_here"
        )

        jellyfin_api = JellyfinAPI(jellyfin_config, logger)

        # Establish connection with retry logic
        if await jellyfin_api.connect():
            logger.info("Successfully connected to Jellyfin")

            # Get specific item details
            item_data = await jellyfin_api.get_item("item_id_123")
            if item_data:
                media_item = jellyfin_api.extract_media_item(item_data)
                logger.info(f"Retrieved: {media_item.name}")

            # Batch retrieve library items
            all_items = await jellyfin_api.get_all_items(batch_size=100)
            logger.info(f"Found {len(all_items)} items in library")
        else:
            logger.error("Failed to connect to Jellyfin server")
        ```

    Note:
        This class is designed to be long-lived and reused across multiple
        operations. The connection state is cached and periodically verified
        to minimize authentication overhead while ensuring reliability.
    """

    def __init__(self, config: JellyfinConfig):
        """
        Initialize Jellyfin API client with configuration and logging.

        Sets up the client with configuration parameters and initializes
        connection tracking variables. The actual connection is established
        separately via the connect() method.

        **Constructor Pattern:**
        This follows the pattern of lightweight construction with separate
        initialization. The constructor sets up the object state without
        performing expensive operations like network connections.

        Args:
            config (JellyfinConfig): Jellyfin server configuration including
                server URL, API key, and user ID

        Example:
            ```python
            config = JellyfinConfig(
                server_url="http://jellyfin:8096",
                api_key="your_api_key",
                user_id="your_user_id"
            )

            jellyfin_api = JellyfinAPI(config)
            logger.info(f"Jellyfin API client created for {config.server_url}")
            ```
        """
        self.config = config
        self.logger = get_logger("jellynouncer.jellyfin")
        self.client = None

        # Cache for server information
        self._cached_server_info = None

        # Connection management
        self.last_connection_check = 0
        self.connection_check_interval = 300  # 5 minutes
        self.max_retries = 3
        self.retry_delay = 2  # seconds

        self.logger.info(f"Jellyfin API client initialized for {config.server_url}")

    async def connect(self) -> bool:
        """
        Establish connection to Jellyfin server with retry logic.

        This method attempts to connect to the Jellyfin server using the
        configured API key and user ID. It implements retry logic with
        exponential backoff to handle temporary network issues.

        **Connection Process:**
        1. Create Jellyfin client instance
        2. Configure server URL and authentication
        3. Verify connection with test API call
        4. Log connection status and server information

        **Retry Strategy:**
        - Exponential backoff: 2s, 4s, 8s between retries
        - Maximum 3 connection attempts
        - Different error messages for different failure types

        Returns:
            bool: True if connection successful, False otherwise

        Example:
            ```python
            if await jellyfin_api.connect():
                logger.info("Ready to make API calls")
            else:
                logger.error("Cannot proceed without Jellyfin connection")
                return False
            ```

        Note:
            This method should be called once during service initialization.
            The connection is reused for all subsequent API calls.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                self.logger.debug(f"Jellyfin connection attempt {attempt}/{self.max_retries}")

                # Create and configure Jellyfin client
                self.client = JellyfinClient()
                self.client.config.app("Jellynouncer", "2.0.0", "jellynouncer", "1.0.0")

                # Auto-enable SSL verification for HTTPS URLs
                if self.config.server_url.lower().startswith('https://'):
                    self.client.config.data["auth.ssl"] = True
                    self.logger.debug("SSL verification enabled for HTTPS connection")
                else:
                    self.client.config.data["auth.ssl"] = False
                    self.logger.debug("SSL verification disabled for HTTP connection")

                # Set server and authentication
                self.client.config.data['auth.server'] = self.config.server_url
                self.client.config.data['auth.server-name'] = "Jellyfin Server"
                self.client.config.data['auth.user_id'] = self.config.user_id
                self.client.config.data['auth.token'] = self.config.api_key

                # Test connection with system info call
                system_info = await self.get_system_info()
                if system_info:
                    server_name = system_info.get('ServerName', 'Unknown')
                    server_version = system_info.get('Version', 'Unknown')

                    self.logger.info(f"Connected to Jellyfin server: {server_name} v{server_version}")
                    self.last_connection_check = time.time()
                    return True
                else:
                    raise Exception("Failed to retrieve system information")

            except ssl.SSLError as e:
                self.logger.error(f"SSL certificate verification failed: {e}")
                self.logger.error("Please ensure your Jellyfin server has a valid SSL certificate")

                if attempt == self.max_retries:
                    raise ConnectionError(f"SSL verification failed: {e}")

            except Exception as e:
                self.logger.warning(f"Connection attempt {attempt} failed: {e}")

                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))  # Exponential backoff
                    self.logger.debug(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    self.logger.error(f"Failed to connect to Jellyfin after {self.max_retries} attempts")

        return False

    async def is_connected(self) -> bool:
        """
        Check if client is connected to Jellyfin server.

        This method verifies the connection status and optionally performs
        a health check if enough time has passed since the last check.
        It's designed to be lightweight for frequent calls.

        **Connection Verification Strategy:**
        - Cached result for recent checks (within 5 minutes)
        - Periodic health check with system info API call
        - Automatic reconnection on connection loss

        Returns:
            bool: True if connected and healthy, False otherwise

        Example:
            ```python
            if await jellyfin_api.is_connected():
                # Safe to make API calls
                items = await jellyfin_api.get_all_items()
            else:
                logger.warning("Jellyfin connection lost - attempting reconnect")
                await jellyfin_api.connect()
            ```

        Note:
            This method is called frequently, so it uses caching to minimize
            API calls while still ensuring connection reliability.
        """
        if not self.client:
            return False

        # Check if we need to verify connection health
        time_since_check = time.time() - self.last_connection_check

        if time_since_check > self.connection_check_interval:
            try:
                # Perform lightweight health check
                system_info = await self.get_system_info()
                if system_info:
                    self.last_connection_check = time.time()
                    self.logger.debug("Connection health check passed")
                    return True
                else:
                    self.logger.warning("Connection health check failed")
                    return False
            except Exception as e:
                self.logger.warning(f"Connection health check error: {e}")
                return False
        else:
            # Use cached connection status
            return True

    async def get_system_info(self) -> Optional[Dict[str, Any]]:
        """
        Get Jellyfin server system information.

        This method retrieves basic system information from the Jellyfin server,
        including server name, version, and operational status. It's used for
        connection verification and diagnostic purposes.

        **System Information Uses:**
        - Connection health verification
        - Server identification in logs
        - Version compatibility checking
        - Diagnostic and monitoring data

        Returns:
            Optional[Dict[str, Any]]: System information dictionary if successful, None otherwise

        Example:
            ```python
            system_info = await jellyfin_api.get_system_info()
            if system_info:
                logger.info(f"Server: {system_info['ServerName']} v{system_info['Version']}")
                logger.info(f"Operating System: {system_info.get('OperatingSystem', 'Unknown')}")
            ```

        Note:
            This method is used internally for connection verification but
            can also be called directly for diagnostic purposes.
        """
        if not self.client:
            return None

        try:
            response = self.client.jellyfin.get_system_info()
            if response:
                self.logger.debug("Successfully retrieved system information")
                return response
            else:
                self.logger.warning("Empty response from system info API")
                return None
        except Exception as e:
            self.logger.error(f"Failed to get system info: {e}")
            return None

    async def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve detailed information for a specific media item.

        This method fetches comprehensive metadata for a single item from
        Jellyfin, including media streams, provider IDs, and extended metadata
        not available in webhook payloads.

        **API Call Details:**
        Uses Jellyfin's Items API with specific field requests to ensure
        all necessary metadata is included in the response. This provides
        much more detail than webhook payloads.

        Args:
            item_id (str): Unique Jellyfin item identifier

        Returns:
            Optional[Dict[str, Any]]: Item data dictionary if found, None otherwise

        Example:
            ```python
            # Get detailed item information
            item_data = await jellyfin_api.get_item("abc123def456")
            if item_data:
                logger.info(f"Item: {item_data['Name']}")
                logger.info(f"Type: {item_data['Type']}")

                # Convert to MediaItem for internal use
                media_item = await jellyfin_api.convert_to_media_item(item_data)
            else:
                logger.warning("Item not found or access denied")
            ```

        Note:
            This method requires an active connection. It will return None
            if the connection is lost or the item doesn't exist/isn't accessible.
        """
        if not await self.is_connected():
            self.logger.error("Cannot retrieve item: not connected to Jellyfin")
            return None

        try:
            # Request comprehensive item data with all metadata fields
            response = self.client.jellyfin.get_item(item_id)

            if response:
                self.logger.debug(f"Retrieved item: {response.get('Name', 'Unknown')} ({item_id})")
                return response
            else:
                self.logger.warning(f"Item not found: {item_id}")
                return None

        except Exception as e:
            self.logger.error(f"Failed to retrieve item {item_id}: {e}")
            return None

    async def get_all_items(self,
                            batch_size: int = 1000,
                            progress_callback: Optional[Callable[[int, int], None]] = None) -> List[Dict[str, Any]]:
        """
        Retrieve all items from Jellyfin library with efficient batch processing.

        This method implements paginated retrieval to handle large libraries
        efficiently without overwhelming the server or consuming excessive memory.
        It supports progress callbacks for monitoring long-running operations.

        **Pagination Strategy:**
        - Requests items in configurable batch sizes
        - Processes batches sequentially to control memory usage
        - Continues until all items are retrieved
        - Handles API rate limiting gracefully

        **Memory Efficiency:**
        Large libraries can contain thousands of items. Batch processing
        prevents memory exhaustion and provides better progress feedback.

        **Optimized Field Selection:**
        This method now requests only the specific fields that correspond to
        webhook payload data, improving API performance and reducing bandwidth.
        Fields are mapped directly to webhook field equivalents for complete
        synchronization coverage.

        Args:
            batch_size (int): Number of items to fetch per API call (default: 100)
            progress_callback (Optional[Callable[[int, int], None]]): Optional callback
                function called with (current_count, total_count) for progress updates

        Returns:
            List[Dict[str, Any]]: List of all library items with webhook-equivalent metadata

        Example:
            ```python
            # Simple retrieval
            all_items = await jellyfin_api.get_all_items()
            logger.info(f"Retrieved {len(all_items)} items from library")

            # With progress callback
            def show_progress(current, total):
                pct = (current / total) * 100 if total > 0 else 0
                logger.info(f"Library sync progress: {current}/{total} ({pct:.1f}%)")

            all_items = await jellyfin_api.get_all_items(
                batch_size=50,
                progress_callback=show_progress
            )
            ```

        Note:
            This method can take significant time for large libraries.
            Use the progress callback to provide user feedback during
            long-running operations like initial library synchronization.
        """
        if not await self.is_connected():
            self.logger.error("Cannot retrieve items: not connected to Jellyfin")
            return []

        try:
            all_items = []
            start_index = 0
            total_record_count = None

            self.logger.info(f"Starting library retrieval with batch size {batch_size}")

            while True:
                try:
                    # Request batch of items with webhook-specific fields only
                    # This field list maps directly to webhook payload fields for complete sync
                    webhook_fields = ",".join([
                        # Core item metadata (maps to webhook base fields)
                        "Overview",  # → Overview
                        "ProductionYear",  # → Year
                        "RunTimeTicks",  # → RunTimeTicks
                        "OfficialRating",  # → N/A (not in webhook but useful)
                        "Tagline",  # → N/A (not in webhook but useful)
                        "PremiereDate",  # → PremiereDate
                        "DateCreated",  # → N/A (internal tracking)
                        "DateModified",  # → N/A (internal tracking)

                        # Media stream information (maps to Video_0_*, Audio_0_*, Subtitle_0_*)
                        "MediaStreams",  # → All Video_0_*, Audio_0_*, Subtitle_0_* fields
                        "MediaSources",  # → Container for MediaStreams + file info

                        # Provider IDs (maps to Provider_* fields)
                        "ProviderIds",  # → Provider_tvdb, Provider_imdb, Provider_tvrage

                        # File system information
                        "Path",  # → File path information

                        # TV Series hierarchy (maps to Series*, Season*, Episode* fields)
                        "IndexNumber",  # → EpisodeNumber, SeasonNumber (depending on type)
                        "ParentIndexNumber",  # → SeasonNumber (for episodes)
                        "SeriesName",  # → SeriesName
                        "SeriesId",  # → SeriesId
                        "SeasonId",  # → SeasonId
                        "ParentId",  # → For hierarchy navigation
                        "AirTime",  # → AirTime

                        # Content metadata (maps to genres, studios, tags arrays)
                        "Genres",  # → Not direct webhook field but needed for templates
                        "Studios",  # → Not direct webhook field but needed for templates
                        "Tags",  # → Not direct webhook field but needed for templates

                        # Music-specific fields
                        "Album",  # → Music metadata
                        "Artists",  # → Artists array
                        "AlbumArtist",  # → Album artist
                        "ArtistItems",  # → Detailed artist information

                        # Photo/image specific fields
                        "Width",  # → Image width
                        "Height",  # → Image height

                        # Additional useful fields for templates
                        "AspectRatio",  # → Video aspect ratio
                        "CommunityRating"  # → Ratings for templates
                    ])

                    response = self.client.jellyfin.user_items(
                        params={
                            'StartIndex': start_index,
                            'Limit': batch_size,
                            'Recursive': True,
                            'Fields': webhook_fields,
                            'IncludeItemTypes': 'Movie,Series,Season,Episode,Audio,MusicAlbum,MusicArtist,MusicVideo,Video'
                        }
                    )

                    if not response or 'Items' not in response:
                        self.logger.warning(f"Empty or invalid response at index {start_index}")
                        break

                    batch_items = response['Items']
                    total_record_count = response.get('TotalRecordCount', 0)

                    if not batch_items:
                        self.logger.debug("No more items to retrieve")
                        break

                    all_items.extend(batch_items)

                    # Call progress callback if provided
                    if progress_callback:
                        progress_callback(len(all_items), total_record_count)

                    self.logger.info(f"Retrieved batch: {len(batch_items)} items (total: {len(all_items)})")

                    # Check if we've retrieved all items
                    if len(all_items) >= total_record_count:
                        break

                    start_index += batch_size

                    # Brief pause to avoid overwhelming the server
                    await asyncio.sleep(0.1)

                except Exception as e:
                    self.logger.error(f"Error retrieving batch at index {start_index}: {e}")
                    break

            self.logger.info(f"Library retrieval completed: {len(all_items)} items retrieved")
            return all_items

        except Exception as e:
            self.logger.error(f"Failed to retrieve library items: {e}")
            return []

    async def convert_to_media_item(self, item_data: Dict[str, Any]) -> MediaItem:
        """
        Convert Jellyfin API response to internal MediaItem format.

        This method transforms the complex Jellyfin API response format into
        our simplified MediaItem dataclass, extracting and normalizing the
        most important metadata fields for notification purposes.

        **Data Transformation Process:**
        1. Extract basic metadata (name, type, year, etc.)
        2. Parse media stream information (video/audio specs)
        3. Extract provider IDs (IMDb, TMDb, TVDb)
        4. Normalize and clean data values
        5. Generate content hash for change detection

        **Stream Processing:**
        Jellyfin provides detailed media stream information including multiple
        video and audio tracks. This method extracts the primary streams and
        their technical specifications.

        **Enhanced Field Mapping:**
        This method now maps ALL webhook fields to ensure complete database
        synchronization and template compatibility. It includes server information,
        comprehensive media stream data, TV series hierarchy, timestamps, and
        all technical specifications that appear in webhook payloads.

        Args:
            item_data (Dict[str, Any]): Raw item data from Jellyfin API

        Returns:
            MediaItem: Normalized media item for internal use

        Example:
            ```python
            # Convert API response to MediaItem
            raw_data = await jellyfin_api.get_item("item123")
            if raw_data:
                media_item = await jellyfin_api.convert_to_media_item(raw_data)

                logger.info(f"Converted: {media_item.name}")
                logger.info(f"Quality: {media_item.video_height}p")
                logger.info(f"Codec: {media_item.video_codec}")
            ```

        Note:
            This method handles missing or malformed data gracefully,
            providing sensible defaults to ensure MediaItem creation
            succeeds even with incomplete Jellyfin data.
        """
        try:
            # ==================== DEBUG LOGGING ====================
            self.logger.debug("=" * 60)
            self.logger.debug(f"JELLYFIN API RESPONSE DEBUG for item: {item_data.get('Name', 'Unknown')}")
            self.logger.debug(f"Item Type: {item_data.get('Type', 'Unknown')}")
            self.logger.debug(f"Item ID: {item_data.get('Id', 'Unknown')}")

            # Log the ImageTags structure
            image_tags = item_data.get('ImageTags', {})
            self.logger.debug(f"ImageTags present: {bool(image_tags)}")
            if image_tags:
                self.logger.debug(f"ImageTags content: {image_tags}")
                self.logger.debug(f"  - Primary: {image_tags.get('Primary', 'NOT SET')}")
                self.logger.debug(f"  - Backdrop: {image_tags.get('Backdrop', 'NOT SET')}")
                self.logger.debug(f"  - Logo: {image_tags.get('Logo', 'NOT SET')}")
                self.logger.debug(f"  - Thumb: {image_tags.get('Thumb', 'NOT SET')}")
                self.logger.debug(f"  - Banner: {image_tags.get('Banner', 'NOT SET')}")
            else:
                self.logger.debug("No ImageTags in API response!")

            # Log episode-specific tags
            if item_data.get('Type') == 'Episode':
                self.logger.debug(f"SeriesPrimaryImageTag: {item_data.get('SeriesPrimaryImageTag', 'NOT SET')}")
                self.logger.debug(f"ParentBackdropImageTags: {item_data.get('ParentBackdropImageTags', 'NOT SET')}")
                self.logger.debug(f"ParentLogoImageTag: {item_data.get('ParentLogoImageTag', 'NOT SET')}")

            self.logger.debug("=" * 60)
            # ==================== CORE IDENTIFICATION ====================
            # Extract basic item information (always required)
            item_id = item_data.get('Id', '')
            name = item_data.get('Name', 'Unknown')
            item_type = item_data.get('Type', 'Unknown')
            year = item_data.get('ProductionYear')
            overview = item_data.get('Overview', '')

            # ==================== SERVER INFORMATION ====================
            # Server info for webhook compatibility (fetch if needed)
            server_id = None
            server_name = None
            server_version = None
            server_url = self.config.server_url

            # Try to get server info if not already fetched
            try:
                if not hasattr(self, '_cached_server_info'):
                    self._cached_server_info = await self.get_system_info()

                if self._cached_server_info:
                    server_id = self._cached_server_info.get('Id')
                    server_name = self._cached_server_info.get('ServerName')
                    server_version = self._cached_server_info.get('Version')
            except Exception as e:
                self.logger.debug(f"Could not fetch server info: {e}")

            # ==================== THUMBNAILS ====================
            # Add image tags from webhook data
            primary_image_tag = item_data.get('ImageTags', {}).get('Primary')
            backdrop_image_tag = item_data.get('ImageTags', {}).get('Backdrop')
            logo_image_tag = item_data.get('ImageTags', {}).get('Logo')
            thumb_image_tag = item_data.get('ImageTags', {}).get('Thumb')
            banner_image_tag = item_data.get('ImageTags', {}).get('Banner')

            # For episodes, also capture series image tags
            series_primary_image_tag = item_data.get('SeriesPrimaryImageTag')
            parent_backdrop_image_tag = item_data.get('ParentBackdropImageTags', [None])[0] if item_data.get(
                'ParentBackdropImageTags') else None
            parent_logo_image_tag = item_data.get('ParentLogoImageTag')

            # DEBUG: Log what we extracted
            self.logger.debug(f"Extracted image tags:")
            self.logger.debug(f"  primary_image_tag: {primary_image_tag}")
            self.logger.debug(f"  backdrop_image_tag: {backdrop_image_tag}")
            self.logger.debug(f"  logo_image_tag: {logo_image_tag}")
            self.logger.debug(f"  Type of primary_image_tag: {type(primary_image_tag)}")

            # ==================== PROVIDER IDS ====================
            # Extract external provider IDs (IMDb, TMDb, TVDb, etc.)
            provider_ids = item_data.get('ProviderIds', {})
            imdb_id = provider_ids.get('Imdb')
            tmdb_id = provider_ids.get('Tmdb')
            tvdb_id = provider_ids.get('Tvdb')

            # ==================== TV SERIES HIERARCHY ====================
            # Extract TV series information for episodes and seasons
            series_name = item_data.get('SeriesName')
            series_id = item_data.get('SeriesId')
            series_premiere_date = None
            season_id = item_data.get('SeasonId')
            season_number = item_data.get('ParentIndexNumber')  # Season number for episodes
            episode_number = item_data.get('IndexNumber')  # Episode number for episodes
            air_time = item_data.get('AirTime')

            # For seasons, IndexNumber is the season number
            if item_type == 'Season':
                season_number = item_data.get('IndexNumber')

            # Generate padded season/episode numbers (webhook SeasonNumber00, etc.)
            season_number_padded = None
            season_number_padded_3 = None
            episode_number_padded = None
            episode_number_padded_3 = None

            if season_number is not None:
                season_number_padded = f"{season_number:02d}"
                season_number_padded_3 = f"{season_number:03d}"

            if episode_number is not None:
                episode_number_padded = f"{episode_number:02d}"
                episode_number_padded_3 = f"{episode_number:03d}"

            # ==================== RUNTIME INFORMATION ====================
            # Extract runtime and convert to different formats
            runtime_ticks = item_data.get('RunTimeTicks')
            runtime_formatted = None

            if runtime_ticks:
                # Convert ticks to HH:MM:SS format (10,000 ticks = 1ms)
                total_seconds = runtime_ticks // 10000000
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                runtime_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            # ==================== MEDIA STREAM PROCESSING ====================
            # Extract comprehensive media stream information
            video_height = None
            video_width = None
            video_codec = None
            video_profile = None
            video_level = None
            video_framerate = None
            video_range = None
            video_interlaced = None
            video_bitrate = None
            video_bitdepth = None
            video_colorspace = None
            video_colortransfer = None
            video_colorprimaries = None
            video_pixelformat = None
            video_refframes = None
            aspect_ratio = None

            # Video stream properties for webhook compatibility
            video_title = None
            video_type = None
            video_language = None

            # Audio stream properties
            audio_codec = None
            audio_channels = None
            audio_bitrate = None
            audio_samplerate = None
            audio_title = None
            audio_type = None
            audio_language = None
            audio_default = None

            # Subtitle stream properties
            subtitle_title = None
            subtitle_type = None
            subtitle_language = None
            subtitle_codec = None
            subtitle_default = None
            subtitle_forced = None
            subtitle_external = None

            # Get media streams from different possible locations
            media_streams = []

            # Try MediaSources first (most common)
            if item_data.get('MediaSources'):
                for source in item_data['MediaSources']:
                    if 'MediaStreams' in source:
                        media_streams.extend(source['MediaStreams'])
            # Fallback to direct MediaStreams
            elif 'MediaStreams' in item_data:
                media_streams = item_data['MediaStreams']

            # Process video streams (map to Video_0_* webhook fields)
            video_streams = [s for s in media_streams if s.get('Type') == 'Video']
            if video_streams:
                video_stream = video_streams[0]  # Primary video stream

                # Core video properties
                video_height = video_stream.get('Height')
                video_width = video_stream.get('Width')
                video_codec = video_stream.get('Codec', '').lower()
                video_profile = video_stream.get('Profile')
                video_level = video_stream.get('Level')
                video_framerate = video_stream.get('RealFrameRate')
                video_range = video_stream.get('VideoRange', 'SDR')
                video_interlaced = video_stream.get('IsInterlaced')
                video_bitrate = video_stream.get('BitRate')
                aspect_ratio = video_stream.get('AspectRatio')

                # Extended video properties for webhook compatibility
                video_title = video_stream.get('DisplayTitle')
                video_type = 'Video'
                video_language = video_stream.get('Language')
                video_bitdepth = video_stream.get('BitDepth')
                video_colorspace = video_stream.get('ColorSpace')
                video_colortransfer = video_stream.get('ColorTransfer')
                video_colorprimaries = video_stream.get('ColorPrimaries')
                video_pixelformat = video_stream.get('PixelFormat')
                video_refframes = video_stream.get('RefFrames')

            # Process audio streams (map to Audio_0_* webhook fields)
            audio_streams = [s for s in media_streams if s.get('Type') == 'Audio']
            if audio_streams:
                audio_stream = audio_streams[0]  # Primary audio stream

                # Core audio properties
                audio_codec = audio_stream.get('Codec', '').lower()
                audio_channels = audio_stream.get('Channels')
                audio_bitrate = audio_stream.get('BitRate')
                audio_samplerate = audio_stream.get('SampleRate')

                # Extended audio properties for webhook compatibility
                audio_title = audio_stream.get('DisplayTitle')
                audio_type = 'Audio'
                audio_language = audio_stream.get('Language')
                audio_default = audio_stream.get('IsDefault')

            # Process subtitle streams (map to Subtitle_0_* webhook fields)
            subtitle_streams = [s for s in media_streams if s.get('Type') == 'Subtitle']
            if subtitle_streams:
                subtitle_stream = subtitle_streams[0]  # Primary subtitle stream

                # Subtitle properties for webhook compatibility
                subtitle_title = subtitle_stream.get('DisplayTitle')
                subtitle_type = 'Subtitle'
                subtitle_language = subtitle_stream.get('Language')
                subtitle_codec = subtitle_stream.get('Codec')
                subtitle_default = subtitle_stream.get('IsDefault')
                subtitle_forced = subtitle_stream.get('IsForced')
                subtitle_external = subtitle_stream.get('IsExternal')

            # ==================== FILE INFORMATION ====================
            # Extract file system information
            file_path = item_data.get('Path')
            file_size = None
            library_name = None

            # Try to get file size from MediaSources
            if item_data.get('MediaSources'):
                for source in item_data['MediaSources']:
                    if 'Size' in source:
                        file_size = source['Size']
                        break

            # Try to extract library name from path or other sources
            # This may require additional API calls in some cases
            if 'ParentId' in item_data:
                # For episodes/seasons, the library info might be in parent data
                pass  # Could enhance this with additional API calls if needed

            # ==================== METADATA COLLECTIONS ====================
            # Extract collection information (genres, studios, tags)
            genres = []
            if 'Genres' in item_data:
                genres = [genre.get('Name', '') if isinstance(genre, dict) else str(genre)
                          for genre in item_data['Genres']]

            studios = []
            if 'Studios' in item_data:
                studios = [studio.get('Name', '') if isinstance(studio, dict) else str(studio)
                           for studio in item_data['Studios']]

            tags = item_data.get('Tags', [])

            # ==================== MUSIC-SPECIFIC FIELDS ====================
            # Handle music-specific metadata
            album = item_data.get('Album')
            album_artist = item_data.get('AlbumArtist')
            artists = []

            if item_type in ['Audio', 'MusicVideo', 'MusicAlbum']:
                # Extract artists from ArtistItems or Artists field
                if 'ArtistItems' in item_data:
                    artists = [artist.get('Name', '') for artist in item_data['ArtistItems']]
                elif 'Artists' in item_data:
                    artists = item_data['Artists']

            # ==================== TIMESTAMP INFORMATION ====================
            # Extract and format timestamp information
            current_time = datetime.now(timezone.utc)

            # Use current time for webhook-style timestamps
            timestamp = current_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + current_time.strftime('%z')
            if not timestamp.endswith('Z') and not timestamp[-6] in ['+', '-']:
                # Ensure proper timezone format
                utc_timestamp = current_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            else:
                utc_timestamp = current_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

            # Extract creation and modification dates
            date_created = item_data.get('DateCreated')
            date_modified = item_data.get('DateLastMediaAdded') or item_data.get('DateModified')
            premiere_date = item_data.get('PremiereDate', '')

            # ==================== ADDITIONAL METADATA ====================
            # Extract additional metadata fields
            official_rating = item_data.get('OfficialRating')
            tagline = item_data.get('Tagline', '')

            # ==================== PHOTO-SPECIFIC METADATA ====================
            # For image/photo content
            width = None
            height = None
            if item_type in ['Photo', 'Image']:
                width = item_data.get('Width')
                height = item_data.get('Height')

            # ==================== CREATE COMPREHENSIVE MEDIAITEM ====================
            # Create MediaItem with ALL webhook fields mapped
            media_item = MediaItem(
                # Core identification (required fields)
                item_id=item_id,
                name=name,
                item_type=item_type,

                # Server information
                server_id=server_id,
                server_name=server_name,
                server_version=server_version,
                server_url=server_url,

                # Content metadata
                year=year,
                series_name=series_name,
                season_number=season_number,
                episode_number=episode_number,
                overview=overview,

                # Image/thumbnail metadata
                primary_image_tag=primary_image_tag,
                backdrop_image_tag=backdrop_image_tag,
                logo_image_tag=logo_image_tag,
                thumb_image_tag=thumb_image_tag,
                banner_image_tag=banner_image_tag,
                series_primary_image_tag=series_primary_image_tag,
                parent_backdrop_image_tag=parent_backdrop_image_tag,
                parent_logo_image_tag=parent_logo_image_tag,

                # Video technical specifications
                video_height=video_height,
                video_width=video_width,
                video_codec=video_codec,
                video_profile=video_profile,
                video_range=video_range,
                video_framerate=video_framerate,
                aspect_ratio=aspect_ratio,

                # Additional video properties for webhook compatibility
                video_title=video_title,
                video_type=video_type,
                video_language=video_language,
                video_level=video_level,
                video_interlaced=video_interlaced,
                video_bitrate=video_bitrate,
                video_bitdepth=video_bitdepth,
                video_colorspace=video_colorspace,
                video_colortransfer=video_colortransfer,
                video_colorprimaries=video_colorprimaries,
                video_pixelformat=video_pixelformat,
                video_refframes=video_refframes,

                # Audio technical specifications
                audio_codec=audio_codec,
                audio_channels=audio_channels,
                audio_bitrate=audio_bitrate,
                audio_samplerate=audio_samplerate,

                # Additional audio properties for webhook compatibility
                audio_title=audio_title,
                audio_type=audio_type,
                audio_language=audio_language,
                audio_default=audio_default,

                # Subtitle information for webhook compatibility
                subtitle_title=subtitle_title,
                subtitle_type=subtitle_type,
                subtitle_language=subtitle_language,
                subtitle_codec=subtitle_codec,
                subtitle_default=subtitle_default,
                subtitle_forced=subtitle_forced,
                subtitle_external=subtitle_external,

                # External provider IDs
                imdb_id=imdb_id,
                tmdb_id=tmdb_id,
                tvdb_id=tvdb_id,

                # TV series hierarchy fields
                series_id=series_id,
                series_premiere_date=series_premiere_date,
                season_id=season_id,
                season_number_padded=season_number_padded,
                season_number_padded_3=season_number_padded_3,
                episode_number_padded=episode_number_padded,
                episode_number_padded_3=episode_number_padded_3,
                air_time=air_time,

                # File system information
                file_path=file_path,
                library_name=library_name,

                # Timestamp information
                timestamp=timestamp,
                utc_timestamp=utc_timestamp,
                premiere_date=premiere_date,

                # Extended metadata from API
                date_created=date_created,
                date_modified=date_modified,
                runtime_ticks=runtime_ticks,
                runtime_formatted=runtime_formatted,
                official_rating=official_rating,
                tagline=tagline,
                genres=genres,
                studios=studios,
                tags=tags,

                # Music-specific metadata
                album=album,
                artists=artists,
                album_artist=album_artist,

                # Photo-specific metadata
                width=width,
                height=height,

                # Internal tracking
                file_size=file_size
            )

            self.logger.debug(f"Converted Jellyfin item to comprehensive MediaItem: {name}")
            return media_item

        except Exception as e:
            self.logger.error(f"Failed to convert item data: {e}")
            # Return minimal MediaItem to prevent complete failure
            return MediaItem(
                item_id=item_data.get('Id', 'unknown'),
                name=item_data.get('Name', 'Unknown Item'),
                item_type=item_data.get('Type', 'Unknown'),
            )

    async def test_connection(self) -> Dict[str, Any]:
        """
        Test connection to Jellyfin and return diagnostic information.

        This method performs a comprehensive connection test including
        authentication verification, API accessibility, and server information
        retrieval. It's useful for troubleshooting connection issues.

        **Test Components:**
        - Network connectivity to server
        - API key authentication validity
        - User ID accessibility
        - Basic API functionality
        - Server version compatibility

        Returns:
            Dict[str, Any]: Comprehensive connection test results

        Example:
            ```python
            test_results = await jellyfin_api.test_connection()

            if test_results['connected']:
                logger.info(f"Connection OK: {test_results['server_info']['ServerName']}")
            else:
                logger.error(f"Connection failed: {test_results['error']}")
            ```

        Note:
            This method is primarily used for diagnostic purposes and
            during initial service configuration validation.
        """
        test_results: Dict[str, Union[bool, Optional[str], Optional[Dict], Optional[float]]] = {
            'connected': False,  # bool
            'server_info': None,  # Optional[Dict]
            'error': None,  # Optional[str]
            'response_time': None  # Optional[float]
        }

        try:
            start_time = time.time()

            # Test basic connection
            if not await self.connect():
                test_results[
                    'error'] = "Failed to establish connection"  # This is valid - assigning str to Optional[str]
                return test_results

            # Get server information
            system_info = await self.get_system_info()
            if not system_info:
                test_results['error'] = "Connected but cannot retrieve server info"
                return test_results

            # Test item retrieval capability (try to get first item)
            try:
                response = self.client.jellyfin.user_items(
                    params={
                        'Limit': 1
                    }
                )
                if not response:
                    test_results['error'] = "Cannot access library - check user permissions"
                    return test_results
            except Exception as e:
                test_results['error'] = f"Library access failed: {str(e)}"
                return test_results

            # Calculate response time
            response_time = time.time() - start_time

            # Success - populate results
            test_results.update({
                'connected': True,
                'server_info': system_info,
                'response_time': round(response_time, 3),
                'error': None  # Explicitly set to None on success
            })

            self.logger.info(f"Connection test passed in {response_time:.3f}s")
            return test_results

        except Exception as e:
            test_results['error'] = str(e)
            self.logger.error(f"Connection test failed: {e}")
            return test_results