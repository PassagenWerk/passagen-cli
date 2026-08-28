import json

import httpx
import pytest

from passagen.config import LlmSettings
from passagen.llm import LlmProviderError, OpenAICompatibleProvider


def test_openai_compatible_provider_sends_json_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PASSAGEN_API_KEY", "test-key")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"] == [{"role": "user", "content": "summarize"}]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = OpenAICompatibleProvider(LlmSettings(base_url="https://llm.test/v1"), client=client)

    response = provider.generate("summarize")

    assert response.content == "{}"
    assert response.input_tokens == 4
    assert response.output_tokens == 2


def test_openai_compatible_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PASSAGEN_API_KEY", raising=False)

    with pytest.raises(LlmProviderError, match="PASSAGEN_API_KEY"):
        OpenAICompatibleProvider(LlmSettings())
