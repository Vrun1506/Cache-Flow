"""The four preset problems the iPad offers (ios ServerProfile.swift `Preset.all`).

A preset session starts with a confirmed problem and a ready phase, and the
plan is built here, so choosing a preset needs no model call to begin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..schemas import Plan


@dataclass(frozen=True)
class Preset:
    id: str
    display: str
    subject: str
    plan: Plan
    transfer: Optional[tuple[str, str]] = None  # (display text, statement_expr) for the independent item


def _plan(question: str, domain: str, expr: Optional[str], kind: str, complexity: int, subdomain: str) -> Plan:
    return Plan(
        domain=domain, subdomain=subdomain, level="secondary", question_clean=question, statement_expr=expr,
        solution_kind=kind, complexity=complexity, safety="ok", is_new_topic=False, visuals=[], confidence=1.0,
    )


PRESETS: dict[str, Preset] = {
    "quad_5_6": Preset(
        "quad_5_6", "x² + 5x + 6 = 0", "algebra",
        _plan("Solve x^2 + 5x + 6 = 0", "math", "x**2+5*x+6=0", "compute", 3, "quadratics"),
        ("x² + 7x + 12 = 0", "x**2+7*x+12=0"),
    ),
    "quad_7_12": Preset(
        "quad_7_12", "x² + 7x + 12 = 0", "algebra",
        _plan("Solve x^2 + 7x + 12 = 0", "math", "x**2+7*x+12=0", "compute", 3, "quadratics"),
        ("x² + 9x + 20 = 0", "x**2+9*x+20=0"),
    ),
    "lin_2_3_11": Preset(
        "lin_2_3_11", "2x + 3 = 11", "algebra",
        _plan("Solve 2x + 3 = 11", "math", "2*x+3=11", "compute", 1, "linear equations"),
        ("3x + 2 = 14", "3*x+2=14"),
    ),
    "phys_forces_box": Preset(
        "phys_forces_box", "Forces on a box resting on a table", "physics",
        _plan("What forces act on a box resting on a table, and why do they balance?", "physics", None, "explain", 2, "forces"),
        None,
    ),
}
