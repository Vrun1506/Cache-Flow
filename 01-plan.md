# Socrates software tutor: build plan

**Current direction, 13 November 2026.** [prd.md](prd.md) is the product source of truth; [Interface.md](Interface.md) is the proposed v2 iPad contract; [architecture.md](architecture.md) and [design.md](design.md) own the system and implementation contracts. This file is the delivery sequence. The earlier robot plan is superseded and available in Git history before this revision.

## 1. Freeze the first demonstration

One student on the SwiftUI/PencilKit iPad, one local DGX Spark, one factoring lesson and one unassisted transfer question. The demo shows handwriting read-back, Check, a real SymPy tool call from the Socratic agent, one spoken Socratic question, one digital tutor annotation, a correction and a local summary. A labelled trace shows vLLM, NemoClaw/OpenShell, NeMo Guardrails, math tool and Magpie contributions. Nothing in the live loop requires internet.

## 2. Integration sequence

| Order | Work | Exit evidence |
| --- | --- | --- |
| 1 | Inventory the Spark: usable memory, driver, Docker, network and local model cache. Pin a local vLLM-compatible Nemotron 3 Nano Omni NVFP4 image/weights. | /v1/models and one image, one text and one schema-valid tool-call smoke; revision and memory log. |
| 2 | Implement a FastAPI v2 session API with revisions, request IDs, replay, local storage and the exact iPad fixture. | Contract tests and a desktop client replay. |
| 3 | Implement bounded algebra parser/SymPy checker and deterministic question templates. | Labelled signs, partial roots, equivalence, malformed input and resource-limit tests. |
| 4 | Install NemoClaw with local vLLM routing and a versioned Socratic skill; expose the math checker as a narrow local MCP tool under OpenShell policy. | Real harness trace of allowed math call and denied unrelated call. |
| 5 | Add NeMo Guardrails input/tool/result/output checks and failure-closed validation. | Tool-schema, prompt-injection, answer-leakage and rail-disabled tests. |
| 6 | Migrate the Swift client/mock from v1 arm events to v2 digital tutor ink, separate layers, revision handling and Check/Hint/Ask intents. | Swift package tests, simulator build and actual iPad turn. |
| 7 | Add local Magpie speech output, then qualify local ASR. Start with current push-to-talk; evaluate streaming only after interruption and latency measurements. | Caption/audio match, cancellation, fallback, and measured first audio. |
| 8 | Add a small curated subject library with local embedding; add a Spark-compatible reranker only if load and recall justify it. | Cited answer, missing-evidence refusal, memory and latency comparison. |
| 9 | Run an internet-disconnected rehearsal and collect p50/p95 latency, correctness, safety and unaided-transfer evidence. | Repeatable demo and honest trace showing which products were actually invoked. |

All steps are proposed work; none of the Spark product services is implemented in this repository today. Parallel owners should agree on exact paths and the v2 contract before editing both sides.

## 3. Model and tool choices

The center is the locally served multimodal Nemotron 3 Nano Omni NVFP4 in vLLM. It can accept image/text/audio inputs and emits text, so its handwriting, question-understanding and tutoring quality must be tested on this dataset. The local Magpie TTS sidecar speaks its final text. NeMo Guardrails and OpenShell have different roles: content/tool validation and sandbox policy. NemoClaw is the harness that packages the agent and routes it to the local model; it is not counted as a separate model inference call. The math tool provides an actual deterministic check; retrieval only runs when a curated source is needed.

Nemotron Parse remains a deferred document-ingestion candidate because the current Parse NIM lists no Spark profile. A smaller NeMo Retriever embedding/reranking pair is an optional local knowledge path, not a reason to load multiple large models during every algebra turn. No cloud endpoints are permitted.

## 4. Build constraints

Use the committed Swift app as the starting surface. The old arm calibration, 540-point fixed strip and any-input pen phase are implementation debt for the software tutor. Do not pass programmatic tutor writing through arm events. Keep student Pencil strokes immutable and separate from generated annotations. Use only server-validated board revisions. Keep role checks, consent, ephemeral audio and deletion on the release path. See [research.md](research.md) for the learning rationale.
