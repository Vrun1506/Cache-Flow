"""Run the solve spine against a real Ollama and print what happened.

    cd backend
    OLLAMA_BASE_URL=http://127.0.0.1:11434 \
    TUTOR_MODEL_REASONER=gemma3:12b TUTOR_MODEL_ROUTER=gemma3:12b TUTOR_MODEL_VISION=gemma3:12b \
    .venv/bin/python scripts/live_solve.py "Solve x^2 + 5x + 6 = 0"

Uses the same env variables as production (tutor/config.py), so the same
command works on the Spark with the real model tags.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tutor.config import LLMConfig, build_client  # noqa: E402
from tutor.runtime import LocalRuntime, RunContext  # noqa: E402
from tutor.tools.worker import MathWorker  # noqa: E402


async def main(question: str) -> None:
    config = LLMConfig.from_env()
    client = build_client(config)
    print(f"ollama: {config.base_url}  models: {config.models}")
    print(f"installed: {await client.tags()}")
    # A cold model load can outlast a node timeout, so load each model first (§15, Phase 0).
    warmed: set[str] = set()
    for tier, tag in config.models.items():
        if tag not in warmed:
            print(f"warming {tier} ({tag}) ... {await client.warm(tier):.1f}s")
            warmed.add(tag)
    print()

    async def show(event: dict) -> None:
        extra = {k: v for k, v in event.items() if k not in ("type", "run_id", "seq", "nodes")}
        print(f"  [{event['seq']:>2}] {event['type']:<9} {extra}")

    worker = MathWorker(timeout=10.0)
    scale = float(os.environ.get("TUTOR_TIMEOUT_SCALE", "1"))  # >1 on a slow dev machine
    runtime = LocalRuntime(client, worker, timeout_scale=scale)
    start = time.monotonic()
    result = await runtime.run_solve_teach(question, RunContext(sink=show))
    print(f"\nstatus: {result.status} in {time.monotonic() - start:.1f}s  error: {result.error}")
    if result.plan:
        p = result.plan
        print(f"plan: {p.domain}/{p.solution_kind} level={p.level} complexity={p.complexity} "
              f"statement={p.statement_expr!r} visuals={[v.concept for v in p.visuals]}")
    if result.solution:
        for step in result.solution.steps:
            print(f"  {step.id}: {step.title} | expr={step.expr!r}")
        print(f"  final: {result.solution.final_answer}")
    if result.report:
        print(f"verify: overall={result.report.overall}")
        for v in result.report.steps + ([result.report.final_answer] if result.report.final_answer else []):
            print(f"  {v.id}: {v.status} ({v.method}) {v.note}")
    worker.close()
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "Solve x^2 + 5x + 6 = 0"))
