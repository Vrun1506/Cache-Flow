# Socrates: local whiteboard tutor — build handoff

**13 November 2026 · design only.** This is the detailed build sequence. [prd.md](prd.md) owns product scope, [Interface.md](Interface.md) owns the iPad protocol, [architecture.md](architecture.md) and [design.md](design.md) own component contracts, [decisions.md](decisions.md) records choices, and [status.md](status.md) records observed implementation. Reconcile any conflict before building. The robot, arm, Isaac and LeRobot path is retired.

## 1. Target experience and demo

A student writes a question or working in the SwiftUI/PencilKit app and taps **Tutor me** or **Check**. The Spark reads the current board and asks for confirmation when a sign, exponent or line is ambiguous. A Socratic tutor asks one question at a time, observes the student's next written or typed answer, checks what can be verified, and responds with a short caption and matching local voice. It can write a step, circle an error, draw a labelled diagram or plot, and explain each addition. **Show the answer** explicitly lets it write the exact answer and explanation; that help is recorded, then the learner gets a fresh related problem without hints. The summary distinguishes assisted progress from independent understanding.

The live, offline demo must show: a handwritten quadratic with an incorrect sign; a real agent-to-math-tool call; a correction and a valid partial root; agent writing and a question-specific visual; a spoken student follow-up through local ASR; local tutor speech and captions; the student requesting the exact answer; one unaided transfer item; and a second subject (for example physics) with an honest evidence label. “General purpose” means this same tutoring loop works across subjects. It does **not** mean all subjects can be graded automatically.

“Real time” means an interruptible turn after a tap or a completed-line pen-up pause: caption first, then speech and ink as ready. Do not stream every Pencil point to the model or keep the microphone always on. Measure p50/p95 time to first caption, audio and ink on the real devices before promising a latency.

## 2. Runtime choice

| Boundary | First-demo choice | Required proof |
| --- | --- | --- |
| Inference | **One local vLLM model on the DGX Spark**, initially [Nemotron 3 Nano Omni 30B A3B NVFP4](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4). Its NVIDIA model card supplies a Spark/ARM64 vLLM recipe for image, audio and text input with text/tool output. | Pin exact image, weights, parsers, context and memory after image, handwriting, text, structured-output and tool-call smoke tests on this Spark. Keep one model resident rather than swapping three models per turn. |
| Agent | **NemoClaw/OpenShell**, using the existing local vLLM endpoint, with a versioned Jane Street-derived Socratic skill. | Prove session API → harness → permitted math MCP tool → harness → session API, plus a denied unrelated tool. [Existing local vLLM routing](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/set-up-vllm) and [skill installation](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/reference/commands) are documented; the exact app bridge remains a Phase 0 test. |
| Gateway | FastAPI session service on the Spark. | Authenticated iPad HTTPS/WSS; request IDs, board revisions, sequencing, cancellation, evidence store and replay. The iPad cannot call the model, sandbox or tools directly. |
| Safety | NeMo Guardrails, OpenShell policy, server authorization and schemas. | Required input/tool/result/output rails active at startup; a denial blocks the affected action. [Guardrails supports local vLLM through an OpenAI-compatible endpoint](https://docs.nvidia.com/nemo/guardrails/latest/about-nemo-guardrails-library/supported-llms). |
| Math | Bounded parser and SymPy behind a narrow `check_math_step` MCP tool. | Step-type-aware verdict with evidence ID, including `partial_root`. The agent may request the tool but cannot override its result. |
| Speech | Local Magpie TTS output and [Parakeet 1.1B CTC English ASR NIM](https://docs.nvidia.com/nim/speech/26.10.0/reference/support-matrix/asr.html) for push-to-talk input on the Spark; matching iPad captions and local iPad voice fallback. | First audio/transcript latency, speech/caption identity, interruption and three-service co-residency. If the Parakeet NIM fails this Spark's load/quality gate, use Omni's local audio input and report Parakeet as unintegrated; spoken input remains required. |
| Visuals | Validated semantic board commands rendered by the iPad. | Separate tutor layer, revision-bound preview/commit, bounded diagrams/plots; no generated JavaScript or arbitrary Python in the iPad app. |

Ollama, NeMo Agent Toolkit (NAT) and a three-model swap scheduler are **removed from the first-demo path**. NAT may later help with evaluations, but duplicating the NemoClaw agent runtime creates another integration and failure boundary. Nemotron Parse, Retriever embedding/reranking and extra NIMs are conditional local features, only if they serve a measured need and fit the Spark memory budget. Do not count planned or imported tools as actual calls. If Omni fails the handwriting or tool-call gate, choose a tested Spark-local vLLM replacement behind the same adapter; record the tradeoff before changing this plan.

The previous plan's asserted 8,192-token cap, one-model rule, supplied Ollama and judging criteria are **unverified organiser claims** in this repo. Treat them as questions until an official source is recorded. Independently of those claims, bound context, output, images, tools, sessions and co-resident memory.

## 3. Jane Street skill and teaching policy

The [Jane Street `socratic-tutor` skill](https://github.com/janestreet/agent_skills/blob/master/socratic-tutor/SKILL.md) teaches via a 3–5 question diagnostic, a 0–5 subskill map, one question at a time, short explanations after struggle, independent checks, spaced review and a “just tell me” override. The repository is [MIT licensed](https://github.com/janestreet/agent_skills/blob/master/LICENSE.md). For implementation: pin an upstream commit, retain attribution/license, install a versioned adaptation into the NemoClaw sandbox, and prove the skill is **active in a real turn**, not merely present on disk.

The app adaptation adds board/evidence events and a **single-problem coaching mode**. **Tutor me** explicitly activates it. A broad-topic course follows the original 3–5 question diagnostic; for one problem, the student's written attempt plus one or two probes place the relevant subskills and the UI labels this shortened placement. **Quick answer** does not silently start a course. **Show the answer** uses the skill's “just tell me” override for this problem, records assistance, explains the answer and offers an independent follow-up. This is an attributed adaptation, not a claim that the upstream skill already specifies whiteboard coaching.

The skill is teaching policy, not a grader. It selects exactly one move per turn: `ask`, `hint`, `explain`, `write_step`, `draw`, `show_answer`, `clarify` or `recap`. The gateway validates every proposed caption, evidence citation, tool result and board command. Helped work cannot earn an independent skill level. Levels 4–5 need repeated independent checks, including a fresh item; the demo may end below mastery.

## 4. Concrete demo turn

1. The learner writes `x² + 5x + 6 = 0`, taps **Tutor me**, and confirms a local vision read-back if a symbol is uncertain.
2. The skill asks one focused question; the caption appears before Magpie audio. Speech never interrupts while the Pencil is down.
3. The learner writes `(x − 2)(x − 3) = 0` and taps **Check**. The agent calls `check_math_step`; the tool returns `invalid/sign_mismatch` with a stable evidence ID. The tutor highlights the middle-term signs and asks one narrower question.
4. The learner writes `(x + 2)(x + 3) = 0`; the tool proves factorization. If the next line is `x = −2`, the verdict is `partial/partial_root`, and the tutor asks for the other branch.
5. The learner asks “Why does the other root matter?” through push-to-talk; local ASR returns a transcript for confirmation. **Draw it** adds a bounded diagram or plot to the tutor layer. **Show the answer** may write `{−2, −3}` and explain it, marking this item assisted. A fresh unassisted item follows.
6. A second subject uses the same agent/board/speech loop. A claim without a registered verifier is labelled “supported by source” or “not verified,” never “mathematically verified.”

## 5. Agent, tool and evidence contracts

One tutor agent owns the conversation. Each call receives a compact state slice: confirmed question, board revision, recent attempts, help used, relevant skill map and evidence IDs. It emits bounded structured turns; the gateway owns sequencing, persistence and presentation. The agent may **actually call** `check_math_step` through NemoClaw/OpenShell. This MCP tool accepts bounded `{problem, previous_step?, candidate_step, step_kind, assumptions, board_revision}` and returns `{verdict, reason_code, normalized_step, proof_or_counterexample?, evidence_id, board_revision, verifier_version}`. The gateway rejects stale or malformed evidence. An optional `retrieve_source` tool searches only an approved local corpus. The tutor has no generic shell, arbitrary code execution, open web browsing, unrestricted files or direct database writes.

For other subjects, the agent can explain, question and draw; it can cite teacher-approved local sources if available. It must state uncertainty when evidence is absent. A critic-model opinion is not a proof. New subject verifiers implement the same evidence contract through a registered Strategy; model, ASR, TTS and renderer providers sit behind Adapters. The session API remains the Facade and revision-bound actions are idempotent Commands. See [architecture.md](architecture.md) and [design.md](design.md) for the component review.

## 6. Math verifier correction

**Do not feed untrusted student or model text into `sympy.parsing.sympy_parser.parse_expr`.** [SymPy says it uses `eval`](https://docs.sympy.org/latest/modules/parsing.html). Build a small tokenizer and whitelist grammar for supported expressions/equations, then construct SymPy objects from that parse tree. Bound bytes, tokens, depth, exponent size, numeric magnitude, variables and runtime. Run symbolic work in a process with CPU/memory/time limits and no file or network reach. Malformed, unsupported, ambiguous or timed-out input is `unknown`, never automatically valid. Numeric sample points can find counterexamples but cannot establish exact equivalence.

The caller declares the **step kind**; comparing solution sets is only appropriate for a claimed equivalent-equation step:

| Step kind | Verification | Example |
| --- | --- | --- |
| `equivalent_equation` | Compare solution sets under stated domain/assumptions, checking excluded values and extraneous roots. | `x²+5x+6=0` ↔ `(x+2)(x+3)=0`: valid. |
| `factorization` / `expression_identity` | Prove an exact symbolic identity. | `(x−2)(x−3)` for `x²+5x+6`: invalid `sign_mismatch`. |
| `candidate_root` | Substitute into the **original** equation, then compare with the complete root set. | `x=−2`: valid root, **partial** solution. |
| `solution_set` | Check each candidate root and completeness. | `{−2,−3}` complete; `{−2}` partial; an extraneous root invalid. |
| `numeric_result` | Recompute with declared units/tolerances where relevant. | Inconsistent units invalid; unsupported assumptions unknown. |

Verdicts: `valid`, `invalid`, `partial`, `unknown`. Reason codes include `sign_mismatch`, `partial_root`, `extraneous_root`, `domain_changed`, `ambiguous_input`, `unsupported` and `timeout`. The tool returns the original problem and board revision. Fixtures must cover correct/incorrect factors, partial/complete roots, division by a possibly zero expression, squaring both sides, domain restrictions, parser attacks, malformed input and timeout. A failed or absent verifier blocks a confident correctness claim.

## 7. iPad protocol and visuals

Use **v2** in [Interface.md](Interface.md) and the repository's pinned Pydantic v2 schemas. Do not add parallel `/session/{id}/solve` endpoints or repurpose v1 arm events. `/question` accepts typed text or bounded question image; `/board` accepts a revisioned snapshot/lines; `/intent` accepts `tutor`, `check`, `hint`, `explain_step`, `draw`, `show_answer`, `ask`, `cancel`, `quiz_answer`; `/audio` accepts a bounded utterance. Every action carries `request_id` and `board_revision`; stale requests conflict.

Every WSS event carries `session_id`, `board_revision`, `turn_id`, monotonic `seq` and run/action ID. Keep `state`, `read_back`, `evidence`, `tutor_turn`, `tutor_ink_preview`, `tutor_ink_commit`, `skills`, `error`; add versioned `agent_trace`, `scene_preview`, `scene_commit` and optional `audio_chunk`. A scene is bounded semantic JSON rendered by the iPad from approved primitives, not executable model code. Tutor writing, diagram, caption and speech share the same turn and revision. The iPad commits ink/scene only after placement acknowledgement and revision match. Pen movement, cancellation or a new question invalidates stale previews and queued speech. Reconnect replays durable state/evidence/board commands from a sequence cursor, never spoken clips.

## 8. Priorities and build gates

**P0 required:** handwritten problem/work with ambiguity confirmation; active Jane Street-derived Socratic skill; actual NemoClaw/OpenShell math-tool call; corrected verifier; Spark-local vLLM Nemotron; active Guardrails; captions, local Magpie speech and one push-to-talk local ASR turn; tutor writing/highlight and one labelled visual; Show the answer; a fresh independent check; v2 cancellation/replay; an offline run. Include one second-domain teaching turn with an honest evidence label.

**P1:** automatic pen-up coaching, more scene templates, curated local retrieval, stronger domain verifiers and timeline polish. **P2:** full-duplex barge-in voice, multiple concurrent students, complex animations, extra model tiers, NAT evaluation and optional local Parse/Retriever NIM after a measured benefit.

| Gate | Work | Evidence to hand off |
| --- | --- | --- |
| 0 | Inventory Spark and smoke-test pinned ARM64 vLLM + Omni, Magpie and local Parakeet co-residency, NemoClaw bridge and skill activation. | Exact revisions, memory, image/text/tool and speech latency, real skill turn and denied tool. |
| 1 | Freeze v2 JSON fixtures; implement bounded parser, step-aware SymPy tool and evidence schema. | Security, sign, partial-root, domain and Swift/backend contract fixtures. |
| 2 | Build gateway, harness/tool integration and required Guardrails. | Real agent→MCP→evidence→tutor trace; failed-rail and stale-revision tests. |
| 3 | Migrate Swift v1 arm shapes to tutor ink, diagrams, captions and interruptible audio. | Package tests, simulator build and real iPad turn. |
| 4 | Add second domain, independent check, local summary and offline rehearsal. | Repeated full runs, p50/p95 timings, failure recovery and actual component trace. |

## 9. Acceptance and handoff

The student can start from handwriting, answer a question at a time by writing, typing or push-to-talk, request a worked answer, hear and see matching explanations, receive a diagram and attempt a fresh item unaided. The sign-error fixture is invalid, corrected factors valid, one root partial, unsupported input unknown. Duplicate requests do not duplicate ink; stale revisions cannot commit it; reconnect restores commands without replaying voice; cancellation stops old audio and ink. A guardrail denial and OpenShell denial are observable. Disconnect the internet after provisioning and complete the lesson. Record actual p50/p95 first caption/transcript/audio/ink and Spark memory with required services loaded. Do not claim deployed or real-time performance from document text or a mock.

The next coding agent should read [AGENTS.md](AGENTS.md), [prd.md](prd.md), [Interface.md](Interface.md), [architecture.md](architecture.md), [design.md](design.md) and [status.md](status.md), claim affected paths on the agent board, and start at Gate 0. No product integration is implemented by this document change.
