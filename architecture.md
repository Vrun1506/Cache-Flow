# Socrates architecture

**Status:** target design, not deployed. Product scope is in [prd.md](prd.md); wire migration is in [Interface.md](Interface.md). The system is one iPad client and one DGX Spark with local inference. There is no robot or simulator path.

## Runtime and trust boundaries

SwiftUI/PencilKit iPad → authenticated local HTTPS/WSS session API on Spark → lesson service. The service owns revisions, modes, evidence and the digital ink authorizer. The Socratic agent runs in a NemoClaw-managed OpenShell sandbox and reaches the already-running Spark-local vLLM model. Only declared local math and retrieval tools are exposed. The Spark also hosts NeMo Guardrails and local speech adapters. The iPad never calls vLLM, the sandbox, or a tool directly.

OpenShell restricts agent file/network/process access; NeMo Guardrails validates content and tool traffic; server-side schema checks and authorization remain separate required boundaries. A NemoClaw installation is an agent harness, not an API wrapper that itself proves a math calculation. [NemoClaw architecture](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/about/how-it-works) · [OpenShell architecture](https://docs.nvidia.com/openshell/about/architecture) · [NeMo Guardrails tool rails](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/tool-calling).

## Core flow

1. iPad sends one bounded question/line image, optional WAV or typed utterance, board revision and request ID to the session API.
2. Local Nemotron 3 Nano Omni via vLLM returns candidate reading and intent. Ambiguity is exposed to the student.
3. The lesson service calls a bounded subject verifier. For algebra, SymPy checks the claimed transformation and returns reason codes, normalized expressions, first-error span and evidence IDs.
4. The Socratic skill consumes the verified evidence and the learner state. NemoClaw/OpenShell mediate the agent's allowed tool calls. NeMo Guardrails validates inbound content, tool call/result and final response.
5. The service emits one versioned tutor turn: caption, audio reference, highlighting and a bounded tutor-ink plan. The iPad previews the plan and commits it only for the matching board revision.
6. The service stores a minimal attempt/evidence record and generates a local summary from records, not an unverified model memory.

Use sequential turns for the first release. Continuous stroke watching and barge-in voice are later modes, sharing the same revision and cancellation contracts.

## Component and extension review

| ID | Responsibility and contract | Expected variation / extension | Protected core and check |
| --- | --- | --- | --- |
| C1 iPad board | Capture student ink; render separate tutor layer and captions by revision. | New input methods through input adapters. | Never mix ownership; revision and clear-layer tests. |
| C2 session API | Authenticate, bound uploads, order events, persist attempts. | New transports behind one event contract. | Session state machine; reconnect/idempotency tests. |
| C3 perception | Image/audio to candidate text with confidence/ambiguity. | Swap local model adapter. | Confirmation rule; labelled recognition set. |
| C4 subject verifier | Structured verdict/reason/evidence; never model-only correctness. | Register subject strategy. | Stable lesson orchestration; algebra negative/partial cases. |
| C5 Socratic agent | Select one pedagogical move from evidence. | Skill/policy versions via composition root. | Output schema and leakage fixtures. |
| C6 safety boundary | Authorize tools, sanitize content, enforce local egress. | Guardrail policies and tool registry. | Explicit denied-call and rail-failure tests. |
| C7 speech | Local WAV→text and caption→audio, cancellable. | ASR/TTS adapters. | Captions, local-only routing and latency tests. |
| C8 knowledge | Curated passage ingestion, retrieve and cite. | Local embedding/rerank adapters. | Source provenance and “unknown” path. |
| C9 tutor ink | Validated strokes/text, preview/commit against revision. | Renderer/font adapters. | Bounded coordinates, immutable student ink tests. |
| C10 evidence store | Append attempt and assistance records, produce summary. | New reports over evidence read model. | Migration, role and deletion tests. |

All ten rows are design reviews with verdict **pending implementation**; the named checks are required evidence, not completed tests. The first release can use one FastAPI process plus local model/speech containers. Keep policy, tools and providers behind interfaces; no microservice split is justified until measured load or isolation requires it. Changes in model, subject or speech provider should register at the composition root without editing the lesson algorithm.

## Resource and deployment plan

The NVFP4 Omni model card estimates 21 GB of weights and lists DGX Spark support, but actual vLLM cache, image input and voice sidecars consume additional unified memory. Pin a Spark-compatible image, model revision, max context and sequence count; cap image size, concurrent sessions, tokens and tool calls. First run each service alone, then together under the demo load. The Spark's actual usable memory is unverified because SSH authentication was unavailable in this review.

Use local Magpie TTS NIM with its smallest supported profile only after measuring shared load; [NVIDIA lists Spark support](https://docs.nvidia.com/nim/speech/26.10.0/reference/support-matrix/tts.html). For speech input, evaluate local Parakeet/Nemotron ASR against using Omni's audio input, and choose the passing lower-latency path. Retrieval is not in the algebra critical path. The newer Nemotron 3.5 Rerank 8B NIM used [55–62 GiB at readiness on GB10](https://docs.nvidia.com/nim/nemo-retriever/reranking/latest/performance.html), so it cannot be assumed to coexist with the tutor on a 64 GB Spark. Evaluate a smaller Spark-supported 1B reranker, or omit reranking for the first demo. The current Nemotron Parse NIM does not list Spark support, so it is not deployable as an assumed local component.

No inference call may fall through to an internet endpoint. Provisioning can download pinned images and weights; the live session must work offline. Log service name, model revision, latency, outcome and evidence ID, without raw ink/audio/child transcripts. Distinguish actual call traces from planned integrations.
