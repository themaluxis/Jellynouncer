import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

# We need to import the 'app' instance from main
from main import app

# Use a mock for the lifespan context manager to avoid running full initialization
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
    # Mock the WebhookService class to return an AsyncMock instance.
    # This instance will have awaitable methods, preventing TypeErrors in the lifespan.
    mock_service_instance = AsyncMock()
    mocker.patch('main.WebhookService', return_value=mock_service_instance)

    with TestClient(app) as test_client:
        # The app's lifespan manager will set the global webhook_service.
        # We need our test to use this instance when calling the endpoint.
        from main import webhook_service
        # The test will fail if the global is not the mock we expect
        assert webhook_service is mock_service_instance
        yield test_client

import json
import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError

from main import validation_exception_handler


def test_validation_error_does_not_crash(client):
    """
    Tests that the validation_exception_handler does not crash on invalid input.
    This is a regression test for the "Object of type bytes is not JSON serializable" bug.
    """
    # Send a request with a raw bytes body and no 'application/json' content-type.
    # This simulates a condition that could cause validation errors.
    response = client.post("/webhook", content=b'{"invalid_json": "true"')

    # The most important assertion is that we get a 422 and not a 500
    assert response.status_code == 422

    # And that the response is valid JSON
    try:
        response.json()
    except json.JSONDecodeError:
        pytest.fail("Response on validation error is not valid JSON")

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
