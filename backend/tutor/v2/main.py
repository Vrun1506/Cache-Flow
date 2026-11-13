"""Run the tutor backend: ``python -m tutor.v2.main`` (Interface.md v2).

Environment. Nothing about the Spark's address, login or any credential is stored in the repo.

    OLLAMA_BASE_URL          http://127.0.0.1:11434 on the Spark, http://127.0.0.1:11435 through the SSH tunnel
    TUTOR_MODEL_ROUTER / _REASONER / _VISION   model tags (defaults are the installed ones)
    TUTOR_HOST / TUTOR_PORT  0.0.0.0 / 8443
    TUTOR_TLS_CERT / _KEY    PEM files: serve HTTPS/WSS directly (the iPad profiles use https://<host>:8443)
    TUTOR_API_TOKEN          optional bearer token; unset means open (use only on a trusted network)
    TUTOR_TTS_URL            local OpenAI-style speech endpoint; unset → captions + on-device speech
    TUTOR_ASR_URL            local OpenAI-style transcription endpoint; unset → /audio answers 503
    TUTOR_TIMEOUT_SCALE      > 1 on a slow dev machine
    TUTOR_WARM=1             load the models at start-up so the first request is not a cold load
    TUTOR_LOG_DIR            session JSONL trace (redacted); unset → no trace
    TUTOR_RECORD_FILE        record every model call (demo insurance)
    TUTOR_REPLAY_FILE        answer from a recording instead of a model (labelled "replayed" in /v2/health)
    TUTOR_SCREEN_W / _H      iPad screen in points (default 1210 x 834), when the iPad does not send its board size
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI

from ..config import LLMConfig, build_scheduled_client
from ..llm.replay import RecordingBackend, ReplayBackend
from ..obs.trace import TraceLog, TracingBackend
from ..speech.asr import HttpASR
from ..speech.tts import HttpTTS, Narrator
from ..tools.worker import MathWorker
from .app import create_app
from .service import TutorService


def build_app(env: Optional[dict[str, str]] = None) -> FastAPI:
    e = env if env is not None else os.environ
    config = LLMConfig.from_env()
    scheduled = build_scheduled_client(config)
    inner: Any = scheduled._inner  # the OllamaClient: health and warm-up talk to it directly
    backend: Any = scheduled
    replay: Optional[ReplayBackend] = None
    if e.get("TUTOR_REPLAY_FILE"):
        replay = ReplayBackend(e["TUTOR_REPLAY_FILE"])
        backend = replay
    elif e.get("TUTOR_RECORD_FILE"):
        backend = RecordingBackend(scheduled, e["TUTOR_RECORD_FILE"])
    if e.get("TUTOR_LOG_DIR"):
        backend = TracingBackend(backend, TraceLog(e["TUTOR_LOG_DIR"], "server"))

    worker = MathWorker(timeout=2.0)
    tts = HttpTTS(e["TUTOR_TTS_URL"]) if e.get("TUTOR_TTS_URL") else None
    asr = HttpASR(e["TUTOR_ASR_URL"]) if e.get("TUTOR_ASR_URL") else None

    async def health_probe() -> dict[str, Any]:
        if replay is not None:  # answering from a recording: the model server is not used, and we say so
            stats = replay.stats()
            return {"vllm": {"ok": True, "revision": "replay",
                             "detail": f"{replay.label}: {stats['hits']} hits, {stats['misses']} misses"}}
        installed = await inner.tags()
        missing = sorted({t for t in config.models.values() if t not in installed})
        detail = f"{config.api}; models {', '.join(sorted(set(config.models.values())))}"
        if missing:
            detail = f"missing: {', '.join(missing)}"
        return {"vllm": {"ok": not missing, "revision": config.api, "detail": detail}}

    service = TutorService(
        backend, worker, narrator=Narrator(tts), asr=asr, timeout_scale=float(e.get("TUTOR_TIMEOUT_SCALE", "1")),
        screen=(float(e.get("TUTOR_SCREEN_W", "1210")), float(e.get("TUTOR_SCREEN_H", "834"))), health_probe=health_probe,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if e.get("TUTOR_WARM") == "1" and replay is None:
            for tier in dict.fromkeys(config.models):
                try:
                    await inner.warm(tier)
                except Exception:
                    pass  # a cold first request is slower, not broken
        yield
        worker.close()
        await inner.aclose()

    app = create_app(service)
    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    import uvicorn

    kwargs: dict[str, Any] = {}
    if os.environ.get("TUTOR_TLS_CERT") and os.environ.get("TUTOR_TLS_KEY"):
        kwargs.update(ssl_certfile=os.environ["TUTOR_TLS_CERT"], ssl_keyfile=os.environ["TUTOR_TLS_KEY"])
    uvicorn.run(build_app(), host=os.environ.get("TUTOR_HOST", "0.0.0.0"), port=int(os.environ.get("TUTOR_PORT", "8443")), **kwargs)


if __name__ == "__main__":
    main()
