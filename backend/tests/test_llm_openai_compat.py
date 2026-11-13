import base64
import json

import httpx
import pytest

from tutor.config import LLMConfig, SchedulerConfig, build_client, build_scheduled_client
from tutor.llm import BudgetExceeded, LLMError, LLMRequest, LLMTimeout, OllamaClient
from tutor.llm.openai_compat import OpenAICompatClient

pytestmark = pytest.mark.anyio

MODELS = {"reasoner": "r-model", "router": "q-model", "vision": "v-model"}


def req(**kw):
    base = dict(agent="a", tier="reasoner", system="SYS", user="USR", temperature=0.2, num_predict=123, seed=7)
    base.update(kw)
    return LLMRequest(**base)


def completion(content='{"x": 1}', finish="stop", usage=True, **extra):
    body = {"model": "r-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": content, **extra}, "finish_reason": finish}]}
    if usage:
        body["usage"] = {"prompt_tokens": 11, "completion_tokens": 22}
    return body


def make(handler, base="http://vllm.test:8000/", **kw):
    return OpenAICompatClient(base, MODELS, transport=httpx.MockTransport(handler), **kw)


async def run(handler, request=None, **kw):
    client = make(handler, **kw)
    try:
        return await client.generate(request or req())
    finally:
        await client.aclose()


def capture(body=None, status=200):
    seen = {}

    def handler(request: httpx.Request):
        seen["path"], seen["method"], seen["headers"] = request.url.path, request.method, request.headers
        seen["json"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=body if body is not None else completion())

    return seen, handler


# ------------------------------------------------------------------ request shape


async def test_text_request_shape():
    seen, handler = capture()
    resp = await run(handler)
    p = seen["json"]
    assert (seen["method"], seen["path"]) == ("POST", "/v1/chat/completions")
    assert p["model"] == "r-model" and p["stream"] is False
    assert p["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}]
    assert (p["temperature"], p["max_tokens"], p["seed"]) == (0.2, 123, 7)
    assert "response_format" not in p and "chat_template_kwargs" not in p and "guided_json" not in p
    assert resp.text == '{"x": 1}'


async def test_tier_selects_the_model():
    seen, handler = capture()
    await run(handler, req(tier="vision"))
    assert seen["json"]["model"] == "v-model"


async def test_images_become_data_url_parts():
    seen, handler = capture()
    await run(handler, req(images=(b"\x89PNGabc", b"two")))
    parts = seen["json"]["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "USR"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"\x89PNGabc").decode()
    assert len(parts) == 3


async def test_schema_is_sent_as_strict_json_schema_response_format():
    seen, handler = capture()
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    await run(handler, req(schema=schema))
    rf = seen["json"]["response_format"]
    assert rf == {"type": "json_schema", "json_schema": {"name": "a", "schema": schema, "strict": True}}
    assert "guided_json" not in seen["json"]


async def test_guided_json_is_sent_only_when_enabled():
    seen, handler = capture()
    schema = {"type": "object"}
    await run(handler, req(schema=schema), guided_json=True)
    assert seen["json"]["guided_json"] == schema and "response_format" in seen["json"]


async def test_schema_name_is_sanitised_for_the_server():
    seen, handler = capture()
    await run(handler, req(agent="animator[2]", schema={"type": "object"}))
    assert seen["json"]["response_format"]["json_schema"]["name"] == "animator_2"


async def test_think_true_sends_enable_thinking():
    seen, handler = capture()
    await run(handler, req(think=True))
    assert seen["json"]["chat_template_kwargs"] == {"enable_thinking": True}


async def test_think_false_sends_nothing():
    seen, handler = capture()
    await run(handler, req(think=False))
    assert "chat_template_kwargs" not in seen["json"]


async def test_thinking_param_none_never_sends_the_field():
    seen, handler = capture()
    await run(handler, req(think=True), thinking_param="none")
    assert "chat_template_kwargs" not in seen["json"]


async def test_think_is_downgraded_when_the_model_is_known_not_to_think():
    seen, handler = capture()
    await run(handler, req(think=True), supports_thinking=False)
    assert "chat_template_kwargs" not in seen["json"]


def test_bad_thinking_param_is_rejected():
    with pytest.raises(ValueError):
        OpenAICompatClient("http://x", MODELS, thinking_param="bogus")


@pytest.mark.parametrize("base", ["http://h:8000", "http://h:8000/", "http://h:8000/v1", "http://h:8000/v1/"])
async def test_base_url_with_or_without_v1(base):
    seen, handler = capture()
    await run(handler, base=base)
    assert seen["path"] == "/v1/chat/completions"


# --------------------------------------------------------------------- responses


async def test_usage_finish_and_model_are_mapped():
    resp = await run(lambda r: httpx.Response(200, json=completion(finish="stop")))
    assert (resp.prompt_tokens, resp.completion_tokens, resp.done_reason, resp.model) == (11, 22, "stop", "r-model")
    assert resp.total_ms >= 0


async def test_length_finish_reason_is_kept():
    resp = await run(lambda r: httpx.Response(200, json=completion(finish="length")))
    assert resp.done_reason == "length"


async def test_missing_usage_falls_back_to_estimates():
    resp = await run(lambda r: httpx.Response(200, json=completion('{"abcdef": 1}', usage=False)))
    assert resp.prompt_tokens > 0 and resp.completion_tokens > 0


async def test_missing_finish_reason_means_stop():
    body = completion()
    del body["choices"][0]["finish_reason"]
    assert (await run(lambda r: httpx.Response(200, json=body))).done_reason == "stop"


async def test_inline_think_block_is_stripped():
    resp = await run(lambda r: httpx.Response(200, json=completion('<think>let me see\n{"no": 0}</think>\n{"x": 1}')))
    assert resp.text == '{"x": 1}'


async def test_bare_closing_think_tag_is_stripped():
    resp = await run(lambda r: httpx.Response(200, json=completion('reasoning...</think>{"x": 1}')))
    assert resp.text == '{"x": 1}'


async def test_reasoning_content_is_ignored():
    resp = await run(lambda r: httpx.Response(200, json=completion('{"x": 1}', reasoning_content="long thoughts")))
    assert resp.text == '{"x": 1}'


async def test_content_parts_are_joined():
    resp = await run(lambda r: httpx.Response(200, json=completion([{"type": "text", "text": '{"x":'}, {"type": "text", "text": " 1}"}])))
    assert resp.text == '{"x": 1}'


@pytest.mark.parametrize("content", ["", "   ", None, "<think>only thoughts</think>"])
async def test_empty_content_is_an_error(content):
    with pytest.raises(LLMError):
        await run(lambda r: httpx.Response(200, json=completion(content)))


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {}}]}])
async def test_malformed_response_is_an_error(body):
    with pytest.raises(LLMError, match="bad /v1/chat/completions"):
        await run(lambda r: httpx.Response(200, json=body))


async def test_non_json_response_is_an_error():
    with pytest.raises(LLMError):
        await run(lambda r: httpx.Response(200, text="<html>nope</html>"))


# ------------------------------------------------------------------------ errors


async def test_timeout_maps_to_llm_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMTimeout):
        await run(handler)


async def test_connection_failure_maps_to_llm_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMError) as exc:
        await run(handler)
    assert not isinstance(exc.value, LLMTimeout)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_retryable_statuses_are_llm_errors(status):
    with pytest.raises(LLMError, match=str(status)):
        await run(lambda r: httpx.Response(status, json={"error": {"message": "busy"}}))


async def test_client_error_carries_the_trimmed_server_message_only():
    msg = "m" * 500
    with pytest.raises(LLMError) as exc:
        await run(lambda r: httpx.Response(404, json={"error": {"message": msg}}), req(user="SECRET-STUDENT-TEXT"))
    text = str(exc.value)
    assert "404" in text and len(text) < 450 and "SECRET-STUDENT-TEXT" not in text


async def test_schema_grammar_rejection_is_called_out():
    with pytest.raises(LLMError, match="structured-output schema"):
        await run(lambda r: httpx.Response(400, json={"error": {"message": "Failed to compile json_schema grammar"}}), req(schema={"type": "object"}))


async def test_plain_400_is_not_labelled_as_a_schema_problem():
    with pytest.raises(LLMError) as exc:
        await run(lambda r: httpx.Response(400, json={"error": {"message": "max_tokens must be positive"}}))
    assert "structured-output" not in str(exc.value)


async def test_non_json_error_body_is_trimmed():
    with pytest.raises(LLMError) as exc:
        await run(lambda r: httpx.Response(502, text="x" * 2000))
    assert len(str(exc.value)) < 450


# ------------------------------------------------------------------------ api key


async def test_api_key_is_sent_as_a_bearer_header():
    seen, handler = capture()
    await run(handler, api_key="sk-test-123")
    assert seen["headers"]["authorization"] == "Bearer sk-test-123"


async def test_no_authorization_header_without_a_key():
    seen, handler = capture()
    await run(handler)
    assert "authorization" not in seen["headers"]


async def test_api_key_never_appears_in_error_text_even_if_the_server_echoes_it():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "Invalid key sk-test-123 for user"}})

    with pytest.raises(LLMError) as exc:
        await run(handler, api_key="sk-test-123")
    assert "sk-test-123" not in str(exc.value) and "[redacted]" in str(exc.value)


def test_api_key_is_not_in_the_config_repr():
    assert "topsecret" not in repr(LLMConfig(api="openai", api_key="topsecret"))


# ---------------------------------------------------------------- budget / helpers


async def test_budget_is_checked_before_any_http_call():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completion())

    client = make(handler)
    with pytest.raises(BudgetExceeded):
        await client.generate(req(user="x" * 30000))
    await client.aclose()
    assert calls == []


async def test_capabilities_never_raise():
    client = make(lambda r: httpx.Response(500))
    assert await client.capabilities("r-model") == {"thinking": True}
    assert await make(lambda r: httpx.Response(500), supports_thinking=False).capabilities("m") == {"thinking": False}
    await client.aclose()


async def test_tags_and_loaded():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"object": "list", "data": [{"id": "r-model"}, {"id": "other"}]})

    client = make(handler)
    assert await client.tags() == ["r-model", "other"]
    assert await client.loaded() == ["r-model"]  # only the configured models the server lists
    await client.aclose()


async def test_tags_errors():
    with pytest.raises(LLMError):
        await make(lambda r: httpx.Response(500, json={"error": "down"})).tags()
    with pytest.raises(LLMError, match="bad /v1/models"):
        await make(lambda r: httpx.Response(200, json={"nope": 1})).tags()


async def test_warm_sends_a_one_token_request_and_returns_seconds():
    seen, handler = capture()
    client = make(handler)
    secs = await client.warm("router")
    await client.aclose()
    assert seen["json"]["model"] == "q-model" and seen["json"]["max_tokens"] == 1 and secs >= 0


async def test_warm_failure_is_an_llm_error():
    with pytest.raises(LLMError, match="warm"):
        await make(lambda r: httpx.Response(503, json={"error": "loading"})).warm("reasoner")


# ----------------------------------------------------------------------- config


def test_config_defaults_to_ollama(monkeypatch):
    for k in ("TUTOR_LLM_API", "TUTOR_LLM_API_KEY", "TUTOR_LLM_BASE_URL", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    cfg = LLMConfig.from_env()
    assert cfg.api == "ollama" and cfg.api_key is None and cfg.base_url == "http://127.0.0.1:11434"
    assert isinstance(build_client(cfg), OllamaClient)


def test_config_switch_returns_the_openai_client(monkeypatch):
    monkeypatch.setenv("TUTOR_LLM_API", "openai")
    monkeypatch.setenv("TUTOR_LLM_API_KEY", "k")
    monkeypatch.delenv("TUTOR_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    cfg = LLMConfig.from_env()
    client = build_client(cfg)
    assert cfg.api == "openai" and cfg.base_url == "http://127.0.0.1:8000"
    assert isinstance(client, OpenAICompatClient) and client._api_key == "k"


def test_llm_base_url_env_takes_precedence(monkeypatch):
    monkeypatch.setenv("TUTOR_LLM_API", "openai")
    monkeypatch.setenv("TUTOR_LLM_BASE_URL", "http://dgx:9000/v1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    assert LLMConfig.from_env().base_url == "http://dgx:9000/v1"


def test_unknown_api_is_rejected(monkeypatch):
    monkeypatch.setenv("TUTOR_LLM_API", "grpc")
    with pytest.raises(ValueError, match="TUTOR_LLM_API"):
        LLMConfig.from_env()


async def test_scheduled_client_works_with_the_openai_backend():
    from tutor.llm.scheduler import ScheduledBackend

    cfg = LLMConfig(api="openai", models=dict(MODELS))
    sched = build_scheduled_client(cfg, SchedulerConfig(max_concurrency=2, swap_threshold_s=1.0))
    assert isinstance(sched, ScheduledBackend) and isinstance(sched._inner, OpenAICompatClient)
    await sched._inner.aclose()


async def test_scheduler_can_drive_the_openai_client_end_to_end():
    from tutor.llm.scheduler import ScheduledBackend

    seen, handler = capture()
    inner = make(handler)
    sched = ScheduledBackend(inner, MODELS)
    resp = await sched.generate(req())
    await inner.aclose()
    assert resp.text == '{"x": 1}' and seen["path"] == "/v1/chat/completions"
