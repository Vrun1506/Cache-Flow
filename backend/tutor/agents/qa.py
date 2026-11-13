"""Q&A agent: answer a typed follow-up from the session memory (final-plan.md §3, §16.9)."""

from __future__ import annotations

from ..llm import LLMBackend, LLMError, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas import inline_schema
from ..schemas.qa import AskDraft, AskReply
from ..state.memory import Memory

FALLBACK_SPOKEN = (
    "I'm not sure how to answer that one right now. Could you ask it a different way, "
    "or tell me which step you'd like me to look at?"
)


def fallback_reply(memory: Memory) -> AskReply:
    return AskReply(answer_spoken=FALLBACK_SPOKEN, resolves_to_step=memory.current_step or None)


async def run_qa(
    backend: LLMBackend,
    memory: Memory,
    question: str,
    *,
    selected_step: int | None = None,
    new_topic: bool = False,
    concise: bool = False,
) -> AskReply:
    """Never raises except on cancellation: a failed call becomes a polite templated reply."""
    prompt = load_prompt("qa")
    user = (
        f"<question>\n{question}\n</question>\n"
        f"<context>\nlevel: {memory.level}\n{memory.qa_slice(selected_step)}\n</context>\n"
        "<task>Answer the student's question.</task>"
    )
    req = LLMRequest(
        agent="qa",
        tier="reasoner",
        system=prompt.text,
        user=user,
        schema=inline_schema(AskDraft),
        temperature=0.3,
        think=False,
        num_predict=500 if concise else 900,
        prompt_version=prompt.version,
    )
    try:
        draft = (await structured_call(backend, req, AskDraft)).value
    except LLMError:
        return fallback_reply(memory)
    return AskReply(**draft.model_dump(), suggest_new_page=new_topic)
