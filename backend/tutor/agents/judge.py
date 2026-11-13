"""Judge: deterministic first, the reasoner only when needed (final-plan.md §15.6, §16.11).

The student's answer is untrusted. Deterministic types (mcq, numeric, short)
are decided by code. Only ``explain`` items and answers code cannot decide go
to the model, and the model sees nothing but the answer and the expected key
points, inside a delimited block.
"""

from __future__ import annotations

import asyncio
import difflib
import math
import re
from typing import Any

from ..llm import LLMBackend, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas import inline_schema
from ..schemas.quiz import QuizItem, Verdict
from ..tools.exprparse import UnsafeExpression, check_expr_safe, split_alternatives
from ..tools.worker import MathWorker, ToolError, ToolTimeout

MAX_ANSWER_CHARS = 600
CORRECT_SAY = "Correct!"
WRONG_SAY = "Not quite."
FALLBACK_SAY = "I couldn't check that one properly. Let's look at it together."


def _verdict(correct: bool, feedback: str = "", *, partial: bool = False, matched: list[str] | None = None) -> Verdict:
    return Verdict(
        correct=correct,
        partial=partial and not correct,
        matched_points=matched or [],
        missing_points=[],
        feedback=feedback or (CORRECT_SAY if correct else WRONG_SAY),
    )


# -- text helpers --------------------------------------------------------------


def _light(text: str) -> str:
    t = (text or "").lower().replace("−", "-").replace("×", "*").replace("’", "'").replace("**", "^")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


_ARTICLES = re.compile(r"^(?:a|an|the)\s+")
_NEGATION = re.compile(r"\b(?:not|no|never|isn'?t|aren'?t|doesn'?t|don'?t|wasn'?t|can'?t|cannot|without)\b")


def _norm_text(text: str) -> str:
    return _ARTICLES.sub("", _light(text))


def _alternatives(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[|;]", value) if p.strip()]


# -- numbers -------------------------------------------------------------------

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)"
_SCI = re.compile(rf"^(?P<m>{_NUM})\s*(?:\*|x)\s*10\s*\^\s*\(?(?P<e>[+-]?\d+)\)?")
_FRAC = re.compile(r"^(?P<a>[+-]?\d+\.?\d*)\s*/\s*(?P<b>\d+\.?\d*)(?![\d.])")
_PLAIN = re.compile(rf"^(?P<n>{_NUM}(?:e[+-]?\d+)?)")
_UNIT = re.compile(
    r"^\s*[a-zµ°ω%$]+(?:\^?-?\d)?(?:\s*[/*]\s*[a-zµ°ω]+(?:\^?-?\d)?)*\s*$"
)
_PREFIXES = (
    re.compile(r"^(?:the\s+)?(?:final\s+)?(?:answer|result|value|solution)\s*(?:is|=|:)?\s*"),
    re.compile(r"^(?:it\s+is|it's|i\s+get|i\s+got|that's|that\s+is|is|equals?)\s+"),
    re.compile(r"^(?:about|approximately|approx\.?|roughly|around|≈|~)\s*"),
    re.compile(r"^[a-z]\s*(?:=|is)\s*"),
)


def parse_number(text: str) -> float | None:
    """Read one number from an answer: ``1,000``, ``1/2``, ``3 x 10^8``, ``9.8 m/s^2``, ``x = 5``."""
    s = (text or "").lower().replace("−", "-").replace("×", "*").replace("**", "^").replace("’", "'")
    s = s.replace("²", "^2").replace("³", "^3")
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)
    s = re.sub(r"[$€£]", "", s)
    s = " ".join(s.split()).rstrip(".!")
    for _ in range(3):
        before = s
        for prefix in _PREFIXES:
            s = prefix.sub("", s, count=1)
        if s == before:
            break
    try:
        m = _SCI.match(s)
        if m:
            return float(m["m"]) * 10 ** int(m["e"]) if abs(int(m["e"])) < 300 and _unit_ok(s[m.end():]) else None
        m = _FRAC.match(s)
        if m:
            return float(m["a"]) / float(m["b"]) if float(m["b"]) != 0 and _unit_ok(s[m.end():]) else None
        m = _PLAIN.match(s)
        if m and _unit_ok(s[m.end():]):
            return float(m["n"])
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return None


def _unit_ok(rest: str) -> bool:
    return rest.strip() == "" or _UNIT.match(rest) is not None


def _decimals(text: str) -> int:
    m = re.search(r"\d*\.(\d+)", text or "")
    return len(m.group(1)) if m else 0


def _judge_numeric(expected: str, answer: str) -> Verdict | None:
    want, got = parse_number(expected), parse_number(answer)
    if want is None or got is None:
        return None
    if math.isclose(want, got, rel_tol=1e-6, abs_tol=1e-9):
        return _verdict(True)
    d = _decimals(answer)
    if _decimals(expected) > d and math.isclose(round(want, d), got, rel_tol=0, abs_tol=10 ** -(d + 3)):
        return _verdict(False, "That's the right value, rounded. Give it to more digits.", partial=True)
    return _verdict(False)


# -- multiple choice -----------------------------------------------------------

_LETTER = re.compile(r"^(?:option|choice|answer)?\s*([a-d])(?:\s+(.*))?$")
_DIGIT = re.compile(r"^(?:option|choice|answer|number)?\s*([1-4])$")


def _mcq_index(options: list[str], answer: str) -> int | None:
    a = _light(answer)
    if not a:
        return None
    norm_options = [_norm_text(o) for o in options]
    if _norm_text(a) in norm_options:
        return norm_options.index(_norm_text(a))
    m = _LETTER.match(a)
    if m:
        idx = ord(m.group(1)) - ord("a")
        rest = m.group(2)
        if idx < len(options) and (not rest or _norm_text(rest) == norm_options[idx]):
            return idx
    m = _DIGIT.match(a)
    if m and int(m.group(1)) - 1 < len(options):
        return int(m.group(1)) - 1
    return None


def _judge_mcq(item: QuizItem, answer: str) -> Verdict | None:
    options = item.options or []
    chosen = _mcq_index(options, answer)
    if chosen is None:
        return None
    return _verdict(chosen == item.expected.option_index)


# -- short text ----------------------------------------------------------------


def _judge_short(expected: str, answer: str) -> Verdict | None:
    accepted = [_norm_text(a) for a in _alternatives(expected)]
    accepted = [a for a in accepted if a]
    got = _norm_text(answer)
    if not accepted or not got:
        return None
    if got in accepted:
        return _verdict(True)
    if _NEGATION.search(_light(answer)):
        return None
    if len(got.split()) <= 8 and any(re.search(rf"\b{re.escape(a)}\b", got) for a in accepted):
        return _verdict(True)
    if len(got) >= 5 and any(
        len(a) >= 5 and difflib.SequenceMatcher(None, got, a).ratio() >= 0.88 for a in accepted
    ):
        return _verdict(True)
    return None


# -- maths expressions (equivalence through the worker) -------------------------


def _safe_math(text: str) -> bool:
    """True when every alternative parses under the whitelist. Nothing is evaluated here."""
    try:
        parts = split_alternatives(text)
        if not parts or len(parts) > 4:
            return False
        for part in parts:
            sides = part.split("=")
            if len(sides) > 2:
                return False
            for side in sides:
                check_expr_safe(side)
        return True
    except (UnsafeExpression, ValueError):
        return False


def _judge_math(expected: str, answer: str, worker: MathWorker | None) -> Verdict | None:
    if worker is None or not _safe_math(expected) or not _safe_math(answer):
        return None
    try:
        result: dict[str, Any] = worker.call("relate", prev=expected, cur=answer)
    except (ToolTimeout, ToolError):
        return None
    status = result.get("status")
    if status == "verified":
        return _verdict(True)
    if status == "failed":
        return _verdict(False)
    return None


# -- public API ----------------------------------------------------------------


def judge_answer(item: QuizItem, answer: str, *, worker: MathWorker | None = None) -> Verdict | None:
    """Decide with code. ``None`` means code cannot decide and the model should look."""
    if not (answer or "").strip():
        return _verdict(False, "I didn't catch an answer. Have a go, even a guess helps.")
    if item.type == "explain":
        return None
    if item.type == "mcq":
        return _judge_mcq(item, answer)
    expected = item.expected.value or ""
    if item.type == "numeric":
        verdict = _judge_numeric(expected, answer)
        if verdict is not None:
            return verdict
    else:
        verdict = _judge_short(expected, answer)
        if verdict is not None:
            return verdict
        verdict = _judge_numeric(expected, answer) if parse_number(expected) is not None else None
        if verdict is not None:
            return verdict
    return _judge_math(expected, answer, worker)


_TAGS = re.compile(r"</?\s*(?:answer|expected|task|context|question)\s*>", re.IGNORECASE)


def clean_answer(answer: str) -> str:
    """Cap the length and remove anything that could close the delimited block early."""
    return _TAGS.sub("", answer or "").strip()[:MAX_ANSWER_CHARS]


def _llm_user(item: QuizItem, answer: str) -> str:
    e = item.expected
    lines = [f"type: {item.type}", f"question: {item.prompt}"]
    if e.value:
        lines.append(f"expected answer: {e.value}")
    if e.key_points:
        lines.append("key points:")
        lines.extend(f"- {p}" for p in e.key_points)
    return (
        "<expected>\n" + "\n".join(lines) + "\n</expected>\n"
        f"<answer>\n{clean_answer(answer)}\n</answer>\n"
        "<task>Compare the answer with the expected points.</task>"
    )


def _sanitise(verdict: Verdict) -> Verdict:
    """A model verdict of "correct" needs evidence: matched points and nothing missing."""
    if verdict.correct and (not verdict.matched_points or verdict.missing_points):
        verdict = verdict.model_copy(update={"correct": False, "partial": bool(verdict.matched_points)})
    if verdict.correct:
        verdict = verdict.model_copy(update={"partial": False})
    if not verdict.feedback:
        verdict = verdict.model_copy(update={"feedback": CORRECT_SAY if verdict.correct else WRONG_SAY})
    return verdict


async def run_judge(
    backend: LLMBackend, item: QuizItem, answer: str, worker: MathWorker | None = None, *, concise: bool = False
) -> Verdict:
    """Judge an answer. Never raises (the fallback verdict is conservative: not correct)."""
    try:
        verdict = await asyncio.to_thread(judge_answer, item, answer, worker=worker)
    except Exception:
        verdict = None
    if verdict is not None:
        return verdict

    prompt = load_prompt("judge")
    req = LLMRequest(
        agent="judge",
        tier="reasoner",
        system=prompt.text,
        user=_llm_user(item, answer),
        schema=inline_schema(Verdict),
        temperature=0.0,
        think=False,
        num_predict=200 if concise else 300,
        prompt_version=prompt.version,
    )
    try:
        return _sanitise((await structured_call(backend, req, Verdict)).value)
    except Exception:  # LLMError and anything unexpected; CancelledError is not an Exception
        return Verdict(correct=False, partial=False, feedback=FALLBACK_SAY)
