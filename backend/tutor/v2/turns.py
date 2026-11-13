"""Turn machinery: one cancellable tutor turn at a time (Interface.md, CONTRACT-QUESTIONS 3).

A turn is the unit the iPad cancels or supersedes. Starting a new turn cancels the running one
and emits ``turn_cancelled`` as its boundary. Ink and scenes follow the preview → ack → commit
rule: the server only commits what the iPad acknowledged as placed, and re-issues stale ink once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from .session import V2Session

log = logging.getLogger("tutor.v2")

ACK_TIMEOUT_S = 4.0
Build = Callable[[str], Awaitable[None]]


class TurnRunner:
    def __init__(self, ack_timeout_s: float = ACK_TIMEOUT_S) -> None:
        self.ack_timeout_s = ack_timeout_s

    # ------------------------------------------------------------------ turns

    async def start(
        self,
        session: V2Session,
        request_id: str,
        rev: int,
        build: Build,
        *,
        end_phase: Callable[[V2Session], str],
        failure_message: str = "Something went wrong on my side. Please try again.",
    ) -> str:
        """Cancel any running turn, open a new one, and run ``build(turn_id)`` in the background."""
        if session.running_turn:
            await self.cancel(session, session.running_turn, "superseded", emit_state=False)
        session.turn_no += 1
        turn = f"t{session.turn_no}"
        session.running_turn = turn
        session.latest_turn_id = turn
        session.emit_state("tutoring", turn=turn, request=request_id, rev=rev)
        session.runner = asyncio.create_task(self._run(session, turn, request_id, rev, build, end_phase, failure_message))
        return turn

    async def _run(self, session: V2Session, turn: str, request_id: str, rev: int, build: Build,
                   end_phase: Callable[[V2Session], str], failure_message: str) -> None:
        try:
            await build(turn)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("turn %s failed", turn)
            if turn not in session.cancelled_turns:
                session.emit("error", {"code": "turn_failed", "message": failure_message, "retryable": True,
                                       "request_id": request_id, "latest_board_revision": session.board_revision},
                             turn=turn, request=request_id, rev=rev)
        finally:
            if session.running_turn == turn and turn not in session.cancelled_turns:
                session.running_turn = None
                session.runner = None
                session.emit_state(end_phase(session), turn=turn, rev=rev)

    async def cancel(self, session: V2Session, turn: str, reason: str, *, emit_state: bool = True,
                     end_phase: Optional[Callable[[V2Session], str]] = None) -> bool:
        """Cancel ``turn`` if it is the running one. The ``turn_cancelled`` event is its last."""
        if session.running_turn != turn or turn in session.cancelled_turns:
            return False
        session.emit("turn_cancelled", {"reason": reason}, turn=turn)
        session.cancelled_turns.add(turn)
        runner, session.runner, session.running_turn = session.runner, None, None
        for key in [k for k in session.acks if not session.acks[k].done()]:
            session.acks[key].cancel()
        if runner and not runner.done() and runner is not asyncio.current_task():
            runner.cancel()
            try:
                await runner
            except (asyncio.CancelledError, Exception):
                pass
        if emit_state and end_phase is not None:
            session.emit_state(end_phase(session))
        return True

    # ------------------------------------------------------------------- acks

    def resolve_ack(self, session: V2Session, key: str, placed: bool, reason: Optional[str]) -> None:
        fut = session.acks.get(key)
        if fut is not None and not fut.done():
            fut.set_result((placed, reason))

    async def _await_ack(self, session: V2Session, key: str) -> tuple[Optional[bool], Optional[str]]:
        fut = session.acks[key]
        try:
            return await asyncio.wait_for(asyncio.shield(fut), self.ack_timeout_s)
        except asyncio.TimeoutError:
            return None, "no_ack"
        finally:
            session.acks.pop(key, None)

    async def place(
        self,
        session: V2Session,
        turn: str,
        rev: int,
        payload: dict[str, Any],
        *,
        preview: str,
        commit: str,
        id_key: str,
        ack_prefix: str,
        new_id: Callable[[], str],
    ) -> bool:
        """Send a preview, wait for the iPad's placement ack, then commit. A stale preview is re-issued once."""
        for attempt in (0, 1):
            ident = payload[id_key]
            key = f"{ack_prefix}:{ident}"
            session.acks[key] = asyncio.get_running_loop().create_future()
            if session.emit(preview, payload, turn=turn, rev=rev) is None:
                session.acks.pop(key, None)
                return False  # the turn was cancelled
            placed, reason = await self._await_ack(session, key)
            if placed:
                session.emit(commit, {"id": ident}, turn=turn, rev=rev)
                return True
            if reason == "stale_revision" and attempt == 0:
                # The student wrote since: re-issue against the revision the ack reported (CONTRACT-QUESTIONS 5).
                payload = {**payload, id_key: new_id()}
                rev = session.board_revision
                continue
            log.info("%s %s not placed (%s)", preview, ident, reason)
            return False
        return False

    async def place_ink(self, session: V2Session, turn: str, rev: int, ink: dict[str, Any]) -> bool:
        return await self.place(
            session, turn, rev, ink, preview="tutor_ink_preview", commit="tutor_ink_commit",
            id_key="ink_id", ack_prefix="ink", new_id=session.next_ink_id,
        )

    async def place_scene(self, session: V2Session, turn: str, rev: int, scene: dict[str, Any]) -> bool:
        return await self.place(
            session, turn, rev, scene, preview="scene_preview", commit="scene_commit",
            id_key="scene_id", ack_prefix="scene", new_id=session.next_scene_id,
        )
