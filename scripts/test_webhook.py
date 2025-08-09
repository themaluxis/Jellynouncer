#!/usr/bin/env python3
"""
Jellynouncer Webhook Test Script

This script sends test webhook payloads to the Jellynouncer service to simulate
Jellyfin webhook notifications. It supports testing different media types,
quality scenarios, and both new item and upgrade workflows.

Usage:
    python test_webhook.py [options]

Examples:
    python test_webhook.py --media-type movie --scenario 4k_hdr
    python test_webhook.py --media-type episode --scenario upgrade
    python test_webhook.py --url http://localhost:8080 --media-type music
    python test_webhook.py --endpoint debug --media-type movie --scenario new

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from urllib.parse import urlparse

import requests


def setup_logging() -> logging.Logger:
    """
    Set up logging configuration for the test script.

    Returns:
        logging.Logger: Configured logger instance
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger(__name__)


class WebhookTester:
    """
    Test class for sending webhook payloads to Jellynouncer service.

    This class generates realistic webhook payloads that match what Jellyfin's
    webhook plugin would send, including complete technical specifications
    and metadata for comprehensive testing.
    """

    def __init__(self, base_url: str, endpoint: str = "debug", logger: Optional[logging.Logger] = None):
        """
        Initialize the webhook tester.

        Args:
            base_url (str): Base URL of the webhook service
            endpoint (str): Endpoint type ('webhook' or 'debug')
            logger (Optional[logging.Logger]): Logger instance
        """
        self.base_url = base_url.rstrip('/')
        self.endpoint = endpoint
        self.logger = logger or setup_logging()

        # Construct full webhook URL
        if endpoint == "debug":
            self.webhook_url = f"{self.base_url}/webhook/debug"
        else:
            self.webhook_url = f"{self.base_url}/webhook"

        self.logger.info(f"Webhook tester initialized for: {self.webhook_url}")

    def get_movie_payload(self, scenario: str = "new") -> Dict[str, Any]:
        """
        Generate a complete movie webhook payload.

        Args:
            scenario (str): Test scenario ('new', '4k_hdr', 'upgrade', 'remux')

        Returns:
            Dict[str, Any]: Complete webhook payload for movie
        """
        base_payload = {
            "ItemId": f"movie-{scenario}-{int(time.time())}",
            "Name": "The Matrix",
            "ItemType": "Movie",
            "ServerId": "test-server-12345",
            "ServerName": "Test Jellyfin Server",
            "ServerVersion": "10.8.13",
            "ServerUrl": "http://jellyfin.local:8096",
            "NotificationType": "ItemAdded",
            "Year": 1999,
            "Overview": "A computer programmer is led to fight an underground war against powerful computers who have constructed his entire reality with a system called the Matrix.",
            "Tagline": "The fight for the future begins.",
            "Genres": "Action|Sci-Fi|Thriller",
            "Studios": "Warner Bros. Pictures|Village Roadshow Pictures",
            "OfficialRating": "R",
            "CommunityRating": 8.7,
            "RunTimeTicks": 81720000000,  # 2 hours 16 minutes in ticks
            "ProductionYear": 1999,
            "PremiereDate": "1999-03-31T00:00:00.0000000Z",
            "Path": "/media/movies/The Matrix (1999)/The Matrix (1999).mkv",
            "FileName": "The Matrix (1999).mkv",
            "Container": "mkv",
            "Size": 25000000000,  # 25GB
            "LibraryName": "Movies",
            "CollectionType": "movies",
            "Timestamp": datetime.now(timezone.utc).isoformat(),
            "UtcTimestamp": datetime.now(timezone.utc).isoformat(),
            "Provider_imdb": "tt0133093",
            "Provider_tmdb": "603",
            "Provider_tvdb": None
        }

        # Scenario-specific configurations
        if scenario == "4k_hdr":
            base_payload.update({
                "ItemId": f"movie-4k-hdr-{int(time.time())}",
                "Name": "The Matrix (4K HDR)",
                "Size": 50000000000,  # 50GB
                "Video_0_Title": "4K HDR Video Track",
                "Video_0_Type": "Video",
                "Video_0_Language": "und",
                "Video_0_Codec": "hevc",
                "Video_0_Profile": "Main 10",
                "Video_0_Level": "5.1",
                "Video_0_Height": 2160,
                "Video_0_Width": 3840,
                "Video_0_AspectRatio": "16:9",
                "Video_0_Interlaced": False,
                "Video_0_FrameRate": 23.976,
                "Video_0_Bitrate": 25000000,
                "Video_0_BitDepth": 10,
                "Video_0_ColorSpace": "bt2020nc",
                "Video_0_ColorTransfer": "smpte2084",
                "Video_0_ColorPrimaries": "bt2020",
                "Video_0_PixelFormat": "yuv420p10le",
                "Video_0_VideoRange": "HDR10",
                "Video_0_RefFrames": 4,
                "Audio_0_Title": "English DTS-HD MA 7.1",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "eng",
                "Audio_0_Codec": "dts",
                "Audio_0_Channels": 8,
                "Audio_0_Bitrate": 4000000,
                "Audio_0_SampleRate": 48000,
                "Audio_0_Default": True,
                "Subtitle_0_Title": "English SDH",
                "Subtitle_0_Type": "Subtitle",
                "Subtitle_0_Language": "eng",
                "Subtitle_0_Codec": "pgs",
                "Subtitle_0_Default": False,
                "Subtitle_0_Forced": False,
                "Subtitle_0_External": False
            })
        elif scenario == "upgrade":
            base_payload.update({
                "ItemId": f"movie-upgrade-{int(time.time())}",
                "Name": "The Matrix (Upgraded)",
                "Size": 15000000000,  # 15GB - upgrade from lower quality
                "Video_0_Title": "Upgraded Video Track",
                "Video_0_Type": "Video",
                "Video_0_Language": "und",
                "Video_0_Codec": "h264",
                "Video_0_Profile": "High",
                "Video_0_Level": "4.1",
                "Video_0_Height": 1080,
                "Video_0_Width": 1920,
                "Video_0_AspectRatio": "16:9",
                "Video_0_Interlaced": False,
                "Video_0_FrameRate": 23.976,
                "Video_0_Bitrate": 8000000,
                "Video_0_BitDepth": 8,
                "Video_0_ColorSpace": "bt709",
                "Video_0_ColorTransfer": "bt709",
                "Video_0_ColorPrimaries": "bt709",
                "Video_0_PixelFormat": "yuv420p",
                "Video_0_VideoRange": "SDR",
                "Video_0_RefFrames": 4,
                "Audio_0_Title": "English AC3 5.1",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "eng",
                "Audio_0_Codec": "ac3",
                "Audio_0_Channels": 6,
                "Audio_0_Bitrate": 448000,
                "Audio_0_SampleRate": 48000,
                "Audio_0_Default": True
            })
        elif scenario == "remux":
            base_payload.update({
                "ItemId": f"movie-remux-{int(time.time())}",
                "Name": "The Matrix (BluRay Remux)",
                "Size": 35000000000,  # 35GB
                "Container": "mkv",
                "Video_0_Title": "BluRay Remux Video",
                "Video_0_Type": "Video",
                "Video_0_Language": "und",
                "Video_0_Codec": "h264",
                "Video_0_Profile": "High",
                "Video_0_Level": "4.1",
                "Video_0_Height": 1080,
                "Video_0_Width": 1920,
                "Video_0_AspectRatio": "16:9",
                "Video_0_Interlaced": False,
                "Video_0_FrameRate": 23.976,
                "Video_0_Bitrate": 30000000,
                "Video_0_BitDepth": 8,
                "Video_0_ColorSpace": "bt709",
                "Video_0_ColorTransfer": "bt709",
                "Video_0_ColorPrimaries": "bt709",
                "Video_0_PixelFormat": "yuv420p",
                "Video_0_VideoRange": "SDR",
                "Video_0_RefFrames": 4,
                "Audio_0_Title": "English DTS-HD MA 5.1",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "eng",
                "Audio_0_Codec": "dts",
                "Audio_0_Channels": 6,
                "Audio_0_Bitrate": 3000000,
                "Audio_0_SampleRate": 48000,
                "Audio_0_Default": True
            })
        else:  # new scenario (default)
            base_payload.update({
                "Video_0_Title": "Standard Video Track",
                "Video_0_Type": "Video",
                "Video_0_Language": "und",
                "Video_0_Codec": "h264",
                "Video_0_Profile": "High",
                "Video_0_Level": "4.0",
                "Video_0_Height": 1080,
                "Video_0_Width": 1920,
                "Video_0_AspectRatio": "16:9",
                "Video_0_Interlaced": False,
                "Video_0_FrameRate": 23.976,
                "Video_0_Bitrate": 5000000,
                "Video_0_BitDepth": 8,
                "Video_0_ColorSpace": "bt709",
                "Video_0_ColorTransfer": "bt709",
                "Video_0_ColorPrimaries": "bt709",
                "Video_0_PixelFormat": "yuv420p",
                "Video_0_VideoRange": "SDR",
                "Video_0_RefFrames": 4,
                "Audio_0_Title": "English AC3 5.1",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "eng",
                "Audio_0_Codec": "ac3",
                "Audio_0_Channels": 6,
                "Audio_0_Bitrate": 448000,
                "Audio_0_SampleRate": 48000,
                "Audio_0_Default": True
            })

        return base_payload

    def get_episode_payload(self, scenario: str = "new") -> Dict[str, Any]:
        """
        Generate a complete TV episode webhook payload.

        Args:
            scenario (str): Test scenario ('new', '4k', 'upgrade', 'season_finale')

        Returns:
            Dict[str, Any]: Complete webhook payload for TV episode
        """
        base_payload = {
            "ItemId": f"episode-{scenario}-{int(time.time())}",
            "Name": "Pilot",
            "ItemType": "Episode",
            "ServerId": "test-server-12345",
            "ServerName": "Test Jellyfin Server",
            "ServerVersion": "10.8.13",
            "ServerUrl": "http://jellyfin.local:8096",
            "NotificationType": "ItemAdded",
            "SeriesName": "Breaking Bad",
            "SeriesId": f"series-bb-{int(time.time())}",
            "SeasonName": "Season 1",
            "SeasonId": f"season-bb-s1-{int(time.time())}",
            "SeasonNumber": 1,
            "SeasonNumber00": "01",
            "SeasonNumber000": "001",
            "EpisodeNumber": 1,
            "EpisodeNumber00": "01",
            "EpisodeNumber000": "001",
            "IndexNumber": 1,
            "ParentIndexNumber": 1,
            "Overview": "When an unassuming high school chemistry teacher discovers he has terminal lung cancer, he turns to cooking methamphetamine with an ex-student to secure his family's future.",
            "Year": 2008,
            "ProductionYear": 2008,
            "PremiereDate": "2008-01-20T00:00:00.0000000Z",
            "AirTime": "2008-01-20T22:00:00.0000000Z",
            "RunTimeTicks": 2760000000,  # 46 minutes in ticks
            "OfficialRating": "TV-MA",
            "CommunityRating": 8.2,
            "Path": "/media/tv/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mkv",
            "FileName": "Breaking Bad - S01E01 - Pilot.mkv",
            "Container": "mkv",
            "Size": 2500000000,  # 2.5GB
            "LibraryName": "TV Shows",
            "CollectionType": "tvshows",
            "Timestamp": datetime.now(timezone.utc).isoformat(),
            "UtcTimestamp": datetime.now(timezone.utc).isoformat(),
            "Provider_imdb": "tt0959621",
            "Provider_tmdb": "62085",
            "Provider_tvdb": "349232"
        }

        # Scenario-specific configurations
        if scenario == "4k":
            base_payload.update({
                "ItemId": f"episode-4k-{int(time.time())}",
                "Name": "Pilot (4K)",
                "Size": 8000000000,  # 8GB
                "Video_0_Title": "4K Video Track",
                "Video_0_Type": "Video",
                "Video_0_Language": "und",
                "Video_0_Codec": "hevc",
                "Video_0_Profile": "Main 10",
                "Video_0_Level": "5.1",
                "Video_0_Height": 2160,
                "Video_0_Width": 3840,
                "Video_0_AspectRatio": "16:9",
                "Video_0_Interlaced": False,
                "Video_0_FrameRate": 23.976,
                "Video_0_Bitrate": 15000000,
                "Video_0_BitDepth": 10,
                "Video_0_ColorSpace": "bt2020nc",
                "Video_0_ColorTransfer": "bt709",
                "Video_0_ColorPrimaries": "bt2020",
                "Video_0_PixelFormat": "yuv420p10le",
                "Video_0_VideoRange": "SDR",
                "Video_0_RefFrames": 4,
                "Audio_0_Title": "English DTS 5.1",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "eng",
                "Audio_0_Codec": "dts",
                "Audio_0_Channels": 6,
                "Audio_0_Bitrate": 1500000,
                "Audio_0_SampleRate": 48000,
                "Audio_0_Default": True
            })
        elif scenario == "upgrade":
            base_payload.update({
                "ItemId": f"episode-upgrade-{int(time.time())}",
                "Name": "Pilot (Upgraded)",
                "Size": 3500000000,  # 3.5GB - upgrade
                "Video_0_Title": "Upgraded Video Track",
                "Video_0_Type": "Video",
                "Video_0_Language": "und",
                "Video_0_Codec": "h264",
                "Video_0_Profile": "High",
                "Video_0_Level": "4.1",
                "Video_0_Height": 1080,
                "Video_0_Width": 1920,
                "Video_0_AspectRatio": "16:9",
                "Video_0_Interlaced": False,
                "Video_0_FrameRate": 23.976,
                "Video_0_Bitrate": 6000000,
                "Video_0_BitDepth": 8,
                "Video_0_ColorSpace": "bt709",
                "Video_0_ColorTransfer": "bt709",
                "Video_0_ColorPrimaries": "bt709",
                "Video_0_PixelFormat": "yuv420p",
                "Video_0_VideoRange": "SDR",
                "Video_0_RefFrames": 4,
                "Audio_0_Title": "English AC3 5.1",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "eng",
                "Audio_0_Codec": "ac3",
                "Audio_0_Channels": 6,
                "Audio_0_Bitrate": 448000,
                "Audio_0_SampleRate": 48000,
                "Audio_0_Default": True
            })
        elif scenario == "season_finale":
            base_payload.update({
                "ItemId": f"episode-finale-{int(time.time())}",
                "Name": "A No-Rough-Stuff-Type Deal",
                "EpisodeNumber": 7,
                "EpisodeNumber00": "07",
                "EpisodeNumber000": "007",
                "IndexNumber": 7,
                "Overview": "Walt and Jesse try to expand into new territory, but complications arise when they find out the dangers of the business.",
                "PremiereDate": "2008-03-09T00:00:00.0000000Z",
                "AirTime": "2008-03-09T22:00:00.0000000Z",
                "Size": 2800000000,  # 2.8GB
                "Video_0_Title": "Season Finale Video",
                "Video_0_Type": "Video",
                "Video_0_Language": "und",
                "Video_0_Codec": "h264",
                "Video_0_Profile": "High",
                "Video_0_Level": "4.0",
                "Video_0_Height": 720,
                "Video_0_Width": 1280,
                "Video_0_AspectRatio": "16:9",
                "Video_0_Interlaced": False,
                "Video_0_FrameRate": 23.976,
                "Video_0_Bitrate": 4000000,
                "Video_0_BitDepth": 8,
                "Video_0_ColorSpace": "bt709",
                "Video_0_ColorTransfer": "bt709",
                "Video_0_ColorPrimaries": "bt709",
                "Video_0_PixelFormat": "yuv420p",
                "Video_0_VideoRange": "SDR",
                "Video_0_RefFrames": 4,
                "Audio_0_Title": "English Stereo",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "eng",
                "Audio_0_Codec": "aac",
                "Audio_0_Channels": 2,
                "Audio_0_Bitrate": 192000,
                "Audio_0_SampleRate": 48000,
                "Audio_0_Default": True
            })
        else:  # new scenario (default)
            base_payload.update({
                "Video_0_Title": "Standard TV Video",
                "Video_0_Type": "Video",
                "Video_0_Language": "und",
                "Video_0_Codec": "h264",
                "Video_0_Profile": "High",
                "Video_0_Level": "4.0",
                "Video_0_Height": 720,
                "Video_0_Width": 1280,
                "Video_0_AspectRatio": "16:9",
                "Video_0_Interlaced": False,
                "Video_0_FrameRate": 23.976,
                "Video_0_Bitrate": 3000000,
                "Video_0_BitDepth": 8,
                "Video_0_ColorSpace": "bt709",
                "Video_0_ColorTransfer": "bt709",
                "Video_0_ColorPrimaries": "bt709",
                "Video_0_PixelFormat": "yuv420p",
                "Video_0_VideoRange": "SDR",
                "Video_0_RefFrames": 4,
                "Audio_0_Title": "English Stereo",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "eng",
                "Audio_0_Codec": "aac",
                "Audio_0_Channels": 2,
                "Audio_0_Bitrate": 192000,
                "Audio_0_SampleRate": 48000,
                "Audio_0_Default": True
            })

        return base_payload

    def get_music_payload(self, scenario: str = "new") -> Dict[str, Any]:
        """
        Generate a complete music webhook payload.

        Args:
            scenario (str): Test scenario ('new', 'flac', 'album', 'high_res')

        Returns:
            Dict[str, Any]: Complete webhook payload for music
        """
        base_payload = {
            "ItemId": f"music-{scenario}-{int(time.time())}",
            "Name": "Bohemian Rhapsody",
            "ItemType": "Audio",
            "ServerId": "test-server-12345",
            "ServerName": "Test Jellyfin Server",
            "ServerVersion": "10.8.13",
            "ServerUrl": "http://jellyfin.local:8096",
            "NotificationType": "ItemAdded",
            "Album": "A Night at the Opera",
            "AlbumArtist": "Queen",
            "Artist": "Queen",
            "Genres": "Rock|Progressive Rock|Hard Rock",
            "Year": 1975,
            "ProductionYear": 1975,
            "RunTimeTicks": 3540000000,  # 5:54 in ticks
            "TrackNumber": 11,
            "DiscNumber": 1,
            "Path": "/media/music/Queen/A Night at the Opera/11 - Bohemian Rhapsody.flac",
            "FileName": "11 - Bohemian Rhapsody.flac",
            "Container": "flac",
            "Size": 45000000,  # 45MB
            "LibraryName": "Music",
            "CollectionType": "music",
            "Timestamp": datetime.now(timezone.utc).isoformat(),
            "UtcTimestamp": datetime.now(timezone.utc).isoformat(),
            "Provider_musicbrainztrack": "b1a9c0e9-d987-4042-ae91-78d6a3267d69",
            "Provider_musicbrainzalbum": "43d2ad0a-2b9a-4f48-bad6-4e18523321b8",
            "Provider_musicbrainzartist": "0383dadf-2a4e-4d10-a46a-e9e041da8eb3"
        }

        # Scenario-specific configurations
        if scenario == "flac":
            base_payload.update({
                "ItemId": f"music-flac-{int(time.time())}",
                "Name": "Bohemian Rhapsody (FLAC)",
                "Container": "flac",
                "Size": 45000000,  # 45MB
                "Audio_0_Title": "FLAC Audio",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "und",
                "Audio_0_Codec": "flac",
                "Audio_0_Channels": 2,
                "Audio_0_Bitrate": 1000000,
                "Audio_0_SampleRate": 44100,
                "Audio_0_Default": True
            })
        elif scenario == "high_res":
            base_payload.update({
                "ItemId": f"music-hires-{int(time.time())}",
                "Name": "Bohemian Rhapsody (Hi-Res)",
                "Container": "flac",
                "Size": 120000000,  # 120MB
                "Audio_0_Title": "Hi-Res FLAC Audio",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "und",
                "Audio_0_Codec": "flac",
                "Audio_0_Channels": 2,
                "Audio_0_Bitrate": 2800000,
                "Audio_0_SampleRate": 96000,
                "Audio_0_Default": True
            })
        elif scenario == "album":
            base_payload.update({
                "ItemId": f"album-{int(time.time())}",
                "Name": "A Night at the Opera",
                "ItemType": "MusicAlbum",
                "Overview": "The fourth studio album by British rock band Queen, released in 1975.",
                "Size": 500000000,  # 500MB for full album
                "Audio_0_Title": "Album Audio",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "und",
                "Audio_0_Codec": "flac",
                "Audio_0_Channels": 2,
                "Audio_0_Bitrate": 1000000,
                "Audio_0_SampleRate": 44100,
                "Audio_0_Default": True
            })
        else:  # new scenario (default)
            base_payload.update({
                "Audio_0_Title": "Standard Audio",
                "Audio_0_Type": "Audio",
                "Audio_0_Language": "und",
                "Audio_0_Codec": "mp3",
                "Audio_0_Channels": 2,
                "Audio_0_Bitrate": 320000,
                "Audio_0_SampleRate": 44100,
                "Audio_0_Default": True
            })

        return base_payload

    def send_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send webhook payload to the configured endpoint.

        Args:
            payload (Dict[str, Any]): Webhook payload to send

        Returns:
            Dict[str, Any]: Test result with success/failure status
        """
        try:
            self.logger.info(f"🚀 Sending {payload['ItemType']} webhook: {payload['Name']}")
            self.logger.debug(f"📡 Target URL: {self.webhook_url}")

            # Send POST request with JSON payload
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Jellyfin-Webhook-Test/2.0.0"
                },
                timeout=30
            )

            # Evaluate response
            if response.status_code == 200:
                self.logger.info("✅ PASS - Webhook processed successfully")
                return {
                    "status": "PASS",
                    "status_code": response.status_code,
                    "response": response.json() if response.content else {},
                    "error": None
                }
            else:
                self.logger.error(f"❌ FAIL - HTTP {response.status_code}")
                self.logger.error(f"Response: {response.text}")
                return {
                    "status": "FAIL",
                    "status_code": response.status_code,
                    "response": response.text,
                    "error": f"HTTP {response.status_code}"
                }

        except requests.exceptions.ConnectionError as e:
            self.logger.error(f"❌ FAIL - Connection error: {e}")
            return {
                "status": "FAIL",
                "status_code": None,
                "response": None,
                "error": f"Connection error: {e}"
            }
        except requests.exceptions.Timeout as e:
            self.logger.error(f"❌ FAIL - Request timeout: {e}")
            return {
                "status": "FAIL",
                "status_code": None,
                "response": None,
                "error": f"Request timeout: {e}"
            }
        except Exception as e:
            self.logger.error(f"❌ FAIL - Unexpected error: {e}")
            return {
                "status": "FAIL",
                "status_code": None,
                "response": None,
                "error": f"Unexpected error: {e}"
            }


def validate_url(url: str) -> str:
    """
    Validate and normalize webhook URL.

    Args:
        url (str): URL to validate

    Returns:
        str: Normalized URL

    Raises:
        ValueError: If URL is invalid
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("URL must include scheme and host")
        return url.rstrip('/')
    except Exception as e:
        raise ValueError(f"Invalid URL: {e}")


def main() -> None:
    """
    Main entry point for the webhook test script.

    Parses command line arguments and executes the appropriate test scenario.
    """
    parser = argparse.ArgumentParser(
        description="Test webhook endpoints for Jellynouncer service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --media-type movie --scenario 4k_hdr
  %(prog)s --media-type episode --scenario upgrade
  %(prog)s --url http://localhost:8080/webhook --media-type music
  %(prog)s --endpoint debug --media-type movie --scenario new
        """
    )

    parser.add_argument(
        "--url",
        type=str,
        default="http://localhost:8080",
        help="Base URL of webhook service (default: http://localhost:8080)"
    )

    parser.add_argument(
        "--endpoint",
        type=str,
        choices=["webhook", "debug"],
        default="debug",
        help="Endpoint type to test (default: debug)"
    )

    parser.add_argument(
        "--media-type",
        type=str,
        choices=["movie", "episode", "music"],
        default="movie",
        help="Type of media to test (default: movie)"
    )

    parser.add_argument(
        "--scenario",
        type=str,
        help="Test scenario (varies by media type)"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Set up logging
    logger = setup_logging()
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Validate URL
    try:
        base_url = validate_url(args.url)
    except ValueError as e:
        logger.error(f"❌ Invalid URL: {e}")
        sys.exit(1)

    # Determine valid scenarios for media type
    scenario_map = {
        "movie": ["new", "4k_hdr", "upgrade", "remux"],
        "episode": ["new", "4k", "upgrade", "season_finale"],
        "music": ["new", "flac", "album", "high_res"]
    }

    valid_scenarios = scenario_map[args.media_type]
    scenario = args.scenario if args.scenario else "new"

    if scenario not in valid_scenarios:
        logger.error(f"❌ Invalid scenario '{scenario}' for {args.media_type}")
        logger.info(f"Valid scenarios for {args.media_type}: {', '.join(valid_scenarios)}")
        sys.exit(1)

    # Initialize tester
    tester = WebhookTester(base_url, args.endpoint, logger)

    # Generate payload based on media type and scenario
    if args.media_type == "movie":
        payload = tester.get_movie_payload(scenario)
    elif args.media_type == "episode":
        payload = tester.get_episode_payload(scenario)
    elif args.media_type == "music":
        payload = tester.get_music_payload(scenario)

    logger.info("=" * 60)
    logger.info("🧪 JELLYNOUNCER WEBHOOK TEST")
    logger.info("=" * 60)
    logger.info(f"📡 Target: {tester.webhook_url}")
    logger.info(f"🎬 Media Type: {args.media_type}")
    logger.info(f"🎯 Scenario: {scenario}")
    logger.info(f"📋 Item ID: {payload['ItemId']}")
    logger.info(f"🏷️  Item Name: {payload['Name']}")
    logger.info("=" * 60)

    # Send webhook
    result = tester.send_webhook(payload)

    # Print final result
    logger.info("=" * 60)
    if result["status"] == "PASS":
        logger.info("🎉 TEST RESULT: PASS")
        sys.exit(0)
    else:
        logger.error(f"💥 TEST RESULT: FAIL - {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()