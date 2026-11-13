"""Fixed graph templates (final-plan.md §16.1).

The Orchestrator never invents a graph. Each intent maps to one template
defined here. Only the nodes that exist today are listed, so the ``plan``
event never promises work that cannot run. Lesson, board, animation and quiz
nodes are added with their agents.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Literal

from ..llm.types import Tier


@dataclass(frozen=True)
class Node:
    id: str
    title: str
    agent: str
    tier: Tier | Literal["none"]
    required: bool
    deps: tuple[str, ...]
    timeout_s: float

    def public(self, state: str = "queued") -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "agent": self.agent,
            "deps": list(self.deps),
            "required": self.required,
            "state": state,
        }


GRAPHS: dict[str, tuple[Node, ...]] = {
    "solve_teach": (
        Node("orchestrate", "Understand the question", "orchestrator", "router", True, (), 20.0),
        Node("solve", "Work out the solution", "solver", "reasoner", True, ("orchestrate",), 60.0),
        Node("verify", "Check the solution", "verifier", "reasoner", True, ("solve",), 30.0),
    ),
}

# Nodes whose number depends on the solution are created after `verify` (final-plan.md §16.1).
LESSON_TIMEOUT_S = 40.0
STEP_TIMEOUT_S = 45.0
ANIMATE_TIMEOUT_S = 40.0
QUIZ_TIMEOUT_S = 30.0


def teaching_nodes(n_steps: int, n_visuals: int) -> tuple[Node, ...]:
    """The per-run nodes for lessons, steps, animations and the closing quiz."""
    nodes: list[Node] = []
    batches = (n_steps + 2) // 3
    for b in range(1, batches + 1):
        nodes.append(Node(f"lesson[{b}]", f"Plan the lesson (part {b})", "lesson_designer", "reasoner", True, ("verify",), LESSON_TIMEOUT_S))
    for i in range(1, n_steps + 1):
        nodes.append(Node(f"step[{i}]", f"Prepare step {i}", "board_director", "router", True, (f"lesson[{(i + 2) // 3}]",), STEP_TIMEOUT_S))
    for k in range(1, n_visuals + 1):
        nodes.append(Node(f"animate[{k}]", f"Animate idea {k}", "animator", "router", False, ("verify",), ANIMATE_TIMEOUT_S))
    nodes.append(Node("quiz", "Write the practice questions", "quiz_designer", "reasoner", False, ("verify",), QUIZ_TIMEOUT_S))
    return tuple(nodes)


RUN_DEADLINE_S = 300.0
