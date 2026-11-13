# Socrates implementation contracts

**Status:** target v2 contracts. [Interface.md](Interface.md) explains the migration from the committed Swift client. These contracts are not implemented by the current mock. Keep actual code and this document synchronized in a future change.

## Domain contracts

Recognition returns Candidate {text, regions, ambiguity, source_revision, model_revision}. If an uncertain sign, exponent or symbol affects a verdict, require student confirmation. The candidate is never silently treated as ground truth.

A subject verifier accepts {subject, problem, prior_step, candidate_step, claimed_goal, budget} and returns Evidence {verdict: valid|invalid|partial|unknown, reason_code, normalized_step, expected_relation, first_error_region?, source_ids[], verifier_version}. The algebra strategy uses a whitelisted expression grammar and SymPy with time/size limits. It must distinguish a valid partial root from a complete solution. It does not execute arbitrary Python or use unrestricted symbolic parsing.

A Socratic turn accepts {learner_intent, confirmed_question, confirmed_work, evidence, assistance_history, retrieved_sources?} and returns exactly one move {ask|hint|demonstrate_one_step|clarify|recap}, caption, optional spoken text, optional highlight, optional tutor-ink intent and cited evidence IDs. A correctness statement must cite a valid verifier result. A subject without a verifier can discuss evidence but must say when it cannot grade. Tutor text and digital writing must pass the same answer-leakage policy.

## Agent and tool boundary

NemoClaw runs a versioned Socratic skill against a Spark-local vLLM endpoint. OpenShell allows only local inference plus a local, authenticated Streamable HTTP MCP math service if the selected harness supports that route. Expose narrow tools such as check_math_step and retrieve_source; each has bounded JSON arguments, no filesystem paths, and a response with explicit reason/evidence. The agent does not get generic shell, code execution, unrestricted browsing, or direct database writes. The lesson service owns session state and verifies every returned tool result before committing a tutor turn.

NeMo Guardrails applies input, tool-call, tool-result and output checks. Pin the engine and use a startup assertion that required tool rails actually loaded; [NVIDIA warns that incompatible IORails configuration can fall back without tool rails](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/tool-calling). A failed rail, invalid result or unavailable verifier yields a clarification or “cannot check this yet” response. It cannot produce a confident correctness claim.

The first integration spike must prove that the NemoClaw agent can receive a request from the local session service, call the math MCP service, and return a structured turn within the latency budget. If this bridge fails, retain the same local vLLM/Guardrails/tool contracts and report NemoClaw as unintegrated; never label a direct Python call as a harness call.

## Digital ink

Tutor writing is a semantic intent {text_or_formula, placement_region, style, source_turn_id} plus a validated board-space preview. The iPad commits a separate tutor layer after a matching {session_id, board_revision, turn_id} check; it must not synthesize a fake physical-arm event. Bound points, strokes, glyphs, coordinates and animation duration. Preserve the student's Pencil drawing and undo history. If the board changed while the tutor was composing, discard the stale preview and re-evaluate. Speech, caption and ink share the turn ID so cancellation stops all three.

The current Swift app has arm-named Step/ArmJob events, any-input tutor phases and fallback ghost ink. Those are v1 compatibility surfaces, not the target implementation. Refactor them together with the mock, tests and [Interface.md](Interface.md). In particular, keep Pencil-only student capture while programmatically rendering tutor ink; do not switch the student's touches to tutor ownership.

## Speech

Current iOS records full 16 kHz mono WAV push-to-talk and plays a clip URL with on-device speech fallback. Keep that v1 path for the first real local demo. Evaluate local Nemotron ASR Streaming or Parakeet on Spark; the prior Parakeet TDT 0.6B v3 selection is full-utterance in NeMo-Speech.cpp and does not itself make the interaction streaming. Use local Magpie TTS streaming for faster first audio when co-residency passes; captions remain authoritative. Do not save microphone WAV beyond the active request. Cancellation invalidates queued clips and ink for the superseded turn.

## Knowledge ingestion

A teacher/operator imports approved pages locally. Parse/OCR runs only through a proven local adapter; attach source title, page, version and rights metadata. Chunk the text, compute local embeddings, optionally rerank locally, and return short cited passages with retrieval scores. Retrieved text is untrusted input and cannot issue agent commands. The tutor cites the source and states uncertainty when evidence is missing. Nemotron Parse is deferred on Spark until a supported local deployment is established.

## Lifecycle and verification

One session has monotonically increasing board and turn revisions. Requests include idempotency IDs; duplicate Check calls cannot duplicate an attempt or tutor ink. Reconnect replays problem, phase, latest board/turn and evidence summary, without replaying audio. Require session-scoped student/operator permissions; admin routes are authenticated, even on LAN. Keep audio and images ephemeral, store only consented minimal evidence, and offer deletion. Run: Swift package tests and simulator build; FastAPI contract and math suites; rail and sandbox deny fixtures; local model/speech smoke; a real offline iPad–Spark rehearsal. All are pending for v2.
