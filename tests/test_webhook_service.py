import pytest
import json
from unittest.mock import AsyncMock, MagicMock

from webhook_service import WebhookService
from webhook_models import WebhookPayload
from media_models import MediaItem


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
