#!/usr/bin/env python3
"""
Jellynouncer Webhook Models

This module contains Pydantic models for webhook payloads and related data structures
received from Jellyfin Webhook Plugin. These models handle data validation and parsing
of the comprehensive webhook information that Jellyfin sends when media events occur.

The WebhookPayload model defines the complete structure that Jellyfin's webhook plugin
sends, including core media information, technical specifications, and metadata from
various sources. Pydantic provides automatic validation, type conversion, and error
handling for incoming webhook data.

Classes:
    WebhookPayload: Complete webhook payload structure from Jellyfin Webhook Plugin

Author: Mark Newton
Project: Jellynouncer
Version: 2.0.0
License: MIT
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class WebhookPayload(BaseModel):
    """
    Enhanced webhook payload structure from Jellyfin Webhook Plugin.

    This Pydantic model defines the complete data structure that Jellyfin sends when the
    webhook plugin triggers. It includes all the fields discovered from webhook analysis,
    providing comprehensive media information for notification processing.

    **Understanding Pydantic Models for Beginners:**

    Pydantic models automatically validate incoming data and convert types. When Jellyfin
    sends JSON data to our webhook endpoint, this model ensures the data matches our
    expected structure. If fields are missing or have wrong types, Pydantic will either
    provide defaults (for Optional fields) or raise validation errors.

    **Key Features:**
    - Automatic type validation and conversion
    - Optional fields with None defaults for missing data
    - Field descriptions for documentation
    - Strict parsing that ignores unknown fields
    - Built-in JSON serialization/deserialization

    **Core Required Fields:**
    These fields are always present in Jellyfin webhook payloads:

    Attributes:
        ItemId (str): Unique identifier for the media item in Jellyfin. This is the
            primary key used to track items across webhook events and API calls.
        Name (str): Display name of the media item. For episodes, this is the episode
            title. For movies, this is the movie title.
        ItemType (str): Type of media content. Common values include:
            - "Movie": Feature films
            - "Episode": TV show episodes
            - "Season": TV show seasons
            - "Series": TV show series
            - "Audio": Music tracks
            - "Album": Music albums
            - "Book": eBooks and audiobooks

    **Server Information:**
    These fields provide context about the Jellyfin server:

    Attributes:
        ServerId (Optional[str]): Jellyfin server unique identifier
        ServerName (Optional[str]): Human-readable server name
        ServerVersion (Optional[str]): Jellyfin server version string
        ServerUrl (Optional[str]): Public URL of the Jellyfin server
        NotificationType (Optional[str]): Type of notification event (e.g., "ItemAdded")

    **Media Content Information:**
    These fields describe the media content itself:

    Attributes:
        Overview (Optional[str]): Plot summary or description
        Year (Optional[int]): Release year for movies, air year for TV episodes
        SeriesName (Optional[str]): Name of TV series (for episodes)
        SeasonNumber (Optional[int]): Season number (for episodes)
        EpisodeNumber (Optional[int]): Episode number within season
        Path (Optional[str]): File system path to the media file
        LibraryName (Optional[str]): Name of the Jellyfin library containing this item

    **Video Stream Technical Information:**
    These fields describe video stream properties for quality detection:

    Attributes:
        Video_0_Title (Optional[str]): Video stream title/name
        Video_0_Type (Optional[str]): Stream type identifier
        Video_0_Language (Optional[str]): Video stream language code
        Video_0_Codec (Optional[str]): Video codec (h264, hevc, av1, etc.)
        Video_0_Profile (Optional[str]): Codec profile (High, Main, etc.)
        Video_0_Level (Optional[str]): Codec level specification
        Video_0_Height (Optional[int]): Video resolution height in pixels
        Video_0_Width (Optional[int]): Video resolution width in pixels
        Video_0_AspectRatio (Optional[str]): Display aspect ratio
        Video_0_Interlaced (Optional[bool]): Whether video is interlaced
        Video_0_Framerate (Optional[float]): Frames per second
        Video_0_Bitrate (Optional[int]): Video bitrate in bits per second
        Video_0_BitDepth (Optional[int]): Color bit depth (8, 10, 12)
        Video_0_ColorSpace (Optional[str]): Color space specification
        Video_0_ColorTransfer (Optional[str]): Color transfer characteristics
        Video_0_ColorPrimaries (Optional[str]): Color primaries specification
        Video_0_PixelFormat (Optional[str]): Pixel format (yuv420p, yuv420p10le, etc.)
        Video_0_RefFrames (Optional[int]): Number of reference frames

    **Audio Stream Technical Information:**
    These fields describe audio stream properties:

    Attributes:
        Audio_0_Title (Optional[str]): Audio stream title/name
        Audio_0_Type (Optional[str]): Stream type identifier
        Audio_0_Language (Optional[str]): Audio language code (eng, spa, etc.)
        Audio_0_Codec (Optional[str]): Audio codec (aac, ac3, dts, etc.)
        Audio_0_Channels (Optional[int]): Number of audio channels
        Audio_0_Bitrate (Optional[int]): Audio bitrate in bits per second
        Audio_0_SampleRate (Optional[int]): Sample rate in Hz (48000, 44100, etc.)
        Audio_0_Default (Optional[bool]): Whether this is the default audio track

    **Subtitle Stream Information:**
    These fields describe subtitle/caption tracks:

    Attributes:
        Subtitle_0_Title (Optional[str]): Subtitle stream title
        Subtitle_0_Type (Optional[str]): Subtitle stream type
        Subtitle_0_Language (Optional[str]): Subtitle language code
        Subtitle_0_Codec (Optional[str]): Subtitle format (srt, ass, pgs, etc.)
        Subtitle_0_Default (Optional[bool]): Whether this is the default subtitle
        Subtitle_0_Forced (Optional[bool]): Whether subtitle is forced display
        Subtitle_0_External (Optional[bool]): Whether subtitle is external file

    **External Provider IDs:**
    These fields link to external movie/TV databases:

    Attributes:
        Provider_imdb (Optional[str]): Internet Movie Database ID
        Provider_tmdb (Optional[str]): The Movie Database ID
        Provider_tvdb (Optional[str]): The TV Database ID
        Provider_tvdbslug (Optional[str]): TVDB URL slug identifier

    **Discord Integration Fields:**
    These fields control Discord notification appearance:

    Attributes:
        MentionType (Optional[str]): Type of Discord mention to use
        EmbedColor (Optional[int]): Discord embed color (RGB integer)
        Username (Optional[str]): Discord username override
        BotUsername (Optional[str]): Discord bot username override

    Example:
        ```python
        # Webhook payload validation
        payload_data = {
            "ItemId": "abc123def456",
            "Name": "The Matrix",
            "ItemType": "Movie",
            "Year": 1999,
            "Video_0_Height": 1080,
            "Video_0_Codec": "h264",
            "Audio_0_Channels": 6
        }

        # Pydantic automatically validates and creates the object
        try:
            payload = WebhookPayload(**payload_data)
            print(f"Processing {payload.ItemType}: {payload.Name}")
            print(f"Resolution: {payload.Video_0_Height}p")
        except ValidationError as e:
            print(f"Invalid webhook data: {e}")
        ```

    Note:
        This model uses `extra='ignore'` configuration, which means additional
        fields present in the webhook payload that aren't defined here will be
        silently ignored rather than causing validation errors. This provides
        forward compatibility if Jellyfin adds new fields in future versions.

        All fields except ItemId, Name, and ItemType are Optional because
        webhook payloads may vary depending on media type and available metadata.
        The service handles missing data gracefully by using None defaults.
    """

    # Pydantic model configuration
    model_config = ConfigDict(
        extra='ignore',  # Ignore unknown fields for forward compatibility
        str_strip_whitespace=True  # Automatically strip whitespace from strings
    )

    # ==================== CORE REQUIRED FIELDS ====================
    # These fields are always present in webhook payloads
    ItemId: str = Field(..., description="Unique Jellyfin item identifier")
    Name: str = Field(..., description="Display name of the media item")
    ItemType: str = Field(..., description="Type of media (Movie, Episode, Series, Audio, etc.)")

    # ==================== SERVER INFORMATION ====================
    ServerId: Optional[str] = Field(default=None, description="Jellyfin server unique identifier")
    ServerName: Optional[str] = Field(default=None, description="Human-readable server name")
    ServerVersion: Optional[str] = Field(default=None, description="Jellyfin server version")
    ServerUrl: Optional[str] = Field(default=None, description="Public URL of the Jellyfin server")
    NotificationType: Optional[str] = Field(default=None, description="Type of notification (ItemAdded, etc.)")

    # ==================== MEDIA CONTENT INFORMATION ====================
    Overview: Optional[str] = Field(default=None, description="Plot summary or description")
    Year: Optional[int] = Field(default=None, description="Release/air year")
    SeriesName: Optional[str] = Field(default=None, description="TV series name (for episodes)")
    SeasonNumber: Optional[int] = Field(default=None, description="Season number (for episodes)")
    EpisodeNumber: Optional[int] = Field(default=None, description="Episode number within season")
    Path: Optional[str] = Field(default=None, description="File system path to media file")
    LibraryName: Optional[str] = Field(default=None, description="Jellyfin library name")

    # ==================== VIDEO STREAM INFORMATION ====================
    Video_0_Title: Optional[str] = Field(default=None, description="Video stream title")
    Video_0_Type: Optional[str] = Field(default=None, description="Video stream type")
    Video_0_Language: Optional[str] = Field(default=None, description="Video language")
    Video_0_Codec: Optional[str] = Field(default=None, description="Video codec (h264, hevc, av1)")
    Video_0_Profile: Optional[str] = Field(default=None, description="Video codec profile")
    Video_0_Level: Optional[str] = Field(default=None, description="Video codec level")
    Video_0_Height: Optional[int] = Field(default=None, description="Video resolution height in pixels")
    Video_0_Width: Optional[int] = Field(default=None, description="Video resolution width in pixels")
    Video_0_AspectRatio: Optional[str] = Field(default=None, description="Display aspect ratio")
    Video_0_Interlaced: Optional[bool] = Field(default=None, description="Whether video is interlaced")
    Video_0_Framerate: Optional[float] = Field(default=None, description="Video framerate (fps)")
    Video_0_Bitrate: Optional[int] = Field(default=None, description="Video bitrate in bits/second")
    Video_0_BitDepth: Optional[int] = Field(default=None, description="Color bit depth (8, 10, 12)")
    Video_0_ColorSpace: Optional[str] = Field(default=None, description="Video color space")
    Video_0_ColorTransfer: Optional[str] = Field(default=None, description="Video color transfer")
    Video_0_ColorPrimaries: Optional[str] = Field(default=None, description="Video color primaries")
    Video_0_PixelFormat: Optional[str] = Field(default=None, description="Video pixel format")
    Video_0_RefFrames: Optional[int] = Field(default=None, description="Video reference frames")

    # ==================== AUDIO STREAM INFORMATION ====================
    Audio_0_Title: Optional[str] = Field(default=None, description="Audio stream title")
    Audio_0_Type: Optional[str] = Field(default=None, description="Audio stream type")
    Audio_0_Language: Optional[str] = Field(default=None, description="Audio language")
    Audio_0_Codec: Optional[str] = Field(default=None, description="Audio codec")
    Audio_0_Channels: Optional[int] = Field(default=None, description="Audio channel count")
    Audio_0_Bitrate: Optional[int] = Field(default=None, description="Audio bitrate")
    Audio_0_SampleRate: Optional[int] = Field(default=None, description="Audio sample rate")
    Audio_0_Default: Optional[bool] = Field(default=None, description="Whether audio is default")

    # ==================== SUBTITLE STREAM INFORMATION ====================
    Subtitle_0_Title: Optional[str] = Field(default=None, description="Subtitle stream title")
    Subtitle_0_Type: Optional[str] = Field(default=None, description="Subtitle stream type")
    Subtitle_0_Language: Optional[str] = Field(default=None, description="Subtitle language")
    Subtitle_0_Codec: Optional[str] = Field(default=None, description="Subtitle codec")
    Subtitle_0_Default: Optional[bool] = Field(default=None, description="Whether subtitle is default")
    Subtitle_0_Forced: Optional[bool] = Field(default=None, description="Whether subtitle is forced")
    Subtitle_0_External: Optional[bool] = Field(default=None, description="Whether subtitle is external file")

    # ==================== EXTERNAL PROVIDER IDS ====================
    Provider_imdb: Optional[str] = Field(default=None, description="IMDb ID")
    Provider_tmdb: Optional[str] = Field(default=None, description="TMDb ID")
    Provider_tvdb: Optional[str] = Field(default=None, description="TVDb ID")
    Provider_tvdbslug: Optional[str] = Field(default=None, description="TVDb slug")

    # ==================== DISCORD-SPECIFIC FIELDS ====================
    MentionType: Optional[str] = Field(default=None, description="Discord mention type")
    EmbedColor: Optional[int] = Field(default=None, description="Discord embed color")
    Username: Optional[str] = Field(default=None, description="Discord username")
    BotUsername: Optional[str] = Field(default=None, description="Discord bot username")

    # Note: Additional fields may be present in the webhook payload
    # but are ignored due to extra='ignore' configuration