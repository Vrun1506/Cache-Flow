# Socrates delivery status

**13 November 2026.** Plan reconciled for the local, general-purpose whiteboard tutor. See [final-plan.md](final-plan.md), [prd.md](prd.md), [Interface.md](Interface.md), [architecture.md](architecture.md) and [research.md](research.md). Implementation status is based on repository inspection through main commit `0268c5e` before this documentation update; it is not a Spark deployment audit.

## iOS v2 migration: implemented, simulator run pending

**Owner:** the iOS agent (Claude Code session for the iPad owner). **Branch:** `ios/v2-migration` (local, uncommitted until the owner asks). **Paths:** `ios/**`, plus this section and the status line in Interface.md. `backend/**` is untouched. Contract decisions and questions for the backend owner are in [ios/CONTRACT-QUESTIONS.md](ios/CONTRACT-QUESTIONS.md).

What exists now:
- v1 arm/calibration code is removed: `Events.swift`, `ArmJob`, `CalibrationLog`, any-input tutor phases, ghost/force-arm UI. The v2 client lives in `CacheFlowKit/Protocol` (envelope, payloads, client messages) and `Net` (`/v2` HTTP; WSS resuming with `after_seq`).
- `Session/LessonEngine.swift` is a pure reducer: seq de-duplication, session/version checks, stale-revision rejection, preview→commit with acks, cancellation/supersession, replay without speech, ink/scene bounds, read-back confirmation. `SpeechQueue` never starts speech while the Pencil is down. `ActionPolicy` decides which buttons show.
- `Mock/MockServer.swift` runs the entire demo in-process. Every envelope is JSON round-tripped. Faults: `loseNextAudio`, `staleNextInk`, `dropConnection()`. It covers algebra (sign error → ambiguous line → valid → partial root → Show the answer → independent transfer) and a physics second subject with not-verified evidence and a free-body diagram.
- App/: the student Pencil canvases hold only student ink. `TutorLayer` renders tutor ink, scenes and highlights separately. The UI has Tutor me, Check, Hint, Explain, Draw it, Show the answer and Stop; typed reply and push-to-talk with transcript confirmation; captions with an on-device-speech fallback badge; the read-back sheet; a summary; settings with the agent trace and session deletion; and the `-uitest` debug panel.
- Tests: 40 deterministic fixtures in `Tests/CacheFlowKitTests/Fixtures/v2`; contract, scenario, engine, speech-queue, net and headless mock-lesson suites; and the simulator UI test `ios/UITests/LessonE2ETests.swift`, with the `CacheFlowUITests` target in `project.yml` and `make e2e` → `ios/scripts/e2e.sh`.

Observed results (13 Nov 2026, this Mac, Command Line Tools only, no Xcode.app):
- `make test`: **52 tests in 9 suites passed**, 5 consecutive runs, including the headless full lesson with lost audio, stale ink, cancellation and reconnect.
- `make typecheck` (App/ against the Mac Catalyst SDK, Swift 6): **passed**. This is a proxy, not an iOS build.
- `make build` and `make e2e`: **not run**, because xcodebuild requires Xcode. Next action for whoever has Xcode: from ios/ run `make bootstrap && make build && make e2e`, then fix anything the real iOS SDK or the simulator surfaces. The likeliest gaps are XCUITest drawing on PKCanvasView, sheet accessibility, and timing in the drop/reconnect step.
- Nothing here exercises a Spark, model, agent, verifier or speech service. A real-device iPad run is still required.

## Present in the repository

- ios/ contains the SwiftUI/PencilKit whiteboard. On branch `ios/v2-migration` it is migrated to the v2 contract with a full in-process mock (see the iOS section above). The merged iPad PR is #4 (v1). The mock does not demonstrate a real tutor backend or real NVIDIA calls.
- backend/ contains requirements.txt with FastAPI and related packages, but no application or tests for tutoring, math checking, storage or inference.
- The GitHub agent board scripts and tests remain development coordination, separate from the product.
- robotics/ contains a placeholder README. No physical implementation has been found. The robot path is retired.
- frontend/ Next.js starter was removed by the iOS merge. The old v1 interface and docs contain robot assumptions; this rewrite makes v2 explicit but does not migrate Swift code.

## Planned, not implemented

| Area | Next proof | Status |
| --- | --- | --- |
| Local Spark vLLM | Pinned Nemotron 3 Nano Omni, image/handwriting and tool-call smoke, Magpie co-residency, memory/latency | Not run on this Spark; no device test performed in this plan-only update. |
| NemoClaw/OpenShell + Jane Street-derived skill | Local vLLM route, active skill turn, real math MCP call and denied-call trace | Not installed or wired in repo. |
| NeMo Guardrails | Loaded rails plus input/tool/result/output fixtures | Not wired. |
| Math checker | Safe bounded grammar, step-aware SymPy, structured valid/invalid/partial/unknown evidence | Not coded; SymPy absent from requirements. The former equal-`solveset`/`parse_expr` plan has been corrected only in documentation. |
| Digital tutor ink and scenes | v2 separate layer, revisioned preview/commit, bounded diagrams/plots, cancellation | Not coded; current client has arm fallback ink. |
| Magpie and Parakeet | Real local speech output, push-to-talk input, caption/transcript match and shared-load benchmark; document Omni audio substitution if Parakeet fails | Not wired. |
| Retrieval | Curated local sources, embedding and optional small reranker | Not wired. Parse deferred for lack of Spark NIM support. |
| Local summary | Assisted versus independent evidence, deletion and roles | Not coded. |
| End-to-end | Internet-disconnected handwritten tutoring turn with tool, voice, writing, visual, answer reveal, independent check and second subject | Not run. |

## Immediate handoffs

1. Integrator: start at Gate 0 in [final-plan.md](final-plan.md); prove the Spark-local vLLM model, Magpie/Parakeet co-residency or documented local substitutions, NemoClaw API bridge, active Jane Street-derived skill and real MCP call before claiming a live path.
2. Backend owner: implement [Interface.md](Interface.md) v2 and the corrected step-aware math tool, including partial roots and safe parsing. Do not use a hosted endpoint.
3. iOS owner: migrate v1 arm events and any-input phases to revision-safe tutor ink, bounded scenes, captions and interruptible audio, then test on the real iPad.
4. Product/QA owner: label math, handwriting and second-domain fixtures; test guardrail failures, Show the answer assistance and unaided transfer; record actual call traces and latency.

No open implementation PR was found during this plan review. The old board comment describes the prior robot proposal; this documentation change does not send a board message. A team member should update the board before parallel implementation.

## Earlier checks (previous documentation rewrite)

- Checked 54 internal Markdown links and anchors: 0 errors. Git diff whitespace check: passed.
- Existing agent-board suite: 10 tests passed.
- Swift package test command did not run tests on this Mac: the selected Command Line Tools toolchain cannot import the Testing module. No Xcode.app is installed here, and xcodebuild is unavailable. This is an environment limitation, not evidence of an iPad regression; the prior iPad PR reported its own build/tests.
- Spark SSH read-only probe failed authentication, so no local model, memory, latency or service claim was verified on the device.

## Checks for the current plan reconciliation

- Reviewed the scope/runtime/protocol references in the changed root documents. Checked 71 relative Markdown links: 0 missing. `git diff --check`: passed.
- No product source code, Swift client, backend runtime or Spark configuration was changed by this plan update.
