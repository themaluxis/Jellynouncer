import pytest
import json
from unittest.mock import AsyncMock, MagicMock

from webhook_service import WebhookService
from webhook_models import WebhookPayload
from media_models import MediaItem

@pytest.fixture
def webhook_payload_data():
    """Provides the raw JSON data from the user."""
    json_string = '{"ServerId":"35b84455fff6406296cced109801e4b8","ServerName":"Media","ServerVersion":"10.10.7","ServerUrl":"https://xx.xxxxxx.fr/","NotificationType":"ItemAdded","Timestamp":"2025-08-18T20:35:28.3097783+02:00","UtcTimestamp":"2025-08-18T18:35:28.3097786Z","Name":"Free Guy","Overview":"Un employe de banque, decouvrant un jour qu’il n’est en fait qu’un personnage d’arriere-plan dans un jeu video en ligne, decide de devenir le heros de sa propre histoire, quitte a la reecrire. Evoluant desormais dans un monde qui ne connait pas de limites, il va tout mettre en œuvre pour le sauver a sa maniere, avant qu’il ne soit trop tard…","Tagline":"Le monde avait besoin d\'un heros. C\'est tombe sur lui.","ItemId":"f8fa7a608e0a60f502854d0b36c2fd6d","ItemType":"Movie","RunTimeTicks":68986860000,"RunTime":"01:54:58","Year":2021,"PremiereDate":"2021-08-11","Genres":"Comedie, Aventure, Science-Fiction","Provider_imdb":"tt6264654","Provider_tmdb":"550988","Provider_tmdbcollection":"861415","Video_0_Title":"1080p HEVC SDR","Video_0_Type":"Video","Video_0_Codec":"hevc","Video_0_Profile":"Main 10","Video_0_Level":120,"Video_0_Height":808,"Video_0_Width":1920,"Video_0_AspectRatio":"2.40:1","Video_0_Interlaced":false,"Video_0_FrameRate":23.976025,"Video_0_VideoRange":"SDR","Video_0_ColorSpace":null,"Video_0_ColorTransfer":null,"Video_0_ColorPrimaries":null,"Video_0_PixelFormat":"yuv420p10le","Video_0_RefFrames":1,"Audio_0_Title":"VFF - Fre - HE-AAC - 5.1 - Par defaut","Audio_0_Type":"Audio","Audio_0_Language":"fre","Audio_0_Codec":"aac","Audio_0_Channels":6,"Audio_0_Bitrate":210266,"Audio_0_SampleRate":48000,"Audio_0_Default":true,"Audio_1_Title":"VO - English - HE-AAC - 5.1","Audio_1_Type":"Audio","Audio_1_Language":"eng","Audio_1_Codec":"aac","Audio_1_Channels":6,"Audio_1_Bitrate":209686,"Audio_1_SampleRate":48000,"Audio_1_Default":false,"Subtitle_0_Title":"Francais - Fre - SUBRIP","Subtitle_0_Type":"Subtitle","Subtitle_0_Language":"fre","Subtitle_0_Codec":"subrip","Subtitle_0_Default":false,"Subtitle_0_Forced":false,"Subtitle_0_External":false,"Subtitle_1_Title":"Francais [forces] - Fre - Par defaut - SUBRIP","Subtitle_1_Type":"Subtitle","Subtitle_1_Language":"fre","Subtitle_1_Codec":"subrip","Subtitle_1_Default":true,"Subtitle_1_Forced":true,"Subtitle_1_External":false,"Subtitle_2_Title":"Anglais - English - SUBRIP","Subtitle_2_Type":"Subtitle","Subtitle_2_Language":"eng","Subtitle_2_Codec":"subrip","Subtitle_2_Default":false,"Subtitle_2_Forced":false,"Subtitle_2_External":false,"Subtitle_3_Title":"Anglais [forces] - English - SUBRIP","Subtitle_3_Type":"Subtitle","Subtitle_3_Language":"eng","Subtitle_3_Codec":"subrip","Subtitle_3_Default":false,"Subtitle_3_Forced":true,"Subtitle_3_External":false,"Subtitle_4_Title":"Anglais [SDH] - English - SUBRIP","Subtitle_4_Type":"Subtitle","Subtitle_4_Language":"eng","Subtitle_4_Codec":"subrip","Subtitle_4_Default":false,"Subtitle_4_Forced":false,"Subtitle_4_External":false}'
    # The JSON has some escaped unicode characters that need to be unescaped
    return json.loads(json_string.encode().decode('unicode-escape'))

@pytest.fixture
def webhook_payload(webhook_payload_data):
    """Provides a WebhookPayload object from the user's data."""
    return WebhookPayload(**webhook_payload_data)

@pytest.fixture
def mocked_webhook_service(mocker):
    """Provides a WebhookService instance with all dependencies mocked."""
    service = WebhookService()
    service.db = AsyncMock()
    service.jellyfin = AsyncMock()
    service.discord = AsyncMock()
    service.change_detector = MagicMock()
    service.metadata_service = AsyncMock()
    service.logger = MagicMock()
    return service

@pytest.mark.asyncio
async def test_process_new_movie_webhook(mocked_webhook_service, webhook_payload):
    """
    Tests the full processing pipeline for a new movie webhook payload.
    """
    service = mocked_webhook_service

    # --- MOCK SETUP ---
    # Mock the return value of jellyfin.get_item to be a dictionary (as the real API would)
    service.jellyfin.get_item.return_value = {"Id": webhook_payload.ItemId, "Name": webhook_payload.Name, "Type": webhook_payload.ItemType}

    # Mock the return value of jellyfin.convert_to_media_item to be a MediaItem instance
    converted_item = MediaItem(item_id=webhook_payload.ItemId, name=webhook_payload.Name, item_type=webhook_payload.ItemType)
    service.jellyfin.convert_to_media_item.return_value = converted_item

    # Mock db.get_item to return None, simulating a new item
    service.db.get_item.return_value = None

    # Mock the metadata service to return the item as-is
    service.metadata_service.enrich_media_item.return_value = converted_item

    # --- EXECUTION ---
    result = await service.process_webhook(webhook_payload)

    # --- ASSERTIONS ---
    # Verify that the correct methods were called
    service.jellyfin.get_item.assert_called_once_with(webhook_payload.ItemId)
    service.db.get_item.assert_called_once_with(webhook_payload.ItemId)
    service.db.save_item.assert_called_once_with(converted_item)
    service.metadata_service.enrich_media_item.assert_called_once_with(converted_item)
    service.discord.send_notification.assert_called_once_with(converted_item, "new_item")

    # Verify that change_detector was NOT called for a new item
    service.change_detector.detect_changes.assert_not_called()

    # Check the result dictionary
    assert result["status"] == "success"
    assert result["action"] == "new_item"
    assert result["item_name"] == "Free Guy"
