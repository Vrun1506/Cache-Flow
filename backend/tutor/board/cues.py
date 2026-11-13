"""Deterministic cue resolution (final-plan.md §16.6).

The server turns each command's symbolic cue (``start``, ``after_intent``,
``mid_explain``, ``end``) into milliseconds from the start of the step's
audio, so the iPad can schedule every command locally with no round trip.
"""

from __future__ import annotations

from ..schemas.board import BoardCmd

WORDS_PER_SECOND = 2.6  # estimate used when no real clip duration exists
PEN_MS_PER_CHAR = 70
PEN_MIN_MS = 600
END_LEAD_MS = 300


def estimate_ms(text: str) -> int:
    """Spoken duration estimate: 2.6 words per second, at least 400 ms for any text."""
    words = len(text.split())
    if words == 0:
        return 0
    return max(400, round(words / WORDS_PER_SECOND * 1000))


def cue_times(d_intent_ms: int, d_explain_ms: int) -> dict[str, int]:
    d_i, d_e = max(0, d_intent_ms), max(0, d_explain_ms)
    return {
        "start": 0,
        "after_intent": d_i,
        "mid_explain": d_i + round(0.5 * d_e),
        "end": max(0, d_i + d_e - END_LEAD_MS),
    }


def _pen_base_ms(cmd: BoardCmd) -> int:
    return max(PEN_MIN_MS, cmd.text_chars * PEN_MS_PER_CHAR)


def resolve_cues(
    cmds: list[BoardCmd], d_intent_ms: int, d_explain_ms: int, estimated: bool = False
) -> list[BoardCmd]:
    """Return copies of ``cmds`` with ``cue.at_ms`` and ``cue.pen_ms`` filled in.

    ``pen_ms`` is ``clamp(chars * 70, 600, time to the next cue)``. When the
    window is shorter than the pen needs, drawing is cut to the window and the
    iPad completes whatever is left instantly (it never waits for the pen).
    ``estimated`` only records that the durations were guessed; the maths is the same.
    """
    times = cue_times(d_intent_ms, d_explain_ms)
    total = max(0, d_intent_ms) + max(0, d_explain_ms)
    out: list[BoardCmd] = []
    for cmd in cmds:
        at_ms = times.get(cmd.cue.at, 0)  # an unknown cue name starts immediately
        out.append(cmd.model_copy(deep=True, update={"cue": cmd.cue.model_copy(update={"at_ms": at_ms})}))
    distinct = sorted({c.cue.at_ms for c in out})
    for cmd in out:
        later = [t for t in distinct if t > cmd.cue.at_ms]
        window = (later[0] if later else max(total, cmd.cue.at_ms)) - cmd.cue.at_ms
        cmd.cue.pen_ms = max(0, min(_pen_base_ms(cmd), window))
    return out
