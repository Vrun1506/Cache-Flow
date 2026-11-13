"""Chemical formulas and reaction balance (AGENT-SYSTEM.md §16.3: only a tool produces ``verified``).

Pure text parsing and exact integer atom counting; nothing here evaluates text. Input is untrusted, so
every parser is bounded: formula ≤ 60 characters, reaction ≤ 200, nesting ≤ 3, counts ≤ 99, ≤ 8 species.
Anything outside the supported grammar (charges, ``⇌``, unknown elements) raises :class:`ChemError`
with ``unsupported=True`` and is reported as "could not check", never as wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import gcd
from typing import Optional

import sympy

MAX_FORMULA = 60
MAX_REACTION = 200
MAX_NESTING = 3
MAX_COUNT = 99
MAX_SPECIES = 8

ELEMENTS = frozenset(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb "
    "Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr "
    "Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split()
)
assert len(ELEMENTS) == 118

_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_ARROW = re.compile(r"<->|<=>|⇌|⇄|<-->|-->|->|=>|⟶|→|=")
_REVERSIBLE = {"<->", "<=>", "⇌", "⇄", "<-->"}
_STATE = re.compile(r"\((?:s|l|g|aq)\)$")
_ALLOWED = re.compile(r"^[A-Za-z0-9().·]*$")


class ChemError(ValueError):
    """Text this module will not interpret. ``unsupported`` means valid chemistry we do not handle."""

    def __init__(self, message: str, *, unsupported: bool = True) -> None:
        super().__init__(message)
        self.unsupported = unsupported


@dataclass(frozen=True)
class BalanceResult:
    status: str  # "balanced" | "unbalanced" | "unsupported"
    note: str = ""
    counts: dict[str, tuple[int, int]] = field(default_factory=dict)  # element -> (left, right)


# --------------------------------------------------------------------------- formulas


def _tidy(text: str) -> str:
    return text.translate(_SUBSCRIPTS).replace(" ", "").replace(" ", "")


def parse_species(text: str) -> dict[str, int]:
    """Atom counts of one species, e.g. ``Fe2(SO4)3`` -> ``{"Fe": 2, "S": 3, "O": 12}``."""
    if not isinstance(text, str):
        raise ChemError("not text")
    t = _tidy(text)
    t = _STATE.sub("", t)
    if not t:
        raise ChemError("empty formula")
    if len(t) > MAX_FORMULA:
        raise ChemError("formula too long")
    if not _ALLOWED.match(t):
        raise ChemError("charges and other symbols are not supported")
    total: dict[str, int] = {}
    for piece in re.split(r"[.·]", t):
        if not piece:
            raise ChemError("empty part in a hydrate")
        m = re.match(r"^(\d+)(.*)$", piece)  # CuSO4.5H2O: the 5 multiplies the water part
        mult, body = (int(m.group(1)), m.group(2)) if m and total else (1, piece)
        if mult < 1 or mult > MAX_COUNT:
            raise ChemError("coefficient out of range")
        for el, n in _parse_group(body).items():
            total[el] = total.get(el, 0) + n * mult
    return total


def _parse_group(s: str) -> dict[str, int]:
    pos = 0

    def number() -> int:
        nonlocal pos
        m = re.compile(r"\d+").match(s, pos)
        if not m:
            return 1
        pos = m.end()
        n = int(m.group())
        if n < 1 or n > MAX_COUNT:
            raise ChemError("subscript out of range")
        return n

    def parse(depth: int, closing: bool) -> dict[str, int]:
        nonlocal pos
        counts: dict[str, int] = {}
        while pos < len(s):
            ch = s[pos]
            if ch == "(":
                if depth >= MAX_NESTING:
                    raise ChemError("brackets nested too deeply")
                pos += 1
                inner = parse(depth + 1, True)
                n = number()
                for el, c in inner.items():
                    counts[el] = counts.get(el, 0) + c * n
            elif ch == ")":
                if not closing:
                    raise ChemError("unbalanced brackets")
                pos += 1
                return counts
            elif ch.isupper():
                m = re.compile(r"[A-Z][a-z]?").match(s, pos)
                sym = m.group()
                if sym not in ELEMENTS:  # "Co" vs "CO": try the one-letter symbol before failing
                    sym = ch
                    if sym not in ELEMENTS:
                        raise ChemError(f"unknown element {m.group()!r}")
                pos += len(sym)
                counts[sym] = counts.get(sym, 0) + number()
            else:
                raise ChemError("unexpected character in formula")
            if sum(counts.values()) > 10_000:
                raise ChemError("formula too large")
        if closing:
            raise ChemError("unbalanced brackets")
        return counts

    out = parse(0, False)
    if not out:
        raise ChemError("no elements found")
    return out


# -------------------------------------------------------------------------- reactions

Term = tuple[int, str]


def _split_term(term: str) -> Term:
    t = _tidy(term)
    if not t:
        raise ChemError("empty species (a charge or a stray +?)")
    m = re.match(r"^(\d+)(.+)$", t)
    if not m:
        return 1, t
    coeff = int(m.group(1))
    if coeff < 1 or coeff > MAX_COUNT:
        raise ChemError("coefficient out of range")
    return coeff, m.group(2)


def looks_like_reaction(text: str) -> bool:
    """True when the text has an arrow, so a failure to parse it means "could not check", not "not chemistry"."""
    return bool(re.search(r"<->|<=>|⇌|⇄|-->|->|=>|⟶|→", text or ""))


def parse_reaction(text: str) -> tuple[list[Term], list[Term]]:
    if not isinstance(text, str):
        raise ChemError("not text")
    if len(text) > MAX_REACTION:
        raise ChemError("reaction too long")
    arrows = _ARROW.findall(text)
    if len(arrows) != 1:
        raise ChemError("expected exactly one reaction arrow", unsupported=not arrows)
    if arrows[0] in _REVERSIBLE:
        raise ChemError("reversible reactions are not supported")
    left, right = _ARROW.split(text)
    sides = []
    for side in (left, right):
        terms = [_split_term(p) for p in side.split("+")]
        sides.append(terms)
    if len(sides[0]) + len(sides[1]) > MAX_SPECIES:
        raise ChemError("too many species")
    for _, sp in sides[0] + sides[1]:
        parse_species(sp)  # validate now so a bad species is reported with the reaction
    return sides[0], sides[1]


def _count(side: list[Term]) -> dict[str, int]:
    out: dict[str, int] = {}
    for coeff, sp in side:
        for el, n in parse_species(sp).items():
            out[el] = out.get(el, 0) + n * coeff
    return out


def is_balanced(text: str) -> BalanceResult:
    try:
        left, right = parse_reaction(text)
        lc, rc = _count(left), _count(right)
    except ChemError as exc:
        return BalanceResult("unsupported", str(exc)[:120])
    counts = {el: (lc.get(el, 0), rc.get(el, 0)) for el in sorted(set(lc) | set(rc))}
    bad = [(el, a, b) for el, (a, b) in counts.items() if a != b]
    if not bad:
        return BalanceResult("balanced", "atoms balance on both sides", counts)
    shown = "; ".join(f"{el} differs {a} vs {b}" for el, a, b in bad[:3])
    return BalanceResult("unbalanced", f"not balanced: {shown}"[:160], counts)


def balance(text: str) -> Optional[list[int]]:
    """The smallest positive whole-number coefficients, one per species in order, or None.

    Internal use only (a repair hint for the solver). It never goes into text the student sees.
    """
    try:
        left, right = parse_reaction(text)
        species = [sp for _, sp in left] + [sp for _, sp in right]
        counts = [parse_species(sp) for sp in species]
    except ChemError:
        return None
    elements = sorted({el for c in counts for el in c})
    sign = [1] * len(left) + [-1] * len(right)
    matrix = sympy.Matrix([[sign[j] * counts[j].get(el, 0) for j in range(len(species))] for el in elements])
    null = matrix.nullspace()
    if len(null) != 1:
        return None
    vec = null[0]
    denom = 1
    for v in vec:
        denom = denom * v.q // gcd(denom, v.q)
    ints = [int(v * denom) for v in vec]
    if all(i <= 0 for i in ints):
        ints = [-i for i in ints]
    if any(i <= 0 for i in ints):
        return None
    g = 0
    for i in ints:
        g = gcd(g, i)
    return [i // g for i in ints]


def check_reaction(text: str) -> Optional[BalanceResult]:
    """Check ``text`` if it is a reaction. None means "this is not a reaction" (leave it to other checks).

    A string with an arrow is always a reaction attempt (unparsable → "unsupported"). A bare ``=`` only counts
    as one when both sides parse as chemical species, so ``n = m/M`` is left alone.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    result = is_balanced(text)
    if looks_like_reaction(text):
        return result
    return result if result.status != "unsupported" else None
