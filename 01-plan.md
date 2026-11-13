# Socrates software tutor: gate index

**13 November 2026.** [prd.md](prd.md) owns product scope, [final-plan.md](final-plan.md) is the full build handoff, and [Interface.md](Interface.md) owns the proposed v2 iPad protocol. This file is a short index, not a competing specification. The robot and simulator path is retired.

## First complete demonstration

A learner handwrites a question and a wrong algebra step. The versioned Jane Street-derived skill is active in a NemoClaw/OpenShell tutor agent using one Spark-local vLLM Nemotron model. The agent calls a bounded math MCP tool, asks one Socratic question, accepts one spoken push-to-talk follow-up through local ASR, speaks with matching captions, writes and draws on its own iPad layer, and acknowledges a valid partial root. An explicit Show the answer request is recorded as assisted; a fresh item tests independent understanding. A second subject demonstrates general-purpose tutoring without an unsupported correctness claim. The rehearsal works with the internet disconnected.

## Gates

| Order | Build proof | Required evidence |
| --- | --- | --- |
| 0 | Qualify the Spark runtime: vLLM Omni image/text/tool output, Magpie and Parakeet co-residency, NemoClaw API bridge and active skill. | Pinned revisions, memory/latency, a real skill turn, allowed math call and denied unrelated call. |
| 1 | Freeze v2 fixtures and implement a safe step-aware SymPy tool. | Wrong signs invalid, corrected factors valid, one root partial, parser attacks and domain changes bounded. |
| 2 | Implement session gateway, Guardrails and tool/evidence flow. | Real agent→MCP→evidence→tutor trace, failed-rail and stale-revision cases. |
| 3 | Migrate Swift v1 arm events to separate tutor ink, a bounded scene and interruptible local voice. | Swift tests/build and a real iPad turn with caption/audio match. |
| 4 | Add the second subject, explicit answer reveal, independent check and local summary. | Repeated offline runs, actual call trace and p50/p95 first caption/audio/ink. |

All gates are planned, not passed. [status.md](status.md) records observed work. Automatic pen-up coaching, richer retrieval and full-duplex voice follow after this loop is reliable. Ollama, NAT, model swapping, Isaac and LeRobot are outside this path.
