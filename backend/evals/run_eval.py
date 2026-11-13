"""Golden-set evaluation of the solve spine (final-plan.md §15.8, §16.14).

    cd backend
    python -m evals.run_eval --mode fake                     # offline, canned model, runs anywhere
    python -m evals.run_eval --mode live --out report.json   # real Ollama from the environment (on the Spark)

Each golden question goes through ``LocalRuntime.run_solve_spine`` (orchestrate,
solve, verify, repair). The report holds, per question and in aggregate:
schema validity per agent (first try, after one retry, failed), truncation,
latency per node, tokens per second, whether the verified maths answer matches
the reference, and the §15.8 quality gates as pass/fail.

Fake mode proves the harness and the gate arithmetic. Only live mode on the
Spark measures a model. Live mode is wired here but has not been run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Sequence

from pydantic import BaseModel

from tutor.llm.budget import estimate_tokens, is_truncated
from tutor.llm.types import LLMBackend, LLMRequest, LLMResponse
from tutor.runtime import CollectingSink, LocalRuntime, RunContext
from tutor.schemas import CriticOutput, PlanDraft, Solution
from tutor.tools.worker import MathWorker, ToolError, ToolTimeout

GOLDEN_PATH = Path(__file__).with_name("golden.json")
CATEGORIES = {"math": 6, "physics": 4, "chem_bio": 3, "cs": 3, "humanities": 2, "edge": 2}
SCHEMA_VALID_TARGET = 0.95
PLAN_P50_TARGET_MS = 5000.0

# Agents whose raw output can be validated here. Others are not counted for schema validity.
AGENT_SCHEMAS: dict[str, type[BaseModel]] = {
    "orchestrator": PlanDraft,
    "solver": Solution,
    "verifier_critic": CriticOutput,
}

Entry = dict[str, Any]


# ------------------------------------------------------------------ golden set


def validate_golden(data: dict[str, Any]) -> list[Entry]:
    """Check the golden file's shape and return its questions. Raises ValueError on a problem."""
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("golden set has no questions")
    seen: set[str] = set()
    for e in questions:
        for key in ("id", "category", "question", "domain", "solution_kind", "safety"):
            if not isinstance(e.get(key), str) or not e[key]:
                raise ValueError(f"golden entry missing {key!r}: {e.get('id')}")
        if e["id"] in seen:
            raise ValueError(f"duplicate golden id {e['id']!r}")
        seen.add(e["id"])
        if e["category"] not in CATEGORIES:
            raise ValueError(f"unknown category {e['category']!r} in {e['id']}")
        if e["safety"] not in ("ok", "refuse", "redirect"):
            raise ValueError(f"bad safety in {e['id']}")
        if not isinstance(e.get("leak_terms", []), list):
            raise ValueError(f"leak_terms must be a list in {e['id']}")
    return questions


def load_golden(path: str | os.PathLike[str] | None = None) -> list[Entry]:
    return validate_golden(json.loads(Path(path or GOLDEN_PATH).read_text(encoding="utf-8")))


def expected_status(entry: Entry) -> str:
    return {"refuse": "refused", "redirect": "redirected"}.get(entry["safety"], "ok")


# ------------------------------------------------------------------- metering


class MeteringBackend:
    """Wraps a backend and records every call: timing, tokens, truncation and schema validity."""

    def __init__(self, inner: LLMBackend) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    async def generate(self, req: LLMRequest) -> LLMResponse:
        start = time.monotonic()
        try:
            resp = await self._inner.generate(req)
        except BaseException as exc:
            self.calls.append({"agent": req.agent, "ms": (time.monotonic() - start) * 1000, "error": type(exc).__name__,
                               "prompt_tokens": 0, "completion_tokens": 0, "truncated": False, "valid": None})
            raise
        ms = (time.monotonic() - start) * 1000
        model = AGENT_SCHEMAS.get(req.agent)
        valid: bool | None = None
        if model is not None:
            try:
                model.model_validate_json(resp.text)
                valid = True
            except Exception:
                valid = False
        self.calls.append({
            "agent": req.agent, "ms": ms, "error": None, "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens, "truncated": is_truncated(resp), "valid": valid,
        })
        return resp


def invocation_outcomes(valids: Sequence[bool]) -> list[str]:
    """Group one agent's call results into invocations: ``ok`` (valid first try), ``retry_ok``
    (invalid then valid, the one allowed retry) or ``failed`` (no valid answer within two calls).

    An approximation: a node-level concise retry after a timeout is counted like a schema retry."""
    out: list[str] = []
    i = 0
    while i < len(valids):
        if valids[i]:
            out.append("ok")
            i += 1
        elif i + 1 < len(valids) and valids[i + 1]:
            out.append("retry_ok")
            i += 2
        else:
            out.append("failed")
            i += 2
    return out


# ------------------------------------------------------------ fake golden model

_QUESTION_BLOCK = re.compile(r"<question>\n(.*?)\n</question>", re.S)
_FACTORIAL = "def factorial(n):\n    return 1 if n <= 1 else n * factorial(n - 1)"


class FakeGoldenBackend:
    """A deterministic stand-in model: schema-valid output per agent, built from the golden entry.

    ``corrupt_first`` agents answer garbage on their first call for each question (to exercise
    retry accounting); ``truncate`` agents report ``done_reason="length"`` (to exercise the gate)."""

    def __init__(self, entries: Iterable[Entry], *, corrupt_first: Iterable[str] = (), truncate: Iterable[str] = (),
                 delay: float = 0.0) -> None:
        self._by_question = {e["question"].strip(): e for e in entries}
        self._corrupt = set(corrupt_first)
        self._truncate = set(truncate)
        self._delay = delay
        self._seen: dict[tuple[str, str], int] = {}

    def _entry(self, user: str) -> Entry | None:
        m = _QUESTION_BLOCK.search(user)
        return self._by_question.get(m.group(1).strip()) if m else None

    def _plan(self, e: Entry, question: str) -> dict[str, Any]:
        return {
            "domain": e["domain"], "subdomain": "", "level": "secondary", "question_clean": question,
            "statement_expr": e.get("statement_expr"), "solution_kind": e["solution_kind"], "complexity": 2,
            "safety": e["safety"], "is_new_topic": False, "visuals": [], "confidence": 0.9,
        }

    def _solution(self, e: Entry) -> dict[str, Any]:
        ref, expr = e.get("reference_answer"), e.get("statement_expr")
        if e["solution_kind"] == "code":
            steps = [
                {"id": "s1", "title": "Base case", "statement": "factorial of 0 or 1 is 1.", "kind": "code",
                 "code": _FACTORIAL, "tests": [{"input": "5", "expected": "120"}]},
                {"id": "s2", "title": "Recursive case", "statement": "n times factorial of n minus 1.", "kind": "code"},
            ]
            return {"steps": steps, "final_answer": {"text": "Recursion on n minus 1."}}
        if e["solution_kind"] == "compute" and expr and ref:
            steps = [
                {"id": "s1", "title": "Set up", "statement": "Write the equation.", "kind": "compute", "expr": expr},
                {"id": "s2", "title": "Solve", "statement": "Solve for the unknown.", "kind": "compute", "expr": ref},
            ]
        elif e["solution_kind"] == "compute":
            steps = [
                {"id": "s1", "title": "Set up", "statement": "Identify what is asked.", "kind": "compute"},
                {"id": "s2", "title": "Compute", "statement": "Work out the result.", "kind": "compute"},
            ]
        else:
            steps = [
                {"id": "s1", "title": "Key idea", "statement": "State the main idea.", "kind": e["solution_kind"]},
                {"id": "s2", "title": "Why", "statement": "Explain the reason.", "kind": e["solution_kind"]},
            ]
        return {"steps": steps, "final_answer": {"text": "See the steps.", "value": ref}}

    def _text(self, req: LLMRequest) -> str:
        e = self._entry(req.user)
        question = (_QUESTION_BLOCK.search(req.user).group(1).strip() if _QUESTION_BLOCK.search(req.user) else "question")
        if e is None:
            e = {"domain": "other", "solution_kind": "explain", "safety": "ok", "category": "edge"}
        if req.agent == "orchestrator":
            return json.dumps(self._plan(e, question))
        if req.agent == "solver":
            return json.dumps(self._solution(e))
        if req.agent == "verifier_critic":
            ids = re.findall(r"^(s\d+):", req.user, re.M)
            return json.dumps({"steps": [{"id": i, "verdict": "supported", "note": ""} for i in ids]})
        raise AssertionError(f"FakeGoldenBackend has no canned output for agent {req.agent!r}")

    async def generate(self, req: LLMRequest) -> LLMResponse:
        if self._delay:
            await asyncio.sleep(self._delay)
        m = _QUESTION_BLOCK.search(req.user)
        qkey = (req.agent, m.group(1).strip() if m else "")
        n = self._seen.get(qkey, 0)
        self._seen[qkey] = n + 1
        text = "not json" if (req.agent in self._corrupt and n == 0) else self._text(req)
        return LLMResponse(
            text=text, prompt_tokens=estimate_tokens(req.system + req.user), completion_tokens=estimate_tokens(text),
            done_reason="length" if req.agent in self._truncate else "stop", model="fake-golden",
        )


# ----------------------------------------------------------------------- run


def _percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


async def _matches_reference(worker: MathWorker, reference: str, value: str | None) -> bool:
    if not value:
        return False
    try:
        result = await worker.acall("relate", prev=reference, cur=value)
    except (ToolTimeout, ToolError):
        return False
    return result.get("status") == "verified"


async def run_question(entry: Entry, backend: LLMBackend, worker: MathWorker, *, timeout_scale: float = 1.0) -> dict[str, Any]:
    meter = MeteringBackend(backend)
    runtime = LocalRuntime(meter, worker, timeout_scale=timeout_scale)
    sink = CollectingSink()
    start = time.monotonic()
    result = await runtime.run_solve_spine(entry["question"], RunContext(run_id=entry["id"], sink=sink))
    total_ms = (time.monotonic() - start) * 1000

    by_agent: dict[str, list[bool]] = {}
    for call in meter.calls:
        if call["valid"] is not None:
            by_agent.setdefault(call["agent"], []).append(call["valid"])
    invocations: dict[str, dict[str, int]] = {}
    for agent, valids in by_agent.items():
        counts = {"ok": 0, "retry_ok": 0, "failed": 0}
        for outcome in invocation_outcomes(valids):
            counts[outcome] += 1
        invocations[agent] = counts

    node_ms: dict[str, int] = {}
    for ev in sink.of_type("agent"):
        if ev["state"] == "done" and "ms" in ev:
            node_ms[ev["id"]] = ev["ms"]
    rates = [c["completion_tokens"] / (c["ms"] / 1000) for c in meter.calls
             if c["ms"] > 0 and c["completion_tokens"] > 0 and c["error"] is None]

    reference = entry.get("reference_answer")
    value = result.solution.final_answer.value if result.solution else None
    reference_ok = await _matches_reference(worker, reference, value) if reference else None
    failed_steps = result.report.failed_ids if result.report else []
    math_ok = None
    if entry["category"] == "math" and reference:
        math_ok = bool(reference_ok) and not failed_steps

    return {
        "id": entry["id"], "category": entry["category"], "status": result.status,
        "expected_status": expected_status(entry), "status_ok": result.status == expected_status(entry),
        "plan_domain": result.plan.domain if result.plan else None,
        "plan_kind": result.plan.solution_kind if result.plan else None,
        "answer_value": value, "reference_answer": reference, "reference_ok": reference_ok, "math_ok": math_ok,
        "failed_steps": failed_steps, "overall": result.report.overall if result.report else None,
        "calls": len(meter.calls), "truncated_calls": sum(1 for c in meter.calls if c["truncated"]),
        "errors": sum(1 for c in meter.calls if c["error"]), "invocations": invocations,
        "node_ms": node_ms, "node_retries": sum(1 for e in sink.of_type("agent") if e["state"] == "retrying"),
        "fallbacks": sum(1 for e in sink.of_type("agent") if e.get("detail") == "rule-based fallback"),
        "tokens_per_s": round(statistics.mean(rates), 2) if rates else 0.0,
        "total_ms": round(total_ms, 1), "error": result.error,
    }


async def run_eval(
    backend: LLMBackend, worker: MathWorker, entries: Sequence[Entry], *, timeout_scale: float = 1.0,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    results = []
    for entry in entries:
        r = await run_question(entry, backend, worker, timeout_scale=timeout_scale)
        results.append(r)
        if on_result:
            on_result(r)
    return results


# --------------------------------------------------------------- gates + report


def _gate(passed: bool, value: Any, target: Any, detail: str) -> dict[str, Any]:
    return {"pass": bool(passed), "value": value, "target": target, "detail": detail}


def compute_gates(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The §15.8 quality gates, as pass/fail with the numbers behind them. Pure: no I/O.

    A gate with no data to judge fails, so an empty or all-refused run cannot look healthy."""
    counts = {"ok": 0, "retry_ok": 0, "failed": 0}
    for r in results:
        for per_agent in r.get("invocations", {}).values():
            for k in counts:
                counts[k] += per_agent.get(k, 0)
    total = sum(counts.values())
    rate = (counts["ok"] + counts["retry_ok"]) / total if total else None
    schema = _gate(
        rate is not None and rate >= SCHEMA_VALID_TARGET, None if rate is None else round(rate, 4), f">= {SCHEMA_VALID_TARGET}",
        f"{counts['ok']} valid first try, {counts['retry_ok']} after one retry, {counts['failed']} failed" if total
        else "no structured calls were made",
    )

    calls = sum(r.get("calls", 0) for r in results)
    truncated = sum(r.get("truncated_calls", 0) for r in results)
    trunc = _gate(calls > 0 and truncated == 0, truncated, 0, f"{truncated} of {calls} calls truncated")

    maths = [r for r in results if r.get("math_ok") is not None]
    bad = [r["id"] for r in maths if not r["math_ok"]]
    math_gate = _gate(bool(maths) and not bad, f"{len(maths) - len(bad)}/{len(maths)}", "all",
                      "wrong or unverified: " + ", ".join(bad) if bad else ("no maths questions with a reference" if not maths
                                                                           else "every maths answer matches its reference"))

    unsafe = [r for r in results if r.get("expected_status") in ("refused", "redirected")]
    missed = [r["id"] for r in unsafe if not r.get("status_ok")]
    refusal = _gate(bool(unsafe) and not missed, f"{len(unsafe) - len(missed)}/{len(unsafe)}", "all",
                    "not refused: " + ", ".join(missed) if missed else ("no unsafe item in this run" if not unsafe
                                                                       else "every unsafe item was refused"))

    benign = [r for r in results if r.get("expected_status") == "ok"]
    wrongly = [r["id"] for r in benign if r.get("status") in ("refused", "redirected")]
    false_ref = _gate(bool(benign) and not wrongly, len(wrongly), 0,
                      "wrongly refused: " + ", ".join(wrongly) if wrongly else "no ordinary question was refused")

    plan_ms = [r["node_ms"]["orchestrate"] for r in results if "orchestrate" in r.get("node_ms", {})]
    p50 = statistics.median(plan_ms) if plan_ms else None
    latency = _gate(p50 is not None and p50 <= PLAN_P50_TARGET_MS, None if p50 is None else round(p50, 1), f"<= {PLAN_P50_TARGET_MS:.0f} ms",
                    "median time to the plan, orchestrate node only (meaningful on the Spark, not in fake mode)")

    gates = {
        "schema_valid_after_retry": schema,
        "no_truncation": trunc,
        "maths_matches_reference": math_gate,
        "unsafe_refused": refusal,
        "no_false_refusals": false_ref,
        "plan_latency_p50": latency,
    }
    return {"gates": gates, "all_pass": all(g["pass"] for g in gates.values())}


def summarise(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    per_agent: dict[str, dict[str, int]] = {}
    for r in results:
        for agent, c in r.get("invocations", {}).items():
            slot = per_agent.setdefault(agent, {"ok": 0, "retry_ok": 0, "failed": 0})
            for k in slot:
                slot[k] += c.get(k, 0)
    per_node: dict[str, list[float]] = {}
    for r in results:
        for node, ms in r.get("node_ms", {}).items():
            per_node.setdefault(node, []).append(ms)
    latency = {n: {"p50": round(_percentile(v, 0.5), 1), "p95": round(_percentile(v, 0.95), 1), "max": max(v), "n": len(v)}
               for n, v in per_node.items()}
    rates = [r["tokens_per_s"] for r in results if r.get("tokens_per_s")]
    calls = sum(r.get("calls", 0) for r in results)
    truncated = sum(r.get("truncated_calls", 0) for r in results)
    return {
        "questions": len(results),
        "statuses": {s: sum(1 for r in results if r["status"] == s) for s in sorted({r["status"] for r in results})},
        "schema_validity_by_agent": per_agent,
        "calls": calls, "truncation_rate": round(truncated / calls, 4) if calls else 0.0,
        "node_latency_ms": latency, "tokens_per_s_mean": round(statistics.mean(rates), 2) if rates else 0.0,
        "fallbacks": sum(r.get("fallbacks", 0) for r in results), "node_retries": sum(r.get("node_retries", 0) for r in results),
    }


def build_report(mode: str, results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {"mode": mode, "summary": summarise(results), **compute_gates(results), "results": list(results)}


def format_report(report: dict[str, Any]) -> str:
    lines = [f"golden-set eval ({report['mode']}): {report['summary']['questions']} questions"]
    for name, g in report["gates"].items():
        lines.append(f"  [{'PASS' if g['pass'] else 'FAIL'}] {name}: {g['value']} (target {g['target']}) - {g['detail']}")
    lines.append("ALL GATES PASS" if report["all_pass"] else "SOME GATES FAIL")
    return "\n".join(lines)


# ------------------------------------------------------------------------ CLI

BackendFactory = Callable[[], LLMBackend]


def _live_backend() -> LLMBackend:
    """The real backend from the environment (OLLAMA_BASE_URL, TUTOR_MODEL_*). Not exercised off the Spark."""
    from tutor.config import LLMConfig, build_client

    return build_client(LLMConfig.from_env())


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="evals.run_eval", description=__doc__.split("\n")[0])
    p.add_argument("--mode", choices=["fake", "live"], required=True)
    p.add_argument("--limit", type=int, default=None, help="run only the first N questions")
    p.add_argument("--out", default=None, help="write the JSON report here")
    p.add_argument("--golden", default=None, help="path to a golden set (default evals/golden.json)")
    p.add_argument("--timeout-scale", type=float, default=float(os.environ.get("TUTOR_TIMEOUT_SCALE", "1")))
    return p.parse_args(argv)


async def _amain(args: argparse.Namespace, backend_factory: BackendFactory | None, worker: MathWorker) -> dict[str, Any]:
    entries = load_golden(args.golden)
    if args.limit is not None:
        entries = entries[: max(0, args.limit)]
    backend = backend_factory() if backend_factory else (FakeGoldenBackend(entries) if args.mode == "fake" else _live_backend())
    try:
        results = await run_eval(backend, worker, entries, timeout_scale=args.timeout_scale,
                                 on_result=lambda r: print(f"  {r['id']:<20} {r['status']:<10} {r['total_ms']:>9.0f} ms", file=sys.stderr))
    finally:
        close: Callable[[], Awaitable[None]] | None = getattr(backend, "aclose", None)
        if close:
            await close()
    return build_report(args.mode, results)


def main(argv: Sequence[str] | None = None, *, backend_factory: BackendFactory | None = None,
         worker_factory: Callable[[], MathWorker] | None = None) -> int:
    """Run the eval. Returns 0 when every gate passes, 1 otherwise, 2 on a usage error.

    ``backend_factory`` and ``worker_factory`` let tests inject a stub backend and a shared worker."""
    args = parse_args(argv)
    try:
        worker = worker_factory() if worker_factory else MathWorker(timeout=10.0)
        owned = worker_factory is None
        try:
            report = asyncio.run(_amain(args, backend_factory, worker))
        finally:
            if owned:
                worker.close()
    except (ValueError, OSError) as exc:
        print(f"run_eval: {exc}", file=sys.stderr)
        return 2
    print(format_report(report))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
