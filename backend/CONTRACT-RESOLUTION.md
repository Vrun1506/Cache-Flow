# Contract resolution: backend answers to `ios/CONTRACT-QUESTIONS.md`, and what the DGX still has to prove

**Status.** The backend now speaks the v2 contract in [Interface.md](../Interface.md) and the iPad's own types and fixtures (branch `ios/v2-migration`, `ios/CONTRACT-QUESTIONS.md`). Every answer below is implemented and covered by a test that drives the real HTTP and WebSocket API with a scripted model. **Nothing here has run against the DGX Spark, a real model, real speech, or the real iPad.** Section 4 is the hand-over list for the DGX owner.

How to read the tests: `backend/tests/test_v2_contract.py` loads the iPad's fixtures (copied to `backend/tests/fixtures/v2/`) and checks every server event against a Python mirror of the Swift decoders. `test_v2_flow.py`, `test_v2_inputs.py` and `test_v2_first_fixture.py` drive the API end to end, including the whole "first fixture" of Interface.md.

---

## 1. The six blocking questions

| # | Question | Decision (implemented) | Test |
| --- | --- | --- | --- |
| 1 | Reconnect ordering | **Accepted as the mock does it.** The server replays missed durable events with their **original `seq`** and `"replayed": true`, strictly after the cursor (no overlap needed), then sends a **fresh `state`** with a new, higher `seq`. Speech and `agent_trace` are never replayed, and a replayed `tutor_turn` is marked so the client suppresses its audio. | `test_reconnect_replays_durable_events_with_original_seq_then_a_fresh_state` |
| 2 | Cursor | `GET /v2/sessions/{id}/events?after_seq=N` is the resume parameter. With **no cursor the server sends only a snapshot** (`state`). | `test_connecting_without_a_cursor_sends_only_a_snapshot` |
| 3 | Cancellation channel | **Both work; the socket message is canonical.** WSS `cancel_turn {turn_id, request_id}` and `POST /intent {action:"cancel", turn_id?}` (no `turn_id` cancels the running turn). The server always answers with the additive **`turn_cancelled {reason}`** event as the last event of that turn, then a `state`. Reasons: `student_cancelled`, `superseded` (a new intent), `deleted`. A cancelled turn's later events are dropped server-side. | `test_cancel_over_the_socket_*`, `test_cancel_over_http_*`, `test_a_new_intent_supersedes_the_running_turn` |
| 4 | Check = board upload + intent | **`/board` only stores the snapshot** (it returns `{request_id}`); it produces no stream event. `check`, `hint`, `explain_step`, `draw` and `show_answer` read the stored snapshot for the intent's `board_revision`. Lines are read once per distinct pixel crop and cached. | `test_a_line_is_read_once_and_reused` |
| 5 | Board revision semantics | **Confirmed.** The revision counts edits to the student's board; tutor ink never bumps it. Every event of a turn **echoes the revision of the request that started it**. A request below the latest revision gets **409 `{latest_board_revision}`**. The `board_revision` on every client message (including acks) updates the server's view. A stale ink or scene ack (`stale_revision`) makes the server **re-issue once**, with a new id and the latest revision. No ack within 4 s means no commit. | `test_stale_board_revision_is_a_409_with_the_latest`, `test_stale_ink_is_reissued_once_with_a_new_id`, `test_missing_ack_means_no_commit_and_the_turn_still_ends` |
| 6 | Authentication | **Optional bearer token plus optional TLS, default open.** `TUTOR_API_TOKEN` enforces `Authorization: Bearer …` on HTTP (and `?token=` on the WebSocket, because `URLSessionWebSocketTask(with: URL)` cannot set headers). `/v2/health` stays open. `TUTOR_TLS_CERT` and `TUTOR_TLS_KEY` make uvicorn serve HTTPS/WSS directly on `:8443`. `X-Socrates-Protocol` other than `2` gets **426**. **Recommendation for the hackathon:** private network (Tailscale or LAN), TLS on, no token, so the iPad needs no change. | `test_protocol_header_and_token` |

---

## 2. The shapes the iPad chose: confirmed, with the exact backend behaviour

* **Envelope and client messages:** exactly as proposed. Client messages are validated (`v == 2`, matching `session_id`); unknown or malformed messages are ignored, never fatal.
* **`state`** is sent on connect and on **every phase change**. Phases used: `idle`, `reading`, `confirm`, `ready`, `tutoring`, `student_turn`, `independent_check`, `complete`. `problem.item` is `lesson` or `independent_check`. `subject` is `algebra` for maths and the orchestrator's domain otherwise (`physics`, `chemistry`, `biology`, `cs`, `humanities`, `other`).
* **`read_back`:** a handwritten question **always** asks for confirmation; a board line only when ambiguous; a transcript is ambiguous on low confidence, empty text or homophones (root/route, to/two…). When there are alternatives, **the main reading is listed first** in `alternatives`, as in the mock. `read_back_confirm.text` is the pick or correction; a confirmed transcript is treated as an `ask`. Declining a question read-back returns to `idle`.
* **`evidence`:** every field is filled. **`label` is `verified` only when the maths checker decided** (`verdict != unknown`, subject `algebra`); everything else is `not_verified`, and an `unknown` verdict can never be labelled `verified` (enforced in `wire.evidence_payload`). `source_ids` is always `[]` (there is no retrieval yet). `independent` is true only for the transfer item answered without help. One **extra reason code**, `not_equivalent` (a plainly wrong step that is not a sign slip), is open vocabulary on the iPad; please confirm the UI copes.
* **`tutor_turn`:** moves used are `ask`, `hint`, `explain`, `show_answer`, `draw`, `clarify`, `recap` (`write_step` and `hint` ink arrive as ink events after the caption). Several `tutor_turn`s may share one `turn_id` (Show the answer, then the transfer question). `audio_url` is `/v2/sessions/{id}/audio/{name}.wav` (resolve against the profile).
* **Typed replies.** The iPad sends typed lesson replies as `ask`. The server answers the pending check when the text is **not a question** (no `?` and no leading question word), and treats it as a follow-up otherwise. `quiz_answer` works for the pending check **and** the independent item. *Request to the iPad:* send `quiz_answer` whenever the last tutor move was `ask` and a check is pending, so the server never has to guess.
* **Ink:** `tutor_ink_preview` → wait for `tutor_ink_ack` → `tutor_ink_commit`. Styles `write`, `highlight`, `circle`. Strokes are board-space points, ≤ 256 strokes, ≤ 512 points per stroke, formula ≤ 200 characters, and every ink passes a Python port of `Bounds.isValid` before it is sent. A write without Hershey strokes falls back to a `formula`.
* **Scenes:** `scene_preview` / `scene_commit` with only the approved kinds, ≤ 64 primitives, ≤ 1024 points, text ≤ 80, all points inside the region. The preview is the **final frame**: the internal animation timeline is dropped because the v2 scene has no timeline. *Open product question below.*
* **`audio_chunk`** is used by `explain_step` (two chunks). `audio_done` is accepted and ignored: the server never waits for playback.
* **`skills`:** sent after Show the answer and after each finished item. Helped work never raises `level`; an unhelped correct answer does.
* **`agent_trace`:** real calls only (`solve`, `verify`, `repair`, `lesson_designer`, `board_director`, `animator`, `qa`, `math`) with latency; no student text.
* **`error` codes:** `no_problem`, `unsupported_action`, `empty_text`, `refused`, `redirected`, `unreadable`, `setup_failed`, `turn_failed`, `board_full`, plus HTTP codes below.
* **HTTP:** `POST /v2/sessions` (`{preset_id?, locale}`), `/question` (multipart `text` or `image`), `/board` (multipart `meta` + `image`), `/audio` (multipart, returns `{request_id, transcript, ambiguous}`), `/intent`, `GET /summary` (`{items, skills, next_step}`), `DELETE`, `GET /v2/health`. Status codes: **409** stale revision, **413** too large (image over 4 MB, audio over 2 MB, more than 40 lines, question over 2000 characters), **426** wrong protocol, **401** bad token, **404** unknown session or clip, **422** malformed input, **503** `asr_unavailable`. Reusing a `request_id` returns the first response and does not run the action again.
* **Presets:** `quad_5_6`, `quad_7_12`, `lin_2_3_11`, `phys_forces_box` start in `ready` with a confirmed problem and need no model call to begin.

### Behaviour the iPad should know about

1. **Tutor me** solves the problem in the background as a hidden reference (orchestrate, solve, verify, one repair), then **asks** the first question. It does not show the answer. `agent_trace` events stream while it works.
2. **Check** reads the newest line (vision), verifies it against the **original problem** (never against the previous line, so a correction is not compared with the wrong line it replaces), then: valid → next question; invalid → hint ladder plus a circle around the line; partial root → asks for the other root; unknown/ambiguous → "not graded". **Non-maths subjects are never graded**: the iPad gets `unknown / unsupported / not_verified`.
3. **Hint ladder** (one rule everywhere): miss 1 a question, miss 2 a narrower hint, miss 3 the tutor writes the step and moves on, marked assisted. A leak guard checks hints 1 and 2 against the answer and falls back to a templated hint.
4. **Show the answer** is explicit and recorded (`assisted: true`), writes the steps on the board, then offers **one fresh problem of the same kind with no hints**. That item counts as independent only if answered without help.
5. **Ink placement is server-side.** The server needs the board size. `POST /v2/sessions` accepts an optional `"board": {"w": …, "h": …}` (the board area in points). Without it the server assumes an 11-inch iPad Pro in landscape (`TUTOR_SCREEN_W/H`, default 1210 × 834, board 1210 × 638, tutor strip 500 wide on the right with five slots). *Request to the iPad: send it.*
6. **No TTS available:** `audio_url` is omitted. *The iPad must speak the caption with its on-device voice.* Please confirm it does.

---

## 3. Decisions that need a human

1. **`final-plan.md` on `main` disagrees with the agent system.** The teammate's commit `8d85d02` rewrote it around one vLLM model, NemoClaw/OpenShell and NeMo Guardrails, and removed Ollama, NeMo Agent Toolkit and the three-model scheduler from the first demo. The backend here follows the earlier plan (preserved as [AGENT-SYSTEM.md](AGENT-SYSTEM.md)): its own agent runtime, Ollama by default. **A vLLM/OpenAI-compatible client is built behind the same interface** (`TUTOR_LLM_API=openai`), so the serving choice is now a configuration decision, but NemoClaw, NeMo Guardrails and NAT are **not integrated**. Someone has to decide whether they are required for judging.
2. **Animation.** The v2 scene is static. The backend computes real motion (projectile paths, algorithm steps, function morphs) and throws it away. Either extend `scene_preview` with an optional `timeline`, or accept static diagrams.
3. **Tutor me vs Calculate.** The product brief described a full lesson from one Calculate press. The v2 contract is a Socratic loop. The full-lesson pipeline (`LocalRuntime.run_solve_teach`) still exists and is tested but is not reachable from the v2 API. A `teach` intent could expose it.

---

## 4. Blockers for the DGX Spark (hand-over list)

Nothing in this section can be done from a laptop. Each item says how to test it.

### 4.1 Serve and measure the models
1. **Choose the serving API.** Ollama (organiser-supplied, port 11434, only reachable from the Spark itself) or vLLM. Set `TUTOR_LLM_API=ollama|openai`, `OLLAMA_BASE_URL` (or `TUTOR_LLM_BASE_URL`), and the model tags (`TUTOR_MODEL_ROUTER/REASONER/VISION`). The backend must run **on the Spark** (or through the SSH tunnel on 11435).
2. **Structured output.** Run `TUTOR_LIVE=1 TUTOR_LIVE_MODEL=<tag> .venv/bin/python -m pytest tests/test_live_schemas.py` for **each** model. It posts all 11 agent schemas to the server's grammar builder. A regex `pattern` is already excluded (it made Ollama answer "failed to parse grammar"). On vLLM check whether `response_format` or `guided_json` is honoured.
3. **`think`.** Does `format` still hold with `think: true` on each model? Which models report the `thinking` capability (`/api/show`)? Otherwise the Solver runs with `think: false`.
4. **Swap time and concurrency.** Measure model swap time and throughput at 1, 2 and 4 concurrent calls, then set `TUTOR_MAX_CONCURRENCY` and `TUTOR_SWAP_THRESHOLD_S`. Default is two tiers (router work runs on the reasoner model); `/health` exposes scheduler stats.
5. **Vision.** Qwen is the only image reader. The reader batches **6 lines per request** and budgets 1,000 tokens per image. Check real image-token cost, multi-image support and handwriting accuracy on about 10 messy crops.
6. **Token budget.** The 3-characters-per-token estimate has never been compared with the real tokenizer. Run a lesson and check the `truncated` flags and `prompt_eval_count` in the trace (`TUTOR_LOG_DIR`).
7. **Timeouts.** Node timeouts (orchestrate 20 s, solve 60 s, verify 30 s, lesson 40 s, board 25 s) are guesses sized for the Spark. Measure, then set `TUTOR_TIMEOUT_SCALE` or edit `tutor/runtime/graphs.py`.
8. **Prompt tuning on the Nemotron models.** The 4B model over-split a two-step problem into six steps. Run `python -m evals.run_eval --mode live` (20 golden questions, the six quality gates of AGENT-SYSTEM.md §15.8) and tune the prompts in `backend/prompts/`.

### 4.2 Speech
9. **TTS.** `HttpTTS` assumes an OpenAI-style `POST /v1/audio/speech` with `{input, voice, response_format: "wav"}`. Unverified against Magpie or Kokoro. Set `TUTOR_TTS_URL`. Clip concatenation and durations assume 16-bit PCM WAV.
10. **ASR.** `HttpASR` assumes `POST /v1/audio/transcriptions` (multipart) as the Parakeet NIM exposes it. Unverified. Set `TUTOR_ASR_URL`. Without it `/audio` answers 503 and `/v2/health` reports `asr` down.
11. **Co-residency** of the model, TTS and ASR in unified memory, and whether the organisers' "one model at a time" rule covers the speech services.

### 4.3 Network and operations
12. **TLS and identity.** The iPad profiles use `https://tutor-spark:8443`. Provide a certificate the iPad trusts (`TUTOR_TLS_CERT/KEY`, or terminate TLS in a reverse proxy). Decide on Tailscale and MagicDNS. Venue Wi-Fi often isolates clients.
13. **Run it as a service** (systemd or docker compose, ARM64): `python -m tutor.v2.main`. No unit file or Dockerfile is written. Python 3.13 is what the code was tested on. Wheels for `pillow`, `pint`, `sympy`, `Hershey-Fonts` and `pydantic` must exist for aarch64.
14. **Sandbox.** Model-written Python runs in a separate process with rlimits and a scrubbed environment. On macOS `RLIMIT_AS` is not enforced; on Linux it is applied but **untested**. It is defence in depth, not a security boundary: run it in a container with no network (`tutor.tools.sandbox.docker_run_command()` builds the command).
15. **Math worker.** It uses `multiprocessing` spawn with a 2 s kill timeout. Confirm it starts on aarch64 under the service manager (its entry module must be importable).
16. **Demo insurance.** Record a good run with `TUTOR_RECORD_FILE=recordings/demo.jsonl`, then serve it with `TUTOR_REPLAY_FILE=…`. `/v2/health` labels it "replayed". Disclose it in the demo.
17. **Logs.** `TUTOR_LOG_DIR` writes redacted JSONL (hashes and sizes only, no student text unless `TUTOR_LOG_TEXT=1`). `ops/purge-logs.sh` deletes them (dry run by default).

### 4.4 Not integrated
18. **NemoClaw/OpenShell, NeMo Guardrails, NeMo Agent Toolkit (NAT).** The agents run on an in-process runtime. NAT needs the 90-minute spike of AGENT-SYSTEM.md §16.13 on the Spark; list it in the submission only if it is really used. Guardrails today is a deterministic rule filter, labelled as such in `/v2/health`.
19. **Retrieval and `supported_by_source`** evidence: there are no sources, so the label is never used.
20. **Outside the checkers:** complex roots, geometry, statistics and chemistry stoichiometry return `unknown`, never `valid`. Chemistry is verified only for **balanced reaction equations** (exact atom counts); other chemistry steps stay `unverified`.

### 4.5 iPad-side changes the backend relies on (no DGX needed)
* Send `board: {w, h}` in `POST /v2/sessions`.
* Send `quiz_answer` for the pending check (see section 2).
* Speak the caption with the on-device voice when a turn has no `audio_url`.
* Show the `agent_trace` timeline, and accept the extra reason code `not_equivalent`.
* The health component named `vllm` reports the **LLM server** whichever it is (`detail` says "ollama" or "vllm"). Rename it to `llm` on both sides if you prefer.
