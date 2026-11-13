# Socrates delivery status

**13 November 2026.** Direction rewritten for a local software tutor. See [prd.md](prd.md), [Interface.md](Interface.md), [architecture.md](architecture.md) and [research.md](research.md). Status here is based on repository inspection at main commit 471fe59 before this docs revision; it is not a Spark deployment audit.

## Present in the repository

- ios/ contains a SwiftUI/PencilKit whiteboard, question capture, line upload requests, push-to-talk WAV recording, caption/audio playback, WebSocket client, mock lesson and Swift package tests. The merged iPad PR is #4. The mock uses arm-named events and placeholder scribbles; it does not demonstrate a real tutor backend or real NVIDIA calls.
- backend/ contains requirements.txt with FastAPI and related packages, but no application or tests for tutoring, math checking, storage or inference.
- The GitHub agent board scripts and tests remain development coordination, separate from the product.
- robotics/ contains a placeholder README. No physical implementation has been found. The robot path is retired.
- frontend/ Next.js starter was removed by the iOS merge. The old v1 interface and docs contain robot assumptions; this rewrite makes v2 explicit but does not migrate Swift code.

## Planned, not implemented

| Area | Next proof | Status |
| --- | --- | --- |
| Local Spark vLLM | Pinned Nemotron 3 Nano Omni, image and tool-call smoke, memory/latency | Not run; SSH authentication unavailable in this review. |
| NemoClaw/OpenShell | Local vLLM route and real math MCP call plus denied-call trace | Not installed or wired in repo. |
| NeMo Guardrails | Loaded rails plus input/tool/result/output fixtures | Not wired. |
| Math checker | Bounded SymPy parser and structured partial/invalid evidence | Not coded; SymPy absent from requirements. |
| Digital tutor ink | v2 separate layer, revisioned preview/commit, cancellation | Not coded; current client has arm fallback ink. |
| Magpie and ASR | Real local audio path and shared-load benchmark | Not wired. |
| Retrieval | Curated local sources, embedding and optional small reranker | Not wired. Parse deferred for lack of Spark NIM support. |
| Local summary | Assisted versus independent evidence, deletion and roles | Not coded. |
| End-to-end | Internet-disconnected iPad↔Spark run | Not run. |

## Immediate handoffs

1. Backend owner: confirm the v2 contract in [Interface.md](Interface.md), implement session and math fixture, then prove local vLLM and NemoClaw tool path. Do not use a hosted endpoint.
2. iOS owner: migrate v1 arm events and any-input phases to separate digital tutor ink, then test on the real iPad.
3. Inference owner: report Spark memory and a co-resident model/speech budget, pinned revisions, and the exact live versus optional NVIDIA components.
4. Product/QA owner: label math and handwriting fixtures, test guardrail failures and unaided transfer, and record actual call traces.

No open implementation PR was found during this review. The old board comment describes the prior robot proposal; this repo change does not send a board message. A team member should update the board before parallel implementation.

## Checks for this documentation rewrite

- Checked 54 internal Markdown links and anchors: 0 errors. Git diff whitespace check: passed.
- Existing agent-board suite: 10 tests passed.
- Swift package test command did not run tests on this Mac: the selected Command Line Tools toolchain cannot import the Testing module. No Xcode.app is installed here, and xcodebuild is unavailable. This is an environment limitation, not evidence of an iPad regression; the prior iPad PR reported its own build/tests.
- Spark SSH read-only probe failed authentication, so no local model, memory, latency or service claim was verified on the device.
