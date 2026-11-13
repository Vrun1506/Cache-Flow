"""The v2 HTTP + WebSocket API the iPad talks to (Interface.md "v2 HTTP" and "v2 stream").

Transport only: validation, size bounds, status codes (409 stale revision, 413 too large, 426 wrong
protocol, 404 unknown session), optional bearer token, and ordered delivery with an ``after_seq``
cursor. Behaviour lives in :class:`tutor.v2.service.TutorService`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .service import MAX_QUESTION_CHARS, ServiceError, TutorService
from .wire import PROTOCOL_HEADER, PROTOCOL_VERSION

log = logging.getLogger("tutor.v2.app")

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_AUDIO_BYTES = 2_000_000
MAX_JSON_FIELD = 64 * 1024


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CreateSessionBody(_Body):
    preset_id: Optional[str] = Field(default=None, max_length=40)
    locale: str = Field(default="en", max_length=20)
    board: Optional[dict[str, Any]] = None  # optional {"w","h"}: the board area in points, so tutor ink fits it


class IntentBody(_Body):
    action: str = Field(max_length=40)
    board_revision: int
    request_id: str = Field(max_length=80)
    text: Optional[str] = Field(default=None, max_length=MAX_QUESTION_CHARS)
    turn_id: Optional[str] = Field(default=None, max_length=40)


async def _read_bounded(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise ServiceError(413, "too_large", "That upload is too large.")
    return data


MAX_BODY_BYTES = MAX_IMAGE_BYTES + 512 * 1024  # an image plus the form fields; nothing legitimate is bigger


def _token_ok(given: str, token: str) -> bool:
    """Constant-time compare on bytes: ``hmac.compare_digest`` raises TypeError on non-ASCII ``str``."""
    return hmac.compare_digest(given.encode("utf-8", "replace"), token.encode("utf-8"))


def create_app(service: TutorService, *, api_token: Optional[str] = None) -> FastAPI:
    token = api_token if api_token is not None else os.environ.get("TUTOR_API_TOKEN") or None
    app = FastAPI(title="socrates-tutor-api", version="0.1.0")
    app.state.service = service

    @app.middleware("http")
    async def bound_body(request: Request, call_next):
        """Refuse an oversized upload from its Content-Length, before the multipart parser spools it to disk."""
        declared = request.headers.get("content-length")
        if request.method in ("POST", "PUT") and declared is not None:
            try:
                too_big = int(declared) > max(MAX_BODY_BYTES, MAX_AUDIO_BYTES + 512 * 1024)
            except ValueError:
                too_big = True
            if too_big:
                return JSONResponse({"code": "too_large", "message": "That upload is too large."}, status_code=413)
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"code": exc.code, "message": exc.message, **exc.extra}, status_code=exc.status)

    def guard(request: Request) -> None:
        """Wrong protocol → 426 (Interface.md); optional bearer token (CONTRACT-QUESTIONS 6)."""
        proto = request.headers.get(PROTOCOL_HEADER)
        if proto is not None and proto.strip() != str(PROTOCOL_VERSION):
            raise ServiceError(426, "upgrade_required", f"This server speaks protocol {PROTOCOL_VERSION}.")
        if token is not None and request.url.path != "/v2/health":
            given = request.headers.get("authorization", "")
            if not (given.lower().startswith("bearer ") and _token_ok(given[7:].strip(), token)):
                raise ServiceError(401, "unauthorized", "Missing or wrong credentials.")

    auth = [Depends(guard)]

    @app.get("/v2/health", dependencies=[Depends(guard)])
    async def health() -> dict[str, Any]:
        return await service.health()

    @app.post("/v2/sessions", dependencies=auth)
    async def create_session(body: CreateSessionBody) -> dict[str, Any]:
        s = await service.create_session(body.preset_id, body.locale, body.board)
        return {"session_id": s.session_id, "protocol_version": PROTOCOL_VERSION, "board_revision": s.board_revision}

    @app.post("/v2/sessions/{session_id}/question", dependencies=auth)
    async def question(
        session_id: str,
        board_revision: int = Form(...),
        request_id: str = Form(..., max_length=80),
        text: Optional[str] = Form(default=None, max_length=MAX_QUESTION_CHARS + 200),
        image: Optional[UploadFile] = File(default=None),
    ) -> dict[str, Any]:
        s = service.get(session_id)
        png = await _read_bounded(image, MAX_IMAGE_BYTES) if image is not None else None
        return await service.submit_question(s, board_revision, request_id, text=text, png=png)

    @app.post("/v2/sessions/{session_id}/board", dependencies=auth)
    async def board(session_id: str, meta: str = Form(..., max_length=MAX_JSON_FIELD), image: UploadFile = File(...)) -> dict[str, Any]:
        s = service.get(session_id)
        try:
            parsed = json.loads(meta)
        except ValueError:
            raise ServiceError(422, "bad_board_meta", "The board upload was malformed.") from None
        if not isinstance(parsed, dict):
            raise ServiceError(422, "bad_board_meta", "The board upload was malformed.")
        return await service.submit_board(s, parsed, await _read_bounded(image, MAX_IMAGE_BYTES))

    @app.post("/v2/sessions/{session_id}/audio", dependencies=auth)
    async def audio(session_id: str, board_revision: int = Form(...), request_id: str = Form(..., max_length=80),
                    audio: UploadFile = File(...)) -> dict[str, Any]:
        s = service.get(session_id)
        return await service.submit_audio(s, board_revision, request_id, await _read_bounded(audio, MAX_AUDIO_BYTES))

    @app.post("/v2/sessions/{session_id}/intent", dependencies=auth)
    async def intent(session_id: str, body: IntentBody) -> dict[str, Any]:
        return await service.intent(service.get(session_id), body.model_dump(exclude_none=True))

    @app.get("/v2/sessions/{session_id}/summary", dependencies=auth)
    async def summary(session_id: str) -> dict[str, Any]:
        return await service.summary(service.get(session_id))

    @app.delete("/v2/sessions/{session_id}", dependencies=auth)
    async def delete(session_id: str) -> dict[str, Any]:
        return await service.delete(session_id)

    @app.get("/v2/sessions/{session_id}/audio/{name}", dependencies=auth)
    async def get_audio(session_id: str, name: str) -> Response:
        wav = service.get(session_id).audio.get(name.removesuffix(".wav"))
        if wav is None:
            raise ServiceError(404, "unknown_clip", "That clip is gone.")
        return Response(wav, media_type="audio/wav")

    @app.websocket("/v2/sessions/{session_id}/events")
    async def events(ws: WebSocket, session_id: str, after_seq: Optional[int] = Query(default=None, ge=0),
                     access_token: Optional[str] = Query(default=None, alias="token")) -> None:
        if token is not None:
            given = ws.headers.get("authorization", "")
            bearer = given[7:].strip() if given.lower().startswith("bearer ") else (access_token or "")
            if not _token_ok(bearer, token):
                await ws.close(code=4401)
                return
        s = service.store.get(session_id)
        if s is None:
            await ws.close(code=4404)
            return
        await ws.accept()
        queue = s.subscribe()  # subscribe first so nothing emitted during the replay is lost; dedupe by seq below
        try:
            # A cursor from the future (a stale client) must not mute the stream: clamp it to what exists.
            sent = min(after_seq, s.seq) if after_seq is not None else s.seq
            if after_seq is not None:
                for env in s.replay_after(sent):
                    await ws.send_json(env)
                    sent = max(sent, env["seq"])
            s.emit_state()  # a fresh snapshot, after any replayed events (CONTRACT-QUESTIONS 1)
            reader = asyncio.create_task(_read(ws, s, service))
            writer = asyncio.create_task(_write(ws, queue, sent))
            done, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
            for task in (*pending, *done):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        except WebSocketDisconnect:
            pass
        finally:
            s.unsubscribe(queue)

    return app


async def _write(ws: WebSocket, queue: asyncio.Queue, sent: int) -> None:
    while True:
        env = await queue.get()
        if env is None:  # the session was deleted, or this client fell too far behind: close so it reconnects
            await ws.close(code=1001)
            return
        if env["seq"] > sent:
            await ws.send_json(env)
            sent = env["seq"]


async def _read(ws: WebSocket, session, service: TutorService) -> None:
    while True:
        try:
            msg = await ws.receive_json()
        except WebSocketDisconnect:
            return
        except ValueError:
            continue
        if isinstance(msg, dict):
            try:
                await service.client_message(session, msg)
            except Exception:  # a bad client message must never kill the stream
                log.exception("client message %r failed", msg.get("type"))
