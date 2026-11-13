# Cache-Flow: Socrates

Socrates is a local, software-only, general-purpose tutoring agent. A student writes on the SwiftUI/PencilKit iPad board; a Nemotron model served by vLLM on a DGX Spark reads the work; an attributed Jane Street-derived Socratic skill in NemoClaw/OpenShell asks one question at a time and calls a bounded math checker when appropriate. The student can also ask by local push-to-talk speech. The tutor responds with matching Magpie speech and captions, separate digital ink and a diagram or plot. The student can explicitly request the exact answer, followed by an independent check. The first verified lesson is quadratic factoring; other subjects share the teaching loop with honest evidence labels.

The [product requirements](prd.md) define scope. [Final plan](final-plan.md) is the detailed build handoff; [01-plan.md](01-plan.md) is its short gate index and [02-architecture-and-flow.md](02-architecture-and-flow.md) explains a turn. [Architecture](architecture.md) and [design](design.md) own component contracts, [Interface.md](Interface.md) proposes the iPad–Spark v2 wire protocol, and [research.md](research.md) records the NVIDIA and tutoring evidence. [Decisions](decisions.md) preserve tradeoffs; [status](status.md) says what is actually built.

## What exists today

The merged [iPad scaffold](ios/README.md) runs a local mock lesson and includes network/audio UI code and Swift package tests. The Spark tutor backend, local model serving, NemoClaw/OpenShell skill, NeMo Guardrails, safe SymPy checker, speech sidecars and v2 digital-ink/scene flow remain to be built. The prior SO-101/Isaac/LeRobot path is retired; the older robot-era spec is available in Git history.

## Working together

Read [AGENTS.md](AGENTS.md) and use the [agent coordination board](https://github.com/Vrun1506/Cache-Flow/issues/1) for team handoffs. The [mailbox guide](docs/agent-board.md) covers automatic polling on each laptop.
