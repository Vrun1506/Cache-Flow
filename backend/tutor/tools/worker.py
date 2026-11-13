"""A persistent child process that runs SymPy with a kill timeout.

SymPy can hang on hostile or unlucky input and cannot be interrupted from the
inside, so verification runs in a separate process that we terminate on
timeout. The child imports sympy once and is reused.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import threading
import time
from typing import Any


class ToolTimeout(Exception):
    """A tool call exceeded its time limit; the worker was killed."""


class ToolError(Exception):
    """A tool call failed (bad op, bad arguments, or the worker died)."""


def _sleep(secs: float) -> dict:
    time.sleep(secs)
    return {"status": "unverified", "method": "none", "note": "slept"}


def _child_main(conn) -> None:
    import sympy  # noqa: F401  - warm the import once

    from . import mathcheck, mathverify

    ops = {
        "relate": mathverify.relate,
        "check_final": mathverify.check_final,
        "check_step": mathcheck.run_check,
        "_sleep": _sleep,
    }
    conn.send(("ready", None))
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            return
        if message is None:
            return
        op, kwargs = message
        try:
            conn.send(("ok", ops[op](**kwargs)))
        except Exception as exc:
            conn.send(("err", f"{type(exc).__name__}: {exc}"[:300]))


class MathWorker:
    def __init__(self, timeout: float = 2.0, start_timeout: float = 60.0) -> None:
        self.timeout = timeout
        self.start_timeout = start_timeout
        self._ctx = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._proc = None
        self._conn = None

    # -- lifecycle -------------------------------------------------------
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def _start(self) -> None:
        parent, child = self._ctx.Pipe()
        proc = self._ctx.Process(target=_child_main, args=(child,), daemon=True)
        proc.start()
        child.close()
        self._proc, self._conn = proc, parent
        try:
            if not parent.poll(self.start_timeout):
                raise ToolError("worker did not start in time")
            status, _ = parent.recv()
            if status != "ready":
                raise ToolError("worker failed to start")
        except (EOFError, OSError):
            self._kill()
            raise ToolError("worker failed to start") from None
        except ToolError:
            self._kill()
            raise

    def _kill(self) -> None:
        proc, conn = self._proc, self._conn
        self._proc = self._conn = None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        if proc is not None:
            proc.terminate()
            proc.join(1)
            if proc.is_alive():
                proc.kill()
                proc.join(1)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.send(None)
                except (OSError, ValueError):
                    pass
            self._kill()

    def __enter__(self) -> "MathWorker":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- calls -----------------------------------------------------------
    def call(self, op: str, **kwargs: Any) -> dict:
        with self._lock:
            if not self._alive():
                self._kill()
                self._start()
            try:
                self._conn.send((op, kwargs))
            except (BrokenPipeError, OSError, ValueError):
                self._kill()
                raise ToolError("worker unavailable") from None
            if not self._conn.poll(self.timeout):
                self._kill()
                raise ToolTimeout(f"{op} exceeded {self.timeout}s")
            try:
                status, value = self._conn.recv()
            except (EOFError, OSError):
                self._kill()
                raise ToolError("worker died") from None
            if status == "err":
                raise ToolError(value)
            return value

    async def acall(self, op: str, **kwargs: Any) -> dict:
        return await asyncio.to_thread(self.call, op, **kwargs)
