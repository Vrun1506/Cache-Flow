"""Live check that every agent schema is accepted by Ollama's grammar builder.

    TUTOR_LIVE=1 TUTOR_LIVE_MODEL=gemma3:4b .venv/bin/python -m pytest tests/test_live_schemas.py

Found the hard way: a regex `pattern` made Ollama answer 400 "failed to parse
grammar". Run this on the Spark against each installed model before relying on
structured output there.
"""

import os

import httpx
import pytest

from tutor.schemas import CriticOutput, PlanDraft, Solution, VerifyReport, inline_schema
from tutor.schemas.board import board_llm_schema
from tutor.schemas.lesson import LessonBatch
from tutor.schemas.qa import AskDraft
from tutor.schemas.quiz import FeedbackText, QuizSet, Verdict
from tutor.schemas.scene import AnimatorDraft

pytestmark = pytest.mark.skipif(os.environ.get("TUTOR_LIVE") != "1", reason="needs a running Ollama (TUTOR_LIVE=1)")


@pytest.mark.live
def _schemas():
    plain = [PlanDraft, Solution, VerifyReport, CriticOutput, LessonBatch, AskDraft, QuizSet, Verdict, FeedbackText]
    return [(m.__name__, inline_schema(m)) for m in plain] + [
        ("AnimatorDraft", inline_schema(AnimatorDraft, require_all=False)),
        ("BoardPlan", board_llm_schema()),
    ]


@pytest.mark.live
@pytest.mark.parametrize("name,schema", _schemas(), ids=lambda v: v if isinstance(v, str) else "")
def test_schema_is_accepted_by_ollama(name, schema):
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    model = os.environ.get("TUTOR_LIVE_MODEL", "gemma3:4b")
    resp = httpx.post(
        f"{base}/api/chat",
        timeout=300,
        json={
            "model": model,
            "stream": False,
            "think": False,
            "format": schema,
            "messages": [{"role": "user", "content": "Reply with a tiny example object."}],
            "options": {"num_ctx": 4096, "num_predict": 60, "temperature": 0},
        },
    )
    assert resp.status_code == 200, resp.text[:300]
