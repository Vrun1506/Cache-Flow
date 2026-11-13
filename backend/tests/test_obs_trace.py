import asyncio
import hashlib
import json
import threading

import pytest

from tutor.llm import FakeLLM, LLMRequest, LLMTimeout
from tutor.obs import TraceLog, TracingBackend, redact

pytestmark = pytest.mark.anyio


def lines(trace):
    return [json.loads(x) for x in trace.path.read_text().splitlines()]


def req(**kw):
    base = dict(agent="solver", tier="reasoner", system="sys", user="Solve my secret homework", prompt_version="3")
    base.update(kw)
    return LLMRequest(**base)


# ------------------------------------------------------------------ redaction


def test_bytes_become_hash_and_size():
    out = redact({"image": b"\x89PNG" * 10, "audio": bytearray(b"abc")})
    assert out["image"] == {"sha256": hashlib.sha256(b"\x89PNG" * 10).hexdigest(), "bytes": 40}
    assert out["audio"]["bytes"] == 3


def test_student_text_is_hash_and_length_only_by_default():
    out = redact({"text": "my homework", "question": "what is x", "count": 3}, log_text=False)
    assert out["text"] == {"sha256": hashlib.sha256(b"my homework").hexdigest(), "len": 11}
    assert "what is x" not in json.dumps(out) and out["count"] == 3


def test_student_text_can_be_enabled_and_is_then_capped():
    out = redact({"text": "a" * 900}, log_text=True)
    assert len(out["text"]) == 501 and out["text"].endswith("…")


def test_env_switch_enables_text(monkeypatch):
    monkeypatch.setenv("TUTOR_LOG_TEXT", "1")
    assert redact({"text": "visible"}) == {"text": "visible"}
    monkeypatch.delenv("TUTOR_LOG_TEXT")
    assert isinstance(redact({"text": "visible"})["text"], dict)


def test_other_strings_are_capped_at_500():
    out = redact({"detail": "x" * 2000})
    assert len(out["detail"]) == 501


@pytest.mark.parametrize("key", ["password", "api_key", "Authorization", "session_token", "client_secret", "cookie"])
def test_secret_keys_are_dropped(key):
    assert redact({key: "hunter2", "ok": 1}) == {"ok": 1}


def test_nested_and_list_values_are_redacted_too():
    out = redact({"event": {"payload": {"text": "private", "token": "t"}, "images": [b"abc"]}, "turns": ["a"] * 200})
    assert out["event"]["payload"] == {"text": {"sha256": hashlib.sha256(b"private").hexdigest(), "len": 7}}
    assert out["event"]["images"][0]["bytes"] == 3
    assert len(out["turns"]) == 50


def test_list_under_a_text_key_is_hashed_per_item():
    out = redact({"lines": ["x^2", "y"]}, log_text=False)
    assert all(isinstance(i, dict) and "sha256" in i for i in out["lines"])


def test_unserialisable_objects_become_capped_reprs():
    class Weird:
        def __repr__(self):
            return "W" * 900

    assert len(redact({"obj": Weird()})["obj"]) == 501


def test_deep_nesting_is_cut_off():
    value: dict = {}
    cur = value
    for _ in range(20):
        cur["n"] = {}
        cur = cur["n"]
    assert "depth limit" in json.dumps(redact(value))


# ------------------------------------------------------------------ TraceLog


def test_creates_directory_and_jsonl_file(tmp_path):
    trace = TraceLog(tmp_path / "logs" / "deep", "abc123")
    trace.log_event("phase", phase="PLANNING")
    assert trace.path.name == "session-abc123.jsonl" and trace.path.exists()
    (rec,) = lines(trace)
    assert rec["kind"] == "event" and rec["type"] == "phase" and rec["session"] == "abc123" and rec["ts"] > 0


def test_session_id_cannot_escape_the_directory(tmp_path):
    trace = TraceLog(tmp_path, "../../etc/passwd")
    assert trace.path.parent == tmp_path and "/" not in trace.path.name[len("session-"):]


def test_call_and_transition_records(tmp_path):
    trace = TraceLog(tmp_path, "s")
    trace.log_call("solver", "1", "reasoner", "m", 100, 50, 1234.56, 40.5, "ok", False, done_reason="stop")
    trace.log_transition("IDLE", "PLANNING", "solve_text", ms_in_prev=5)
    call, transition = lines(trace)
    assert call["kind"] == "call" and call["agent"] == "solver" and call["ms"] == 1234.6 and call["done_reason"] == "stop"
    assert transition == {**transition, "kind": "transition", "prev": "IDLE", "state": "PLANNING", "event": "solve_text"}


def test_log_never_contains_student_words_or_secrets(tmp_path):
    trace = TraceLog(tmp_path, "s")
    trace.log_event("question", text="SECRET HOMEWORK", image=b"PNGDATA", token="abc")
    raw = trace.path.read_text()
    assert "SECRET HOMEWORK" not in raw and "PNGDATA" not in raw and "abc" not in raw


def test_concurrent_writes_from_threads_do_not_interleave(tmp_path):
    trace = TraceLog(tmp_path, "s")

    def work(n):
        for i in range(100):
            trace.log_event("tick", worker=n, i=i, detail="y" * 300)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    recs = lines(trace)  # every line must be valid JSON
    assert len(recs) == 800


async def test_concurrent_async_writes(tmp_path):
    trace = TraceLog(tmp_path, "s")
    await asyncio.gather(*(asyncio.to_thread(trace.log_event, "tick", i=i) for i in range(200)))
    assert len(lines(trace)) == 200


# ------------------------------------------------------------ TracingBackend


async def test_wrapper_logs_a_call_and_returns_the_response_unchanged(tmp_path):
    trace = TraceLog(tmp_path, "s")
    llm = FakeLLM({"solver": ['{"a":1}']})
    wrapped = TracingBackend(llm, trace)
    resp = await wrapped.generate(req(images=(b"pngbytes",)))
    assert resp.text == '{"a":1}'
    (rec,) = lines(trace)
    assert rec["agent"] == "solver" and rec["prompt_version"] == "3" and rec["tier"] == "reasoner"
    assert rec["status"] == "ok" and rec["truncated"] is False and rec["done_reason"] == "stop" and rec["images"] == 1
    assert rec["completion_tokens"] > 0 and rec["prompt_tokens"] > 0
    assert "Solve my secret homework" not in trace.path.read_text()


async def test_wrapper_flags_truncation(tmp_path):
    from tutor.llm import LLMResponse

    trace = TraceLog(tmp_path, "s")
    llm = FakeLLM({"solver": [LLMResponse("x", 10, 10, "length", "m")]})
    await TracingBackend(llm, trace).generate(req())
    (rec,) = lines(trace)
    assert rec["status"] == "truncated" and rec["truncated"] is True


async def test_wrapper_logs_errors_and_reraises_the_same_exception(tmp_path):
    trace = TraceLog(tmp_path, "s")
    err = LLMTimeout("slow")
    wrapped = TracingBackend(FakeLLM({"solver": [err]}), trace)
    with pytest.raises(LLMTimeout) as info:
        await wrapped.generate(req())
    assert info.value is err
    (rec,) = lines(trace)
    assert rec["status"] == "error" and rec["error"] == "LLMTimeout"


async def test_wrapper_does_not_swallow_cancellation(tmp_path):
    trace = TraceLog(tmp_path, "s")
    wrapped = TracingBackend(FakeLLM({"solver": ["{}"]}, delay=5.0), trace)
    task = asyncio.create_task(wrapped.generate(req()))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lines(trace)[0]["status"] == "error"


async def test_wrapper_forwards_other_attributes(tmp_path):
    class Inner:
        async def generate(self, r):
            raise AssertionError

        def stats(self):
            return {"swaps": 2}

    wrapped = TracingBackend(Inner(), TraceLog(tmp_path, "s"))
    assert wrapped.stats() == {"swaps": 2}
    with pytest.raises(AttributeError):
        wrapped.nonexistent


# ------------------------------------------------------------ purge-logs.sh

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "ops" / "purge-logs.sh"


def purge(*args):
    return subprocess.run(["sh", str(SCRIPT), *args], capture_output=True, text=True)


def make_logs(tmp_path, old_days=10):
    old = tmp_path / "session-old.jsonl"
    new = tmp_path / "session-new.jsonl"
    other = tmp_path / "notes.txt"
    for f in (old, new, other):
        f.write_text("x")
    past = time.time() - old_days * 86400
    os.utime(old, (past, past))
    return old, new, other


def test_purge_is_a_dry_run_by_default(tmp_path):
    old, new, _ = make_logs(tmp_path)
    r = purge("--dir", str(tmp_path), "--days", "7")
    assert r.returncode == 0 and "would delete: session-old.jsonl" in r.stdout and "session-new" not in r.stdout
    assert old.exists() and new.exists()


def test_purge_yes_deletes_only_old_session_logs(tmp_path):
    old, new, other = make_logs(tmp_path)
    r = purge("--dir", str(tmp_path), "--days", "7", "--yes")
    assert r.returncode == 0 and not old.exists() and new.exists() and other.exists()


def test_purge_all_deletes_every_session_log_but_nothing_else(tmp_path):
    old, new, other = make_logs(tmp_path)
    assert purge("--dir", str(tmp_path), "--all", "--yes").returncode == 0
    assert not old.exists() and not new.exists() and other.exists()


def test_purge_does_not_touch_subdirectories_or_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "session-victim.jsonl"
    victim.write_text("keep me")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "session-link.jsonl").symlink_to(victim)
    sub = logs / "nested"
    sub.mkdir()
    (sub / "session-deep.jsonl").write_text("deep")
    (logs / "session-real.jsonl").write_text("x")
    purge("--dir", str(logs), "--all", "--yes")
    assert victim.exists() and (sub / "session-deep.jsonl").exists()
    assert not (logs / "session-real.jsonl").exists()


@pytest.mark.parametrize("bad", ["/", "/etc", "/tmp"])
def test_purge_refuses_system_directories(bad):
    r = purge("--dir", bad, "--all", "--yes")
    assert r.returncode == 2 and "refusing" in r.stderr


def test_purge_refuses_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = purge("--dir", str(tmp_path), "--all", "--yes")
    assert r.returncode == 2 and "refusing" in r.stderr


def test_purge_argument_errors(tmp_path):
    assert purge("--dir", str(tmp_path / "nope")).returncode == 2
    assert purge("--days", "abc", "--dir", str(tmp_path)).returncode == 2
    assert purge("--bogus").returncode == 2
    assert purge("--dir").returncode == 2


def test_purge_with_nothing_to_delete_succeeds(tmp_path):
    r = purge("--dir", str(tmp_path), "--all", "--yes")
    assert r.returncode == 0 and "nothing to delete" in r.stdout
