# Socrates delivery status

**13 November 2026 · Product planning.** See the [PRD](prd.md) for scope and release gates, [architecture](architecture.md) for ownership, and [implementation contracts](design.md) for integration details.

## Repository baseline

- `frontend/` has a Next.js starter and package scripts. `backend/` has a Python requirements file including FastAPI. `robotics/` has a placeholder README. The inspected baseline (`72ee253`) has no tutor lesson, iPad board, inference wiring, or robot implementation.
- The existing GitHub [agent coordination board](https://github.com/Vrun1506/Cache-Flow/issues/1), mailbox scripts and tests support development coordination. They are separate from the product.
- The initial planning pass checked the upstream branch and pull request refs, open issues and board comments. No teammate product plan was available at that point. Reconcile any later proposal in the decision log and update the PRD if it changes scope.
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
- Which teammate owns each work area, and when does the demo need to be ready?
