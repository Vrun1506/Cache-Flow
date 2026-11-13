"""Shared helpers for the v2 tests: a mirror of the Swift decoder's shapes, PNGs, and a socket client that acks ink."""

from __future__ import annotations

import io
import json
from typing import Any, Callable

from PIL import Image, ImageDraw

VERDICTS = {"valid", "invalid", "partial", "unknown"}


class ShapeError(AssertionError):
    pass


def _req(d: dict, key: str, kind, where: str):
    if key not in d:
        raise ShapeError(f"{where}: missing required key {key!r} in {sorted(d)}")
    if not isinstance(d[key], kind) or (kind is int and isinstance(d[key], bool)):
        raise ShapeError(f"{where}.{key}: expected {kind}, got {type(d[key]).__name__}")
    return d[key]


def _opt(d: dict, key: str, kind, where: str):
    if key in d and d[key] is not None:
        _req(d, key, kind, where)


def _rect(a, where: str):
    if not (isinstance(a, list) and len(a) == 4 and all(isinstance(v, (int, float)) for v in a) and a[2] >= 0 and a[3] >= 0):
        raise ShapeError(f"{where}: rect must be [x, y, w, h], got {a!r}")


def _points(strokes, where: str):
    for line in strokes:
        for p in line:
            if not (isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p)):
                raise ShapeError(f"{where}: bad point {p!r}")


def _region(r: dict, where: str):
    _rect(_req(r, "bbox", list, where), where + ".bbox")
    _opt(r, "line_index", int, where)


def _skill(s: dict, where: str):
    _req(s, "skill", str, where); _req(s, "label", str, where); _req(s, "level", int, where)
    _req(s, "independent_evidence", int, where); _req(s, "assisted", bool, where)


def _problem(p: dict, where: str):
    for k in ("text", "subject", "item"):
        _req(p, k, str, where)
    _req(p, "confirmed", bool, where)


def check_payload(type: str, p: dict) -> None:
    w = f"payload[{type}]"
    if type == "state":
        _req(p, "phase", str, w)
        if "problem" in p and p["problem"] is not None:
            _problem(p["problem"], w + ".problem")
        _opt(p, "latest_turn_id", str, w)
        for s in p.get("skills", []):
            _skill(s, w + ".skills")
    elif type == "read_back":
        _req(p, "request_id", str, w); _req(p, "source", str, w); _req(p, "text", str, w)
        _opt(p, "ambiguous", bool, w); _opt(p, "note", str, w)
        for r in p.get("regions", []):
            _region(r, w + ".regions")
        for a in p.get("alternatives", []):
            assert isinstance(a, str)
    elif type == "evidence":
        for k in ("evidence_id", "step_kind", "reason_code", "verifier_version"):
            _req(p, k, str, w)
        if _req(p, "verdict", str, w) not in VERDICTS:
            raise ShapeError(f"{w}: bad verdict {p['verdict']!r}")
        _opt(p, "label", str, w); _opt(p, "request_id", str, w); _opt(p, "line_index", int, w)
        _opt(p, "independent", bool, w); _opt(p, "normalized_step", str, w); _opt(p, "subject", str, w)
        for r in p.get("highlights", []):
            _region(r, w + ".highlights")
    elif type == "tutor_turn":
        _req(p, "move", str, w); _req(p, "caption", str, w)
        _opt(p, "audio_url", str, w); _opt(p, "assisted", bool, w)
        for r in p.get("highlights", []):
            _region(r, w + ".highlights")
    elif type == "tutor_ink_preview":
        _req(p, "ink_id", str, w)
        _rect(_req(p, "region", list, w), w + ".region")
        _points(p.get("strokes", []), w + ".strokes")
        _opt(p, "style", str, w); _opt(p, "formula", str, w)
    elif type in ("tutor_ink_commit", "scene_commit"):
        _req(p, "id", str, w)
    elif type == "scene_preview":
        _req(p, "scene_id", str, w)
        _rect(_req(p, "region", list, w), w + ".region")
        for prim in p.get("primitives", []):
            _req(prim, "kind", str, w)
            _points([prim.get("points", [])], w + ".points")
            _opt(prim, "text", str, w)
    elif type == "audio_chunk":
        _req(p, "chunk_index", int, w); _req(p, "audio_url", str, w)
        _opt(p, "caption_span", str, w); _opt(p, "final", bool, w)
    elif type == "agent_trace":
        for k in ("component", "action", "outcome"):
            _req(p, k, str, w)
        _req(p, "latency_ms", int, w); _opt(p, "evidence_id", str, w)
    elif type == "skills":
        for s in _req(p, "skills", list, w):
            _skill(s, w)
    elif type == "error":
        _req(p, "code", str, w); _req(p, "message", str, w); _req(p, "retryable", bool, w)
        _opt(p, "request_id", str, w); _opt(p, "latest_board_revision", int, w)
    elif type == "turn_cancelled":
        _req(p, "reason", str, w)
    # unknown types decode as `.unknown` on the iPad: allowed


def check_envelope(env: dict) -> dict:
    """Raise unless ``env`` would decode as a Swift ``ServerEnvelope``. Returns it for chaining."""
    where = "envelope"
    if env.get("v") != 2:
        raise ShapeError(f"{where}: v must be 2")
    _req(env, "type", str, where); _req(env, "session_id", str, where)
    _req(env, "seq", int, where); _req(env, "board_revision", int, where)
    for k in ("turn_id", "run_id", "request_id"):
        _opt(env, k, str, where)
    _opt(env, "replayed", bool, where)
    check_payload(env["type"], _req(env, "payload", dict, where))
    return env


def png(width: int = 1200, height: int = 640, lines: int = 1) -> bytes:
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    for i in range(lines):
        d.text((40, 180 + 60 * i), f"line {i}: x + {i + 2} = 0", fill="black")  # distinct pixels per line
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def meta(rev: int, rid: str, lines: int = 1) -> str:
    return json.dumps({
        "board_revision": rev, "request_id": rid, "image_region": [0, 0, 1200, 640],
        "lines": [{"line_index": i, "bbox": [40, 170 + 60 * i, 300, 50]} for i in range(lines)],
    })


class Client:
    """A scripted iPad: a TestClient + a socket that checks every event's shape and acks ink and scenes."""

    def __init__(self, tc, *, ack: Callable[[dict], tuple[bool, str | None]] | None = None):
        self.tc = tc
        self.ack = ack or (lambda env: (True, None))
        self.rev = 0
        self.n = 0
        self.sid = ""
        self.ws = None
        self._cm = None
        self.events: list[dict] = []
        self.pending_acks = True

    def open(self, preset: str | None = "quad_5_6", after_seq: int | None = None, headers: dict | None = None) -> dict:
        r = self.tc.post("/v2/sessions", json={"preset_id": preset, "locale": "en_GB"}, headers=headers or {})
        assert r.status_code == 200, r.text
        info = r.json()
        self.sid = info["session_id"]
        self.connect(after_seq)
        return info

    def connect(self, after_seq: int | None = None):
        q = "" if after_seq is None else f"?after_seq={after_seq}"
        self._cm = self.tc.websocket_connect(f"/v2/sessions/{self.sid}/events{q}")
        self.ws = self._cm.__enter__()

    def close(self):
        if self._cm is not None:
            self._cm.__exit__(None, None, None)
            self._cm = None

    def rid(self) -> str:
        self.n += 1
        return f"r{self.n}"

    # ------------------------------------------------------------------ socket

    def send(self, type: str, **payload) -> None:
        self.ws.send_json({"v": 2, "type": type, "session_id": self.sid, "board_revision": self.rev, "payload": payload})

    def _recv(self, timeout: float = 8.0) -> dict:
        """Receive with a timeout, so a missing event fails the test with context instead of hanging it."""
        import anyio

        async def _receive():
            with anyio.fail_after(timeout):
                return await self.ws._send_rx.receive()

        try:
            msg = self.ws.portal.call(_receive)
        except TimeoutError:
            recent = [(e["type"], e["payload"].get("phase") or e["payload"].get("move") or e["payload"].get("code")) for e in self.events[-12:]]
            raise AssertionError(f"timed out waiting for an event; recent: {recent}") from None
        self.ws._raise_on_close(msg)
        return json.loads(msg["text"])

    def next(self) -> dict:
        env = check_envelope(self._recv())
        self.events.append(env)
        if env["type"] in ("tutor_ink_preview", "scene_preview") and not env.get("replayed") and self.pending_acks:
            placed, reason = self.ack(env)
            key = "ink_id" if env["type"] == "tutor_ink_preview" else "scene_id"
            kind = "tutor_ink_ack" if env["type"] == "tutor_ink_preview" else "scene_ack"
            self.send(kind, turn_id=env["turn_id"], **{key: env["payload"][key]}, placed=placed,
                      **({"reason": reason} if reason else {}))
        return env

    def until(self, pred: Callable[[dict], bool], limit: int = 400) -> list[dict]:
        seen = []
        for _ in range(limit):
            env = self.next()
            seen.append(env)
            if pred(env):
                return seen
        raise AssertionError(f"never saw the event; last: {[(e['type'], e['payload'].get('phase') or e['payload'].get('move')) for e in seen[-8:]]}")

    def until_phase(self, phase: str, limit: int = 400) -> list[dict]:
        return self.until(lambda e: e["type"] == "state" and e["payload"]["phase"] == phase, limit)

    def until_type(self, type: str, limit: int = 400) -> list[dict]:
        return self.until(lambda e: e["type"] == type, limit)

    # -------------------------------------------------------------------- http

    def intent(self, action: str, text: str | None = None, *, rid: str | None = None, rev: int | None = None, **extra):
        body = {"action": action, "board_revision": self.rev if rev is None else rev, "request_id": rid or self.rid(), **extra}
        if text is not None:
            body["text"] = text
        return self.tc.post(f"/v2/sessions/{self.sid}/intent", json=body)

    def question(self, *, text: str | None = None, image: bytes | None = None, rid: str | None = None):
        data = {"board_revision": str(self.rev), "request_id": rid or self.rid()}
        files = {}
        if text is not None:
            data["text"] = text
        if image is not None:
            files["image"] = ("question.png", image, "image/png")
        return self.tc.post(f"/v2/sessions/{self.sid}/question", data=data, files=files or None)

    def board(self, rev: int, lines: int = 1, rid: str | None = None, image: bytes | None = None):
        rid = rid or self.rid()
        self.rev = rev
        return self.tc.post(f"/v2/sessions/{self.sid}/board", data={"meta": meta(rev, rid, lines)},
                            files={"image": ("board.png", image or png(lines=lines), "image/png")})

    def types(self, events: list[dict]) -> list[str]:
        return [e["type"] for e in events]
