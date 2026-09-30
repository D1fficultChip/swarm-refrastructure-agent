import json

import httpx
import pytest

from backend.app.agent.config import AgentConfig
from backend.app.agent.providers.base import ModelError
from backend.app.agent.providers.openai_compatible import OpenAICompatibleProvider
from backend.app.agent.tool_registry import tool_registry, tool_schemas
from scripts.phase5_scenarios import action


def reply(action_value=None):
    return {"choices":[{"message":{"content":json.dumps(action_value or action("get_current_state_summary")),
                                   "reasoning_content":"PRIVATE_REASONING_MUST_NOT_ESCAPE"}}],
            "model":"test-model","usage":{"prompt_tokens":10,"completion_tokens":12,"total_tokens":22}}


def call(transport, config=None, max_attempts=3):
    return OpenAICompatibleProvider(config or AgentConfig(),transport=httpx.MockTransport(transport)).generate_action(
        {"state":{"version":0}},tool_schemas(tool_registry()),timeout_seconds=1,max_attempts=max_attempts)


def test_real_transport_contract_strict_schema_and_no_private_reasoning(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY","test-secret")
    def transport(request):
        assert request.url == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "qwen3.8-max" and body["enable_thinking"] is False
        assert body["response_format"]["json_schema"]["strict"] is True
        schema = body["response_format"]["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert "arguments" in schema["required"]
        assert "test-secret" not in request.content.decode()
        return httpx.Response(200,json=reply())
    result = call(transport)
    assert result.model_name == "test-model" and result.token_usage["total_tokens"] == 22
    assert "PRIVATE_REASONING" not in str(result)


def test_missing_key_makes_zero_http_calls(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY",raising=False)
    with pytest.raises(ModelError) as error:
        call(lambda r: pytest.fail("No credential must mean no HTTP request"))
    assert error.value.code == "MODEL_API_KEY_MISSING" and error.value.attempts == 0


@pytest.mark.parametrize("status",[429,500,502,503,504])
def test_transient_response_retry_is_bounded(monkeypatch,status):
    monkeypatch.setenv("MODEL_API_KEY","test-secret")
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(status,json={"error":"raw-body-not-retained"}) if len(calls) == 1 else httpx.Response(200,json=reply())
    assert call(transport).attempts == len(calls) == 2
    calls.clear()
    with pytest.raises(ModelError) as error:
        call(transport,max_attempts=1)
    assert error.value.code == f"MODEL_HTTP_{status}" and len(calls) == 1


def test_nonretryable_http_error_and_timeout_are_sanitized(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY","test-secret")
    calls = []
    def unauthorized(request):
        calls.append(request)
        return httpx.Response(401,json={"error":"test-secret PRIVATE_REASONING"})
    with pytest.raises(ModelError) as error:
        call(unauthorized)
    assert str(error.value) == "MODEL_HTTP_401" and len(calls) == 1
    def timeout(request):
        raise httpx.ReadTimeout("test-secret raw request detail")
    with pytest.raises(ModelError) as error:
        call(timeout)
    assert str(error.value) == "MODEL_TIMEOUT" and error.value.attempts == 2


@pytest.mark.parametrize("payload",[{},{"choices":[]},{"choices":[{}]}])
def test_malformed_provider_envelope(payload,monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY","test-secret")
    with pytest.raises(ModelError,match="MODEL_RESPONSE_INVALID"):
        call(lambda request:httpx.Response(200,json=payload))


def test_environment_model_configuration(monkeypatch):
    monkeypatch.setenv("MODEL_NAME","future-model")
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS","7")
    monkeypatch.setenv("AGENT_FALLBACK_ENABLED","true")
    config = AgentConfig.load()
    assert config.model_name == "future-model" and config.model_timeout_seconds == 7 and config.fallback_enabled
    assert "api_key" not in AgentConfig.model_fields
