import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import Request
from fastapi.exceptions import RequestValidationError
import json

# We need to import the 'app' instance from main
from main import app, validation_exception_handler

@pytest.fixture
def client(mocker):
    # Set dummy environment variables to satisfy the configuration validator
    mocker.patch.dict(
        'os.environ',
        {
            'JELLYFIN_SERVER_URL': 'http://test.com',
            'JELLYFIN_API_KEY': 'test',
            'JELLYFIN_USER_ID': 'test',
            'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/123/abc'
        }
    )
    # Mock the WebhookService class to return a mock instance
    mock_service_class = mocker.patch('main.WebhookService', autospec=True)
    mock_instance = mock_service_class.return_value
    mock_instance.initialize = AsyncMock()
    mock_instance.process_webhook = AsyncMock()
    # Add a mock logger to the service instance
    mock_instance.logger = MagicMock()

    with TestClient(app) as test_client:
        yield test_client, mock_instance

def test_webhook_happy_path(client, webhook_payload_data):
    """
    Tests the happy path for the /webhook endpoint with a valid payload.
    """
    test_client, mock_service = client

    # Configure the mock to return a successful response
    success_response = {"status": "success", "action": "new_item"}
    mock_service.process_webhook.return_value = success_response

    # Post the valid JSON data
    response = test_client.post("/webhook", json=webhook_payload_data)

    # Assert a successful response
    assert response.status_code == 200
    assert response.json() == success_response

    # Verify that the service's process_webhook method was called
    mock_service.process_webhook.assert_called_once()

def test_json_decode_error(client):
    """
    Tests that the endpoint returns a 400 Bad Request for malformed JSON.
    """
    test_client, _ = client
    response = test_client.post("/webhook", content=b'{"invalid_json": "true"')
    assert response.status_code == 400
    assert "Invalid JSON" in response.json()["detail"]

def test_validation_error_from_invalid_data(client):
    """
    Tests that the endpoint returns a 422 for JSON that is valid
    but does not match the Pydantic model.
    """
    test_client, _ = client
    # Send valid JSON, but missing required fields like 'ItemId'
    response = test_client.post("/webhook", json={"Name": "Test", "ItemType": "Movie"})
    assert response.status_code == 422
    json_response = response.json()
    assert "Webhook Validation Error" in json_response["error"]

@pytest.mark.asyncio
async def test_validation_handler_decodes_bytes():
    """
    Unit tests the validation_exception_handler directly to ensure it
    correctly decodes bytes in the error details.
    """
    # 1. Create a mock request object (it's not used by the handler but is required)
    mock_request = Request(scope={"type": "http"})

    # 2. Create a fake RequestValidationError that contains a bytes object
    #    This simulates the exact condition that caused the original bug.
    raw_error = {'input': b'raw_bytes_input', 'loc': ('body',), 'msg': 'some error', 'type': 'some_type'}
    mock_exception = RequestValidationError([raw_error])

    # 3. Call the handler directly with the mock request and exception
    response = await validation_exception_handler(mock_request, mock_exception)

    # 4. Check that the response body is now JSON serializable
    body = json.loads(response.body)

    # 5. Assert that the 'input' field has been correctly decoded from bytes to a string
    assert "details" in body
    assert len(body["details"]) == 1
    assert "input" in body["details"][0]
    assert body["details"][0]["input"] == 'raw_bytes_input'
    assert isinstance(body["details"][0]["input"], str)
