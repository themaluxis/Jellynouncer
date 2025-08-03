#!/usr/bin/env python3
"""
Jellynouncer Jellyfin API Client

This module handles communication with the Jellyfin server including
authentication, library retrieval, and media metadata extraction.
It provides a high-level interface to the Jellyfin API with comprehensive
error handling, retry logic, and efficient batch processing capabilities.

The JellyfinAPI class wraps the official jellyfin-apiclient-python library
and adds production-ready features like connection management, retry logic,
and data normalization for consistent integration with other service components.

Classes:
    JellyfinAPI: Enhanced Jellyfin API client with retry logic and error handling

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import time
import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable

from jellyfin_apiclient_python import JellyfinClient

from config_models import JellyfinConfig
from media_models import MediaItem


class JellyfinAPI:
    """
    Enhanced Jellyfin API client with retry logic and comprehensive error handling.

    This class manages communication with the Jellyfin server, providing a reliable
    interface for webhook processing and library synchronization. It builds upon
    the official jellyfin-apiclient-python library with additional production-ready
    features.

    **Understanding API Clients for Beginners:**
    
    An API client is a piece of code that communicates with external services
    (like Jellyfin) over HTTP. This class handles:
    - Authentication (proving we're allowed to access the server)
    - Request formatting (converting our needs into HTTP requests)
    - Response processing (converting server responses into usable data)
    - Error handling (dealing with network issues, server problems, etc.)

    **Key Features:**
    - Connection management with automatic retry and exponential backoff
    - API key authentication (more secure than passwords for services)
    - Efficient batch retrieval of library items with pagination
    - Media metadata extraction and normalization to MediaItem format
    - Connection health monitoring with periodic checks
    - Comprehensive error handling for production reliability
    - Memory-efficient streaming of large library datasets

    **Authentication Method:**
    This client uses API key authentication rather than username/password.
    API keys are more secure for automated services because they:
    - Can be easily revoked without changing user passwords
    - Have limited scope and permissions
    - Don't expose user credentials in configuration files
    - Can be regenerated independently

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
            print("Successfully connected to Jellyfin")
            
            # Get specific item details
            item_data = await jellyfin_api.get_item("item_id_123")
            if item_data:
                media_item = jellyfin_api.extract_media_item(item_data)
                print(f"Retrieved: {media_item.name}")
            
            # Batch retrieve library items
            all_items = await jellyfin_api.get_all_items(batch_size=100)
            print(f"Found {len(all_items)} items in library")
        else:
            print("Failed to connect to Jellyfin server")
        ```

    Note:
        This class is designed to be long-lived and reused across multiple
        operations. The connection state is cached and periodically verified
        to minimize authentication overhead while ensuring reliability.
    """

    def __init__(self, config: JellyfinConfig, logger: logging.Logger):
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
                server URL, API key, user ID, and client identification
            logger (logging.Logger): Logger instance for API operations

        Example:
            ```python
            config = JellyfinConfig(
                server_url="http://jellyfin:8096",
                api_key="your_api_key",
                user_id="your_user_id"
            )
            api = JellyfinAPI(config, logger)
            ```
        """
        self.config = config
        self.logger = logger
        self.client = None  # Will be initialized in connect()
        self.last_connection_check = 0  # Unix timestamp of last check
        self.connection_check_interval = 60  # Check connection every 60 seconds
        self.max_retries = 3  # Maximum connection attempts
        self.retry_delay = 5  # Base delay between retries (seconds)

    async def connect(self) -> bool:
        """
        Connect to Jellyfin server with automatic retry logic.

        This method attempts to establish a connection to the Jellyfin server
        using the configured credentials. It includes sophisticated retry logic
        to handle temporary network issues, server restarts, or high load conditions.

        **Exponential Backoff Strategy:**
        The retry logic uses exponential backoff to avoid overwhelming a server
        that might be temporarily overloaded:
        - First retry: immediate
        - Second retry: 5 seconds delay
        - Third retry: 10 seconds delay
        - Each failure doubles the delay (up to a maximum)

        **Authentication Process:**
        1. Create new JellyfinClient instance
        2. Configure client identification (app name, version, device info)
        3. Set up SSL configuration based on server URL scheme
        4. Authenticate using API key and user ID
        5. Verify connection by requesting system information

        Returns:
            bool: True if connection successful, False after all retries exhausted

        Example:
            ```python
            # Connect with automatic retries
            if await jellyfin_api.connect():
                print("Connected successfully")
                # Connection is now ready for API calls
            else:
                print("Connection failed after all retries")
                # Handle connection failure appropriately
            ```

        Note:
            This method can be called multiple times to re-establish connection
            after network issues. It will always create a fresh connection
            rather than attempting to reuse a potentially stale one.
        """
        for attempt in range(self.max_retries):
            try:
                self.logger.info(f"Attempting Jellyfin connection (attempt {attempt + 1}/{self.max_retries})")
                
                # Create new client instance for clean connection
                self.client = JellyfinClient()

                # Configure client identification for Jellyfin logs and dashboard
                self.client.config.app(
                    self.config.client_name,      # App name shown in Jellyfin
                    self.config.client_version,   # Version for compatibility tracking
                    self.config.device_name,      # Device name in dashboard
                    self.config.device_id         # Unique device identifier
                )

                # Configure SSL based on server URL scheme
                # This ensures proper certificate handling for HTTPS endpoints
                self.client.config.data["auth.ssl"] = self.config.server_url.startswith('https')

                # Use API key authentication (preferred for services)
                # This is more secure than username/password for automated services
                credentials = {
                    "Servers": [{
                        "AccessToken": self.config.api_key,
                        "address": self.config.server_url,
                        "UserId": self.config.user_id,
                        "Id": self.config.device_id
                    }]
                }

                # Authenticate with the server using API key
                self.client.authenticate(credentials, discover=False)

                # Test connection by requesting system information
                # This verifies both authentication and basic API functionality
                response = self.client.jellyfin.get_system_info()
                if response:
                    server_name = response.get('ServerName', 'Unknown')
                    server_version = response.get('Version', 'Unknown')
                    self.logger.info(f"Connected to Jellyfin server: {server_name} v{server_version}")
                    self.last_connection_check = time.time()
                    return True

                self.logger.warning(f"Connection attempt {attempt + 1} failed: No response from server")

            except Exception as e:
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {e}")

                # Wait before retrying (except on last attempt)
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    self.logger.info(f"Retrying connection in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    self.logger.error(f"Failed to connect to Jellyfin after {self.max_retries} attempts")

        return False

    async def is_connected(self) -> bool:
        """
        Check if currently connected to Jellyfin server with intelligent caching.

        This method implements connection caching to avoid excessive API calls
        while ensuring connection validity. It only performs actual connectivity
        checks at specified intervals, returning cached results otherwise.

        **Connection Caching Strategy:**
        - Recent connections (within check interval): Return cached result
        - Stale connections: Perform lightweight connectivity test
        - Failed connections: Attempt reconnection automatically

        **Lightweight Connectivity Test:**
        Uses a minimal API call (system info) to verify the connection is
        still active without transferring large amounts of data.

        Returns:
            bool: True if connected and verified, False otherwise

        Example:
            ```python
            # Check connection before making API calls
            if await jellyfin_api.is_connected():
                # Safe to make API calls
                items = await jellyfin_api.get_all_items()
            else:
                # Handle disconnection
                print("Connection lost, attempting to reconnect...")
                await jellyfin_api.connect()
            ```

        Note:
            This method can trigger automatic reconnection attempts if the
            cached connection is found to be invalid. This provides transparent
            connection recovery for long-running services.
        """
        if not self.client:
            return False

        current_time = time.time()
        
        # Return cached result if recent check was successful
        if current_time - self.last_connection_check < self.connection_check_interval:
            return True

        # Perform lightweight connectivity test
        try:
            response = self.client.jellyfin.get_system_info()
            if response:
                self.last_connection_check = current_time
                return True
        except Exception as e:
            self.logger.warning(f"Connection check failed: {e}")

        # Connection failed, attempt automatic reconnection
        self.logger.info("Connection lost, attempting automatic reconnection...")
        return await self.connect()

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
                print(f"Item: {item_data['Name']}")
                print(f"Type: {item_data['Type']}")
                
                # Convert to MediaItem for internal use
                media_item = jellyfin_api.extract_media_item(item_data)
            else:
                print("Item not found or access denied")
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
                           batch_size: int = 100, 
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
        Large libraries can contain thousands of items. This method processes
        items in batches to maintain reasonable memory usage while providing
        progress feedback for long operations.

        Args:
            batch_size (int): Number of items to retrieve per API call.
                Larger batches are more efficient but use more memory.
            progress_callback (Optional[Callable[[int, int], None]]): Optional
                callback function called with (current_count, total_count)
                for progress monitoring.

        Returns:
            List[Dict[str, Any]]: List of all library items with full metadata

        Example:
            ```python
            # Simple batch retrieval
            all_items = await jellyfin_api.get_all_items(batch_size=50)
            print(f"Retrieved {len(all_items)} items")

            # With progress monitoring
            def show_progress(current, total):
                print(f"Progress: {current}/{total} items ({current/total*100:.1f}%)")

            items = await jellyfin_api.get_all_items(
                batch_size=100,
                progress_callback=show_progress
            )
            ```

        Note:
            This method can take significant time for large libraries (10,000+ items).
            The progress callback allows monitoring and user feedback during
            long-running synchronization operations.
        """
        if not await self.is_connected():
            self.logger.error("Cannot retrieve items: not connected to Jellyfin")
            return []

        all_items = []
        start_index = 0
        total_items = None

        try:
            self.logger.info("Starting batch retrieval of all library items")

            while True:
                # Request batch of items with comprehensive metadata
                response = self.client.jellyfin.get_items(
                    start_index=start_index,
                    limit=batch_size,
                    include_item_types=None,  # All item types
                    fields="MediaStreams,ProviderIds,Path,MediaSources,Overview,Genres,Studios,Tags"
                )

                if not response:
                    self.logger.warning("Empty response from Jellyfin items API")
                    break

                # Extract items and total count from response
                items = response.get('Items', [])
                if total_items is None:
                    total_items = response.get('TotalRecordCount', 0)
                    self.logger.info(f"Found {total_items} total items to retrieve")

                if not items:
                    # No more items to retrieve
                    break

                # Add items to result collection
                all_items.extend(items)
                start_index += len(items)

                # Call progress callback if provided
                if progress_callback:
                    progress_callback(len(all_items), total_items)

                self.logger.debug(f"Retrieved batch: {len(items)} items (total: {len(all_items)}/{total_items})")

                # Check if we've retrieved all items
                if len(all_items) >= total_items:
                    break

                # Small delay to avoid overwhelming the server
                await asyncio.sleep(0.1)

            self.logger.info(f"Completed batch retrieval: {len(all_items)} items retrieved")
            return all_items

        except Exception as e:
            self.logger.error(f"Failed to retrieve library items: {e}")
            return all_items  # Return partial results if available

    def extract_media_item(self, jellyfin_item: Dict[str, Any]) -> MediaItem:
        """
        Convert Jellyfin API response to normalized MediaItem format.

        This method handles the complex task of converting Jellyfin's variable
        API response format into our standardized MediaItem representation.
        It extracts and normalizes metadata from multiple nested structures
        within the Jellyfin response.

        **Data Normalization Challenges:**
        Jellyfin's API responses have complex, nested structures that vary
        by media type. This method handles:
        - Different field names for similar concepts
        - Optional fields that may not exist for all media types
        - Nested arrays of media stream information
        - Provider ID extraction and mapping
        - Type-specific field handling (seasons, episodes, etc.)

        **Media Stream Processing:**
        Video and audio streams are stored in MediaStreams arrays with
        type-specific information. This method extracts the primary streams
        and maps their properties to MediaItem fields.

        Args:
            jellyfin_item (Dict[str, Any]): Raw item dictionary from Jellyfin API

        Returns:
            MediaItem: Normalized MediaItem instance with extracted metadata

        Example:
            ```python
            # Raw data from Jellyfin API
            jellyfin_data = {
                'Id': 'abc123',
                'Name': 'The Matrix',
                'Type': 'Movie',
                'ProductionYear': 1999,
                'MediaStreams': [
                    {
                        'Type': 'Video',
                        'Height': 1080,
                        'Width': 1920,
                        'Codec': 'h264'
                    },
                    {
                        'Type': 'Audio',
                        'Codec': 'ac3',
                        'Channels': 6,
                        'Language': 'eng'
                    }
                ],
                'ProviderIds': {
                    'Imdb': 'tt0133093',
                    'Tmdb': '603'
                }
            }

            # Convert to internal format
            media_item = jellyfin_api.extract_media_item(jellyfin_data)
            print(f"Converted: {media_item.name} ({media_item.video_height}p)")
            ```

        Note:
            This method provides robust error handling to ensure a valid
            MediaItem is always returned, even if some fields are missing
            or malformed in the Jellyfin response. Missing data is handled
            gracefully with None values.
        """
        try:
            # Extract media stream information for technical specifications
            media_streams = jellyfin_item.get('MediaStreams', [])
            video_stream = next((s for s in media_streams if s.get('Type') == 'Video'), {})
            audio_stream = next((s for s in media_streams if s.get('Type') == 'Audio'), {})

            # Extract provider IDs for external database linking
            provider_ids = jellyfin_item.get('ProviderIds', {})

            # Handle season/episode indexing based on item type
            season_number = None
            episode_number = None

            if jellyfin_item.get('Type') == 'Season':
                season_number = jellyfin_item.get('IndexNumber')
            elif jellyfin_item.get('Type') == 'Episode':
                episode_number = jellyfin_item.get('IndexNumber')
                season_number = jellyfin_item.get('ParentIndexNumber')

            # Extract file information if available
            media_sources = jellyfin_item.get('MediaSources', [])
            file_path = None
            file_size = None
            if media_sources:
                primary_source = media_sources[0]
                file_path = primary_source.get('Path')
                file_size = primary_source.get('Size')

            # Create normalized MediaItem with comprehensive metadata
            media_item = MediaItem(
                # ==================== CORE IDENTIFICATION ====================
                item_id=jellyfin_item['Id'],
                name=jellyfin_item.get('Name', ''),
                item_type=jellyfin_item.get('Type', ''),

                # ==================== CONTENT METADATA ====================
                year=jellyfin_item.get('ProductionYear'),
                series_name=jellyfin_item.get('SeriesName'),
                season_number=season_number,
                episode_number=episode_number,
                overview=jellyfin_item.get('Overview'),

                # ==================== VIDEO SPECIFICATIONS ====================
                video_height=video_stream.get('Height'),
                video_width=video_stream.get('Width'),
                video_codec=video_stream.get('Codec'),
                video_profile=video_stream.get('Profile'),
                video_range=self._determine_video_range(video_stream),
                video_framerate=video_stream.get('RealFrameRate') or video_stream.get('AverageFrameRate'),
                aspect_ratio=video_stream.get('AspectRatio'),

                # ==================== AUDIO SPECIFICATIONS ====================
                audio_codec=audio_stream.get('Codec'),
                audio_channels=audio_stream.get('Channels'),
                audio_language=audio_stream.get('Language'),
                audio_bitrate=audio_stream.get('BitRate'),

                # ==================== EXTERNAL REFERENCES ====================
                imdb_id=provider_ids.get('Imdb'),
                tmdb_id=provider_ids.get('Tmdb'),
                tvdb_id=provider_ids.get('Tvdb'),

                # ==================== EXTENDED METADATA ====================
                date_created=jellyfin_item.get('DateCreated'),
                date_modified=jellyfin_item.get('DateModified'),
                runtime_ticks=jellyfin_item.get('RunTimeTicks'),
                official_rating=jellyfin_item.get('OfficialRating'),
                genres=jellyfin_item.get('Genres', []),
                studios=[studio.get('Name') for studio in jellyfin_item.get('Studios', [])],
                tags=jellyfin_item.get('Tags', []),
                community_rating=jellyfin_item.get('CommunityRating'),
                critic_rating=jellyfin_item.get('CriticRating'),
                premiere_date=jellyfin_item.get('PremiereDate'),

                # ==================== MUSIC-SPECIFIC ====================
                album=jellyfin_item.get('Album'),
                artists=[artist.get('Name') for artist in jellyfin_item.get('ArtistItems', [])],
                album_artist=jellyfin_item.get('AlbumArtist'),

                # ==================== PHOTO-SPECIFIC ====================
                width=jellyfin_item.get('Width'),
                height=jellyfin_item.get('Height'),

                # ==================== INTERNAL TRACKING ====================
                file_path=file_path,
                file_size=file_size,
                last_modified=jellyfin_item.get('DateModified'),

                # ==================== RELATIONSHIPS ====================
                series_id=jellyfin_item.get('SeriesId'),
                parent_id=jellyfin_item.get('ParentId')
            )

            self.logger.debug(f"Extracted MediaItem: {media_item.name} ({media_item.item_type})")
            return media_item

        except Exception as e:
            self.logger.error(f"Failed to extract MediaItem from Jellyfin data: {e}")
            # Return minimal MediaItem on error to prevent complete failure
            return MediaItem(
                item_id=jellyfin_item.get('Id', 'unknown'),
                name=jellyfin_item.get('Name', 'Unknown'),
                item_type=jellyfin_item.get('Type', 'Unknown')
            )

    def _determine_video_range(self, video_stream: Dict[str, Any]) -> Optional[str]:
        """
        Determine HDR/SDR video range from stream metadata.

        This private method analyzes video stream properties to determine
        if content is Standard Dynamic Range (SDR) or High Dynamic Range (HDR)
        and which specific HDR format is used.

        **HDR Detection Logic:**
        HDR content can be identified through several video stream properties:
        - Color transfer characteristics (BT.2020, PQ, HLG)
        - Color primaries (BT.2020 color space)
        - Bit depth (10-bit or higher typically indicates HDR capability)
        - Codec-specific metadata

        Args:
            video_stream (Dict[str, Any]): Video stream metadata from MediaStreams

        Returns:
            Optional[str]: Video range identifier ('SDR', 'HDR10', 'HDR10+', 'Dolby Vision', etc.)

        Example:
            Internal method called during extract_media_item():
            ```python
            video_range = self._determine_video_range(video_stream)
            # Returns: 'HDR10', 'SDR', 'Dolby Vision', etc.
            ```
        """
        if not video_stream:
            return None

        # Check for Dolby Vision indicators
        codec = video_stream.get('Codec', '').lower()
        if 'dovi' in codec or video_stream.get('VideoRangeType') == 'DOVI':
            return 'Dolby Vision'

        # Check color transfer and primaries for HDR indicators
        color_transfer = video_stream.get('ColorTransfer', '').lower()
        color_primaries = video_stream.get('ColorPrimaries', '').lower()
        
        # HDR10 indicators
        if ('bt2020' in color_transfer or 'smpte2084' in color_transfer or 
            'bt2020' in color_primaries):
            return 'HDR10'
        
        # Check bit depth (10-bit often indicates HDR capability)
        bit_depth = video_stream.get('BitDepth', 8)
        if bit_depth >= 10 and ('hevc' in codec or 'av1' in codec):
            return 'HDR10'

        return 'SDR'  # Default to SDR if no HDR indicators found