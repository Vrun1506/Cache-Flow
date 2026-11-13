"""Verifier: tools decide truth where they can, and the report says so honestly.

Rules (final-plan.md §16.3):
- Only a tool can produce ``verified``. The critic LLM never does.
- A tool crash or timeout is ``unverified``, not ``failed``.
- Steps with no checkable expression get method ``none`` and are left out of the
  overall result, so pure explanation steps do not show an "unverified" badge.
- Physics steps are checked with pint (dimensions) plus a numeric recompute within 1%
  (tutor/tools/units.py). When that check cannot decide, the algebra check still runs but
  the step stays ``unverified``. Chemistry is capped at ``unverified`` for algebra, with one
  exception: a step (or final answer) that is a chemical reaction is checked by exact atom
  counting (tutor/tools/chem.py): ``verified`` if balanced, ``failed`` if not, ``unverified``
  if it cannot be parsed or the check itself errors.
- Code steps run in the sandbox (tutor/tools/sandbox.py): ``verified`` only when every test
  passes, ``failed`` when a test fails, the code raises or it does not compile, and
  ``unverified`` when there are no tests or the sandbox timed out, ran out of memory or crashed.
"""

from __future__ import annotations

import asyncio
import re
from typing import Callable

from ..llm import LLMBackend, LLMError, LLMRequest, structured_call
from ..prompts import load_prompt
from ..schemas import (
    CriticOutput,
    Plan,
    Solution,
    StepVerdict,
    VerifyReport,
    inline_schema,
)
from ..tools.chem import BalanceResult, check_reaction
from ..tools.sandbox import SandboxResult, run_python
from ..tools.units import verify_physics_final, verify_physics_step
from ..tools.worker import MathWorker, ToolError, ToolTimeout

UNITS_PENDING = "units not checked yet"
SANDBOX_PARALLEL = 3  # each sandbox run is a child process with its own memory budget
_FUNCTION_NAMES = re.compile(r"\b(sin|cos|tan|asin|acos|atan|exp|log|ln|sqrt|abs|floor|ceil|ceiling|min|max|pi|E)\b")
_UNITS_DOMAINS = {"physics", "chemistry"}


def has_symbol(expr: str) -> bool:
    """True if the expression still contains a variable (not just numbers and functions)."""
    return bool(re.search(r"[A-Za-z]", _FUNCTION_NAMES.sub("", expr)))


def _overall(verdicts: list[StepVerdict]) -> str:
    counted = [v for v in verdicts if v.method != "none"]
    if not counted:
        return "unverified"
    if all(v.status == "verified" for v in counted):
        return "verified"
    if any(v.status == "verified" for v in counted):
        return "partial"
    return "unverified"


def _repair_hint(verdicts: list[StepVerdict]) -> str | None:
    lines = [f"{v.id}: {v.note or 'failed the check'}" for v in verdicts if v.status == "failed"]
    return "; ".join(lines)[:400] if lines else None


class Verifier:
    def __init__(
        self,
        backend: LLMBackend,
        worker: MathWorker,
        *,
        sandbox: Callable[..., SandboxResult] = run_python,
    ):
        self._backend = backend
        self._worker = worker
        self._sandbox = sandbox
        self._sandbox_slots = asyncio.Semaphore(SANDBOX_PARALLEL)

    async def verify(self, plan: Plan, solution: Solution) -> VerifyReport:
        checkable = any(s.expr for s in solution.steps)
        chem = self._chem_checks(plan, solution)
        if plan.solution_kind == "code" or any(s.kind == "code" for s in solution.steps):
            steps, final = await self._code_checks(solution), None
        elif plan.solution_kind in ("explain", "compare") or not checkable:
            steps, final = await self._critic(plan, solution), None
        else:
            steps, final = await self._math_chain(plan, solution, chem)
        if chem:
            steps = [chem.get(v.id, v) for v in steps]
            final = chem.get("final", final)

        everything = steps + ([final] if final else [])
        return VerifyReport(
            steps=steps,
            final_answer=final,
            overall=_overall(everything),
            repair_hint=_repair_hint(everything),
        )

    # -- chemistry (reaction balance) ---------------------------------------

    @staticmethod
    def _chem_verdict(id: str, text: str) -> StepVerdict | None:
        """A verdict for ``text`` if it is a reaction, else None. Any error is "could not check", never failed."""
        try:
            result: BalanceResult | None = check_reaction(text)
        except Exception as exc:
            return StepVerdict(id=id, status="unverified", method="sympy", note=f"checker error: {exc}"[:160])
        if result is None:
            return None
        if result.status == "balanced":
            return StepVerdict(id=id, status="verified", method="sympy", note=result.note)
        if result.status == "unbalanced":
            return StepVerdict(id=id, status="failed", method="sympy", note=result.note)
        return StepVerdict(id=id, status="unverified", method="sympy", note=f"not checked: {result.note}"[:160])

    def _chem_checks(self, plan: Plan, solution: Solution) -> dict[str, StepVerdict]:
        """Reaction steps (and a reaction final answer) of a chemistry solution, keyed by step id / "final"."""
        if plan.domain != "chemistry":
            return {}
        out: dict[str, StepVerdict] = {}
        for step in solution.steps:
            if step.expr and (v := self._chem_verdict(step.id, step.expr)) is not None:
                out[step.id] = v
        value = solution.final_answer.value
        if value and (v := self._chem_verdict("final", value)) is not None:
            out["final"] = v
        return out

    # -- maths -------------------------------------------------------------

    async def _relate(self, prev: str, cur: str) -> tuple[str, str]:
        try:
            result = await self._worker.acall("relate", prev=prev, cur=cur)
            return result["status"], result.get("note", "")
        except ToolTimeout:
            return "unverified", "checker timed out"
        except ToolError as exc:
            return "unverified", f"checker error: {exc}"[:160]

    async def _math_chain(
        self, plan: Plan, solution: Solution, chem: dict[str, StepVerdict] | None = None
    ) -> tuple[list[StepVerdict], StepVerdict | None]:
        chem = chem or {}
        capped = plan.domain in _UNITS_DOMAINS
        physics = plan.domain == "physics"
        facts = [f for s in solution.steps for f in s.facts]
        prev = plan.statement_expr
        verdicts: list[StepVerdict] = []
        anchor = plan.statement_expr
        units_note = UNITS_PENDING

        for step in solution.steps:
            if not step.expr:
                verdicts.append(StepVerdict(id=step.id, status="unverified", method="none", note=""))
                continue
            if step.id in chem:  # a reaction: decided by atom counting, not by the algebra chain
                verdicts.append(chem[step.id])
                continue
            if physics:
                u = await asyncio.to_thread(verify_physics_step, step.expr, facts)
                if u.status in ("verified", "failed"):
                    verdicts.append(StepVerdict(id=step.id, status=u.status, method="units", note=u.note))
                    if anchor is None and "=" in step.expr:
                        anchor = step.expr
                    if has_symbol(step.expr):
                        prev = step.expr
                    continue
                units_note = f"{UNITS_PENDING}: {u.note}"
            if prev is None:
                lonely = "nothing earlier to compare with" + (f"; {units_note}" if physics else "")
                verdicts.append(StepVerdict(id=step.id, status="unverified", method="sympy", note=lonely))
            else:
                status, note = await self._relate(prev, step.expr)
                if capped and status == "verified":
                    status, note = "unverified", f"algebra ok; {units_note if physics else UNITS_PENDING}"
                verdicts.append(StepVerdict(id=step.id, status=status, method="sympy", note=note))
            if anchor is None and "=" in step.expr:
                anchor = step.expr
            # An arithmetic side-check (no variables) must not break the chain.
            if has_symbol(step.expr):
                prev = step.expr

        final = None
        value = solution.final_answer.value
        if value and "final" not in chem:
            if physics and any(v.status == "failed" for v in verdicts):
                # A final answer that only matches a step that failed its check proves nothing.
                return verdicts, StepVerdict(id="final", status="unverified", method="units",
                                             note="not checked: a step failed its check")
            if physics:
                fu = await asyncio.to_thread(verify_physics_final, value, [s.expr for s in solution.steps if s.expr], facts)
                if fu.status in ("verified", "failed"):
                    return verdicts, StepVerdict(id="final", status=fu.status, method="units", note=fu.note)
            final = await self._check_final(anchor, value, capped)
        return verdicts, final

    async def _check_final(self, anchor: str | None, value: str, capped: bool) -> StepVerdict:
        if not anchor:
            return StepVerdict(id="final", status="unverified", method="none", note="no equation to check against")
        try:
            result = await self._worker.acall("check_final", equation=anchor, answer=value)
            status, note = result["status"], result.get("note", "")
        except ToolTimeout:
            status, note = "unverified", "checker timed out"
        except ToolError as exc:
            status, note = "unverified", f"checker error: {exc}"[:160]
        if capped and status == "verified":
            status, note = "unverified", f"answer satisfies the equation; {UNITS_PENDING}"
        return StepVerdict(id="final", status=status, method="sympy", note=note)

    # -- code (sandbox) ------------------------------------------------------

    async def _run_sandbox(self, step) -> StepVerdict:
        if not step.tests:
            return StepVerdict(id=step.id, status="unverified", method="python", note="no tests to run")
        try:
            async with self._sandbox_slots:
                result = await asyncio.to_thread(self._sandbox, step.code, step.tests)
        except Exception as exc:  # a broken tool is "could not check", never "wrong"
            return StepVerdict(id=step.id, status="unverified", method="python", note=f"sandbox error: {exc}"[:160])
        if result.outcome == "passed":
            return StepVerdict(id=step.id, status="verified", method="python", note=f"{len(result.results)} tests passed")
        if result.outcome == "failed":
            return StepVerdict(id=step.id, status="failed", method="python", note=result.summary())
        reason = {"timeout": "timed out", "memory": "ran out of memory"}.get(result.outcome, "could not be run")
        return StepVerdict(id=step.id, status="unverified", method="python", note=f"not checked: {reason}"[:160])

    async def _code_checks(self, solution: Solution) -> list[StepVerdict]:
        async def one(step) -> StepVerdict:
            if not step.code:
                return StepVerdict(id=step.id, status="unverified", method="none", note="")
            return await self._run_sandbox(step)

        return list(await asyncio.gather(*(one(s) for s in solution.steps)))

    # -- critic (non-tool domains) ----------------------------------------

    async def _critic(self, plan: Plan, solution: Solution) -> list[StepVerdict]:
        prompt = load_prompt("verifier_critic")
        body = "\n".join(f"{s.id}: {s.statement} | {s.rationale}" for s in solution.steps)
        user = (
            f"<question>\n{plan.question_clean}\n</question>\n<solution>\n{body}\n</solution>\n"
            "<task>Judge each step.</task>"
        )
        req = LLMRequest(
            agent="verifier_critic",
            tier="reasoner",
            system=prompt.text,
            user=user,
            schema=inline_schema(CriticOutput),
            temperature=0.0,
            think=False,
            num_predict=700,
            prompt_version=prompt.version,
        )
        try:
            critic = (await structured_call(self._backend, req, CriticOutput)).value
        except LLMError:
            return [
                StepVerdict(id=s.id, status="unverified", method="critic", note="reviewer unavailable")
                for s in solution.steps
            ]
        by_id = {c.id: c for c in critic.steps}
        out = []
        for s in solution.steps:
            c = by_id.get(s.id)
            if c is None:
                out.append(StepVerdict(id=s.id, status="unverified", method="critic", note="not reviewed"))
            elif c.verdict == "wrong":
                out.append(StepVerdict(id=s.id, status="failed", method="critic", note=c.note))
            else:  # a critic can never produce "verified"
                out.append(StepVerdict(id=s.id, status="unverified", method="critic", note=c.note))
        return out
