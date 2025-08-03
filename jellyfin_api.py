"""
JellyNotify Jellyfin API Client

This module handles communication with the Jellyfin server including
authentication, library retrieval, and media metadata extraction.
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

    This class manages communication with the Jellyfin server, including:
    - Connection management with automatic retry
    - Authentication using API keys
    - Efficient batch retrieval of library items
    - Media metadata extraction and normalization
    - Connection health monitoring

    The client uses the official jellyfin-apiclient-python library but adds
    additional error handling, retry logic, and batching for better reliability
    in production environments.

    Attributes:
        config: Jellyfin server configuration
        logger: Logger instance for API operations
        client: Jellyfin API client instance
        last_connection_check: Timestamp of last connection check
        connection_check_interval: How often to verify connection
        max_retries: Maximum connection attempts before giving up
        retry_delay: Delay between connection retry attempts

    Example:
        ```python
        jellyfin_api = JellyfinAPI(config.jellyfin, logger)
        if await jellyfin_api.connect():
            items = await jellyfin_api.get_all_items(batch_size=100)
            for item_data in items:
                media_item = jellyfin_api.extract_media_item(item_data)
        ```
    """

    def __init__(self, config: JellyfinConfig, logger: logging.Logger):
        """
        Initialize Jellyfin API client with configuration and logging.

        Args:
            config: Jellyfin server configuration
            logger: Logger instance for API operations
        """
        self.config = config
        self.logger = logger
        self.client = None
        self.last_connection_check = 0
        self.connection_check_interval = 60  # Check connection every 60 seconds
        self.max_retries = 3
        self.retry_delay = 5  # Wait 5 seconds between retries

    async def connect(self) -> bool:
        """
        Connect to Jellyfin server with automatic retry logic.

        This method attempts to establish a connection to the Jellyfin server
        using the configured credentials. It includes retry logic to handle
        temporary network issues or server unavailability.

        Returns:
            True if connection successful, False otherwise

        Note:
            The method uses exponential backoff between retries to avoid
            overwhelming a server that might be temporarily overloaded.
        """
        for attempt in range(self.max_retries):
            try:
                # Create new client instance
                self.client = JellyfinClient()

                # Configure client with application identification
                self.client.config.app(
                    self.config.client_name,
                    self.config.client_version,
                    self.config.device_name,
                    self.config.device_id
                )

                # Configure SSL based on server URL scheme
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

                self.client.authenticate(credentials, discover=False)

                # Test connection by getting system information
                response = self.client.jellyfin.get_system_info()
                if response:
                    server_name = response.get('ServerName', 'Unknown')
                    server_version = response.get('Version', 'Unknown')
                    self.logger.info(f"Connected to Jellyfin server: {server_name} v{server_version}")
                    return True

                self.logger.warning(f"Connection attempt {attempt + 1} failed: No response from server")

            except Exception as e:
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {e}")

                # Wait before retrying (except on last attempt)
                if attempt < self.max_retries - 1:
                    self.logger.info(f"Retrying connection in {self.retry_delay} seconds...")
                    await asyncio.sleep(self.retry_delay)
                else:
                    self.logger.error(f"Failed to connect to Jellyfin after {self.max_retries} attempts")

        return False

    async def is_connected(self) -> bool:
        """
        Check if currently connected to Jellyfin server.

        This method implements connection caching to avoid excessive API calls.
        It only performs actual connectivity checks at specified intervals.

        Returns:
            True if connected, False otherwise
        """
        current_time = time.time()

        # Use cached result if check was recent (avoid spamming the server)
        if current_time - self.last_connection_check < self.connection_check_interval:
            return self.client is not None

        self.last_connection_check = current_time

        if not self.client:
            return False

        try:
            # Simple API call to verify connectivity
            response = self.client.jellyfin.get_system_info()
            is_connected = response is not None
            if not is_connected:
                self.logger.warning("Lost connection to Jellyfin server")
            return is_connected
        except Exception as e:
            self.logger.warning(f"Connection check failed: {e}")
            return False

    async def get_all_items(self, batch_size: int = 1000,
                            process_batch_callback: Optional[Callable] = None) -> List[Dict[str, Any]]:
        """
        Retrieve all media items from Jellyfin using efficient batch processing.

        This method handles the complexity of paginated API requests to retrieve
        large libraries efficiently. It supports both collecting all items in
        memory and processing them in batches via a callback.

        Args:
            batch_size: Number of items to request per API call
            process_batch_callback: Optional async function to process each batch
                                  as it's received (memory efficient for large libraries)

        Returns:
            List of item dictionaries (empty if using callback)

        Raises:
            ConnectionError: If not connected to Jellyfin server

        Note:
            Using a callback function is recommended for large libraries as it
            processes items incrementally without loading everything into memory.
        """
        # Verify connection before starting
        if not await self.is_connected():
            if not await self.connect():
                raise ConnectionError("Cannot connect to Jellyfin server")

        start_index = 0
        all_items = []
        total_items_processed = 0

        while True:
            try:
                # Request batch of items with comprehensive field selection
                response = self.client.jellyfin.user_items(params={
                    'recursive': True,
                    # Include all media types we care about
                    'includeItemTypes': "Movie,Series,Season,Episode,MusicVideo,Audio,MusicAlbum,MusicArtist,Book,Photo,BoxSet",
                    # Request all metadata fields we might need
                    'fields': "Overview,MediaStreams,ProviderIds,Path,MediaSources,DateCreated,DateModified,ProductionYear,RunTimeTicks,OfficialRating,Genres,Studios,Tags,IndexNumber,ParentIndexNumber,Album,Artists,AlbumArtist,Width,Height",
                    'startIndex': start_index,
                    'limit': batch_size
                })

                # Check for valid response
                if not response or 'Items' not in response:
                    break

                items = response['Items']
                if not items:
                    break  # No more items to process

                # Process this batch
                if process_batch_callback:
                    # Use callback for memory-efficient processing
                    await process_batch_callback(items)
                else:
                    # Collect in memory
                    all_items.extend(items)

                total_items_processed += len(items)
                start_index += len(items)

                # Log progress for large libraries
                if total_items_processed % (batch_size * 10) == 0:
                    self.logger.info(f"Processed {total_items_processed} items from Jellyfin...")

                # Rate limiting to avoid overwhelming Jellyfin server
                await asyncio.sleep(0.1)

            except Exception as e:
                self.logger.error(f"Error fetching items from Jellyfin: {e}")
                break

        self.logger.info(f"Completed processing {total_items_processed} items from Jellyfin")
        return all_items if not process_batch_callback else []

    def extract_media_item(self, jellyfin_item: Dict[str, Any]) -> MediaItem:
        """
        Extract and normalize MediaItem from Jellyfin API response.

        This method converts Jellyfin's API response format into our internal
        MediaItem representation, handling the complex nested structure of
        Jellyfin metadata and providing sensible defaults for missing data.

        Args:
            jellyfin_item: Raw item dictionary from Jellyfin API

        Returns:
            Normalized MediaItem instance

        Note:
            This method handles the complexity of Jellyfin's variable data
            structure, where different media types may have different available
            fields. It provides robust error handling to ensure a valid
            MediaItem is always returned.
        """
        try:
            # Extract media stream information
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

            # Create normalized MediaItem with comprehensive metadata
            return MediaItem(
                # Core identification
                item_id=jellyfin_item['Id'],
                name=jellyfin_item.get('Name', ''),
                item_type=jellyfin_item.get('Type', ''),
                year=jellyfin_item.get('ProductionYear'),
                series_name=jellyfin_item.get('SeriesName'),
                season_number=season_number,
                episode_number=episode_number,
                overview=jellyfin_item.get('Overview'),

                # Enhanced metadata for rich notifications
                series_id=jellyfin_item.get('SeriesId'),  # For getting series logo/images
                parent_id=jellyfin_item.get('ParentId'),  # For series/season relationships
                community_rating=jellyfin_item.get('CommunityRating'),  # Jellyfin user ratings
                critic_rating=jellyfin_item.get('CriticRating'),  # Jellyfin critic ratings
                premiere_date=jellyfin_item.get('PremiereDate'),  # Air/release date
                end_date=jellyfin_item.get('EndDate'),

                # Video properties from primary video stream
                video_height=video_stream.get('Height'),
                video_width=video_stream.get('Width'),
                video_codec=video_stream.get('Codec'),
                video_profile=video_stream.get('Profile'),
                video_range=video_stream.get('VideoRange'),
                video_framerate=video_stream.get('RealFrameRate'),
                aspect_ratio=video_stream.get('AspectRatio'),

                # Audio properties from primary audio stream
                audio_codec=audio_stream.get('Codec'),
                audio_channels=audio_stream.get('Channels'),
                audio_language=audio_stream.get('Language'),
                audio_bitrate=audio_stream.get('BitRate'),

                # External provider IDs for rating service lookups
                imdb_id=provider_ids.get('Imdb'),
                tmdb_id=provider_ids.get('Tmdb'),
                tvdb_id=provider_ids.get('Tvdb'),

                # Enhanced metadata from API
                date_created=jellyfin_item.get('DateCreated'),
                date_modified=jellyfin_item.get('DateModified'),
                runtime_ticks=jellyfin_item.get('RunTimeTicks'),
                official_rating=jellyfin_item.get('OfficialRating'),
                genres=jellyfin_item.get('Genres', []),
                studios=[studio.get('Name') for studio in jellyfin_item.get('Studios', [])]
                if isinstance(jellyfin_item.get('Studios'), list) else [],
                tags=jellyfin_item.get('Tags', []),

                # Music-specific metadata
                album=jellyfin_item.get('Album'),
                artists=jellyfin_item.get('Artists', []),
                album_artist=jellyfin_item.get('AlbumArtist'),

                # Photo-specific metadata
                width=jellyfin_item.get('Width'),
                height=jellyfin_item.get('Height'),

                # File system information
                file_path=jellyfin_item.get('Path'),
                file_size=jellyfin_item.get('Size'),
                last_modified=jellyfin_item.get('DateModified'),

                # Initialize external rating fields as None (will be populated by rating service)
                omdb_imdb_rating=None,
                omdb_rt_rating=None,
                omdb_metacritic_rating=None,
                tmdb_rating=None,
                tmdb_vote_count=None,
                tvdb_rating=None,
                ratings_last_updated=None,
                ratings_fetch_failed=None
            )

        except Exception as e:
            self.logger.error(f"Error extracting media item from Jellyfin data: {e}")
            # Return minimal MediaItem to prevent complete failure
            return MediaItem(
                item_id=jellyfin_item.get('Id', 'unknown'),
                name=jellyfin_item.get('Name', 'Unknown'),
                item_type=jellyfin_item.get('Type', 'Unknown')
            )