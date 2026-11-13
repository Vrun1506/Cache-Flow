import os

import pytest
from pydantic import BaseModel

from tutor.llm import (
    BudgetExceeded,
    FakeLLM,
    LLMRequest,
    LLMResponse,
    OllamaClient,
    StructuredOutputError,
    structured_call,
)
from tutor.schemas import inline_schema

pytestmark = pytest.mark.anyio


class Tiny(BaseModel):
    answer: int
    note: str = ""


def req(**kw):
    base = dict(agent="t", tier="reasoner", system="SYS", user="USR", think=True, num_predict=500)
    base.update(kw)
    return LLMRequest(**base)


async def test_valid_first_try():
    fake = FakeLLM({"t": ['{"answer": 4}']})
    out = await structured_call(fake, req(), Tiny)
    assert out.value == Tiny(answer=4)
    assert out.attempts == 1
    assert len(fake.calls) == 1


async def test_retry_on_invalid_json_appends_error_and_succeeds():
    fake = FakeLLM({"t": ["this is not json", '{"answer": 2}']})
    out = await structured_call(fake, req(), Tiny)
    assert out.value.answer == 2
    assert out.attempts == 2
    assert len(fake.calls) == 2
    retry = fake.calls[1]
    assert retry.user.startswith("USR")
    assert "Your previous output was invalid" in retry.user
    assert "Return only valid JSON matching the schema." in retry.user
    assert retry.think is True  # a validation retry does not change thinking
    assert len(retry.user) < len("USR") + 450


async def test_retry_on_schema_violation():
    fake = FakeLLM({"t": ['{"answer": "four"}', '{"answer": 4}']})
    out = await structured_call(fake, req(), Tiny)
    assert out.attempts == 2


async def test_truncation_retries_with_think_false_and_concise_note():
    cut = LLMResponse('{"answer": 1', 100, 500, "length", "m")
    fake = FakeLLM({"t": [cut, '{"answer": 9}']})
    out = await structured_call(fake, req(think=True), Tiny)
    assert out.value.answer == 9
    assert out.attempts == 2
    assert fake.calls[0].think is True
    assert fake.calls[1].think is False
    assert fake.calls[1].user.endswith("Be concise.")


async def test_valid_but_truncated_retries_then_accepts_valid_final_attempt():
    flagged = LLMResponse('{"answer": 1}', 8000, 192, "stop", "m")  # tokens fill the window
    fake = FakeLLM({"t": [flagged, flagged]})
    out = await structured_call(fake, req(), Tiny)
    assert out.value.answer == 1
    assert out.attempts == 2


async def test_raises_structured_output_error_after_final_failure():
    fake = FakeLLM({"t": ["nope", "still nope"]})
    with pytest.raises(StructuredOutputError) as exc:
        await structured_call(fake, req(agent="t"), Tiny)
    assert exc.value.agent == "t"
    assert exc.value.raw == "still nope"
    assert exc.value.last_error
    assert len(fake.calls) == 2


async def test_retries_zero_makes_one_call():
    fake = FakeLLM({"t": ["nope", '{"answer": 1}']})
    with pytest.raises(StructuredOutputError):
        await structured_call(fake, req(), Tiny, retries=0)
    assert len(fake.calls) == 1


async def test_budget_checked_before_any_call():
    fake = FakeLLM({"t": ['{"answer": 1}']})
    big = req(system="x" * 30000)
    with pytest.raises(BudgetExceeded):
        await structured_call(fake, big, Tiny)
    assert fake.calls == []


async def test_budget_rechecked_on_retry():
    # fits at first, but the appended error note pushes it over
    system = "x" * (3 * (8192 - 300 - 500) - 5)
    fake = FakeLLM({"t": ["nope", '{"answer": 1}']})
    with pytest.raises(BudgetExceeded):
        await structured_call(fake, req(system=system, user=""), Tiny)
    assert len(fake.calls) == 1


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("TUTOR_LIVE") != "1", reason="set TUTOR_LIVE=1 with a running Ollama")
async def test_live_smoke_structured_call():
    model = os.environ.get("TUTOR_LIVE_MODEL", "gemma3:4b")
    client = OllamaClient(
        "http://127.0.0.1:11434",
        {"reasoner": model, "router": model, "vision": model},
        timeout=120.0,
    )
    try:
        schema = inline_schema(Tiny)
        r = LLMRequest(
            agent="live",
            tier="router",
            system="You answer with JSON only. Fields: answer (integer), note (short string).",
            user="What is 6 times 7? Put the number in answer.",
            schema=schema,
            num_predict=200,
            think=False,
        )
        out = await structured_call(client, r, Tiny)
        print("LIVE value:", out.value, "attempts:", out.attempts, "raw:", out.response.text,
              "tokens:", out.response.prompt_tokens, out.response.completion_tokens,
              "ms:", round(out.response.total_ms))
        assert out.value.answer == 42
        assert out.response.model
    finally:
        await client.aclose()
