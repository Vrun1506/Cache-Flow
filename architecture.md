# Socrates architecture

**Status:** target design, not deployed. Product scope is in [prd.md](prd.md), the build handoff is [final-plan.md](final-plan.md), and wire migration is in [Interface.md](Interface.md). The system is one iPad client and one DGX Spark with local inference. There is no robot or simulator path.

## Runtime and trust boundaries

SwiftUI/PencilKit iPad → authenticated local HTTPS/WSS session API on Spark → lesson service. The service owns revisions, modes, evidence, run ordering, cancellation and the digital ink authorizer. One tutor agent runs a versioned Jane Street-derived Socratic skill in a NemoClaw-managed OpenShell sandbox and reaches one resident Spark-local vLLM Nemotron model. Only declared local math and optional retrieval tools are exposed. The Spark also hosts active NeMo Guardrails, local Magpie speech output and a qualified local Parakeet push-to-talk ASR path. The iPad never calls vLLM, the sandbox, or a tool directly. Ollama and NeMo Agent Toolkit are outside the first-demo runtime.

OpenShell restricts agent file/network/process access; NeMo Guardrails validates content and tool traffic; server-side schema checks and authorization remain separate required boundaries. A NemoClaw installation is an agent harness, not an API wrapper that itself proves a math calculation. [NemoClaw architecture](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/about/how-it-works) · [OpenShell architecture](https://docs.nvidia.com/openshell/about/architecture) · [NeMo Guardrails tool rails](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/tool-calling).

## Core flow

1. iPad sends a bounded handwritten question/line image, optional WAV or typed utterance, board revision and request ID to the session API. `tutor` explicitly activates the skill.
2. Local Nemotron 3 Nano Omni via vLLM returns candidate reading and symbol regions. Ambiguity is exposed to the student before a correctness judgment.
3. The Socratic skill uses learner state to ask one question or choose a bounded tutoring move. For supported algebra the agent calls the local `check_math_step` MCP tool through NemoClaw/OpenShell; the lesson service validates the returned revision, schema and evidence. A single valid root is partial progress.
4. NeMo Guardrails validates inbound content, tool call/result and the proposed response. The gateway enforces revision, authorization, evidence and board-command checks even after a guardrail pass.
5. The service emits one versioned tutor turn: caption first, then matching local Magpie audio, highlighting and bounded tutor ink or scene. The iPad previews and commits only for the matching board revision. `show_answer` is allowed on explicit request and records assistance.
6. A new unassisted item checks understanding. The service stores a minimal attempt/evidence record and generates a local summary from records, not an unverified model memory. A second subject uses the same teaching loop with an honest evidence label.

Use sequential, interruptible turns for the first demo. Auto coach after a completed-line pen-up pause and full-duplex barge-in voice are later modes, sharing the same revision and cancellation contracts.

## Component and extension review

| ID | Responsibility and contract | Expected variation / extension | Protected core and check |
| --- | --- | --- | --- |
| C1 iPad board | Capture student ink; render separate tutor ink, bounded scene, captions and audio by revision. | New input methods and scene primitives through registered handlers. | Never mix ownership; revision, clear-layer and scene tests. |
| C2 session API | Authenticate, bound uploads, order events, persist attempts. | New transports behind one event contract. | Session state machine; reconnect/idempotency tests. |
| C3 perception | Image/audio to candidate text with confidence/ambiguity. | Swap local model adapter. | Confirmation rule; labelled recognition set. |
| C4 subject verifier | Structured verdict/reason/evidence; never model-only correctness. A grammar constructs bounded SymPy objects. | Register subject strategy. | Stable lesson orchestration; algebra negative, partial, domain and parser-attack cases. |
| C5 Socratic agent | Run the attributed skill and select one pedagogical move from evidence. | Skill/policy versions via composition root. | Active-skill turn, output schema and leakage fixtures. |
| C6 safety boundary | Authorize tools, sanitize content, enforce local egress. | Guardrail policies and tool registry. | Explicit denied-call and rail-failure tests. |
| C7 speech | Local caption→Magpie audio and Parakeet WAV→text, both cancellable. | ASR/TTS adapters. | Transcript confirmation, speech/caption identity, local-only routing, co-residency and latency tests. |
| C8 knowledge | Curated passage ingestion, retrieve and cite. | Local embedding/rerank adapters. | Source provenance and “unknown” path. |
| C9 tutor ink and scene | Validated strokes/text/diagram primitives, preview/commit against revision. | Renderer/font/scene handlers. | Bounded coordinates and complexity, immutable student ink tests. |
| C10 evidence store | Append attempt and assistance records, produce summary. | New reports over evidence read model. | Migration, role and deletion tests. |

All ten rows are design reviews with verdict **pending implementation**; the named checks are required evidence, not completed tests. The first release can use one FastAPI process plus local model/speech containers. Keep policy, tools and providers behind interfaces; no microservice split is justified until measured load or isolation requires it. Changes in model, subject or speech provider should register at the composition root without editing the lesson algorithm.

## Resource and deployment plan

The NVFP4 Omni model card estimates 21 GB of weights and lists DGX Spark support, but actual vLLM cache, image input and voice sidecars consume additional unified memory. Pin a Spark-compatible image, model revision, max context and sequence count; cap image size, concurrent sessions, tokens and tool calls. First run each service alone, then together under the demo load. The Spark's actual usable memory and any organiser-imposed 8,192-token cap are unverified in this repo; do not state either as a tested constraint.

Local Magpie TTS and push-to-talk Parakeet ASR are proposed for the first demo; [NVIDIA lists Spark support for Magpie TTS](https://docs.nvidia.com/nim/speech/26.10.0/reference/support-matrix/tts.html) and [Parakeet 1.1B CTC English ASR](https://docs.nvidia.com/nim/speech/26.10.0/reference/support-matrix/asr.html). Test both alongside vLLM. If Magpie fails the load gate, choose and document a tested local speech-output equivalent while keeping the iPad caption/on-device fallback; if Parakeet fails, qualify Omni's local audio input and report Parakeet as unintegrated. Retrieval is outside the algebra critical path. The newer Nemotron 3.5 Rerank 8B NIM used [55–62 GiB at readiness on GB10](https://docs.nvidia.com/nim/nemo-retriever/reranking/latest/performance.html), so do not assume it coexists with the tutor on a 64 GB Spark. Nemotron Parse is also deferred until a supported local Spark path is proved.

No inference call may fall through to an internet endpoint. Provisioning can download pinned images and weights; the live session must work offline. Log service name, model revision, latency, outcome and evidence ID, without raw ink/audio/child transcripts. Distinguish actual call traces from planned integrations.
