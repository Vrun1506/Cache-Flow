"""Board geometry and tutor placement for the iPad v2 contract.

``BoardLayout`` mirrors ``BoardLayout.swift`` exactly (same constants, same
arithmetic) so a region the backend picks is a region the iPad accepts. Board
space has its origin at the top-left of the board area (below the header), in
points. On an 11-inch iPad Pro in landscape (1210 x 834) the board is
1210 x 638 and the tutor strip is the right-hand 500 points.

``Placer`` resolves a board command's relative anchor (below, right_of, inside,
column, free) to a concrete ``Rect``. It looks inside the tutor strip first,
then in the free parts of the work area, never overlapping the student's lines
or objects already placed. It is deterministic: the same inputs give the same
rectangles, scanned on an 8-point grid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# --- constants copied from BoardLayout.swift -------------------------------
SLOT_COUNT = 5
HEADER_HEIGHT = 64.0
CAPTION_BAR_HEIGHT = 132.0
TUTOR_STRIP_WIDTH = 500.0
QUESTION_BOX_HEIGHT = 132.0
QUESTION_BOX_INSET = 12.0
SLOT_SPACING = 8.0
STRIP_INSET = 12.0

DEFAULT_SCREEN = (1210.0, 834.0)  # 11-inch iPad Pro (M4), landscape

GRID = 8.0  # scan step of the placer
DEFAULT_GAP = 12.0
MIN_SCALE = 0.4


@dataclass(frozen=True)
class Rect:
    """An axis-aligned rectangle ``(x, y, w, h)``, y growing downwards."""

    x: float
    y: float
    w: float
    h: float

    @property
    def minx(self) -> float:
        return self.x

    @property
    def miny(self) -> float:
        return self.y

    @property
    def maxx(self) -> float:
        return self.x + self.w

    @property
    def maxy(self) -> float:
        return self.y + self.h

    @property
    def midx(self) -> float:
        return self.x + self.w / 2

    @property
    def midy(self) -> float:
        return self.y + self.h / 2

    @property
    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    def contains(self, other: "Rect") -> bool:
        """Containment like ``CGRect.contains(CGRect)``: edges may touch."""
        return (
            other.minx >= self.minx - 1e-9
            and other.miny >= self.miny - 1e-9
            and other.maxx <= self.maxx + 1e-9
            and other.maxy <= self.maxy + 1e-9
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.minx - 1e-9 <= x <= self.maxx + 1e-9 and self.miny - 1e-9 <= y <= self.maxy + 1e-9

    def intersects(self, other: "Rect") -> bool:
        """True when the interiors overlap (touching edges do not count)."""
        return (
            self.minx < other.maxx - 1e-9
            and other.minx < self.maxx - 1e-9
            and self.miny < other.maxy - 1e-9
            and other.miny < self.maxy - 1e-9
        )

    def inset(self, dx: float, dy: float | None = None) -> "Rect":
        """Like ``CGRect.insetBy``: positive shrinks, negative grows."""
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)

    def union(self, other: "Rect") -> "Rect":
        x0, y0 = min(self.minx, other.minx), min(self.miny, other.miny)
        x1, y1 = max(self.maxx, other.maxx), max(self.maxy, other.maxy)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def intersection(self, other: "Rect") -> Optional["Rect"]:
        x0, y0 = max(self.minx, other.minx), max(self.miny, other.miny)
        x1, y1 = min(self.maxx, other.maxx), min(self.maxy, other.maxy)
        if x1 <= x0 or y1 <= y0:
            return None
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def to_list(self) -> list[float]:
        return [round(self.x, 2), round(self.y, 2), round(self.w, 2), round(self.h, 2)]

    @staticmethod
    def from_list(a: Sequence[float]) -> "Rect":
        if len(a) != 4:
            raise ValueError("a rect is [x, y, w, h]")
        return Rect(float(a[0]), float(a[1]), float(a[2]), float(a[3]))


@dataclass(frozen=True)
class BoardLayout:
    screen_w: float
    screen_h: float
    header: Rect
    board: Rect  # in screen coordinates
    caption_bar: Rect
    student_region: Rect  # all of these are in board space from here on
    question_box: Rect
    work_area: Rect
    tutor_strip: Rect
    slots: tuple[Rect, ...]

    @staticmethod
    def for_screen(w: float = DEFAULT_SCREEN[0], h: float = DEFAULT_SCREEN[1]) -> "BoardLayout":
        header = Rect(0, 0, w, HEADER_HEIGHT)
        caption_bar = Rect(0, h - CAPTION_BAR_HEIGHT, w, CAPTION_BAR_HEIGHT)
        board = Rect(0, HEADER_HEIGHT, w, max(0.0, h - HEADER_HEIGHT - CAPTION_BAR_HEIGHT))

        local = Rect(0, 0, board.w, board.h)
        strip_w = min(TUTOR_STRIP_WIDTH, local.w * 0.45)
        student = Rect(0, 0, local.w - strip_w, local.h)
        q = QUESTION_BOX_INSET
        qbox = Rect(q, q, student.w - 2 * q, QUESTION_BOX_HEIGHT)
        work = Rect(0, qbox.maxy + q, student.w, max(0.0, student.h - qbox.maxy - q))
        strip = Rect(student.maxx, 0, strip_w, local.h)

        inner = strip.inset(STRIP_INSET, STRIP_INSET)
        spacing = SLOT_SPACING * (SLOT_COUNT - 1)
        slot_h = max(0.0, (inner.h - spacing) / SLOT_COUNT)
        slots = tuple(
            Rect(inner.minx, inner.miny + i * (slot_h + SLOT_SPACING), inner.w, slot_h) for i in range(SLOT_COUNT)
        )
        return BoardLayout(w, h, header, board, caption_bar, student, qbox, work, strip, slots)

    @property
    def board_bounds(self) -> Rect:
        """The whole board in board space: the rectangle every ink and scene must sit inside."""
        return Rect(0, 0, self.board.w, self.board.h)

    @property
    def fingerprint(self) -> str:
        return f"{int(self.screen_w)}x{int(self.screen_h)}"

    @property
    def strip_inner(self) -> Rect:
        return self.tutor_strip.inset(STRIP_INSET, STRIP_INSET)

    def slot_index(self, x: float, y: float) -> Optional[int]:
        for i, slot in enumerate(self.slots):
            if slot.contains_point(x, y):
                return i
        return None


# ------------------------------------------------------------------ placement


def _fit(size: tuple[float, float], area: Rect, scale: float) -> Optional[tuple[float, float]]:
    """Scale ``size`` (preserving aspect) so it fits ``area``; None if smaller than ``scale`` of natural."""
    w, h = max(1.0, float(size[0])), max(1.0, float(size[1]))
    k = min(1.0, area.w / w, area.h / h)
    if k < scale:
        return None
    return w * k, h * k


@dataclass
class Placer:
    """Finds room on the board for tutor objects.

    ``student_regions`` are the bounding boxes of the student's lines (and the
    question box if you want it kept clear). Placed objects are remembered by
    id so later commands can anchor to them with ``ref``.
    """

    layout: BoardLayout
    student_regions: list[Rect] = field(default_factory=list)
    _placed: dict[str, Rect] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)
    _named: dict[str, Rect] = field(default_factory=dict)

    # ---- bookkeeping

    def register(self, name: str, rect: Rect) -> None:
        """Make a student object addressable by ``ref`` (for example ``line-2``). Also keeps it clear."""
        self._named[name] = rect
        if rect not in self.student_regions:
            self.student_regions.append(rect)

    def record(self, id: str, rect: Rect) -> None:
        """Remember (or update) where ``id`` ended up."""
        if id not in self._placed:
            self._order.append(id)
        self._placed[id] = rect

    def rect_of(self, id: Optional[str]) -> Optional[Rect]:
        if id is None:
            return None
        return self._placed.get(id) or self._named.get(id)

    @property
    def latest(self) -> Optional[Rect]:
        return self._placed[self._order[-1]] if self._order else None

    @property
    def placed_rects(self) -> list[Rect]:
        return [self._placed[i] for i in self._order]

    # ---- the public entry point

    def place(self, cmd, natural_size: tuple[float, float]) -> Optional[Rect]:
        """Choose a rectangle for ``cmd`` (a BoardCmd) and record it under ``cmd.id``.

        ``natural_size`` is the size the object wants; ``cmd.size_hint`` overrides it. Returns None only
        when nothing at least 40% of the natural size fits anywhere (the board is full).
        """
        size = natural_size
        hint = getattr(cmd, "size_hint", None)
        if hint is not None:
            size = (float(hint.w), float(hint.h))
        anchor = cmd.anchor
        gap = float(anchor.gap) if anchor.gap is not None else DEFAULT_GAP
        ref = self.rect_of(anchor.ref) if anchor.ref else None
        if ref is None and anchor.rel in ("below", "right_of", "inside"):
            ref = self.latest

        rect: Optional[Rect] = None
        if anchor.rel == "inside" and ref is not None:
            rect = self._inside(ref, size)
        elif anchor.rel == "column":
            rect = self._column(anchor.col or 1, size, gap)
        elif anchor.rel in ("below", "right_of") and ref is not None:
            rect = self._next_to(anchor.rel, ref, size, gap)
        if rect is None:
            rect = self._scan(size, gap)
        if rect is not None:
            self.record(cmd.id, rect)
        return rect

    # ---- strategies

    def _blockers(self, gap: float) -> list[Rect]:
        pad = min(gap, 6.0)
        return [r.inset(-pad, -pad) for r in (*self.student_regions, *self._placed.values())]

    def _free(self, rect: Rect, gap: float, bounds: Rect) -> bool:
        if rect.is_empty or not bounds.contains(rect):
            return False
        return not any(rect.intersects(b) for b in self._blockers(gap))

    def _areas(self) -> list[Rect]:
        lay = self.layout
        return [lay.strip_inner, lay.work_area.inset(STRIP_INSET, 0)]

    def _inside(self, ref: Rect, size: tuple[float, float]) -> Optional[Rect]:
        area = ref.inset(6, 6)
        if area.w < 8 or area.h < 8:
            return None
        fit = _fit(size, area, 0.0)  # a label inside a box may shrink as far as it must
        if fit is None:
            return None
        w, h = fit
        return Rect(area.midx - w / 2, area.midy - h / 2, w, h)

    def _next_to(self, rel: str, ref: Rect, size: tuple[float, float], gap: float) -> Optional[Rect]:
        bounds = self.layout.board_bounds
        for scale in (1.0, 0.85, 0.7, 0.55, MIN_SCALE):
            w, h = max(1.0, size[0] * scale), max(1.0, size[1] * scale)
            if rel == "below":
                cand = Rect(ref.minx, ref.maxy + gap, w, h)
            else:
                cand = Rect(ref.maxx + gap, ref.miny, w, h)
            if self._free(cand, gap, bounds) and self._same_zone(ref, cand):
                return cand
        return None

    def _same_zone(self, a: Rect, b: Rect) -> bool:
        """Keep an anchored object in the same zone (strip or student side) as what it hangs from."""
        strip = self.layout.tutor_strip
        return strip.contains(a) == strip.contains(b) or not strip.contains(a)

    def _column(self, col: int, size: tuple[float, float], gap: float) -> Optional[Rect]:
        inner = self.layout.strip_inner
        ncols = 2 if col <= 2 else 4
        width = inner.w / ncols
        col = max(1, min(col, ncols))
        area = Rect(inner.minx + (col - 1) * width, inner.miny, width, inner.h)
        return self._scan_in([area], size, gap)

    def _scan(self, size: tuple[float, float], gap: float) -> Optional[Rect]:
        return self._scan_in(self._areas(), size, gap)

    def _scan_in(self, areas: Iterable[Rect], size: tuple[float, float], gap: float) -> Optional[Rect]:
        """First free spot on an 8-point grid. Full size in every area first, then smaller sizes."""
        areas = [a for a in areas if not a.is_empty]
        for scale in (1.0, 0.85, 0.7, 0.55, MIN_SCALE):
            for area in areas:
                fit = _fit((size[0] * scale, size[1] * scale), area, 0.0)
                if fit is None:
                    continue
                w, h = fit
                y = area.miny
                while y + h <= area.maxy + 1e-9:
                    x = area.minx
                    while x + w <= area.maxx + 1e-9:
                        cand = Rect(x, y, w, h)
                        if self._free(cand, gap, self.layout.board_bounds):
                            return cand
                        x += GRID
                    y += GRID
        return None


def round_up(v: float, step: float = GRID) -> float:
    return math.ceil(v / step) * step
