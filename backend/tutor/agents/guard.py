"""Guard: hint-leak check and basic safety filter (final-plan.md §16.10, §15.4, §15.6).

Everything here is deterministic. An optional LLM paraphrase pass is a later
(P2) addition; ``guard_hint`` already takes a ``backend`` so it can be added
without changing callers.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Iterable, Literal

from ..schemas.quiz import Hint
from ..tools.exprparse import split_alternatives

REFUSAL = "I can't help with that one. Ask me something about a topic you're studying and I'll gladly explain it."
REDIRECT = "That looks like it's outside what I can teach. Try asking about a subject you're studying."

Safety = Literal["ok", "redirect", "refuse"]

# -- leak check ----------------------------------------------------------------

_MATHY = re.compile(r"[0-9=+\-*/^()]")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_PLAIN_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")
# A short number only counts as a leak when the hint states it as an answer:
# "x = -2", "the answer is 5". "What is 2 + 3?" and "add to 5" stay legal.
_STRONG = r"(?:=|\bequals?\b|\banswers?\b|\bgives?\b|\bresults?\b|\bvalue\b)"
_WEAK = r"(?:\bis\b|\bare\b|\bget\b)"  # "there are 5 questions" is not an answer, so a following plural noun cancels it
_MIN_CANDIDATE = 3


def _light(text: str) -> str:
    """Lowercase and unify symbols, keeping spaces (needed to read numbers and words)."""
    t = (text or "").lower()
    for src, dst in (("−", "-"), ("–", "-"), ("—", "-"), ("×", "*"), ("·", "*"), ("÷", "/"), ("**", "^")):
        t = t.replace(src, dst)
    return t.replace("²", "^2").replace("³", "^3")


def normalise_for_leak(text: str) -> str:
    """Lowercase, unify symbols and drop spaces and ``*`` so ``2*x`` and ``2 x`` compare equal."""
    return re.sub(r"[\s*]+", "", _light(text))


def _words(text: str) -> str:
    """Word form for text candidates: lowercase, punctuation to spaces."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _candidates(final_value: str | None, step_exprs: Iterable[str]) -> list[str]:
    raw: list[str] = []
    for text in [final_value or "", *step_exprs]:
        text = (text or "").strip()
        if not text:
            continue
        raw.append(text)
        try:
            parts = split_alternatives(text)
        except Exception:
            parts = []
        if len(parts) > 1:
            raw.extend(parts)
    seen: set[str] = set()
    out: list[str] = []
    for c in raw:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _numbers_in(texts: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for t in texts:
        found.update(m.lstrip("-") for m in _NUMBER.findall(_light(t)))
    return found


def _number_leaks(hint_l: str, token: str) -> bool:
    digits = token.lstrip("-")
    if len(digits) >= 2 or "." in digits:
        return re.search(rf"(?<![\d.]){re.escape(digits)}(?!\d|\.\d)", hint_l) is not None
    # A single digit: only when stated as the answer, with the same sign, and not part of a sum.
    sign = "-" if token.startswith("-") else ""
    num = rf"\s*(?:of\s+)?{re.escape(sign)}{re.escape(digits)}(?!\d|\.\d|\s*[+\-*/^])"
    if re.search(_STRONG + num, hint_l):
        return True
    return re.search(_WEAK + num + r"(?!\s+[a-z]{3,}s\b)", hint_l) is not None


def find_leaks(
    hint_text: str,
    final_value: str | None,
    step_exprs: Iterable[str] = (),
    *,
    allowed: Iterable[str] = (),
) -> list[str]:
    """Return the parts of the answer that ``hint_text`` gives away.

    ``allowed`` holds text the student already has (the question, the original
    equation). Candidates and numbers found there are never counted as leaks.
    """
    allowed = [a for a in allowed if a]
    allowed_norm = {normalise_for_leak(a) for a in allowed}
    allowed_numbers = _numbers_in(allowed)
    hint_n = normalise_for_leak(hint_text)
    hint_w = _words(hint_text)
    hint_l = " ".join(_light(hint_text).split())
    leaked: list[str] = []

    for cand in _candidates(final_value, step_exprs):
        nc = normalise_for_leak(cand)
        if len(nc) < _MIN_CANDIDATE or nc in allowed_norm:
            continue
        if _PLAIN_NUMBER.fullmatch(nc):
            continue  # a bare number is handled by the number rules below
        if _MATHY.search(nc):
            tail = r"(?!\d|\.\d)" if nc[-1].isdigit() else ""
            if re.search(re.escape(nc) + tail, hint_n):
                leaked.append(cand)
        else:
            wc = _words(cand)
            if wc and re.search(rf"\b{re.escape(wc)}\b", hint_w):
                leaked.append(cand)

    for token in sorted(_numbers_in([final_value or ""])):
        if token in allowed_numbers:
            continue
        # The sign matters for single digits, so look the original token up again.
        signed = [m for m in _NUMBER.findall(_light(final_value or "")) if m.lstrip("-") == token]
        if any(_number_leaks(hint_l, t) for t in signed):
            leaked.append(token)
    return leaked


def leaks(hint_text: str, final_value: str | None, step_exprs: Iterable[str] = (), *, allowed: Iterable[str] = ()) -> bool:
    return bool(find_leaks(hint_text, final_value, step_exprs, allowed=allowed))


_TEMPLATED = {
    1: "Look back at the problem. What is the very first thing you could try, and why?",
    2: "Think about which rule or formula fits this kind of problem, then apply it one step at a time.",
}


def templated_hint(level: int) -> Hint:
    level = min(max(level, 1), 3)
    return Hint(level=level, say=_TEMPLATED.get(level, _TEMPLATED[2]))


async def guard_hint(
    backend: Any,
    hint: Hint,
    level: int,
    final_value: str | None,
    step_exprs: Iterable[str],
    regenerate: Callable[[list[str]], Awaitable[Hint]],
    *,
    allowed: Iterable[str] = (),
) -> Hint:
    """Levels 1 and 2 must not give the answer away. Regenerate once, then use a template.

    Level 3 is the worked explanation and is exempt. ``backend`` is reserved for
    the optional LLM paraphrase pass and is unused today.
    """
    if level >= 3:
        return hint
    step_exprs = list(step_exprs)
    allowed = list(allowed)
    found = find_leaks(hint.say, final_value, step_exprs, allowed=allowed)
    if not found:
        return hint
    try:
        retry = await regenerate(found)
    except Exception:  # regenerate is best-effort; CancelledError is not an Exception
        retry = None
    if retry is not None and not leaks(retry.say, final_value, step_exprs, allowed=allowed):
        return retry
    return templated_hint(level)


# -- safety --------------------------------------------------------------------

_HOW = r"(?:how (?:do|can|could|would|should) (?:i|you|we|one)|how to|ways? to|best way to|steps? to|instructions? (?:for|to|on)|help me|teach me|show me how to|tell me how to|guide to)"
_MAKE = r"(?:make|making|build|building|construct|assemble|synthesi[sz]e|cook|produce|3d[- ]?print|manufacture)"
_WEAPON = (
    r"(?:bomb|bombs|explosives?|grenades?|nerve agents?|sarin|napalm|molotovs?|molotov cocktails?|"
    r"ghost guns?|untraceable guns?|ricin|methamphetamine|meth|bioweapons?|chemical weapons?)"
)

_SELF_HARM = re.compile(
    rf"\b(?:{_HOW}|methods? (?:of|to|for)|painless(?:ly)? (?:way|ways|method|methods)(?: to)?)\b.{{0,30}}\b"
    r"(?:kill myself|commit suicide|end my life|hurt myself|harm myself|cut myself|take my own life|die)\b"
)
_WEAPONS = re.compile(rf"\b{_HOW}\b.{{0,25}}\b{_MAKE}\b.{{0,30}}\b{_WEAPON}\b")
_MALWARE = re.compile(
    r"\b(?:write|create|build|code|make|develop|generate|program)\b.{0,30}\b"
    r"(?:ransomware|keyloggers?|computer virus|malware|botnets?|ddos (?:script|tool|attack)|trojans?|rootkits?|spyware|"
    r"credential stealers?|phishing (?:page|site|kit))\b"
)
_MALWARE_BENIGN = re.compile(
    r"\b(?:detect|detects|detecting|remove|removal|scan|scanner|prevent|protect|defend|defense|against|analy[sz]e|"
    r"essay|report|summary|about|history|paragraph|poem|quiz)\b"
)
_MINORS_SEX = re.compile(
    r"\b(?:write|describe|generate|create|show|give me|draw|roleplay)\b.{0,30}\b"
    r"(?:sexual|erotic|porn\w*|nude|naked|sex scene)\b.{0,40}\b"
    r"(?:child|children|minor|minors|underage|kid|kids|teen\w*|boy|girl|\d{1,2}[- ]year[- ]old)\b"
)
_ADULT_SEX = re.compile(r"\b(?:write|describe|generate|create|give me)\b.{0,20}\b(?:erotic|porn\w*|sex scene|explicit sex\w*)\b")
_CHEAT_FOR_ME = re.compile(
    r"\b(?:do|write|complete|finish|take|solve)\b.{0,15}\b(?:my|our)\b.{0,15}\b"
    r"(?:homework|assignment|essay|exam|test|quiz|thesis|dissertation|coursework|paper)\b.{0,25}\bfor me\b"
)
_CHEAT_DIRECT = re.compile(
    r"\b(?:can|could|will|would) you (?:just )?(?:do|write|complete|finish|take) my\b.{0,15}\b"
    r"(?:homework|assignment|essay|exam|test|quiz|thesis|dissertation|coursework|paper)\b"
)
_CHEAT_DETECT = re.compile(
    r"\b(?:bypass|beat|avoid|evade|trick|fool|get past|pass)\b.{0,15}\b(?:turnitin|gptzero|ai detect\w*|plagiari[sz]m (?:check|detect)\w*)\b"
    r"|\bundetectable (?:by|to)\b.{0,15}\b(?:turnitin|gptzero|ai detect\w*)\b"
)
_BENIGN_PHRASES = re.compile(r"\b(?:bath bombs?|photo ?bombs?|bomb calorimeters?|cherry bombs? (?:cocktail|recipe))\b")


def check_safety(text: str) -> Safety:
    """Deterministic topic filter. ``refuse`` for harm, ``redirect`` for off-purpose requests."""
    t = " ".join((text or "").lower().replace("’", "'").split())
    t = _BENIGN_PHRASES.sub(" ", t)
    if _SELF_HARM.search(t) or _WEAPONS.search(t) or _MINORS_SEX.search(t):
        return "refuse"
    m = _MALWARE.search(t)
    if m and not _MALWARE_BENIGN.search(m.group(0)):
        return "refuse"
    if _ADULT_SEX.search(t) or _CHEAT_FOR_ME.search(t) or _CHEAT_DIRECT.search(t) or _CHEAT_DETECT.search(t):
        return "redirect"
    return "ok"


_PERSONAL = re.compile(r"\b(?:should i|can i|do i|am i|may i|my|i have|i am|i'm|i've|for me)\b")
_ADVICE_TOPICS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "medical",
        re.compile(
            r"\b(?:diagnos\w*|symptoms?|dosage|dose|medications?|prescri\w+|treatments?|medical|pregnan\w*|chest pain|"
            r"rash|infection|blood pressure|antibiotics?)\b"
        ),
        "I'm a study helper, not a doctor. Treat this as general information and check with a medical professional about your own situation.",
    ),
    (
        "legal",
        re.compile(
            r"\b(?:lawsuit|sue|legal advice|contracts?|custody|divorce|arrest\w*|visa|immigration|tenant|eviction|court|"
            r"lawyer|attorney)\b"
        ),
        "I'm a study helper, not a lawyer. Treat this as general information and ask a legal professional about your own case.",
    ),
    (
        "financial",
        re.compile(
            r"\b(?:invest\w*|stocks?|crypto\w*|bitcoin|loans?|mortgage|retirement|taxes|tax return|portfolio|savings account)\b"
        ),
        "I'm a study helper, not a financial adviser. Treat this as general information and speak to a qualified adviser before deciding.",
    ),
)


def professional_advice_framing(text: str) -> str | None:
    """A short disclaimer for personal medical, legal or financial questions, else ``None``."""
    t = " ".join((text or "").lower().replace("’", "'").split())
    if not _PERSONAL.search(t):
        return None
    for _name, pattern, message in _ADVICE_TOPICS:
        if pattern.search(t):
            return message
    return None
