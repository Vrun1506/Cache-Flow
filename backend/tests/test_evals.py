import asyncio
import json
from collections import Counter
from pathlib import Path

import pytest

from evals import run_eval as ev
from tutor.llm import FakeLLM

GOLDEN = Path(ev.GOLDEN_PATH)


# ---------------------------------------------------------------- golden file


def test_golden_file_validates_with_20_unique_ids_and_the_right_categories():
    entries = ev.load_golden()
    assert len(entries) == 20 and len({e["id"] for e in entries}) == 20
    assert Counter(e["category"] for e in entries) == ev.CATEGORIES


def test_golden_content_matches_the_plan():
    entries = {e["id"]: e for e in ev.load_golden()}
    maths = [e for e in entries.values() if e["category"] == "math"]
    assert all(e["domain"] == "math" and e["reference_answer"] for e in maths)
    assert {e["domain"] for e in entries.values() if e["category"] == "physics"} == {"physics"}
    assert {e["domain"] for e in entries.values() if e["category"] == "chem_bio"} == {"chemistry", "biology"}
    assert all(e["domain"] == "cs" for e in entries.values() if e["category"] == "cs")
    unsafe = [e for e in entries.values() if e["safety"] == "refuse"]
    assert [e["id"] for e in unsafe] == ["edge-unsafe"]
    assert ev.expected_status(unsafe[0]) == "refused"
    assert all(isinstance(e["leak_terms"], list) for e in entries.values())


def test_every_domain_and_kind_is_a_value_the_plan_schema_accepts():
    from tutor.schemas import PlanDraft

    for e in ev.load_golden():
        PlanDraft.model_validate({"domain": e["domain"], "question_clean": e["question"],
                                  "solution_kind": e["solution_kind"], "safety": e["safety"]})


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda d: d["questions"].append(dict(d["questions"][0])), "duplicate"),
        (lambda d: d["questions"][0].pop("question"), "missing"),
        (lambda d: d["questions"][0].update(category="art"), "category"),
        (lambda d: d["questions"][0].update(safety="maybe"), "safety"),
        (lambda d: d.update(questions=[]), "no questions"),
        (lambda d: d["questions"][0].update(leak_terms="x"), "leak_terms"),
    ],
)
def test_validate_golden_rejects_bad_files(mutate, message):
    data = json.loads(GOLDEN.read_text())
    mutate(data)
    with pytest.raises(ValueError, match=message):
        ev.validate_golden(data)


# ------------------------------------------------------------ pure helpers


@pytest.mark.parametrize(
    "valids,expected",
    [
        ([], []),
        ([True], ["ok"]),
        ([False, True], ["retry_ok"]),
        ([False, False], ["failed"]),
        ([False], ["failed"]),
        ([True, False, True, True], ["ok", "retry_ok", "ok"]),
        ([False, False, True], ["failed", "ok"]),
    ],
)
def test_invocation_outcomes(valids, expected):
    assert ev.invocation_outcomes(valids) == expected


def test_percentile():
    assert ev._percentile([], 0.5) == 0.0
    assert ev._percentile([10], 0.95) == 10
    assert ev._percentile([1, 2, 3, 4], 0.5) == 2.5


def result(**kw):
    base = dict(id="q", category="math", status="ok", expected_status="ok", status_ok=True, math_ok=True,
                calls=3, truncated_calls=0, node_ms={"orchestrate": 1000},
                invocations={"orchestrator": {"ok": 1, "retry_ok": 0, "failed": 0}})
    base.update(kw)
    return base


def passing_set():
    return [result(id="a"), result(id="b", category="edge", expected_status="refused", status="refused", math_ok=None)]


def test_gates_all_pass_on_a_healthy_run():
    out = ev.compute_gates(passing_set())
    assert out["all_pass"] is True
    assert set(out["gates"]) == {"schema_valid_after_retry", "no_truncation", "maths_matches_reference",
                                 "unsafe_refused", "no_false_refusals", "plan_latency_p50"}
    assert all(g["pass"] for g in out["gates"].values())


def test_schema_gate_threshold_is_95_percent():
    inv = lambda ok, retry, failed: {"solver": {"ok": ok, "retry_ok": retry, "failed": failed}}
    row = [result(invocations=inv(18, 1, 1))]  # 19 of 20 = 0.95 passes exactly
    assert ev.compute_gates(row)["gates"]["schema_valid_after_retry"]["pass"] is True
    row = [result(invocations=inv(18, 0, 2))]  # 18 of 20 = 0.90
    g = ev.compute_gates(row)["gates"]["schema_valid_after_retry"]
    assert g["pass"] is False and g["value"] == 0.9
    assert ev.compute_gates(row)["all_pass"] is False


def test_truncation_gate_fails_on_any_truncated_call():
    rows = passing_set()
    rows[0]["truncated_calls"] = 1
    assert ev.compute_gates(rows)["gates"]["no_truncation"]["pass"] is False


def test_maths_gate_names_the_wrong_question():
    rows = passing_set()
    rows[0].update(id="math-quadratic", math_ok=False)
    g = ev.compute_gates(rows)["gates"]["maths_matches_reference"]
    assert g["pass"] is False and "math-quadratic" in g["detail"]


def test_unsafe_item_that_was_answered_fails_the_refusal_gate():
    rows = passing_set()
    rows[1].update(status="ok", status_ok=False)
    g = ev.compute_gates(rows)["gates"]["unsafe_refused"]
    assert g["pass"] is False and "b" in g["detail"]


def test_ordinary_question_that_was_refused_fails_the_false_refusal_gate():
    rows = passing_set()
    rows[0].update(status="refused", status_ok=False)
    assert ev.compute_gates(rows)["gates"]["no_false_refusals"]["pass"] is False


def test_plan_latency_gate_uses_the_median():
    rows = [result(id=str(i), node_ms={"orchestrate": ms}) for i, ms in enumerate([1000, 2000, 9000])]
    rows.append(result(id="u", category="edge", expected_status="refused", status="refused", math_ok=None, node_ms={}))
    g = ev.compute_gates(rows)["gates"]["plan_latency_p50"]
    assert g["pass"] is True and g["value"] == 2000
    slow = [result(id=str(i), node_ms={"orchestrate": 8000}) for i in range(3)]
    assert ev.compute_gates(slow)["gates"]["plan_latency_p50"]["pass"] is False


def test_an_empty_run_cannot_look_healthy():
    out = ev.compute_gates([])
    assert out["all_pass"] is False and not any(g["pass"] for g in out["gates"].values())


# ---------------------------------------------------------- end to end (fake)


@pytest.fixture(scope="module")
def entries():
    return ev.load_golden()


def run(backend, worker, entries, **kw):
    return asyncio.run(ev.run_eval(backend, worker, entries, **kw))


def test_fake_mode_runs_all_20_and_every_gate_is_computed_and_passes(math_worker, entries):
    results = run(ev.FakeGoldenBackend(entries), math_worker, entries)
    report = ev.build_report("fake", results)
    assert len(results) == 20
    by_id = {r["id"]: r for r in results}
    assert by_id["edge-unsafe"]["status"] == "refused" and by_id["edge-unsafe"]["status_ok"]
    assert all(r["status_ok"] for r in results), [r["id"] for r in results if not r["status_ok"]]
    for r in results:
        if r["category"] == "math":
            assert r["reference_ok"] is True and r["math_ok"] is True, r
    assert report["all_pass"] is True, ev.format_report(report)
    s = report["summary"]
    assert s["questions"] == 20 and s["truncation_rate"] == 0.0 and s["statuses"] == {"ok": 19, "refused": 1}
    assert {"orchestrator", "solver"} <= set(s["schema_validity_by_agent"])
    assert "orchestrate" in s["node_latency_ms"] and s["calls"] > 20


def test_report_serialises_to_json(math_worker, entries):
    report = ev.build_report("fake", run(ev.FakeGoldenBackend(entries), math_worker, entries[:3]))
    assert json.loads(json.dumps(report))["mode"] == "fake"


def test_retries_are_counted_when_the_model_needs_a_second_try(math_worker, entries):
    backend = ev.FakeGoldenBackend(entries, corrupt_first=["orchestrator"])
    results = run(backend, math_worker, entries[:3])
    for r in results:
        assert r["invocations"]["orchestrator"] == {"ok": 0, "retry_ok": 1, "failed": 0}
    assert ev.compute_gates(results)["gates"]["schema_valid_after_retry"]["pass"] is True


def test_truncation_is_detected_and_fails_the_gate(math_worker, entries):
    results = run(ev.FakeGoldenBackend(entries, truncate=["solver"]), math_worker, entries[:2])
    assert all(r["truncated_calls"] >= 1 for r in results)
    assert ev.compute_gates(results)["gates"]["no_truncation"]["pass"] is False


def test_a_model_that_gets_maths_wrong_fails_the_maths_gate(math_worker, entries):
    class WrongAnswers(ev.FakeGoldenBackend):
        def _solution(self, e):
            sol = super()._solution(e)
            if e.get("reference_answer"):
                sol["final_answer"]["value"] = "x=1234"
            return sol

    results = run(WrongAnswers(entries), math_worker, [e for e in entries if e["id"] == "math-linear"])
    assert results[0]["math_ok"] is False
    assert ev.compute_gates(results)["gates"]["maths_matches_reference"]["pass"] is False


def test_unparsable_model_output_is_a_failed_invocation_not_a_crash(math_worker, entries):
    backend = FakeLLM({"orchestrator": ["x"] * 4, "solver": ["x"] * 4})
    results = run(backend, math_worker, [e for e in entries if e["id"] == "math-linear"])
    r = results[0]
    assert r["invocations"]["solver"]["failed"] >= 1 and r["status"] in ("failed", "ok")


# --------------------------------------------------------------------- CLI


def test_cli_fake_mode_writes_a_report_and_exits_zero(tmp_path, math_worker, capsys):
    out = tmp_path / "report.json"
    code = ev.main(["--mode", "fake", "--limit", "4", "--out", str(out)], worker_factory=lambda: math_worker)
    report = json.loads(out.read_text())
    assert code == 1  # a 4-question slice has no unsafe item, so that gate has no data and fails
    assert report["gates"]["unsafe_refused"]["pass"] is False and report["gates"]["maths_matches_reference"]["pass"] is True
    assert report["mode"] == "fake" and len(report["results"]) == 4
    assert "golden-set eval (fake)" in capsys.readouterr().out


def test_cli_full_fake_run_exits_zero(tmp_path, math_worker):
    out = tmp_path / "r.json"
    assert ev.main(["--mode", "fake", "--out", str(out)], worker_factory=lambda: math_worker) == 0
    assert json.loads(out.read_text())["all_pass"] is True


def test_cli_live_mode_is_wired_through_the_backend_factory(tmp_path, math_worker, entries):
    """Live mode is only built, not run: the factory stands in for the real Ollama backend."""
    seen = {}

    def factory():
        seen["built"] = True
        return ev.FakeGoldenBackend(entries)

    code = ev.main(["--mode", "live"], backend_factory=factory, worker_factory=lambda: math_worker)
    assert seen == {"built": True} and code == 0


def test_cli_live_mode_builds_the_real_backend_from_the_environment(monkeypatch):
    from tutor.llm.client import OllamaClient

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TUTOR_MODEL_REASONER", "tag-r")
    backend = ev._live_backend()
    assert isinstance(backend, OllamaClient)
    asyncio.run(backend.aclose())


def test_cli_closes_the_backend_when_it_can(math_worker, entries):
    closed = []

    class Closable(ev.FakeGoldenBackend):
        async def aclose(self):
            closed.append(True)

    ev.main(["--mode", "fake", "--limit", "1"], backend_factory=lambda: Closable(entries), worker_factory=lambda: math_worker)
    assert closed == [True]


def test_cli_bad_golden_path_is_a_usage_error(math_worker, capsys):
    code = ev.main(["--mode", "fake", "--golden", "/nonexistent/golden.json"], worker_factory=lambda: math_worker)
    assert code == 2 and "run_eval" in capsys.readouterr().err


def test_cli_requires_a_mode():
    with pytest.raises(SystemExit):
        ev.parse_args([])
