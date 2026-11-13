# Socrates product requirements

**Decision date:** 13 November 2026. **Status:** approved product direction; implementation and model qualification pending. This is the source of truth for product scope. [final-plan.md](final-plan.md) is the build handoff, [Interface.md](Interface.md) owns the proposed wire contract, and [status.md](status.md) separates code from plans.

## Goal and audience

Give a learner responsive, grounded, spoken and written tutoring on a shared iPad board using only a local DGX Spark and local iPad services. The student can request questions, checking, an explanation, a drawing or the exact answer. The product aims to improve unaided understanding, measured on a fresh problem, rather than merely completing the current answer. Start with a supervised single-student demo. “General purpose” means a reusable interaction loop across subjects; it does not mean the first release can certify correctness in every subject.

## First complete journey

1. Open a local session, handwrite a question or choose a preset, and tap **Tutor me**. Confirm an ambiguous read-back.
2. The versioned Jane Street-derived Socratic skill asks one focused question. The tutor captions and speaks it locally; the learner answers by writing or typing.
3. Tap **Check**, **Hint**, **Explain this step**, **Draw it** or **Show the answer**. The iPad sends a bounded snapshot with board revision and request ID. A line-completion Auto coach mode is optional after the explicit loop works.
4. For supported algebra, the agent calls the local math tool through NemoClaw/OpenShell. The gateway validates its evidence; the tutor highlights a sign, writes a step or draws a diagram while Magpie speaks the same explanation as the caption.
5. The learner revises the work. A valid single root is acknowledged as partial progress. An explicit Show the answer request may reveal the full answer and is recorded as assisted.
6. A new, unassisted item ends the session. The local summary records the skill, evidence, help level and next practice step. A second subject demonstrates the same teaching loop without claiming unimplemented automatic grading.

## Release scope and acceptance

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| P1 | Swift iPad board keeps student and tutor ink separate, editable, and legible. | Tutor writing appears with preview/commit, can be cleared alone, and never changes the student layer. |
| P2 | Student can activate Tutor me, Check, Hint, Explain this step, Draw it, Show the answer or Ask, and answer by writing, typing or push-to-talk. | Each action yields a caption even when audio fails; spoken input uses local ASR; no unsolicited speech during active writing. |
| P3 | A local vLLM multimodal model reads bounded board images and proposes candidate text. | Labelled sign/exponent/line-order set; ambiguity causes confirmation rather than a false correction. |
| P4 | A subject tool supplies structured evidence before any correctness claim. | Algebra suite includes correct/incorrect factors, equivalent transformations, partial roots, domain changes, malformed input and parser attacks; no raw model text decides correctness. |
| P5 | NemoClaw runs an attributed, versioned Jane Street-derived Socratic skill under OpenShell policy and invokes the math MCP tool. | A real trace shows skill use, allowed tool call/result and policy denial for an unapproved tool. |
| P6 | NeMo Guardrails checks learner input, tool invocation/results and tutor output, including answer leakage and prompt injection. | Golden adversarial fixtures demonstrate enforced configuration; a disabled or failed rail visibly blocks the affected action. |
| P7 | Local Magpie speaks the same text shown in the caption, and local Parakeet transcribes one push-to-talk learner turn; microphone capture stays local. | Real two-way Spark-to-iPad voice demo, transcript confirmation, cancellation, caption/on-device fallback and measured first-audio/transcript delay. If Parakeet fails the device gate, use Omni audio input and report that substitution. |
| P8 | The tutor can write, highlight and draw a bounded subject-relevant diagram in a separate layer. | A real handwritten problem produces revision-safe tutor ink and a labelled visual; reconnect replays commands without replaying speech. |
| P9 | Explicit answer reveal is permitted but cannot be counted as mastery. | A fresh unassisted item is scored separately; the local summary records assistance and can be deleted by an authorized operator. |
| P10 | No student content is sent to external inference or telemetry. | Offline rehearsal after model download; network inspection and config review show only iPad↔Spark traffic. |

## Supported subjects

- **First demo:** the same tutoring loop handles an algebra lesson and one physics or other non-math concept with a generated, labelled visual. SymPy validates supported algebra step types; a single root is partial progress. For other subjects the tutor may explain and ask questions, but labels correctness as unverified unless a registered verifier or approved source supports a narrower claim.
- **Next:** teacher-approved science, history or writing material can be imported locally and cited through retrieval. Such subjects receive “supported by source,” “needs review,” or “unknown” evidence, not a SymPy-style correctness verdict.
- **Outside current scope:** autonomous grading across every subject, unsupervised use by children, replacing a qualified human teacher, live camera surveillance, autonomous web browsing, robot hardware, and cloud inference.

## Interaction and learning rules

The Jane Street-derived Socratic skill tracks the current learner attempt, likely misconception, help already given, next question and source/evidence IDs. It asks one question at a time, starts with a diagnostic appropriate to a broad topic or one problem, and gives a short worked step after a learner request or repeated struggle. **Show the answer** is an explicit override: write and explain the exact answer, record assistance, then offer a fresh unassisted item. Help never implies mastery. Levels 4–5 require repeated independent evidence. This design follows the evidence in [research.md](research.md), but learning benefit still needs learner testing.

## Local NVIDIA stack

The baseline is one Spark-local vLLM endpoint serving [Nemotron 3 Nano Omni NVFP4](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4) for image understanding, dialogue and tool proposals. Its model card includes a Spark vLLM recipe; the exact image/model revision and memory settings must be pinned after a device test. [NemoClaw local vLLM](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/set-up-vllm) is the agent harness route. A versioned Jane Street-derived skill, NeMo Guardrails, OpenShell, a bounded SymPy MCP tool, local Magpie TTS and [Spark-supported Parakeet ASR](https://docs.nvidia.com/nim/speech/26.10.0/reference/support-matrix/asr.html) complete the proposed critical loop. If the Parakeet co-residency gate fails, qualify Omni audio input; the current iPad contract remains full-utterance push-to-talk. Ollama and NAT are not first-demo runtimes.

Nemotron Parse is a document-page parser, not a proven handwriting checker. Its [2.4 NIM support matrix](https://docs.nvidia.com/nim/ingestion/parse/2.4/support-matrix.html) does not list DGX Spark. It is deferred unless a fully local, supported Spark deployment is proved. NeMo Retriever embedding/reranking is for a curated knowledge library, not every algebra turn; it is gated by memory and latency tests. No hosted substitute satisfies P10.

## Quality gates

For a demo, complete a real iPad→Spark→iPad run with the network disconnected from the internet after provisioning. Record model/container revisions, Spark memory, p50/p95 Check-to-caption and Check-to-first-audio, transcription accuracy on a labelled set, math-tool accuracy, stale-event rejection, guardrail blocks, and an unaided transfer result. Proposed targets to validate with the team: p95 Check-to-caption under 4 seconds, first audio under 6 seconds, zero false “correct” claims in the curated math suite, zero stale tutor-ink commits. These are targets, not measured results.

Before any pilot with minors, agree on age, consent, access control, retention/deletion and human escalation. The demo uses synthetic or consenting adult data.
