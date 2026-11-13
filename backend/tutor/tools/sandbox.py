"""Run model-written Python against a few tests (final-plan.md §16.3 `code` row, §15.4).

**This is defence in depth, not a security boundary.** What it does:

- runs the code in a separate interpreter (``-I -S -B``) in its own session, so the whole
  process group is killed on timeout, memory excess, or when the run ends;
- scrubs the environment (no secrets, no ``HOME``/``PATH``), closes stdin, and uses an empty
  throw-away working directory that is deleted afterwards;
- applies ``setrlimit`` limits for CPU, file size, open files, core dumps and process count
  (``RLIMIT_AS`` on Linux only), reported in ``SandboxResult.limits_applied``;
- enforces the memory limit from the parent by polling the child's resident set size, because
  macOS does not enforce ``RLIMIT_AS``;
- inside the child, before any user code runs: poisons ``socket``, ``subprocess``, ``ctypes``,
  ``multiprocessing``, ``importlib`` and friends in ``sys.modules`` (so stdlib modules cannot
  import them either), disables ``os.system``/``exec*``/``fork``/``spawn*``/``popen``, restricts
  ``open()`` and the ``os`` file functions to the working directory, and removes ``eval``,
  ``exec`` and ``compile`` from the user's builtins.

What it does NOT do: a determined attacker can escape it (object-graph tricks, forging the
result channel, ``posix`` functions that importlib needs). The threat model is model-written
code that is buggy or was talked into something silly, not a human adversary. **On the Spark,
also run it in a container with no network** (see :func:`docker_run_command`).

Test convention (``tests`` is a list of ``{input, expected}`` strings, as the Solver emits):

- ``input`` is an expression such as ``fib(10)``: it is evaluated in the module's namespace and the
  result is compared with ``expected`` parsed by ``ast.literal_eval`` (floats compare with a
  1e-9 relative tolerance), or by ``str``/``repr`` when ``expected`` is not a literal;
- ``input`` is a literal such as ``(3, 4)`` and the code defines ``solve``: ``solve(*args)`` is called;
- otherwise ``input`` is stdin text (possibly empty): the program runs with that stdin and its
  printed output is compared with ``expected`` (trailing whitespace ignored).

Outcomes: ``passed``, ``failed`` (a test failed, the code raised or did not compile),
``timeout``, ``memory``, ``crash`` (the child died without reporting), ``setup_error``.
Only ``passed`` and ``failed`` say anything about the code; the rest mean "could not check".
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

CHILD = Path(__file__).with_name("_sandbox_child.py")
MAX_TESTS = 8
MAX_FIELD = 200
MAX_CODE = 20_000
POLL_S = 0.025
PROTOCOL_TAIL = 1_000_000  # the most of the child's stdout file we are willing to read

Outcome = Literal["passed", "failed", "timeout", "memory", "crash", "setup_error"]


@dataclass
class SandboxResult:
    outcome: Outcome
    results: list[dict[str, Any]] = field(default_factory=list)
    timed_out: bool = False
    error: str | None = None
    limits_applied: list[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [r for r in self.results if not r.get("ok")]

    def summary(self, limit: int = 160) -> str:
        """Counter-evidence in one line, for a repair hint."""
        if self.outcome == "passed":
            return "all tests passed"
        if self.error and not self.results:
            return self.error[:limit]
        bad = self.failures
        if not bad:
            return (self.error or self.outcome)[:limit]
        first = bad[0]
        what = first.get("error") or f"got {first.get('actual')!r}, expected {first.get('expected')!r}"
        return f"{len(bad)} of {len(self.results)} tests failed; input {first.get('input')!r}: {what}"[:limit]


def _normalise_tests(tests: Iterable[Any]) -> list[dict[str, str]]:
    out = []
    for t in list(tests)[:MAX_TESTS]:
        if hasattr(t, "model_dump"):
            t = t.model_dump()
        elif hasattr(t, "input") and not isinstance(t, dict):
            t = {"input": t.input, "expected": t.expected}
        out.append({"input": str(t.get("input", ""))[:MAX_FIELD], "expected": str(t.get("expected", ""))[:MAX_FIELD]})
    return out


def _rss_mb(pid: int) -> float | None:
    """Resident memory of ``pid`` in MB, or None if it cannot be read."""
    try:
        with open(f"/proc/{pid}/statm", "rb") as fh:  # Linux: cheap
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:  # macOS and others
        res = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=1)
        return int(res.stdout.strip()) / 1024 if res.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _scrubbed_env() -> dict[str, str]:
    return {"PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _parse(raw: bytes, nonce: str) -> tuple[list[dict[str, Any]], bool]:
    """Protocol lines only: anything without the nonce came from the user's code and is ignored."""
    lines, prefix = [], (nonce + " ").encode()
    for line in raw[:PROTOCOL_TAIL].splitlines():
        if line.startswith(prefix):
            try:
                lines.append(json.loads(line[len(prefix):]))
            except ValueError:
                continue
    return lines, any(ln.get("t") == "done" for ln in lines)


def run_python(
    code: str,
    tests: Iterable[Any],
    *,
    timeout_s: float = 5.0,
    mem_mb: int = 128,
    max_output: int = 10_000,
) -> SandboxResult:
    """Run ``code`` against ``tests`` in a sandboxed child. Blocking: call it via ``asyncio.to_thread``."""
    started = time.monotonic()
    tests = _normalise_tests(tests)
    if not isinstance(code, str) or not code.strip():
        return SandboxResult("setup_error", error="no code to run")
    if len(code) > MAX_CODE:
        return SandboxResult("setup_error", error="code is too long to run")
    if not tests:
        return SandboxResult("setup_error", error="no tests to run")

    top = tempfile.mkdtemp(prefix="sbx-")
    proc: subprocess.Popen | None = None
    try:
        work, io_dir = Path(top, "work"), Path(top, "io")
        work.mkdir()
        io_dir.mkdir()
        nonce = uuid.uuid4().hex
        job = io_dir / "job.json"
        job.write_text(json.dumps({
            "nonce": nonce, "code": code, "tests": tests, "workdir": str(work), "max_output": max_output,
            "mem_mb": mem_mb, "cpu_s": int(timeout_s) + 1,
        }))
        out_path, err_path = io_dir / "out.txt", io_dir / "err.txt"
        try:
            with open(out_path, "wb") as out, open(err_path, "wb") as err:
                proc = subprocess.Popen(
                    [sys.executable, "-I", "-S", "-B", str(CHILD), str(job)],
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=str(work), env=_scrubbed_env(),
                    start_new_session=True, close_fds=True,
                )
        except OSError as exc:
            return SandboxResult("setup_error", error=f"could not start the sandbox: {exc}"[:200])

        outcome: Outcome | None = None
        error: str | None = None
        deadline = started + timeout_s
        last_mem_check = 0.0
        while proc.poll() is None:
            now = time.monotonic()
            if now >= deadline:
                outcome, error = "timeout", f"timed out after {timeout_s:g}s"
                break
            if now - last_mem_check >= 0.05:
                last_mem_check = now
                rss = _rss_mb(proc.pid)
                if rss is not None and rss > mem_mb:
                    outcome, error = "memory", f"memory limit of {mem_mb} MB exceeded"
                    break
            time.sleep(POLL_S)
        _kill_group(proc.pid)  # also clears any stragglers when the child exited normally
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL cannot be ignored
            pass

        lines, done = _parse(out_path.read_bytes() if out_path.exists() else b"", nonce)
        limits = next((ln.get("applied", []) for ln in lines if ln.get("t") == "limits"), [])
        results = [{k: v for k, v in ln.items() if k != "t"} for ln in lines if ln.get("t") == "test"]
        load = next((ln for ln in lines if ln.get("t") == "load"), None)
        duration = int((time.monotonic() - started) * 1000)

        def result(o: Outcome, err: str | None = error) -> SandboxResult:
            return SandboxResult(o, results, o == "timeout", err, limits, duration)

        if outcome is not None:
            return result(outcome)
        if load is not None and not load.get("ok"):
            return result("failed", load.get("error") or "the code does not compile")
        if not done:
            tail = err_path.read_bytes()[-200:].decode("utf-8", "replace").strip() if err_path.exists() else ""
            code_ = proc.returncode
            how = f"killed by signal {-code_}" if code_ is not None and code_ < 0 else f"exit code {code_}"
            return result("crash", f"the sandbox ended without a result ({how}) {tail}".strip()[:200])
        if len(results) < len(tests):
            return result("crash", "the sandbox reported fewer results than tests")
        if all(r.get("ok") for r in results):
            return result("passed", None)
        return result("failed", None)
    finally:
        if proc is not None:
            _kill_group(proc.pid)
        shutil.rmtree(top, ignore_errors=True)


def docker_run_command(
    directory: str,
    *,
    image: str = "python:3.13-slim",
    mem_mb: int = 128,
    timeout_s: float = 5.0,
) -> list[str]:
    """argv for running the same child inside a locked-down container. Not used by default.

    ``directory`` must contain ``_sandbox_child.py`` and a ``job.json`` (mounted read-only). The
    container has no network, a read-only root, no capabilities and tight cpu/memory/pid limits.
    """
    return [
        "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--memory", f"{mem_mb + 64}m", "--cpus", "1", "--pids-limit", "32",
        "--tmpfs", "/work:rw,noexec,nosuid,size=16m", "--stop-timeout", "1",
        "-v", f"{directory}:/sandbox:ro", "-w", "/work", image,
        "timeout", str(int(timeout_s) + 1), "python", "-I", "-S", "-B", "/sandbox/_sandbox_child.py", "/sandbox/job.json",
    ]
