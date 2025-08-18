import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from config_models import DiscordConfig, JellyfinConfig, TemplatesConfig, NotificationsConfig
from media_models import MediaItem
from discord_services import DiscordNotifier

@pytest.fixture
def discord_config():
    """Provides a default DiscordConfig."""
    return DiscordConfig.model_validate({
        "webhooks": {
            "default": {
                "url": "https://discord.com/api/webhooks/1234567890/abcdefg",
                "name": "Default",
                "enabled": True
            }
        }
    })

@pytest.fixture
def jellyfin_config():
    """Provides a default JellyfinConfig."""
    return JellyfinConfig(server_url="http://jellyfin.example.com", api_key="test", user_id="test")

@pytest.fixture
def templates_config(tmp_path):
    """Provides a default TemplatesConfig pointing to a temporary directory."""
    d = tmp_path / "templates"
    d.mkdir()
    return TemplatesConfig(directory=str(d))

@pytest.fixture
def notifications_config():
    """Provides a default NotificationsConfig."""
    return NotificationsConfig()

@pytest.fixture
def notifier(discord_config, jellyfin_config, templates_config, notifications_config):
    """Provides an initialized DiscordNotifier instance."""
    session = AsyncMock()
    n = DiscordNotifier(discord_config)
    asyncio.run(n.initialize(session, jellyfin_config, templates_config, notifications_config))
    # Since we are using a mock session, we need to manually set the thumbnail_manager's session
    n.thumbnail_manager.session = session
    return n

@pytest.fixture
def media_item():
    """Provides a sample MediaItem."""
    return MediaItem(item_id="1", name="Test Item", item_type="Movie")

@pytest.mark.asyncio
async def test_send_notification_queues_when_rate_limited(notifier, media_item, mocker):
    """Tests that a notification is queued if the service is rate-limited."""
    # Mock the is_rate_limited method to return True
    mocker.patch.object(notifier, 'is_rate_limited', AsyncMock(return_value=True))

    # Mock get_webhook_url to return a valid URL
    mocker.patch.object(notifier, 'get_webhook_url', MagicMock(return_value="http://example.com/webhook"))

    # The queue should be empty initially
    assert len(notifier.notification_queue["new_items"]) == 0

    # Attempt to send a notification
    result = await notifier.send_notification(media_item, "new_item")

    # Check that the result indicates success (queued)
    assert result["success"] is True
    assert "queued" in result["message"]

    # Check that the item was added to the queue
    assert len(notifier.notification_queue["new_items"]) == 1
    assert notifier.notification_queue["new_items"][0] == media_item

@pytest.mark.asyncio
async def test_send_notification_sends_when_not_rate_limited(notifier, media_item, mocker):
    """Tests that a notification is sent normally when not rate-limited."""
    # Mock the is_rate_limited method to return False
    mocker.patch.object(notifier, 'is_rate_limited', AsyncMock(return_value=False))

    # Mock the actual send_webhook call to avoid real network requests
    mocker.patch.object(notifier, 'send_webhook', AsyncMock(return_value=True))

    # Mock get_webhook_url to return a valid URL
    mocker.patch.object(notifier, 'get_webhook_url', MagicMock(return_value="http://example.com/webhook"))

    # Mock thumbnail manager
    mocker.patch.object(notifier.thumbnail_manager, 'get_thumbnail_url', AsyncMock(return_value=None))

    # Mock render_embed
    mocker.patch.object(notifier, 'render_embed', AsyncMock(return_value={"embeds": []}))

    # The queue should be empty
    assert len(notifier.notification_queue["new_items"]) == 0

    # Attempt to send a notification
    result = await notifier.send_notification(media_item, "new_item")

    # Check that the result indicates success (sent)
    assert result["success"] is True
    assert "sent" in result["message"]

    # Check that the item was NOT added to the queue
    assert len(notifier.notification_queue["new_items"]) == 0
    # Verify that send_webhook was called
    notifier.send_webhook.assert_called_once()
