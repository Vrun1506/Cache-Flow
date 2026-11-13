"""Live checks for the Board director against a running Ollama.

    TUTOR_LIVE=1 TUTOR_LIVE_MODEL=gemma3:4b .venv/bin/python -m pytest tests/test_board_live.py
"""

import os

import httpx
import pytest

from tutor.agents.board_director import run_board_director
from tutor.config import LLMConfig, build_client
from tutor.schemas.board import board_llm_schema

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("TUTOR_LIVE") != "1", reason="needs a running Ollama (TUTOR_LIVE=1)"),
]


def test_board_schema_is_accepted_by_ollama():
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    resp = httpx.post(
        f"{base}/api/chat",
        timeout=300,
        json={
            "model": os.environ.get("TUTOR_LIVE_MODEL", "gemma3:4b"),
            "stream": False,
            "think": False,
            "format": board_llm_schema(),
            "messages": [{"role": "user", "content": "Reply with a tiny example."}],
            "options": {"num_ctx": 4096, "num_predict": 80, "temperature": 0},
        },
    )
    assert resp.status_code == 200, resp.text[:300]


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_director_end_to_end(anyio_backend):
    client = build_client(LLMConfig.from_env())
    try:
        plan = await run_board_director(
            client, 2, "Factorise", ["(x+2)(x+3) = 0"],
            "Now we use that pair.", "Write the quadratic as two brackets, x plus 2 times x plus 3.",
            [{"id": "b-1-1", "kind": "write", "label": "x^2 + 5x + 6 = 0"}],
        )
    finally:
        await client.aclose()
    assert plan.commands and plan.commands[0].id == "b-2-1"
