import asyncio
import json

import pytest

from quiz_samples import quiz_json
from sample_data import ORCHESTRATOR_OK, SOLUTION_OK
from tutor.llm import FakeLLM, LLMTimeout
from tutor.runtime import CollectingSink, LocalRuntime, Pacer, RunContext
from tutor.speech.tts import FakeTTS, Narrator
from tutor.state.memory import Memory

pytestmark = pytest.mark.anyio

QUESTION = "Solve x^2 + 5x + 6 = 0"


def lesson_entry(sid):
    return {
        "step_id": sid,
        "intent": f"Now step {sid[1:]}.",
        "explain": "This is why we do it, in plain words.",
        "check": {
            "type": "numeric", "prompt": "What is two times three?", "options": None,
            "expected": {"value": "6", "key_points": None, "option_index": None},
            "hints": ["What is 2 groups of 3?", "Multiplication.", "It is six."],
        },
    }


LESSON = json.dumps({"steps": [lesson_entry(s) for s in ("s1", "s2", "s3")]})


def board(step):
    return json.dumps({"commands": [
        {"id": f"b-{step}-1", "op": "write", "anchor": {"rel": "free"}, "payload": {"text": "2*3=6"}, "cue": {"at": "after_intent"}},
        {"id": f"b-{step}-2", "op": "highlight", "anchor": {"rel": "free"}, "payload": {"ref": f"b-{step}-1", "kind": "box"}, "cue": {"at": "end"}},
    ]})


ANIMATION = json.dumps({"template": "fn_plot", "title": "Parabola", "captions": ["It crosses the axis twice."],
                        "params": {"expr": "x^2+5*x+6", "x_min": -5, "x_max": 1}})


def script(**over):
    base = {
        "orchestrator": [ORCHESTRATOR_OK],
        "solver": [SOLUTION_OK],
        "lesson_designer": [LESSON],
        "board_director": [board(1), board(2), board(3)],
        "animator": [ANIMATION],
        "quiz_designer": [quiz_json()],
    }
    base.update(over)
    return base


def make(math_worker, scr, **kw):
    llm = FakeLLM(scr)
    return llm, LocalRuntime(llm, math_worker, narrator=Narrator(FakeTTS()), **kw)


async def go(rt, memory=None, question=QUESTION):
    sink = CollectingSink()
    memory = memory or Memory()
    result = await rt.run_solve_teach(question, RunContext(run_id="r1", sink=sink), memory=memory)
    return result, sink, memory


async def test_full_lesson_streams_steps_scene_and_quiz(math_worker):
    llm, rt = make(math_worker, script())
    result, sink, memory = await go(rt)
    assert result.status == "ok"

    types = [e["type"] for e in sink.events]
    assert types[0] == "plan" and "problem" in types and types[-1] == "run_done"
    steps = sink.of_type("step")
    assert [s["index"] for s in steps] == [1, 2, 3] and all(s["of"] == 3 for s in steps)
    first = steps[0]
    assert [c["id"] for c in first["clips"]] == ["s1-intent", "s1-explain"]
    assert all(c["duration_ms"] > 0 for c in first["clips"])
    write = first["board"][0]
    assert write["cue"]["at_ms"] == first["clips"][0]["duration_ms"]  # after_intent
    assert write["id"] == "b-1-1"
    assert first["verified"] in ("verified", "unverified")

    (scene,) = sink.of_type("scene")
    assert scene["scene"]["kind"] == "plot" and scene["id"] == "animate[1]"
    assert len(memory.checks) == 3 and len(memory.quiz) == 2
    assert memory.current_step == 3 and any(r.id == "b-2-1" for r in memory.board_index)
    seqs = [e["seq"] for e in sink.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_plan_is_extended_with_the_lesson_nodes_after_verify(math_worker):
    _, rt = make(math_worker, script())
    _, sink, _ = await go(rt)
    plans = sink.of_type("plan")
    assert len(plans) == 2 and plans[1]["update"] is True
    ids = [n["id"] for n in plans[1]["nodes"]]
    assert ids == ["lesson[1]", "step[1]", "step[2]", "step[3]", "animate[1]", "quiz"]


async def test_steps_come_before_slow_side_work_finishes_and_events_are_ordered(math_worker):
    _, rt = make(math_worker, script())
    _, sink, _ = await go(rt)
    first_step = next(i for i, e in enumerate(sink.events) if e["type"] == "step")
    verify_done = sink.events.index(next(e for e in sink.events if e["type"] == "agent" and e["id"] == "verify" and e["state"] == "done"))
    assert first_step > verify_done


async def test_lesson_failure_still_completes_with_plain_lessons(math_worker):
    _, rt = make(math_worker, script(lesson_designer=["x", "x", "x", "x"]))
    result, sink, _ = await go(rt)
    assert result.status == "ok" and len(sink.of_type("step")) == 3
    assert all(l.check.type == "explain" for l in result.lessons)


async def test_board_failure_falls_back_to_a_plain_board(math_worker):
    _, rt = make(math_worker, script(board_director=["x"] * 8))
    _, sink, _ = await go(rt)
    steps = sink.of_type("step")
    assert len(steps) == 3 and all(s["board"] for s in steps)


async def test_animator_failure_yields_a_concept_card_not_an_error(math_worker):
    _, rt = make(math_worker, script(animator=["x", "x"]))
    result, sink, _ = await go(rt)
    assert result.status == "ok" and len(sink.of_type("scene")) == 1


async def test_quiz_failure_uses_generic_questions(math_worker):
    _, rt = make(math_worker, script(quiz_designer=[LLMTimeout("t"), LLMTimeout("t")]))
    _, _, memory = await go(rt)
    assert len(memory.quiz) >= 2


async def test_no_visuals_means_no_animate_nodes(math_worker):
    plan = json.loads(ORCHESTRATOR_OK)
    plan["visuals"] = []
    _, rt = make(math_worker, script(orchestrator=[json.dumps(plan)], animator=[]))
    _, sink, _ = await go(rt)
    assert not sink.of_type("scene")
    assert all(not n["id"].startswith("animate") for n in sink.of_type("plan")[1]["nodes"])


async def test_tts_outage_gives_estimated_clips_not_a_failure(math_worker):
    class Broken:
        async def synth(self, text):
            raise ConnectionError("down")

    llm = FakeLLM(script())
    rt = LocalRuntime(llm, math_worker, narrator=Narrator(Broken()))
    result, sink, _ = await go(rt)
    assert result.status == "ok"
    assert all(c["estimated"] for c in sink.of_type("step")[0]["clips"])


async def test_clip_urls_come_from_the_callback(math_worker):
    llm = FakeLLM(script())
    rt = LocalRuntime(llm, math_worker, narrator=Narrator(FakeTTS()), clip_url=lambda c: f"/audio/{c.id}.wav")
    _, sink, _ = await go(rt)
    assert sink.of_type("step")[0]["clips"][0]["url"] == "/audio/s1-intent.wav"


async def test_refused_questions_never_reach_the_lesson(math_worker):
    plan = json.loads(ORCHESTRATOR_OK) | {"safety": "refuse"}
    llm, rt = make(math_worker, {"orchestrator": [json.dumps(plan)]})
    result, sink, _ = await go(rt)
    assert result.status == "refused" and not sink.of_type("step")
    assert [c.agent for c in llm.calls] == ["orchestrator"]


async def test_solver_failure_means_no_teaching(math_worker):
    llm, rt = make(math_worker, {"orchestrator": [ORCHESTRATOR_OK], "solver": ["bad"] * 4})
    result, sink, _ = await go(rt)
    assert result.status == "failed" and not sink.of_type("step")


async def test_unverified_steps_are_labelled_and_the_lesson_says_so(math_worker):
    # The first step has no checkable expression, so it can never be `verified`.
    _, rt = make(math_worker, script())
    _, sink, _ = await go(rt)
    s1 = sink.of_type("step")[0]
    assert s1["verified"] == "unverified"
    assert "could not fully check" in s1["explain"].lower()


async def test_cancellation_mid_lesson_stops_everything(math_worker):
    llm = FakeLLM(script(), delay=0.3)
    rt = LocalRuntime(llm, math_worker, narrator=Narrator(FakeTTS()))
    sink = CollectingSink()
    task = asyncio.create_task(rt.run_solve_teach(QUESTION, RunContext(run_id="r", sink=sink)))
    await asyncio.sleep(1.0)  # past orchestrate and solve, inside the lesson
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    n = len(sink.events)
    await asyncio.sleep(0.4)
    assert len(sink.events) == n


async def test_pacer_limits_how_far_generation_runs_ahead(math_worker):
    pacer = Pacer(ahead=1, grace_s=30)
    llm, rt = make(math_worker, script(), pacer=pacer)
    sink = CollectingSink()
    task = asyncio.create_task(rt.run_solve_teach(QUESTION, RunContext(run_id="r", sink=sink)))
    await asyncio.sleep(1.0)
    assert [s["index"] for s in sink.of_type("step")] == [1]  # step 2 waits for step 1 to be acked
    await pacer.ack(1)
    await asyncio.sleep(0.3)
    assert [s["index"] for s in sink.of_type("step")] == [1, 2]
    await pacer.ack(2)
    result = await asyncio.wait_for(task, 5)
    assert result.status == "ok" and len(sink.of_type("step")) == 3


async def test_pacer_never_stalls_when_the_ack_is_lost():
    pacer = Pacer(ahead=1, grace_s=0.1)
    pacer.expect(1, 100)
    await asyncio.wait_for(pacer.before(2), 2)  # step 1 never acked: moves on after duration + grace
