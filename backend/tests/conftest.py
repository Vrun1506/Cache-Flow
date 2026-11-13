import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def math_worker():
    """One real SymPy worker process for the whole test session (spawn is slow)."""
    from tutor.tools.worker import MathWorker

    worker = MathWorker(timeout=10.0)
    yield worker
    worker.close()
