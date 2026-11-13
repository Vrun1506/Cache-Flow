import asyncio

import pytest

from tutor.llm import (
    BudgetExceeded,
    FakeLLM,
    LLMRequest,
    LLMResponse,
    check_budget,
    estimate_tokens,
    is_truncated,
    request_tokens,
)
from tutor.llm.budget import IMAGE_TOKENS

pytestmark = pytest.mark.anyio


def req(**kw):
    base = dict(agent="a", tier="reasoner", system="s", user="u")
    base.update(kw)
    return LLMRequest(**base)


def test_estimate_tokens_is_ceil_len_over_three():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("abcd") == 2
    assert estimate_tokens("x" * 300) == 100


def test_request_tokens_counts_system_user_and_images_not_schema():
    r = req(system="x" * 30, user="y" * 60, schema={"type": "object", "properties": {"k": {"type": "string"}}})
    assert request_tokens(r) == 10 + 20
    r2 = req(system="x" * 30, user="y" * 60, images=(b"1", b"2"))
    assert request_tokens(r2) == 30 + 2 * IMAGE_TOKENS


def test_check_budget_boundary():
    # prompt 1000 tokens + num_predict 6892 + margin 300 = 8192 -> allowed
    ok = req(system="x" * 3000, user="", num_predict=6892)
    check_budget(ok)
    over = req(system="x" * 3000, user="", num_predict=6893)
    with pytest.raises(BudgetExceeded) as exc:
        check_budget(over)
    assert "8193" in str(exc.value) and "8192" in str(exc.value)


def test_check_budget_counts_images_and_custom_ctx():
    with pytest.raises(BudgetExceeded):
        check_budget(req(images=(b"x",) * 8, num_predict=500))  # 8000 + 500 + 300 > 8192
    check_budget(req(images=(b"x",) * 7, num_predict=500))  # 7000 + 2 + 500 + 300 fits
    check_budget(req(num_predict=500), num_ctx=1000)  # 1 + 1 + 500 + 300 fits


def test_is_truncated():
    ok = LLMResponse("x", 100, 100, "stop", "m")
    assert not is_truncated(ok)
    assert is_truncated(LLMResponse("x", 10, 10, "length", "m"))
    assert is_truncated(LLMResponse("x", 8000, 192, "stop", "m"))
    assert not is_truncated(LLMResponse("x", 8000, 192, "stop", "m"), num_ctx=9000)


async def test_fake_pops_in_order_per_agent_and_records_calls():
    fake = FakeLLM({"a": ["one", "two"], "b": ["bee"]})
    r1 = await fake.generate(req(agent="a"))
    r2 = await fake.generate(req(agent="b"))
    r3 = await fake.generate(req(agent="a"))
    assert (r1.text, r2.text, r3.text) == ("one", "bee", "two")
    assert r1.done_reason == "stop" and r1.model == "fake"
    assert r1.completion_tokens == estimate_tokens("one")
    assert [c.agent for c in fake.calls] == ["a", "b", "a"]


async def test_fake_returns_llmresponse_and_raises_exceptions():
    custom = LLMResponse("t", 1, 2, "length", "x")
    fake = FakeLLM({"a": [custom, RuntimeError("boom")]})
    assert await fake.generate(req(agent="a")) is custom
    with pytest.raises(RuntimeError, match="boom"):
        await fake.generate(req(agent="a"))


async def test_fake_empty_queue_names_the_agent():
    fake = FakeLLM({"a": []})
    with pytest.raises(AssertionError, match="'a'"):
        await fake.generate(req(agent="a"))
    with pytest.raises(AssertionError, match="'zzz'"):
        await fake.generate(req(agent="zzz"))


async def test_fake_delay_can_be_cancelled():
    fake = FakeLLM({"a": ["never"]}, delay=5.0)
    task = asyncio.create_task(fake.generate(req(agent="a")))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(fake.calls) == 1
