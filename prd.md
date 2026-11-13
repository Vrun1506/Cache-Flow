# Socrates product requirements

**Decision date:** 13 November 2026. **Status:** approved direction from the product owner; implementation and model qualification pending. This is the source of truth for product scope. [Interface.md](Interface.md) owns the proposed wire contract; [status.md](status.md) separates code from plans.

## Goal and audience

Give a learner timely, grounded, spoken and written tutoring on a shared iPad board using only a local DGX Spark and local iPad services. The product aims to improve unaided understanding, measured on a fresh problem, rather than merely completing the current answer. Start with a supervised single-student demo. “General purpose” means a reusable interaction loop and adapters; it does not mean the first release can certify correctness in every subject.

## First complete journey

1. Open a local session, choose a factoring preset or handwrite a question, and confirm the tutor's read-back.
2. Write a line. Tap Check, ask a spoken question, or tap Hint. The iPad uploads a snapshot with a monotonically increasing board revision and a request ID.
3. The tutor shows what it read. If the transcription is ambiguous, it asks the learner to correct it before judging.
4. For a supported math item, the local math tool returns verified evidence. The tutor highlights one relevant region, asks one concise question, and may write one step in its own layer while Magpie speaks a matching caption.
5. The learner revises their own work. The tutor rechecks the changed revision and adapts its next move.
6. A new, unassisted transfer item ends the session. The local summary records the skill, evidence, help level and next practice step.

## Release scope and acceptance

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| P1 | Swift iPad board keeps student and tutor ink separate, editable, and legible. | Tutor writing appears with preview/commit, can be cleared alone, and never changes the student layer. |
| P2 | Student can activate Check, Hint, Teach one step, or Ask by text or push-to-talk. | Each action yields a caption even when audio fails; no unsolicited interruption during active writing. |
| P3 | The local vLLM Nemotron model reads a bounded board image and proposes a candidate transcription. | Labelled sign/exponent/line-order set; ambiguity causes confirmation rather than a false correction. |
| P4 | A subject tool supplies structured evidence before any correctness claim. | Algebra suite includes correct/incorrect factors, equivalent transformations, partial roots and malformed input; no raw model text decides correctness. |
| P5 | NemoClaw runs the Socratic skill under OpenShell policy and invokes the math tool through a narrow, auditable interface. | A real trace shows agent action, allowed tool call/result and policy denial for an unapproved tool. |
| P6 | NeMo Guardrails checks learner input, tool invocation/results and tutor output, including answer leakage and prompt injection. | Golden adversarial fixtures demonstrate enforced configuration; a disabled or failed rail is visible and blocks the affected action. |
| P7 | Magpie speaks the same text shown in the caption; microphone capture stays local. | Real Spark-to-iPad voice demo, cancellation, audio failure fallback, and measured first-audio delay. |
| P8 | The session survives reconnect and rejects stale tutor ink. | Reconnect replays durable state without replaying speech; old board revisions cannot commit annotations. |
| P9 | Local summary distinguishes assisted and unaided work. | A fresh transfer problem is scored separately and the summary can be deleted by an authorized operator. |
| P10 | No student content is sent to external inference or telemetry. | Offline rehearsal after model download; network inspection and config review show only iPad↔Spark traffic. |

## Supported subjects

- **First release:** one algebra lesson on factoring and roots of monic quadratics, plus a linear-equation smoke item. SymPy validates step type and mathematical effect. A single root is partial progress, not a claim that the full solution set is complete.
- **Next:** teacher-approved science, history or writing content can be discussed through retrieval and source citations. Such subjects receive “supported by source,” “needs review,” or “unknown” evidence, not a SymPy-style correctness verdict.
- **Outside current scope:** autonomous grading across every subject, unsupervised use by children, replacing a qualified human teacher, live camera surveillance, autonomous web browsing, robot hardware, and cloud inference.

## Interaction and learning rules

The Socratic skill tracks the current learner attempt, likely misconception, help already given, next question, and source/evidence IDs. It asks for a student attempt before revealing a step when possible. A short worked step is allowed after the learner requests it or repeated struggle; the agent then asks the learner to do the next step. It never states that help implies mastery. A final unassisted item is required to move the skill to “checked independently.” This design follows the evidence in [research.md](research.md), but learning benefit still needs learner testing.

## Local NVIDIA stack

The baseline is one Spark-local vLLM endpoint serving [Nemotron 3 Nano Omni NVFP4](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4) for image understanding, dialogue and tool proposals. NVIDIA's [Spark playbook](https://build.nvidia.com/spark/nemotron/overview) describes a local vLLM path; the exact image/model revision and memory settings must be pinned after a device test. [NemoClaw local vLLM](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/set-up-vllm) is the planned harness route. NeMo Guardrails, OpenShell, SymPy and a local Magpie TTS service complete the critical loop. A local Parakeet or Nemotron ASR streaming service is a separate speech input candidate; the current iPad contract is full-utterance push-to-talk.

Nemotron Parse is a document-page parser, not a proven handwriting checker. Its [2.4 NIM support matrix](https://docs.nvidia.com/nim/ingestion/parse/2.4/support-matrix.html) does not list DGX Spark. It is deferred unless a fully local, supported Spark deployment is proved. NeMo Retriever embedding/reranking is for a curated knowledge library, not every algebra turn; it is gated by memory and latency tests. No hosted substitute satisfies P10.

## Quality gates

For a demo, complete a real iPad→Spark→iPad run with the network disconnected from the internet after provisioning. Record model/container revisions, Spark memory, p50/p95 Check-to-caption and Check-to-first-audio, transcription accuracy on a labelled set, math-tool accuracy, stale-event rejection, guardrail blocks, and an unaided transfer result. Proposed targets to validate with the team: p95 Check-to-caption under 4 seconds, first audio under 6 seconds, zero false “correct” claims in the curated math suite, zero stale tutor-ink commits. These are targets, not measured results.

Before any pilot with minors, agree on age, consent, access control, retention/deletion and human escalation. The demo uses synthetic or consenting adult data.
