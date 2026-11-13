"""Board commands: what the agent pen writes and draws (final-plan.md §5, §16.2).

The LLM-facing schema is deliberately flat: one permissive ``Payload`` whose
fields are all optional, instead of a union per op. That keeps the grammar
Ollama builds small and avoids ``null`` padding. Server-only fields
(``strokes``, ``metrics``, ``at_ms``, ``pen_ms``) are filled in by code and are
removed from the wire schema by :func:`board_llm_schema`.

Serialise with ``model_dump(by_alias=True, exclude_none=True)`` so the arrow
payload field is called ``from`` as in the spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .jsonschema import inline_schema
from .text import Hard, Soft, cap_list, trim_sentence

Op = Literal["write", "shape", "arrow", "plot", "table", "highlight", "underline", "erase", "group", "focus"]
Rel = Literal["below", "right_of", "inside", "column", "free"]
CueAt = Literal["start", "after_intent", "mid_explain", "end"]
Color = Literal["agent", "accent", "warn"]
Kind = Literal["rect", "circle", "line", "triangle", "brace", "box", "glow"]

MAX_COMMANDS = 12
MAX_TEXT_CHARS = 400
MAX_GAP = 48
ID_FORMAT = "b-{step}-{n}"

# Fields only the server fills in. They never appear in the schema the model sees.
SERVER_FIELDS = frozenset({"strokes", "metrics", "at_ms", "pen_ms"})

# Lowest number = most important. Used when the text cap forces commands to be dropped.
_PRIORITY = {
    "write": 0,
    "table": 1,
    "plot": 1,
    "shape": 2,
    "arrow": 2,
    "highlight": 3,
    "underline": 3,
    "group": 4,
    "focus": 4,
    "erase": 4,
}


def _clamp(lo: int, hi: int):
    def _c(v: Any) -> Any:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        return max(lo, min(hi, int(v)))

    return BeforeValidator(_c)


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Anchor(_Base):
    rel: Rel = "free"
    ref: Optional[Soft(40)] = None  # none with below/right_of means "relative to the latest object"
    col: Annotated[Optional[int], _clamp(1, 4)] = None
    gap: Annotated[Optional[int], _clamp(0, MAX_GAP)] = None


class SizeHint(_Base):
    w: Annotated[int, _clamp(10, 2000)]
    h: Annotated[int, _clamp(10, 2000)]


class Style(_Base):
    color: Color = "agent"


class Cue(_Base):
    at: CueAt = "start"
    at_ms: Optional[int] = None  # server only: resolved by tutor.board.cues
    pen_ms: Optional[int] = None  # server only: how long the pen takes


class Metrics(_Base):
    w: float
    h: float


class Payload(_Base):
    text: Optional[Soft(120)] = None  # write text (use \n between lines), shape label, plot title
    speed: Optional[Literal["slow", "normal"]] = None
    kind: Optional[Kind] = None  # shape kind or highlight kind
    from_: Optional[Soft(40)] = Field(default=None, alias="from")  # arrow start: an object id
    to: Optional[Soft(40)] = None  # arrow end: an object id
    ref: Optional[Soft(40)] = None  # target of highlight, underline, erase, focus
    refs: Annotated[Optional[list[Soft(40)]], cap_list(6)] = None  # group members
    expr: Optional[Hard(120)] = None  # plot expression (final-plan.md §16.7 grammar)
    x: Annotated[Optional[list[float]], cap_list(2)] = None
    y: Annotated[Optional[list[float]], cap_list(2)] = None
    rows: Annotated[Optional[list[Annotated[list[Soft(24)], cap_list(4)]]], cap_list(6)] = None
    zoom: Optional[float] = None
    strokes: Optional[list[list[list[float]]]] = None  # server only: local-coordinate pen strokes
    metrics: Optional[Metrics] = None  # server only

    def refs_used(self) -> list[str]:
        out = [r for r in (self.from_, self.to, self.ref) if r]
        return out + list(self.refs or [])

    def text_chars(self) -> int:
        n = len(self.text or "")
        for row in self.rows or []:
            n += sum(len(cell) for cell in row)
        return n


class BoardCmd(_Base):
    id: Soft(24)
    op: Op
    anchor: Anchor = Field(default_factory=Anchor)
    size_hint: Optional[SizeHint] = None
    style: Style = Field(default_factory=Style)
    payload: Payload = Field(default_factory=Payload)
    cue: Cue = Field(default_factory=Cue)

    @property
    def text_chars(self) -> int:
        return self.payload.text_chars()

    def refs_used(self) -> list[str]:
        out = self.payload.refs_used()
        if self.anchor.ref:
            out.append(self.anchor.ref)
        return out


def _apply_caps(cmds: list[BoardCmd]) -> list[BoardCmd]:
    """Enforce the per-step limits: at most 12 commands and 400 text characters."""
    cmds = list(cmds[:MAX_COMMANDS])
    total = sum(c.text_chars for c in cmds)
    if total <= MAX_TEXT_CHARS:
        return cmds
    # Drop the least important commands first (latest first within a priority) until it fits.
    writes_left = sum(1 for c in cmds if c.op == "write")
    dropped: set[int] = set()
    for i in sorted(range(len(cmds)), key=lambda i: (-_PRIORITY[cmds[i].op], -i)):
        if total <= MAX_TEXT_CHARS:
            break
        if cmds[i].text_chars == 0:
            continue  # dropping a command with no text would not help
        if cmds[i].op == "write":
            if writes_left <= 1:
                continue  # keep the last write; it is trimmed below
            writes_left -= 1
        total -= cmds[i].text_chars
        dropped.add(i)
    kept = [c for i, c in enumerate(cmds) if i not in dropped]
    if total > MAX_TEXT_CHARS:
        for c in kept:  # one oversized command is left: trim its text
            if c.payload.text:
                other = total - len(c.payload.text)
                c.payload.text = trim_sentence(c.payload.text, max(20, MAX_TEXT_CHARS - other))
                break
    return kept


class BoardPlan(_Base):
    commands: Annotated[list[BoardCmd], cap_list(MAX_COMMANDS * 2)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _limits(self) -> "BoardPlan":
        self.commands = _apply_caps(self.commands)
        return self


@dataclass(frozen=True)
class ClipTiming:
    """Durations of a step's two spoken clips, used to resolve board cues."""

    intent_ms: int
    explain_ms: int
    estimated: bool = False

    @property
    def total_ms(self) -> int:
        return self.intent_ms + self.explain_ms


def board_id(step: int, n: int) -> str:
    return ID_FORMAT.format(step=step, n=n)


def _strip_server_fields(node: Any) -> None:
    if isinstance(node, list):
        for n in node:
            _strip_server_fields(n)
    elif isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for name in SERVER_FIELDS & set(props):
                del props[name]
            if isinstance(node.get("required"), list):
                node["required"] = [r for r in node["required"] if r in props]
        for value in node.values():
            _strip_server_fields(value)


def board_llm_schema() -> dict[str, Any]:
    """JSON schema for the Board director. Optional fields stay optional to keep outputs short."""
    schema = inline_schema(BoardPlan, require_all=False)
    item = schema["properties"]["commands"]["items"]
    item["required"] = ["id", "op", "anchor", "payload", "cue"]
    item["properties"]["anchor"]["required"] = ["rel"]
    item["properties"]["cue"]["required"] = ["at"]
    _strip_server_fields(schema)
    schema["required"] = ["commands"]
    return schema
