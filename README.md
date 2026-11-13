# Cache-Flow: Socrates

Socrates is a local, software-only learning agent. A student writes on the SwiftUI/PencilKit iPad board; a Nemotron model served by vLLM on a DGX Spark reads the work, calls a bounded math checker through a NemoClaw/OpenShell Socratic harness, and responds with a spoken hint and separate digital tutor ink. The first complete lesson is quadratic factoring, with an independent transfer question. The architecture can add other subjects through evidence and verifier adapters.

The [product requirements](prd.md) define scope. [01-plan.md](01-plan.md) is the build sequence, [02-architecture-and-flow.md](02-architecture-and-flow.md) explains a turn, [architecture.md](architecture.md) and [design.md](design.md) own system contracts, [Interface.md](Interface.md) proposes the iPad–Spark v2 wire protocol, and [research.md](research.md) records the NVIDIA and tutoring evidence. [Decisions](decisions.md) preserve the change from the robot plan; [status](status.md) says what is actually built.

## What exists today

The merged [iPad scaffold](ios/README.md) runs a local mock lesson and includes network/audio UI code and Swift package tests. The Spark tutor backend, local model serving, NemoClaw/OpenShell, NeMo Guardrails, SymPy checker, speech sidecars and v2 digital-ink flow remain to be built. The prior SO-101/Isaac/LeRobot path is retired; the older robot-era spec is available in Git history.

## Working together

Read [AGENTS.md](AGENTS.md) and use the [agent coordination board](https://github.com/Vrun1506/Cache-Flow/issues/1) for team handoffs. The [mailbox guide](docs/agent-board.md) covers automatic polling on each laptop.
