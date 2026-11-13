# The two plans, and how they relate

There are two planning documents in this repository. They were written separately and **describe different runtimes for the same product**. They share the iPad contract ([Interface.md](Interface.md), v2), which is what the iPad and the backend actually implement.

| | [final-plan.md](final-plan.md) | [backend/AGENT-SYSTEM.md](backend/AGENT-SYSTEM.md) |
| --- | --- | --- |
| Title | "Socrates: local whiteboard tutor, build handoff" (13 Nov 2026, commit `8d85d02`) | The agent-system plan and build log (first committed as `final-plan.md` in `0268c5e`, now with the build log) |
| Runtime | **One local vLLM model** on the Spark (Nemotron 3 Nano Omni 30B A3B), an agent run through **NemoClaw/OpenShell** with a Socratic skill | **Our own agent runtime** with an orchestrator and specialist agents on **Ollama** (router, reasoner and vision models) plus a swap-aware scheduler. A vLLM/OpenAI-compatible client also exists |
| Safety | NeMo Guardrails, OpenShell policy | A rule-based guard and leak check. NeMo Guardrails is not integrated |
| Correctness | A narrow `check_math_step` tool with step kinds and reason codes | The same idea, built: `check_math_step`, units, a code sandbox, a chemistry balance check. A model can never mark a step verified |
| Speech | Magpie TTS, Parakeet ASR | The same, through configurable endpoints (clients unverified) |
| Status | Design only | **Built and tested with scripted models** (1,863 tests). Never run on the DGX Spark or with the real models |

## What is built

`backend/` implements the v2 contract from `Interface.md` using the agent system in `AGENT-SYSTEM.md`. It does **not** integrate NemoClaw, NeMo Guardrails or NeMo Agent Toolkit.

## Decisions still open (see [backend/CONTRACT-RESOLUTION.md](backend/CONTRACT-RESOLUTION.md) section 3)

1. Whether NemoClaw and NeMo Guardrails are required for judging. If yes, a bridge has to be built. If no, `final-plan.md` should be updated to describe what exists.
2. Whether `scene_preview` gets an optional timeline so the animations the backend already computes can reach the iPad.
3. Whether a `teach` intent should expose the full-lesson pipeline.

## Where to start

* Running the backend: [backend/README.md](backend/README.md)
* Contract decisions and the DGX Spark hand-over list: [backend/CONTRACT-RESOLUTION.md](backend/CONTRACT-RESOLUTION.md) (section 4 is the DGX list)
