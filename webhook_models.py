"""
JellyNotify Webhook Models

This module contains Pydantic models for webhook payloads and related data structures
received from Jellyfin Webhook Plugin.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class WebhookPayload(BaseModel):
    """
    Enhanced webhook payload structure from Jellyfin Webhook Plugin.

    This class defines the complete data structure that Jellyfin sends when the
    webhook plugin triggers, including all the additional fields discovered
    from the debug webhook analysis.

    Attributes:
        Core required fields:
            ItemId: Unique identifier for the media item in Jellyfin
            Name: Display name of the media item
            ItemType: Type of media (Movie, Episode, Season, Series, Audio, etc.)

        Server information:
            ServerId: Jellyfin server unique identifier
            ServerName: Human-readable server name
            ServerVersion: Jellyfin server version
            ServerUrl: Public URL of the Jellyfin server
            NotificationType: Type of notification (ItemAdded, etc.)
            Timestamp: Local timestamp with timezone
            UtcTimestamp: UTC timestamp

        Basic metadata:
            Year: Release year (for movies) or air year (for TV)
            Overview: Description/synopsis of the media item
            Tagline: Marketing tagline
            RunTimeTicks: Duration in Jellyfin's tick format (100ns intervals)
            RunTime: Human-readable duration string
            PremiereDate: Release/air date
            Genres: Comma-separated genre list

        TV Series specific fields:
            SeriesName: Name of TV series (for episodes)
            SeriesId: Unique ID of the parent series
            SeriesPremiereDate: Series premiere date
            SeasonId: Unique ID of the parent season
            SeasonNumber: Season number (integer)
            SeasonNumber00: Zero-padded season number (e.g., "01")
            SeasonNumber000: Three-digit padded season number (e.g., "001")
            EpisodeNumber: Episode number (integer)
            EpisodeNumber00: Zero-padded episode number (e.g., "05")
            EpisodeNumber000: Three-digit padded episode number (e.g., "005")
            AirTime: Episode air time

        Video stream information (Video_0_*): Information about the primary video stream
        Audio stream information (Audio_0_*): Information about the primary audio stream
        Subtitle stream information (Subtitle_0_*): Information about the primary subtitle stream

        Provider fields (Provider_*): External database IDs (IMDb, TMDb, TVDb)

        Discord-specific fields:
            MentionType: Discord mention configuration
            EmbedColor: Discord embed color
            Username: Discord username
            BotUsername: Discord bot username

    Example:
        ```python
        # Typical episode payload from Jellyfin:
        payload = WebhookPayload(
            ItemId="d59aceff2218f3d94c59436326c97dd1",
            Name="Slippery When Wet",
            ItemType="Episode",
            SeriesName="Nautilus",
            SeriesId="906d53497fc1d61353a961806a08a1f7",
            SeasonNumber=1,
            EpisodeNumber=4,
            Video_0_Height=960,
            Video_0_Codec="hevc",
            Audio_0_Codec="eac3",
            Audio_0_Channels=6,
            Provider_imdb="tt16275890",
            Provider_tvdb="10541775"
        )
        ```

    Note:
        The model uses extra='ignore' to handle cases where Jellyfin sends
        additional fields that we don't specifically need to process.
    """
    model_config = ConfigDict(extra='ignore')  # Ignore unknown fields from Jellyfin

    # ==================== REQUIRED CORE FIELDS ====================
    ItemId: str = Field(..., description="Jellyfin item ID")
    Name: str = Field(..., description="Item name")
    ItemType: str = Field(..., description="Item type (Movie, Episode, Series, etc.)")

    # ==================== SERVER INFORMATION ====================
    ServerId: Optional[str] = Field(default=None, description="Jellyfin server unique identifier")
    ServerName: Optional[str] = Field(default=None, description="Jellyfin server name")
    ServerVersion: Optional[str] = Field(default=None, description="Jellyfin server version")
    ServerUrl: Optional[str] = Field(default=None, description="Jellyfin server public URL")
    NotificationType: Optional[str] = Field(default=None, description="Notification type (ItemAdded, etc.)")
    Timestamp: Optional[str] = Field(default=None, description="Local timestamp with timezone")
    UtcTimestamp: Optional[str] = Field(default=None, description="UTC timestamp")

    # ==================== BASIC METADATA ====================
    Year: Optional[int] = Field(default=None, description="Release year")
    Overview: Optional[str] = Field(default=None, description="Item overview/description")
    Tagline: Optional[str] = Field(default=None, description="Marketing tagline")
    RunTimeTicks: Optional[int] = Field(default=None, description="Duration in ticks (100ns intervals)")
    RunTime: Optional[str] = Field(default=None, description="Human-readable duration (HH:MM:SS)")
    PremiereDate: Optional[str] = Field(default=None, description="Release/premiere date")
    Genres: Optional[str] = Field(default=None, description="Comma-separated genre list")

    # ==================== TV SERIES SPECIFIC FIELDS ====================
    SeriesName: Optional[str] = Field(default=None, description="Series name for episodes")
    SeriesId: Optional[str] = Field(default=None, description="Unique ID of parent series")
    SeriesPremiereDate: Optional[str] = Field(default=None, description="Series premiere date")
    SeasonId: Optional[str] = Field(default=None, description="Unique ID of parent season")
    SeasonNumber: Optional[int] = Field(default=None, description="Season number (integer)")
    SeasonNumber00: Optional[str] = Field(default=None, description="Season number (zero-padded)")
    SeasonNumber000: Optional[str] = Field(default=None, description="Season number (three-digit padded)")
    EpisodeNumber: Optional[int] = Field(default=None, description="Episode number (integer)")
    EpisodeNumber00: Optional[str] = Field(default=None, description="Episode number (zero-padded)")
    EpisodeNumber000: Optional[str] = Field(default=None, description="Episode number (three-digit padded)")
    AirTime: Optional[str] = Field(default=None, description="Episode air time")

    # ==================== VIDEO STREAM INFORMATION ====================
    Video_0_Title: Optional[str] = Field(default=None, description="Video stream title")
    Video_0_Type: Optional[str] = Field(default=None, description="Video stream type")
    Video_0_Codec: Optional[str] = Field(default=None, description="Video codec")
    Video_0_Profile: Optional[str] = Field(default=None, description="Video profile")
    Video_0_Level: Optional[int] = Field(default=None, description="Video level")
    Video_0_Height: Optional[int] = Field(default=None, description="Video height in pixels")
    Video_0_Width: Optional[int] = Field(default=None, description="Video width in pixels")
    Video_0_AspectRatio: Optional[str] = Field(default=None, description="Video aspect ratio")
    Video_0_Interlaced: Optional[bool] = Field(default=None, description="Whether video is interlaced")
    Video_0_FrameRate: Optional[float] = Field(default=None, description="Video frame rate")
    Video_0_VideoRange: Optional[str] = Field(default=None, description="Video range (HDR/SDR)")
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