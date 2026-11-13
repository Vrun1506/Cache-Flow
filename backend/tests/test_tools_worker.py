import time

import pytest

from tutor.tools import MathWorker, ToolError, ToolTimeout

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def math_worker():
    worker = MathWorker(timeout=10.0)
    try:
        yield worker
    finally:
        worker.close()


def test_relate_through_worker(math_worker):
    result = math_worker.call("relate", prev="x**2+5*x+6=0", cur="(x+2)*(x+3)=0")
    assert result["status"] == "verified"
    assert result["method"] == "sympy"


def test_check_final_through_worker(math_worker):
    result = math_worker.call("check_final", equation="x**2+5*x+6=0", answer="x=-2 or x=-3")
    assert result["status"] == "verified"


def test_worker_error_on_bad_op(math_worker):
    with pytest.raises(ToolError, match="KeyError"):
        math_worker.call("no_such_op")


def test_worker_error_on_bad_arguments(math_worker):
    with pytest.raises(ToolError, match="TypeError"):
        math_worker.call("relate", prev="x")
    # the worker is still usable afterwards
    assert math_worker.call("relate", prev="x=4", cur="x=4")["status"] == "verified"


async def test_acall_under_anyio(math_worker):
    result = await math_worker.acall("relate", prev="2*x+3=11", cur="x=4")
    assert result["status"] == "verified"


def test_timeout_kills_and_restarts():
    with MathWorker(timeout=0.5, start_timeout=60.0) as worker:
        # Warm start is not counted against the call timeout.
        assert worker.call("relate", prev="x=1", cur="x=1")["status"] == "verified"
        first_pid = worker._proc.pid
        started = time.monotonic()
        with pytest.raises(ToolTimeout):
            worker.call("_sleep", secs=5)
        assert time.monotonic() - started < 3
        assert worker._proc is None  # marked dead
        # The next call restarts a fresh child and works.
        assert worker.call("relate", prev="x=2", cur="x=2")["status"] == "verified"
        assert worker._proc.pid != first_pid


def test_close_is_idempotent():
    worker = MathWorker(timeout=10.0)
    worker.call("relate", prev="x=1", cur="x=1")
    worker.close()
    worker.close()
    assert worker._proc is None
