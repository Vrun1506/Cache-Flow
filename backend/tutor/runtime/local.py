"""LocalRuntime: an asyncio executor over the fixed graph templates.

This is the always-available runtime (final-plan.md §16.13). It is also the
reference the NAT runtime must match event for event.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, TypeVar

from ..agents.animator import run_animator
from ..agents.board_director import fallback_board, run_board_director
from ..agents.lesson_designer import batches, check_to_item, fallback_step, run_lesson_designer
from ..agents.orchestrator import fallback_plan, run_orchestrator
from ..agents.quiz_designer import generic_quiz, run_quiz_designer
from ..agents.solver import run_solver
from ..agents.verifier import Verifier
from ..board.cues import estimate_ms, resolve_cues
from ..board.strokes import attach_strokes
from ..llm import LLMBackend, LLMError
from ..schemas import Plan, Solution, VerifyReport
from ..schemas.board import BoardPlan
from ..schemas.lesson import LessonStep
from ..schemas.scene import SceneSpec
from ..speech.tts import Clip, Narrator
from ..state.memory import Memory
from ..tools.worker import MathWorker
from .events import RunContext
from .graphs import GRAPHS, RUN_DEADLINE_S, Node, teaching_nodes
from .steps import ClipUrl, build_step_event, index_board

T = TypeVar("T")

REFUSAL = "I can't help with that one. Ask me something about a topic you're studying and I'll gladly explain it."
REDIRECT = "That looks like it's outside what I can teach. Try asking about a subject you're studying."


class NodeFailed(Exception):
    def __init__(self, node_id: str, cause: BaseException):
        super().__init__(f"{node_id} failed: {cause}")
        self.node_id = node_id
        self.cause = cause


@dataclass
class RunResult:
    run_id: str
    status: Literal["ok", "refused", "redirected", "failed"]
    plan: Plan | None = None
    solution: Solution | None = None
    report: VerifyReport | None = None
    error: str | None = None
    lessons: list[LessonStep] = field(default_factory=list)
    scenes: list[SceneSpec] = field(default_factory=list)


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


class LocalRuntime:
    def __init__(
        self,
        backend: LLMBackend,
        worker: MathWorker,
        *,
        deadline_s: float = RUN_DEADLINE_S,
        timeout_scale: float = 1.0,
        narrator: Narrator | None = None,
        clip_url: ClipUrl | None = None,
        pacer: "Pacer | None" = None,
    ):
        # The default timeouts are sized for the Spark. A slower dev machine scales them up.
        self._scale = timeout_scale
        self._backend = backend
        self._verifier = Verifier(backend, worker)
        self._deadline = deadline_s * timeout_scale
        self._narrator = narrator or Narrator(None)  # no TTS: estimated durations, on-device speech
        self._clip_url = clip_url
        self._pacer = pacer

    async def run_solve_teach(
        self,
        question: str,
        ctx: RunContext | None = None,
        *,
        level_hint: str | None = None,
        memory: Memory | None = None,
    ) -> RunResult:
        """The whole Calculate: solve, verify, then stream the lesson (final-plan.md §4)."""
        return await self._run(question, ctx or RunContext(), level_hint, memory or Memory(), teach=True)

    async def run_solve_spine(
        self,
        question: str,
        ctx: RunContext | None = None,
        *,
        level_hint: str | None = None,
        plan: Plan | None = None,
    ) -> RunResult:
        """Orchestrate, solve and verify only: the hidden reference for the Socratic flow.

        A ``plan`` that is already known (a preset, or a problem the student confirmed) skips the orchestrator.
        """
        return await self._run(question, ctx or RunContext(), level_hint, Memory(), teach=False, plan=plan)

    async def _run(self, question: str, ctx: RunContext, level_hint: str | None, memory: Memory, *,
                   teach: bool, plan: Plan | None = None) -> RunResult:
        try:
            async with asyncio.timeout(self._deadline):
                result = await self._solve_teach(question, ctx, level_hint, plan)
                if teach and result.status == "ok":
                    await self._teach(ctx, memory, result)
                if teach:
                    await ctx.emit("run_done", status=result.status)
                return result
        except TimeoutError:
            await ctx.emit("run_error", message="This is taking too long. Please try again.")
            return RunResult(ctx.run_id, "failed", error="run deadline exceeded")

    async def _solve_teach(self, question: str, ctx: RunContext, level_hint: str | None,
                           known_plan: Plan | None = None) -> RunResult:
        nodes = {n.id: n for n in GRAPHS["solve_teach"]}
        shown = [n for n in nodes.values() if known_plan is None or n.id != "orchestrate"]
        await ctx.emit("plan", template="solve_teach", nodes=[n.public() for n in shown])

        plan = known_plan or await self._node(
            ctx,
            nodes["orchestrate"],
            lambda concise: run_orchestrator(
                self._backend, question, level_hint=level_hint, concise=concise
            ),
            fallback=lambda: fallback_plan(question),
        )
        await ctx.emit("problem", question=plan.question_clean, domain=plan.domain, level=plan.level,
                       statement=plan.statement_expr)
        if plan.safety != "ok":
            say = REFUSAL if plan.safety == "refuse" else REDIRECT
            await ctx.emit("tutor_say", caption=say)
            return RunResult(ctx.run_id, "refused" if plan.safety == "refuse" else "redirected", plan=plan)

        try:
            solution = await self._node(
                ctx, nodes["solve"], lambda concise: run_solver(self._backend, plan, concise=concise)
            )
            report = await self._node(ctx, nodes["verify"], lambda _c: self._verifier.verify(plan, solution), retry=False)
        except NodeFailed as exc:
            await ctx.emit("run_error", message="I couldn't work that one out. Please try again.", node=exc.node_id)
            return RunResult(ctx.run_id, "failed", plan=plan, error=str(exc))

        solution, report = await self._repair(ctx, plan, solution, report)
        return RunResult(ctx.run_id, "ok", plan=plan, solution=solution, report=report)

    # ------------------------------------------------------------- teaching

    async def _teach(self, ctx: RunContext, memory: Memory, result: RunResult) -> None:
        """Stream the lesson: step events as they are ready, scenes and the quiz in parallel."""
        plan, solution, report = result.plan, result.solution, result.report
        assert plan and solution and report
        status = {v.id: v.status for v in report.steps}
        status["final"] = report.final_answer.status if report.final_answer else "unverified"
        memory.set_solution(plan, solution, status)
        visuals = plan.visuals[:3]
        nodes = {n.id: n for n in teaching_nodes(len(solution.steps), len(visuals))}
        await ctx.emit("plan", template="solve_teach", update=True, nodes=[n.public() for n in nodes.values()])

        side = [
            asyncio.create_task(self._animate(ctx, nodes[f"animate[{k}]"], memory, v, result))
            for k, v in enumerate(visuals, start=1)
        ]
        side.append(asyncio.create_task(self._quiz(ctx, nodes["quiz"], memory)))
        try:
            await self._lesson_steps(ctx, nodes, memory, result)
            await asyncio.gather(*side)
        finally:
            for task in side:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*side, return_exceptions=True)

    async def _lesson_steps(self, ctx: RunContext, nodes: dict[str, Node], memory: Memory, result: RunResult) -> None:
        solution = result.solution
        steps = {s.id: s for s in solution.steps}
        total = len(solution.steps)
        for b, ids in enumerate(batches(solution), start=1):
            lessons = await self._node(
                ctx,
                nodes[f"lesson[{b}]"],
                lambda concise, ids=ids: run_lesson_designer(self._backend, memory, ids, concise=concise),
                fallback=lambda ids=ids: [fallback_step(steps[i], memory.verified.get(i, "unverified")) for i in ids],
            )
            for lesson in lessons:
                index = int(lesson.step_id[1:])
                if self._pacer:
                    await self._pacer.before(index)
                step = steps[lesson.step_id]
                clips, board = await self._node(
                    ctx,
                    nodes[f"step[{index}]"],
                    lambda concise, i=index, st=step, ls=lesson: self._prepare_step(memory, i, st, ls, concise),
                    fallback=lambda i=index, st=step, ls=lesson: self._fallback_step(i, st, ls),
                )
                result.lessons.append(lesson)
                memory.checks.append(check_to_item(lesson, step))
                memory.current_step = index
                index_board(memory, step.id, board.commands)
                if self._pacer:
                    self._pacer.expect(index, sum(c.duration_ms for c in clips))
                await ctx.emit(
                    "step",
                    **build_step_event(index, total, step, lesson, clips, board,
                                       memory.verified.get(step.id, "unverified"), self._clip_url),
                )

    async def _prepare_step(self, memory: Memory, index: int, step, lesson: LessonStep, concise: bool):
        """Board director and narrator run together; cues need both (final-plan.md §16.6)."""
        director = run_board_director(
            self._backend, index, step.title, step.board_text, lesson.intent, lesson.explain,
            [r.model_dump() for r in memory.board_recent()], concise=concise,
        )
        clips, plan = await asyncio.gather(self._narrator.narrate(lesson.intent, lesson.explain, prefix=f"s{index}"), director)
        return clips, self._finish_board(plan, clips)

    def _fallback_step(self, index: int, step, lesson: LessonStep):
        clips = [
            Clip(f"s{index}-intent", lesson.intent, lesson.intent, estimate_ms(lesson.intent), True),
            Clip(f"s{index}-explain", lesson.explain, lesson.explain, estimate_ms(lesson.explain), True),
        ]
        return clips, self._finish_board(fallback_board(index, step.board_text, step.title), clips)

    @staticmethod
    def _finish_board(plan: BoardPlan, clips: list[Clip]) -> BoardPlan:
        d = [c.duration_ms for c in clips] + [0, 0]
        cmds = resolve_cues(plan.commands, d[0], d[1], any(c.estimated for c in clips))
        try:
            cmds = attach_strokes(cmds)
        except Exception:  # strokes are an optimisation: the iPad can draw text itself
            pass
        return BoardPlan(commands=cmds)

    async def _animate(self, ctx: RunContext, node: Node, memory: Memory, visual, result: RunResult) -> None:
        facts = [f for s in memory.solution.steps for f in s.facts][:6] + list(memory.solution.key_concepts)[:2]

        async def work(concise: bool) -> SceneSpec:
            return await run_animator(self._backend, visual, facts, memory.level, concise=concise, scene_id=node.id)

        try:
            scene = await self._node(ctx, node, work)
        except NodeFailed:
            return  # optional: the lesson goes on without it
        result.scenes.append(scene)
        await ctx.emit("scene", id=scene.id, scene=scene.to_wire(), concept=visual.concept)

    async def _quiz(self, ctx: RunContext, node: Node, memory: Memory) -> None:
        plan, solution = memory.plan, memory.solution
        quiz = await self._node(
            ctx,
            node,
            lambda concise: run_quiz_designer(self._backend, plan, solution, memory.level, concise=concise),
            fallback=lambda: generic_quiz(plan, solution),
        )
        memory.quiz = list(quiz.items)

    async def _repair(
        self, ctx: RunContext, plan: Plan, solution: Solution, report: VerifyReport
    ) -> tuple[Solution, VerifyReport]:
        """One repair loop: re-solve only what failed, then verify again (§16.3)."""
        failed = report.failed_ids
        if not failed:
            return solution, report
        start = time.monotonic()
        await ctx.emit("agent", id="repair", state="running", detail=f"fixing {', '.join(failed)}")
        try:
            async with asyncio.timeout(GRAPH_TIMEOUT["solve"] * self._scale):
                fixed = await run_solver(
                    self._backend, plan, repair_hint=report.repair_hint, previous=solution, failed_ids=failed
                )
            new_report = await self._verifier.verify(plan, fixed)
        except (LLMError, TimeoutError) as exc:
            await ctx.emit("agent", id="repair", state="failed", ms=_ms(start), detail=str(exc)[:200])
            return solution, report  # keep the original; its failed steps stay labelled
        await ctx.emit("agent", id="repair", state="done", ms=_ms(start), detail=f"overall: {new_report.overall}")
        return fixed, new_report

    async def _node(
        self,
        ctx: RunContext,
        node: Node,
        work: Callable[[bool], Awaitable[T]],
        *,
        fallback: Callable[[], T] | None = None,
        retry: bool = True,
    ) -> T:
        """Run one node with its timeout, one concise retry, and an optional fallback."""
        start = time.monotonic()
        await ctx.emit("agent", id=node.id, state="running", tier=node.tier)
        last: BaseException | None = None
        for attempt, concise in enumerate((False, True) if retry else (False,)):
            try:
                async with asyncio.timeout(node.timeout_s * self._scale):
                    result = await work(concise)
                await ctx.emit("agent", id=node.id, state="done", ms=_ms(start))
                return result
            except (LLMError, TimeoutError) as exc:
                last = exc
                if retry and attempt == 0:
                    await ctx.emit("agent", id=node.id, state="retrying", detail=type(exc).__name__)
        if fallback is not None:
            await ctx.emit("agent", id=node.id, state="done", ms=_ms(start), detail="rule-based fallback")
            return fallback()
        await ctx.emit("agent", id=node.id, state="failed", ms=_ms(start), detail=str(last)[:200])
        raise NodeFailed(node.id, last or RuntimeError("unknown"))


GRAPH_TIMEOUT = {n.id: n.timeout_s for n in GRAPHS["solve_teach"]}


class Pacer:
    """Keeps generation at most ``ahead`` steps in front of playback (final-plan.md §16.6).

    The iPad's ``audio_done`` calls :meth:`ack`. If it never arrives, generation still moves on
    after the clip's duration plus a grace period, so a lost ack cannot stall the lesson.
    """

    def __init__(self, ahead: int = 2, grace_s: float = 5.0) -> None:
        self.ahead, self.grace_s = ahead, grace_s
        self._acked = 0
        self._cond = asyncio.Condition()
        self._deadlines: dict[int, float] = {}

    def expect(self, index: int, duration_ms: int) -> None:
        self._deadlines[index] = time.monotonic() + duration_ms / 1000 + self.grace_s

    async def ack(self, index: int) -> None:
        async with self._cond:
            self._acked = max(self._acked, index)
            self._cond.notify_all()

    async def before(self, index: int) -> None:
        need = index - self.ahead
        if need <= 0:
            return
        async with self._cond:
            while self._acked < need:
                due = self._deadlines.get(need)
                remaining = None if due is None else due - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining if remaining else 1.0)
                except asyncio.TimeoutError:
                    continue
