# Socrates backend: the agents and the v2 API

The agent system behind the iPad whiteboard tutor. It speaks the **v2 contract** in [../Interface.md](../Interface.md) (HTTP + one WebSocket of sequenced events), runs only local models, and never lets a model decide whether something is correct: a tool does.

* **Decisions on the iPad contract and the DGX hand-over list:** [CONTRACT-RESOLUTION.md](CONTRACT-RESOLUTION.md).
* **Agent design, schemas, budgets, failure behaviour:** [AGENT-SYSTEM.md](AGENT-SYSTEM.md) (§16 is the authoritative spec; its §17 is the build log).

## Run it

```sh
cd backend
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest                       # 1,800+ tests, no model or network needed
OLLAMA_BASE_URL=http://127.0.0.1:11434 .venv/bin/python -m tutor.v2.main   # serves :8443
```

On the Spark, Ollama only accepts requests from the Spark itself, so the backend runs **there**. From a laptop, tunnel first: `ssh -N -L 127.0.0.1:11435:127.0.0.1:11434 <user>@<spark>` and use `OLLAMA_BASE_URL=http://127.0.0.1:11435`. Nothing about the Spark's address or login belongs in this repository.

## Configuration (environment variables only)

| Variable | Default | Meaning |
| --- | --- | --- |
| `TUTOR_LLM_API` | `ollama` | `ollama` (native `/api/chat`) or `openai` (vLLM, NIM, llama.cpp server) |
| `OLLAMA_BASE_URL` / `TUTOR_LLM_BASE_URL` | `http://127.0.0.1:11434` | Model server (the second wins) |
| `TUTOR_LLM_API_KEY` | unset | Bearer token for the model server, never logged |
| `TUTOR_MODEL_ROUTER` / `_REASONER` / `_VISION` | installed tags | Role → model tag. Only the vision tier reads images |
| `TUTOR_MAX_CONCURRENCY`, `TUTOR_SWAP_THRESHOLD_S` | `2`, `8` | Scheduler limits (measure on the Spark) |
| `TUTOR_KEEP_ALIVE`, `TUTOR_HTTP_TIMEOUT` | `5m`, `90` | Ollama keep-alive; HTTP read timeout in seconds |
| `TUTOR_TIMEOUT_SCALE` | `1` | Multiplies every node timeout (use more than 1 on a slow machine) |
| `TUTOR_WARM` | unset | `1` loads the models at start-up |
| `TUTOR_HOST`, `TUTOR_PORT` | `0.0.0.0`, `8443` | Listen address |
| `TUTOR_TLS_CERT`, `TUTOR_TLS_KEY` | unset | PEM files: serve HTTPS/WSS directly |
| `TUTOR_API_TOKEN` | unset | Require `Authorization: Bearer …` (WebSocket: `?token=`). Unset means open |
| `TUTOR_TTS_URL` | unset | OpenAI-style speech endpoint. Unset: captions and the iPad's own voice |
| `TUTOR_ASR_URL` | unset | OpenAI-style transcription endpoint. Unset: `/audio` answers 503 |
| `TUTOR_SCREEN_W`, `TUTOR_SCREEN_H` | `1210`, `834` | iPad screen in points, used for ink placement if the iPad does not send its board size |
| `TUTOR_LOG_DIR` | unset | Redacted JSONL trace (hashes and sizes only; `TUTOR_LOG_TEXT=1` adds text) |
| `TUTOR_RECORD_FILE` / `TUTOR_REPLAY_FILE` | unset | Record model calls, or answer from a recording (labelled "replayed" in `/v2/health`) |

## Layout

```
tutor/
  v2/          the iPad API: wire format, sessions + replay, turns and acks, the Socratic service, layout/ink/scene mapping
  agents/      orchestrator, solver, verifier, lesson designer, board director, animator, quiz designer, judge, hint,
               guard, Q&A, vision reader, summariser
  tools/       safe maths parser + SymPy worker, step checker, units (pint), chemistry balance, Python sandbox
  llm/         Ollama and OpenAI-compatible clients, token budget, structured calls, scheduler, record/replay
  schemas/     pydantic models for every agent output
  scenes/ board/ speech/ media/ obs/ state/ runtime/   scene templates, board cues and strokes, TTS/ASR clients,
                                                       PNG/WAV guards, tracing, memory slices, the graph runtime
prompts/       one versioned prompt per agent (each stays under 900 tokens)
evals/         the 20-question golden set and the quality-gate runner
ops/           purge-logs.sh
tests/         scripted-model tests, the iPad's fixtures in tests/fixtures/v2
```

## What the tests prove, and what they cannot

They drive the real HTTP and WebSocket API with a scripted model (`FakeLLM`), a scripted iPad that acknowledges ink, and the iPad's own JSON fixtures. They prove the protocol, the lesson logic, the maths checker and the failure behaviour. They **cannot** show model quality, latency, token usage on the real tokenizer, speech, or behaviour on the DGX: see section 4 of CONTRACT-RESOLUTION.md.

Live checks (need a model server, skipped by default): `TUTOR_LIVE=1 TUTOR_LIVE_MODEL=<tag> .venv/bin/python -m pytest tests/test_live_schemas.py`, and `python -m evals.run_eval --mode live`.
