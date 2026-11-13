# Socrates delivery status

**13 November 2026 · Product planning.** See the [PRD](prd.md) for scope and release gates, [architecture](architecture.md) for ownership, and [implementation contracts](design.md) for integration details.

## Repository baseline

- The Next.js starter has been removed; the iPad app now lives in `ios/` (SwiftUI + PencilKit). `backend/` has a Python requirements file including FastAPI. `robotics/` has a placeholder README. The inspected baseline (`72ee253`) has no tutor lesson, iPad board, inference wiring, or robot implementation.
- The existing GitHub [agent coordination board](https://github.com/Vrun1506/Cache-Flow/issues/1), mailbox scripts and tests support development coordination. They are separate from the product.
- Varun's `01-plan.md` and `02-architecture-and-flow.md` arrived in `423ba59`. They propose SwiftUI/PencilKit, WebSockets, Ollama and a broader Teach-first demo. The other proposal uses Next.js, HTTP/SSE, vLLM and a factoring-first digital slice. The team still needs to choose the authoritative scope and implementation contract.
- Parakeet TDT 0.6B v3 and MagpieTTS Multilingual 357M plus NanoCodec are selected as initial speech models. Their local deployment and latency on this Spark remain unverified.
- The stack has been reviewed against upstream documentation. Spark co-residency, iPad input, recognition, speech, robot placement and contact have not been measured in this repository.

## Build and handoffs

The roles below describe work areas. Individual owners and dates are open for the team to assign.

| Work area | State | Next deliverable | Evidence that unblocks the next area |
| --- | --- | --- | --- |
| Shared contracts | Proposed | Agree on the v1 payloads and synthetic seed → mistake → correction → transfer fixture. | Board, backend and simulated robot replay the same fixture. |
| Backend / maths | Planned | Implement typed confirmation, bounded factor/root checking, fixed questions and session evidence. | Labelled correctness suite and idempotent local API replay. |
| Frontend / iPad | Planned | Build the typed board and session summary, then add Pencil capture and Mistake Glow. | Supervised replay on the actual iPad, including an ambiguous sign and a reconnect. |
| Inference / voice | Planned | Qualify the Spark vision recipe, Nemotron tutor and NeMo-Speech.cpp candidates one at a time. Benchmark shared load before enabling them together. | Record runtime/model revisions, labelled recognition quality, p95 latency, policy failures and fallback behavior. |
| Robotics | Planned | Verify stop circuit and fixture, then dry-run and calibrate corners. Proceed through line, circle, square, digits, and equation. | Publish calibrated paper trace and stop measurements. Treat Apple Pencil contact as a separate gate. |
| QA / rehearsal | Planned | Run the golden lesson, a degraded run, and each enabled physical mode against PRD gates. | Rehearsal log shows which mode is safe and ready to demonstrate. |

## Open questions

- Which iPad, Apple Pencil model, SO-101 fixture, and operator stop hardware will the team use? Who owns calibration and sign-off?
- Can the two model candidates and speech sidecar meet the latency and memory budget together on the available Spark? Which exact versions and weights pass the smoke test?
- Which consent, deletion, and caregiver pairing rules are required before a pilot with the intended age group?
- Which teammate owns each work area, when is the demo, and which proposal governs the first release?
- What are the competition's exact judging criteria and which NVIDIA products or services count separately? Reconcile the rubric with demonstrated feature value before setting priorities.
