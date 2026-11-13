import base64
import json

import httpx
import pytest

from tutor.config import LLMConfig, build_client
from tutor.llm import LLMError, LLMRequest, LLMTimeout, OllamaClient

pytestmark = pytest.mark.anyio

MODELS = {"reasoner": "r-model", "router": "q-model", "vision": "v-model"}


def req(**kw):
    base = dict(agent="a", tier="reasoner", system="SYS", user="USR", temperature=0.2, num_predict=123, seed=7)
    base.update(kw)
    return LLMRequest(**base)


def ok_body(**over):
    body = {
        "model": "r-model",
        "message": {"role": "assistant", "content": '{"x": 1}'},
        "done_reason": "stop",
        "prompt_eval_count": 11,
        "eval_count": 22,
        "total_duration": 3_000_000_000,
    }
    body.update(over)
    return body


def make_client(handler, sleeps=None, **kw):
    async def fake_sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    return OllamaClient(
        "http://ollama.test:11434/",
        MODELS,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
        **kw,
    )


async def test_payload_shape_and_response_parsing():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    client = make_client(handler, keep_alive="10m")
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    resp = await client.generate(req(schema=schema, think=True))
    await client.aclose()

    assert seen["method"] == "POST" and seen["path"] == "/api/chat"
    p = seen["json"]
    assert p["model"] == "r-model"
    assert p["stream"] is False and p["think"] is True
    assert p["keep_alive"] == "10m"
    assert p["format"] == schema
    assert p["options"] == {"num_ctx": 8192, "num_predict": 123, "temperature": 0.2, "seed": 7}
    assert p["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    assert resp.text == '{"x": 1}'
    assert (resp.prompt_tokens, resp.completion_tokens) == (11, 22)
    assert resp.done_reason == "stop" and resp.model == "r-model"
    assert resp.total_ms == pytest.approx(3000.0)


async def test_format_omitted_without_schema_and_think_false_sent():
    seen = {}

    def handler(request):
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    client = make_client(handler)
    await client.generate(req())
    await client.aclose()
    assert "format" not in seen["json"]
    assert seen["json"]["think"] is False
    assert seen["json"]["keep_alive"] == "5m"


async def test_images_are_base64_on_user_message_and_tier_picks_model():
    seen = {}

    def handler(request):
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    client = make_client(handler)
    await client.generate(req(tier="vision", images=(b"\x89PNG", b"abc")))
    await client.aclose()
    p = seen["json"]
    assert p["model"] == "v-model"
    assert p["messages"][1]["images"] == [base64.b64encode(b"\x89PNG").decode(), base64.b64encode(b"abc").decode()]
    assert "images" not in p["messages"][0]


async def test_defaults_for_missing_fields():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "hi"}})

    client = make_client(handler)
    resp = await client.generate(req())
    await client.aclose()
    assert resp.done_reason == "stop"
    assert resp.prompt_tokens == 0 and resp.completion_tokens == 0
    assert resp.model == "r-model"


async def test_retries_5xx_twice_then_succeeds():
    calls = []
    sleeps = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=ok_body())

    client = make_client(handler, sleeps)
    resp = await client.generate(req())
    await client.aclose()
    assert resp.text == '{"x": 1}'
    assert len(calls) == 3
    assert sleeps == [0.5, 1.5]


async def test_5xx_exhausts_retries_with_error_mapping():
    calls = []
    sleeps = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, text="x" * 500)

    client = make_client(handler, sleeps)
    with pytest.raises(LLMError) as exc:
        await client.generate(req())
    await client.aclose()
    assert len(calls) == 3
    assert sleeps == [0.5, 1.5]
    assert "500" in str(exc.value)
    assert len(str(exc.value)) < 400


async def test_4xx_is_not_retried_and_includes_status_and_body():
    calls = []
    sleeps = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404, text='{"error":"model not found"}')

    client = make_client(handler, sleeps)
    with pytest.raises(LLMError) as exc:
        await client.generate(req())
    await client.aclose()
    assert len(calls) == 1 and sleeps == []
    assert "404" in str(exc.value) and "model not found" in str(exc.value)


async def test_transport_errors_are_retried_then_raise_llmerror():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("refused", request=request)

    sleeps = []
    client = make_client(handler, sleeps)
    with pytest.raises(LLMError):
        await client.generate(req())
    await client.aclose()
    assert len(calls) == 3 and sleeps == [0.5, 1.5]


async def test_timeout_maps_to_llmtimeout_without_retry():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    client = make_client(handler)
    with pytest.raises(LLMTimeout):
        await client.generate(req())
    await client.aclose()
    assert len(calls) == 1


async def test_bad_json_maps_to_llmerror():
    client = make_client(lambda request: httpx.Response(200, text="<html>not json</html>"))
    with pytest.raises(LLMError):
        await client.generate(req())
    await client.aclose()

    client = make_client(lambda request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(LLMError):
        await client.generate(req())
    await client.aclose()


async def test_tags_and_loaded():
    def handler(request: httpx.Request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma3:4b"}, {"name": "llama3.2:1b"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    client = make_client(handler)
    assert await client.tags() == ["gemma3:4b", "llama3.2:1b"]
    assert await client.loaded() == []
    await client.aclose()


def test_config_defaults_and_env(monkeypatch):
    for k in ("OLLAMA_BASE_URL", "TUTOR_MODEL_REASONER", "TUTOR_MODEL_ROUTER", "TUTOR_MODEL_VISION", "TUTOR_KEEP_ALIVE"):
        monkeypatch.delenv(k, raising=False)
    cfg = LLMConfig.from_env()
    assert cfg.base_url == "http://127.0.0.1:11434"
    assert cfg.models == {
        "reasoner": "nemotron-3-nano:30b",
        "router": "nemotron-3.5-lightning:30b",
        "vision": "qwen3.6:35b-a3b",
    }
    assert cfg.num_ctx == 8192 and cfg.keep_alive == "5m"

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("TUTOR_MODEL_REASONER", "gemma3:12b")
    monkeypatch.setenv("TUTOR_KEEP_ALIVE", "0")
    cfg = LLMConfig.from_env()
    assert cfg.base_url == "http://127.0.0.1:11435"
    assert cfg.models["reasoner"] == "gemma3:12b"
    assert cfg.keep_alive == "0"

    client = build_client(cfg)
    assert isinstance(client, OllamaClient)
    assert client.models["reasoner"] == "gemma3:12b" and client.num_ctx == 8192
