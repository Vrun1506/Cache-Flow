import json

import httpx
import pytest

from tutor.llm import LLMError, LLMRequest, OllamaClient

pytestmark = pytest.mark.anyio

MODELS = {"reasoner": "r:1", "router": "q:1", "vision": "v:1"}


def make(handler):
    return OllamaClient("http://x", MODELS, transport=httpx.MockTransport(handler))


def req(**kw):
    base = dict(agent="a", tier="reasoner", system="s", user="u")
    base.update(kw)
    return LLMRequest(**base)


async def test_think_is_dropped_for_models_without_the_thinking_capability():
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        seen.update(body)
        return httpx.Response(200, json={"message": {"content": "{}"}, "done_reason": "stop"})

    await make(handler).generate(req(think=True))
    assert seen["think"] is False


async def test_think_is_kept_for_models_that_support_it():
    seen = {}

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking"]})
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "{}"}})

    await make(handler).generate(req(think=True))
    assert seen["think"] is True


async def test_unknown_capabilities_do_not_change_the_request():
    seen = {}

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(404, json={"error": "nope"})
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "{}"}})

    await make(handler).generate(req(think=True))
    assert seen["think"] is True


async def test_capabilities_are_cached():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking"]})
        return httpx.Response(200, json={"message": {"content": "{}"}})

    client = make(handler)
    await client.generate(req(think=True))
    await client.generate(req(think=True))
    assert calls.count("/api/show") == 1


async def test_no_capability_lookup_when_not_thinking():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"message": {"content": "{}"}})

    await make(handler).generate(req(think=False))
    assert calls == ["/api/chat"]


async def test_warm_loads_the_tiers_model_without_generating():
    seen = {}

    def handler(request):
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"done": True})

    seconds = await make(handler).warm("router")
    assert seen["path"] == "/api/chat"
    assert seen["body"]["model"] == "q:1" and seen["body"]["messages"] == []
    assert seen["body"]["keep_alive"] == "5m" and seconds >= 0


async def test_warm_failure_is_an_llm_error():
    client = make(lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(LLMError):
        await client.warm("vision")
