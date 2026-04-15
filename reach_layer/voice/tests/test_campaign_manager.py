# telephony_adapter/tests/test_campaign_manager.py
import pytest
import respx
import httpx
from src.campaign_manager import CampaignManager


@pytest.fixture
def config():
    return {
        "telephony_adapter": {
            "vobiz": {
                "auth_id": "MA_TEST123",
                "auth_token": "token123",
                "api_base": "https://api.vobiz.ai/api/v1",
                "from_number": "+918011223344",
            },
            "public_url": "https://example.ngrok.app",
        }
    }


@pytest.mark.asyncio
async def test_initiate_call_sends_correct_payload(config):
    with respx.mock:
        route = respx.post(
            "https://api.vobiz.ai/api/v1/Account/MA_TEST123/Call/"
        ).mock(return_value=httpx.Response(200, json={"callSid": "CA999"}))

        mgr = CampaignManager(config)
        result = await mgr.initiate_call(to_number="+919148223344")

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["to"] == "+919148223344"
    assert body["from"] == "+918011223344"
    assert body["answer_url"] == "https://example.ngrok.app/answer"
    assert body["answer_method"] == "POST"
    assert route.calls[0].request.headers["X-Auth-ID"] == "MA_TEST123"
    assert route.calls[0].request.headers["X-Auth-Token"] == "token123"
    assert result["callSid"] == "CA999"


@pytest.mark.asyncio
async def test_initiate_call_retries_on_429(config):
    with respx.mock:
        responses = [
            httpx.Response(429, json={"error": "rate limit"}),
            httpx.Response(200, json={"callSid": "CA888"}),
        ]
        respx.post(
            "https://api.vobiz.ai/api/v1/Account/MA_TEST123/Call/"
        ).mock(side_effect=responses)

        mgr = CampaignManager(config)
        result = await mgr.initiate_call(to_number="+919148223344")

    assert result["callSid"] == "CA888"


@pytest.mark.asyncio
async def test_initiate_call_raises_after_max_retries(config):
    with respx.mock:
        respx.post(
            "https://api.vobiz.ai/api/v1/Account/MA_TEST123/Call/"
        ).mock(return_value=httpx.Response(429, json={"error": "rate limit"}))

        mgr = CampaignManager(config)
        with pytest.raises(Exception, match="outbound call failed"):
            await mgr.initiate_call(to_number="+919148223344")


@pytest.mark.asyncio
async def test_initiate_call_empty_to_number_raises(config):
    mgr = CampaignManager(config)
    with pytest.raises(ValueError, match="to_number"):
        await mgr.initiate_call(to_number="")


@pytest.mark.asyncio
async def test_missing_config_raises():
    with pytest.raises(ValueError, match="auth_id"):
        CampaignManager({})
