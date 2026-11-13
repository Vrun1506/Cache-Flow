import asyncio

import pytest

from sample_data import (
    ORCHESTRATOR_OK,
    QUESTION,
    SOLUTION_BAD_S2,
    SOLUTION_OK,
    plan_json,
)
from tutor.llm import FakeLLM
from tutor.runtime import CollectingSink, GRAPHS, LocalRuntime, RunContext

pytestmark = pytest.mark.anyio


def make(math_worker, script, **kw):
    llm = FakeLLM(script, **kw.pop("llm_kw", {}))
    return llm, LocalRuntime(llm, math_worker, **kw)


async def run(rt, question=QUESTION):
    sink = CollectingSink()
    ctx = RunContext(run_id="run-1", sink=sink)
    result = await rt.run_solve_spine(question, ctx)
    return result, sink


async def test_happy_path_events_and_result(math_worker):
    llm, rt = make(math_worker, {"orchestrator": [ORCHESTRATOR_OK], "solver": [SOLUTION_OK]})
    result, sink = await run(rt)

    assert result.status == "ok" and result.report.overall == "verified"
    assert [e["type"] for e in sink.events][0] == "plan"
    nodes = sink.events[0]["nodes"]
    assert [n["id"] for n in nodes] == ["orchestrate", "solve", "verify"]
    assert all(n["state"] == "queued" for n in nodes)
    assert sink.agent_states() == [
        ("orchestrate", "running"), ("orchestrate", "done"),
        ("solve", "running"), ("solve", "done"),
        ("verify", "running"), ("verify", "done"),
    ]
    seqs = [e["seq"] for e in sink.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert {e["run_id"] for e in sink.events} == {"run-1"}
    assert all(e["ms"] >= 0 for e in sink.of_type("agent") if e["state"] == "done")


async def test_plan_event_matches_the_template(math_worker):
    _, rt = make(math_worker, {"orchestrator": [ORCHESTRATOR_OK], "solver": [SOLUTION_OK]})
    _, sink = await run(rt)
    assert [n["id"] for n in sink.events[0]["nodes"]] == [n.id for n in GRAPHS["solve_teach"]]


async def test_orchestrator_failure_falls_back_to_rules_and_run_continues(math_worker):
    # Two attempts per node run, two model calls per attempt.
    llm, rt = make(math_worker, {"orchestrator": ["x", "x", "x", "x"], "solver": [SOLUTION_OK]})
    result, sink = await run(rt, "Solve x^2 + 5x + 6 = 0")
    assert result.status == "ok"
    assert result.plan.confidence == 0.0 and result.plan.statement_expr is not None
    states = sink.agent_states()
    assert ("orchestrate", "retrying") in states and ("orchestrate", "done") in states
    assert [e for e in sink.of_type("agent") if e.get("detail") == "rule-based fallback"]


async def test_refusal_never_reaches_the_solver(math_worker):
    llm, rt = make(math_worker, {"orchestrator": [plan_json(safety="refuse")]})
    result, sink = await run(rt)
    assert result.status == "refused" and result.solution is None
    assert sink.of_type("tutor_say")
    assert [c.agent for c in llm.calls] == ["orchestrator"]


async def test_redirect_status(math_worker):
    _, rt = make(math_worker, {"orchestrator": [plan_json(safety="redirect")]})
    result, _ = await run(rt)
    assert result.status == "redirected"


async def test_solver_failure_ends_the_run_cleanly(math_worker):
    llm, rt = make(math_worker, {"orchestrator": [ORCHESTRATOR_OK], "solver": ["bad"] * 4})
    result, sink = await run(rt)
    assert result.status == "failed" and result.plan is not None
    assert ("solve", "failed") in sink.agent_states()
    assert sink.of_type("run_error")[0]["node"] == "solve"
    assert ("verify", "running") not in sink.agent_states()


async def test_repair_loop_fixes_a_failed_step(math_worker):
    llm, rt = make(
        math_worker,
        {"orchestrator": [ORCHESTRATOR_OK], "solver": [SOLUTION_BAD_S2, SOLUTION_OK]},
    )
    result, sink = await run(rt)
    assert result.status == "ok" and result.report.failed_ids == []
    assert result.report.overall == "verified"
    states = sink.agent_states()
    assert ("repair", "running") in states and ("repair", "done") in states
    repair_prompt = llm.calls[-1].user
    assert "<repair>" in repair_prompt and "s2:" in repair_prompt


async def test_repair_is_attempted_once_and_failures_stay_labelled(math_worker):
    llm, rt = make(
        math_worker,
        {"orchestrator": [ORCHESTRATOR_OK], "solver": [SOLUTION_BAD_S2, SOLUTION_BAD_S2]},
    )
    result, sink = await run(rt)
    assert result.status == "ok"
    assert result.report.failed_ids[0] == "s2"  # still failed, honestly reported
    assert [c.agent for c in llm.calls].count("solver") == 2  # one repair, no more


async def test_repair_model_outage_keeps_the_original(math_worker):
    llm, rt = make(
        math_worker,
        {"orchestrator": [ORCHESTRATOR_OK], "solver": [SOLUTION_BAD_S2, "bad", "bad"]},
    )
    result, sink = await run(rt)
    assert result.status == "ok" and result.report.failed_ids
    assert ("repair", "failed") in sink.agent_states()


async def test_cancellation_stops_the_run_and_emits_nothing_more(math_worker):
    llm, rt = make(
        math_worker,
        {"orchestrator": [ORCHESTRATOR_OK], "solver": [SOLUTION_OK]},
        llm_kw={"delay": 5.0},
    )
    sink = CollectingSink()
    task = asyncio.create_task(rt.run_solve_spine(QUESTION, RunContext(run_id="r", sink=sink)))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    count = len(sink.events)
    await asyncio.sleep(0.3)
    assert len(sink.events) == count
    assert ("orchestrate", "done") not in sink.agent_states()


async def test_run_deadline(math_worker):
    llm, rt = make(
        math_worker,
        {"orchestrator": [ORCHESTRATOR_OK], "solver": [SOLUTION_OK]},
        llm_kw={"delay": 5.0},
        deadline_s=0.3,
    )
    result, sink = await run(rt)
    assert result.status == "failed" and "deadline" in result.error
    assert sink.of_type("run_error")


async def test_node_timeout_triggers_concise_retry_then_fallback(math_worker, monkeypatch):
    import dataclasses

    from tutor.runtime import graphs, local

    fast = tuple(dataclasses.replace(n, timeout_s=0.2) if n.id == "orchestrate" else n for n in GRAPHS["solve_teach"])
    monkeypatch.setitem(graphs.GRAPHS, "solve_teach", fast)
    monkeypatch.setitem(local.GRAPHS, "solve_teach", fast)
    llm, rt = make(
        math_worker,
        {"orchestrator": [ORCHESTRATOR_OK, ORCHESTRATOR_OK], "solver": [SOLUTION_OK]},
        llm_kw={"delay": 0.0},
    )
    original = llm.generate

    async def slow_orchestrator(req):
        if req.agent == "orchestrator":
            await asyncio.sleep(1.0)
        return await original(req)

    llm.generate = slow_orchestrator
    result, sink = await run(rt)  # the rule-based plan extracts the same quadratic
    assert ("orchestrate", "retrying") in sink.agent_states()
    assert result.status == "ok"
    assert result.plan.confidence == 0.0  # the rule-based plan took over


async def test_timeout_scale_lets_a_slow_machine_finish(math_worker, monkeypatch):
    """The same 0.2 s node limit fails at scale 1 and succeeds at scale 20."""
    import dataclasses

    from tutor.runtime import graphs, local

    tight = tuple(dataclasses.replace(n, timeout_s=0.2) for n in GRAPHS["solve_teach"])
    monkeypatch.setitem(graphs.GRAPHS, "solve_teach", tight)
    monkeypatch.setitem(local.GRAPHS, "solve_teach", tight)

    async def run_at(scale):
        llm = FakeLLM({"orchestrator": [ORCHESTRATOR_OK] * 2, "solver": [SOLUTION_OK] * 2}, delay=0.5)
        rt = LocalRuntime(llm, math_worker, timeout_scale=scale)
        return (await run(rt))[0]

    assert (await run_at(1.0)).status == "failed"
    assert (await run_at(20.0)).status == "ok"


async def test_known_plan_skips_the_orchestrator(math_worker):
    from tutor.schemas import Plan

    llm, rt = make(math_worker, {"solver": [SOLUTION_OK]})
    plan = Plan.model_validate_json(ORCHESTRATOR_OK)
    sink = CollectingSink()
    result = await rt.run_solve_spine(QUESTION, RunContext(run_id="r", sink=sink), plan=plan)
    assert result.status == "ok" and result.plan is plan
    assert [c.agent for c in llm.calls] == ["solver"]
    assert [n["id"] for n in sink.events[0]["nodes"]] == ["solve", "verify"]
    assert ("orchestrate", "running") not in sink.agent_states()
