"""Animator: choose a scene template and its parameters (final-plan.md §6, §16.4, §16.7).

The LLM never writes frames. If the model fails, the scene falls back to a
concept card built from the visual's concept and the given facts, so an
optional animation can never fail a lesson.
"""

from __future__ import annotations

from ..llm import LLMBackend, LLMError, LLMRequest, structured_call
from ..prompts import load_prompt
from ..scenes import build_scene, concept_card
from ..schemas import Visual, inline_schema
from ..schemas.scene import AnimatorDraft, SceneSpec
from ..schemas.text import trim_sentence

MAX_FACTS = 6


def _facts(facts: list[str]) -> list[str]:
    return [trim_sentence(" ".join(str(f).split()), 100) for f in facts if str(f).strip()][:MAX_FACTS]


async def run_animator(
    backend: LLMBackend,
    visual: Visual,
    facts: list[str],
    level: str = "secondary",
    *,
    concise: bool = False,
    scene_id: str | None = None,
) -> SceneSpec:
    """Return a valid scene for ``visual``. Never raises (CancelledError aside)."""
    prompt = load_prompt("animator")
    shown = _facts(facts)
    user = (
        f"<context>\nconcept: {visual.concept}\nkind_hint: {visual.kind_hint}\nlevel: {level}\n"
        + ("facts:\n" + "\n".join(f"- {f}" for f in shown) + "\n" if shown else "")
        + "</context>\n<task>Choose the template and fill its parameters."
        + (" Keep it minimal." if concise else "")
        + "</task>"
    )
    req = LLMRequest(
        agent="animator",
        tier="router",
        system=prompt.text,
        user=user,
        schema=inline_schema(AnimatorDraft, require_all=False),
        temperature=0.2,
        think=False,
        num_predict=600 if concise else 1200,
        prompt_version=prompt.version,
    )
    try:
        draft = (await structured_call(backend, req, AnimatorDraft)).value
    except LLMError:
        return _fallback(visual, shown, scene_id)
    return build_scene(draft, visual.kind_hint, scene_id=scene_id, fallback_bullets=shown)


def _fallback(visual: Visual, facts: list[str], scene_id: str | None) -> SceneSpec:
    spec = concept_card(visual.concept, facts or [visual.concept])
    if scene_id:
        spec.id = scene_id[:60]
    return spec
