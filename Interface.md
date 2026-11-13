# iPad ↔ Spark interface

**Status:** proposed v2 contract, not implemented. [prd.md](prd.md) owns product scope and [final-plan.md](final-plan.md) is the build handoff. The committed Swift client and mock still implement robot-era v1 shapes in ios/Packages/CacheFlowKit/Sources/CacheFlowKit/Protocol/Events.swift and Net/TutorAPI.swift. A backend does not yet exist. Change this document, both Swift contracts, MockServer and tests together; until then use v1 only for the mock.

## Boundary and invariants

The iPad talks only to the Spark session API over authenticated local HTTPS/WSS. The API calls local vLLM, NemoClaw/OpenShell, math, guardrails and speech services. No hosted inference. Student and tutor ink are separate layers. Every upload, event and annotation carries session_id and a monotonically increasing board_revision. Every requested tutor action carries request_id for idempotency. Server events carry a turn_id, run_id and monotonically increasing sequence so reconnect and cancellation are deterministic. Coordinate units are board-space points; source regions are snapshots of that board revision. Unknown additive event types may be ignored; incompatible versions cause an explicit upgrade message.

## v2 HTTP

| Method | Body | Response |
| --- | --- | --- |
| POST /v2/sessions | optional preset ID, locale | session_id, protocol_version, board_revision |
| POST /v2/sessions/{id}/question | bounded image/png or typed text, board_revision, request_id | accepted request_id; candidate read-back on stream |
| POST /v2/sessions/{id}/board | bounded PNG plus line boxes or stroke snapshot, board_revision, request_id | accepted request_id; result on stream |
| POST /v2/sessions/{id}/audio | 16 kHz mono WAV, board_revision, request_id | transcript candidate and request_id; an utterance still needs explicit confirmation when ambiguous |
| POST /v2/sessions/{id}/intent | action tutor, check, hint, explain_step, draw, show_answer, ask, cancel or quiz_answer; board_revision, request_id, optional text | accepted request_id; result on stream |
| GET /v2/sessions/{id}/summary | no student-supplied data | authorized evidence and transfer result |
| DELETE /v2/sessions/{id} | operator authorization | deletion receipt |
| GET /v2/health | no student data | availability and revisions of local components |

Bound image/audio bytes, dimensions, duration, line count and tool budgets server-side. A reused request_id returns the first result, and a stale board_revision returns a conflict with the latest revision. Audio, transcription and vision results must never be silently substituted for confirmed student input. `tutor` explicitly activates the Socratic skill; `show_answer` records assistance. A pen-up Auto coach mode, if enabled later, submits the same revisioned `check` intent after a completed-line pause.

## v2 stream

GET /v2/sessions/{id}/events is a WSS channel carrying sequenced JSON envelopes. On connect, the server sends a state snapshot, not historical spoken clips. A replay cursor may resume later events. The server sends:

| Event | Required payload | Behavior |
| --- | --- | --- |
| state | phase, problem, board_revision, latest_turn_id, skills | Replace view state on connect/reconnect. |
| read_back | request_id, candidate text, ambiguity, line regions | Student can accept or correct the reading. |
| evidence | request_id, board_revision, step_kind, verdict, reason_code, evidence_id, verifier_version, optional highlight/source IDs | Show what was checked; “unknown” and a partial root are explicit. |
| tutor_turn | turn_id, board_revision, move, caption, optional audio URL | Show caption immediately, play matching local audio. `show_answer` is an explicit, assisted move. |
| tutor_ink_preview | turn_id, board_revision, bounded strokes or typeset formula, region | Show ghost ink in tutor layer only. |
| tutor_ink_commit | turn_id, board_revision | Commit only after local revision match and placement acknowledgement. |
| scene_preview / scene_commit | turn_id, board_revision, bounded semantic primitives and placement | Render only approved diagram/plot types in the tutor layer; no executable model code. |
| audio_chunk | turn_id, chunk_index, caption_span, local audio reference | Optional streamed speech; discard chunks for cancelled or stale turns. |
| agent_trace | turn_id, component, action, outcome, latency, evidence_id? | Show only actual local calls; no raw student ink/audio in the trace. |
| skills | evidence-backed levels and assistance flags | Distinguish independent from helped work. |
| error | request_id, code, retryable | Plain-language recovery action. |

The iPad sends read_back_confirm, audio_done, tutor_ink_ack, scene_ack and cancel_turn, each with turn/request IDs. The server can send a new turn only after a cancellation boundary; old audio, ink and scenes are ignored. Pen movement or a changed board revision invalidates pending tutor previews. Reconnect replays durable board/scene commands from a sequence cursor, never speech clips. No arm, arm_touches, calibration or motor event exists in v2.

## Migration from committed v1

The existing iPad already supports question PNG, line uploads, push-to-talk WAV, captions, local speech fallback, WebSocket event decoding and mock modes. Preserve those reusable components. A coordinated Swift change must replace ArmJob/arm state with tutor-ink and scene events, stop using any-input/tutor-ink phases for student touches, add board revisions and action IDs, and update the in-process mock. The current fixed tutor strip may be retained as the first digital writing region; it no longer needs physical calibration and can later resize. Remove or hide calibration and force-arm controls. Version the API as v2 rather than redefining the existing v1 event names in place.

## First fixture

Student writes x² + 5x + 6 = 0, confirms read-back and activates `tutor`; writes (x − 2)(x − 3) = 0; the agent calls the math MCP tool; verifier returns `invalid/sign_mismatch`; tutor asks about the middle-term sign, speaks it and highlights the signs; student corrects to (x + 2)(x + 3) = 0 and then x = −2, which returns `partial/partial_root`; tutor asks for the other root. `show_answer` writes and explains the complete answer, records assistance, and offers one independent transfer item. A second fixture checks a labelled non-math diagram; separate fixtures check ambiguous handwriting as ungraded, stale ink/scene rejection, a guardrail denial, a lost audio clip with caption fallback, and reconnect without audio replay.

## Open integration facts

The backend owner must prove the local NemoClaw ↔ session API bridge, active skill, exact local vLLM model/parser combination, a local push-to-talk ASR path and co-resident memory. The iOS owner must prove separate layers, cancellation and v2 replay on a real device. The operator must set a trusted local HTTPS identity and session/role mechanism. See [status.md](status.md).
