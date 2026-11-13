# Socratic Robot Tutor — MVP Spec

Nov 13, 2026 · @Varun Nayak

## 1. Product and key decisions

**What it is.** A physical maths tutor that sits on the student's desk. The student poses a problem on an iPad, either handwritten or picked from a list. The tutor can then:

- **Teach** it: a full walkthrough, explaining every step out loud while an SO-101 robot arm writes the working onto the iPad.
- **Guide** the student to solve it themselves, Socratic-style: it reads their handwriting and catches the exact line where they went wrong.

It builds on Jane Street's socratic-tutor skill, extended with a full walkthrough mode. **Every model runs on the DGX Spark.** There is no cloud inference, and no audio or handwriting leaves your own hardware.

**Design principle: SymPy decides the maths, a state machine decides what happens next, and the LLM only explains.** This split means the arm can never write a wrong step by accident (except in the deliberate twist), and the LLM can't leak answers in Guide mode.

| Area | Decision | Why |
| --- | --- | --- |
| Inference | All local on the DGX Spark: Ollama for the LLM and vision model, NeMo containers for speech | NVIDIA's own-your-inference requirement, on one box |
| Tutor brain | A Nemotron Nano-class model, Jane Street's socratic-tutor SKILL.md, and our addendum for Teach mode | Small and fast enough for voice turns |
| Orchestration | Plain FastAPI service, with no NemoClaw or agent framework | Fewest moving parts, so you can debug it yourselves |
| Maths engine | SymPy generates the correct solution steps and checks every student line | Deterministic, testable, never wrong |
| Reading handwriting | The iPad splits strokes into lines, a vision model transcribes each line, and the app reads it back to the student | Accurate, and mistakes are visible |
| Speech in | Parakeet TDT 0.6B on the Spark, push-to-talk | NVIDIA model, robust in a noisy hall |
| Speech out | Magpie TTS (NVIDIA, open weights) as primary, Kokoro-82M as the fast fallback, OpenAudio S1-mini (Fish Audio's open model) optional | All three run locally |
| iPad app | Native SwiftUI + PencilKit | Best Pencil latency, exact stroke data |
| Arm | SO-101 with LeRobot for motor I/O, scripted IK and Hershey fonts | Writes arbitrary text reliably |
| Network | Tailscale (approved on the Spark), plus a plain-LAN fallback rehearsed in advance | You own the troubleshooting, so keep it boring |
| Cut | NemoClaw, Isaac Lab / Eureka / GR00T, txt2kg, Cosmos legibility check, the cloud Fish Audio API | Not worth the time, or not local |

## 2. Modes, user journey and demo script

### Problem types (MVP)

| Type | Example | Steps the planner generates | What the arm writes |
| --- | --- | --- | --- |
| Linear equation | 3x + 5 = 20 | Subtract 5 → divide by 3 → answer → check | Each line |
| Quadratic (monic, factorisable) | x² + 5x + 6 = 0 | Identify a, b, c → find the factor pair → factored form → zero-product rule → roots → check by substitution | The pair, the brackets, the roots |
| Shape | "Properties of a right-angled triangle" | Draw → label → properties, from a template | The shape and its labels |

Stretch types: the quadratic formula, non-monic quadratics, completing the square and simultaneous equations.

### Posing a question

- **Pick from a preset list.** This always works and is the demo default.
- **Write it in the question box.** The vision model reads it and the app shows "I read: x² + 5x + 6 = 0. Is that right?". The student taps ✓ or rewrites it.
- **Classify the problem type.** Rules come first (contains x², contains =, shape words), with an LLM fallback. An unsupported type gets: "I can't do that kind of question yet. Try a quadratic or a linear equation."

### The three modes

**Teach (full walkthrough).** For each step in the SymPy plan:

1. **Intent.** The voice says what we're trying to do and why: "We want to split this into two brackets, so we need two numbers that multiply to 6 and add to 5."
2. **Write.** The arm writes the step on the tutor board while the voice explains the reasoning. The narration is sized to the drawing time.
3. **Check.** One short question: "Why plus 2 and plus 3, and not minus?" The student answers aloud or in writing. A right answer moves to the next step. A wrong one gets one Socratic follow-up, then the explanation, then the walkthrough carries on.

Teach mode ends with a recap ("three moves: find the pair, write the brackets, set each bracket to zero") and offers a similar problem to try in Guide mode.

**Guide (Socratic).** The student writes, and on Check the system finds the first wrong line, highlights it and asks one question. Two misses on the same step make the arm write that step while the tutor explains it, a one-step walkthrough. Then control goes back to the student.

**Check.** One final problem, solved without help, including the spot-the-mistake twist: the arm writes a worked solution containing one planted error, and the student circles it. The skill bars update.

**Switching modes.** The student taps **Teach me** or **Let me try**. Saying "I'm stuck" during Guide switches to Teach from the current step onwards, which makes a nice demo beat.

### Demo script (about 3.5 minutes)

| Time | Beat |
| --- | --- |
| 0:00 | Pitch: Bloom's two-sigma result. One-to-one tutoring works but is expensive. This runs on one box on a desk, and nothing leaves it. |
| 0:20 | The student writes x² + 5x + 6 = 0 in the question box. The tutor reads it back, and the student says "Teach me". |
| 0:35 | **Walkthrough.** The arm writes "2 × 3 = 6, 2 + 3 = 5", then (x+2)(x+3) = 0, then x = −2, x = −3, narrating each step. The student answers one check question aloud. |
| 1:50 | "Your turn": x² − 5x + 6 = 0 in Guide mode. The student writes (x+2)(x+3) and the line goes red. A Socratic question, then a second miss, and the arm writes the step. |
| 2:50 | The spot-the-mistake twist. |
| 3:20 | The skill map, then the architecture slide: Nemotron, Parakeet and Magpie, all on the Spark. Optionally pull the network cable to prove it (see section 11). |

Rehearse this exact script at least ten times, and record a backup video.

## 3. Architecture and data flow

### Where the hardware sits: decide this first

There are two possible layouts. Build so either works by making the arm service a separate process with its own URL in config.

- **A. The Spark is on your desk (preferred).** The arm's USB plugs into the Spark. The Mac only builds the iPad app and mirrors its screen.
- **B. The Spark is elsewhere** (a rack or another room, reachable only over Tailscale). The arm's USB plugs into the Mac, and `arm_server` runs on the Mac. The orchestrator sends whole drawing jobs over Tailscale, while the 50 Hz servo loop stays on the Mac, so network jitter can never make the arm stutter.

```
iPad (SwiftUI + PencilKit) ── Tailscale or LAN ──► DGX Spark "tutor-spark"
                                                 orchestrator :8000  (only exposed port)
                                                   ├─ Ollama  :11434   tutor LLM + vision LLM
                                                   ├─ ASR     :8001    Parakeet TDT 0.6B
                                                   ├─ TTS     :8002    Magpie (Kokoro fallback)
                                                   ├─ SymPy            in-process planner + checker
                                                   └─ arm     :8003 ─USB─► SO-101      (layout A)
Mac: Xcode, screen mirroring, SSH to the Spark   (layout B: the Mac also runs arm :8003)
```

### Services

| Service | Runs on | Port | Bound to | Tech |
| --- | --- | --- | --- | --- |
| Orchestrator | Spark | 8000 | 0.0.0.0, firewalled to Tailscale + LAN | FastAPI, uvicorn |
| LLM / VLM | Spark | 11434 | 127.0.0.1 | Ollama |
| ASR | Spark | 8001 | 127.0.0.1 | NeMo container with a FastAPI wrapper |
| TTS | Spark | 8002 | 127.0.0.1 | NeMo Magpie wrapper or Kokoro-FastAPI, behind one `/tts` interface |
| Arm | Spark (A) or Mac (B) | 8003 | 127.0.0.1 (A) / Mac's Tailscale IP (B) | FastAPI + LeRobot |

### Data flow: one Guide turn

1. The student taps Check. The iPad splits the strokes into lines and POSTs the line PNGs with their bounding boxes.
2. The orchestrator sends every line to the vision model in parallel and gets back expression strings.
3. SymPy validates each line against the problem's solution set and finds the first wrong line.
4. The policy picks the move (hint, arm step or praise). The LLM phrases it as JSON.
5. TTS synthesises sentence by sentence, and the first sentence goes out as soon as it's ready.
6. The WebSocket pushes `read_back`, `highlight`, `tutor_say` (with audio) and `skills`.

The target is under 3.5 s from tapping Check to hearing the first word. Measure each stage in the logs.

### Data flow: one Teach step

1. **Generate everything up front.** When the problem is confirmed, the planner computes the full SymPy step list. One batched LLM call writes the narration for every step (intent, explanation and check question). TTS pre-renders all of it while step 1 plays. This is what makes Teach mode feel instant, because there's never an LLM wait in the middle of the walkthrough.
2. For step *k*, the orchestrator sends the arm job and a `step` event to the iPad.
3. The iPad plays the intent clip, the arm starts writing, and the iPad plays the explanation. The orchestrator waits for both `arm: done` and the iPad's `audio_done`.
4. The iPad plays the check question and waits for a spoken (push-to-talk) or written answer.
5. The answer goes to ASR, then to the judge: a deterministic number comparison first, with the LLM as fallback. That gives a right or wrong verdict and a follow-up, then the next step.

## 4. Networking and Tailscale runbook

You own the troubleshooting, so **keep the network boring**: one tailnet, three devices and one port, plus a plain-LAN fallback that you've tested before you need it.

### One-time setup (first hour)

1. **One account.** Create one Tailscale account for the team; NVIDIA's playbook suggests a free account on a non-work email. Every device signs into the same tailnet.
2. **Spark.** Install Tailscale with apt by following NVIDIA's "Set up Tailscale on Your Spark" playbook, then run:

```bash
sudo tailscale up --hostname=tutor-spark
tailscale ip -4        # note the 100.x address
tailscale status       # all three devices should be listed
```

3. **Mac and iPad.** Install the Tailscale app on the Mac and sign in. Install Tailscale from the App Store on the iPad, sign in and turn the VPN on.
4. **Admin console.** Turn MagicDNS on, and **disable key expiry** for tutor-spark and the iPad, so nothing gets logged out mid-demo.
5. **Firewall (only if `ufw` is already active).** Open just what you need:

```bash
sudo ufw status
sudo ufw allow in on tailscale0 to any port 8000 proto tcp
sudo ufw allow in on tailscale0 to any port 22 proto tcp
```

If `ufw` is inactive, leave it inactive. Never switch a firewall on in the middle of a hackathon: it's the classic way to lock yourselves out of SSH.

6. **Test from every device.** On the Mac, `curl http://tutor-spark:8000/health` and `tailscale ping tutor-spark`. On the iPad, open the same URL in Safari.

### Don'ts

- **Don't turn on Tailscale SSH or edit the sshd config.** NVIDIA's playbook flags SSH configuration conflicts as a risk. Plain OpenSSH over the tailnet is enough.
- **Don't use Funnel, Serve, exit nodes or subnet routes.** You need none of them, and each one is something else to debug.
- **Don't hard-code addresses in the app.** Use a settings sheet with three saved profiles: `Tailscale (MagicDNS)`, `Tailscale (100.x IP)` and `LAN`.
- **Don't log the Spark out of Tailscale or re-authenticate it** unless you have another way in: a keyboard and monitor, or LAN SSH.
- **Assign one person to own the Spark's network config.** Nobody else touches it.

### Direct vs relayed connections

`tailscale ping` and `tailscale status` show whether a link is direct or going through a DERP relay. A relayed link works, but it adds latency to every request, and venue networks that block UDP force relaying. `tailscale netcheck` shows whether UDP is getting through. If you're being relayed, move all three devices onto a phone hotspot.

### Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| The Mac reaches the Spark but the iPad doesn't | The iPad's VPN is off, or iOS isn't using MagicDNS | Open the Tailscale app and toggle the VPN, then switch the app to the 100.x IP profile |
| Nothing reaches the Spark | The Spark shows offline in the admin console | Get in over LAN or with a keyboard, run `sudo systemctl restart tailscaled`, then `tailscale status` |
| `curl localhost:8000` works on the Spark but not over the tailnet | uvicorn is bound to 127.0.0.1, or the firewall is blocking it | Start with `--host 0.0.0.0` and check `sudo ufw status` |
| The hostname doesn't resolve | MagicDNS is off, or a stale DNS cache | Use the 100.x IP profile |
| Slow or laggy turns | DERP relay | `tailscale ping`, then move to a hotspot |
| It worked, then stopped | A key expired or re-authentication is needed | Disable key expiry, then log back in |
| The iOS app can't connect over LAN | Local Network permission denied | Settings → Privacy & Security → Local Network → enable the app |
| Xcode logs show an ATS error | iOS is blocking plain `http` | Set `NSAllowsArbitraryLoads` in Info.plist |

### Fallback ladder (rehearse step 3 once before the demo)

1. Tailscale over the venue Wi-Fi.
2. Tailscale over a phone hotspot, with all three devices on the hotspot.
3. **No Tailscale at all (layout A only).** Put every device on the same hotspot or travel router, find the Spark's address with `hostname -I`, and switch the app to the LAN profile (`http://<lan-ip>:8000`). If `ufw` is active, allow port 8000 on that interface as well.
4. If the Spark is remote (layout B) and Tailscale is down, there is **no fallback**. That's the strongest argument for getting the Spark onto your desk.

On privacy: Tailscale traffic is end-to-end encrypted with WireGuard. Tailscale's servers coordinate keys and, when relaying, forward packets they can't read. To claim "nothing leaves the room", demo on the LAN profile.

## 5. Models on the DGX Spark (all local)

The Spark has plenty of memory but limited memory bandwidth, so **choose small or mixture-of-experts models for anything in the voice loop.** Don't load a 120B model just because it fits.

| Role | First choice | Fallback | Licence | Serving | Rough memory |
| --- | --- | --- | --- | --- | --- |
| Tutor LLM | Nemotron Nano (Nemotron 3 Nano MoE or Nano 9B v2) | Qwen3 30B-A3B / 8B | NVIDIA open model | Ollama | 10–25 GB |
| Handwriting → expression | Qwen2.5-VL / Qwen3-VL 7–8B | Nemotron Nano VL | Open | Ollama | 6–10 GB |
| Speech-to-text | Parakeet TDT 0.6B v2 (English) or v3 (multilingual) | On-device iOS speech | NVIDIA open | NeMo container | 2–3 GB |
| Text-to-speech | Magpie TTS Multilingual 357M | Kokoro-82M | NVIDIA Open Model License / Apache 2.0 | NeMo container / Kokoro-FastAPI | about 2 GB / under 1 GB |
| TTS (optional) | OpenAudio S1-mini 0.5B from Fish Audio, with emotion tags | – | CC BY-NC-SA: fine for a hackathon, not for a product | fish-speech `api_server` | 2–4 GB |
| Maths | SymPy | – | BSD | In-process | – |

The total fits comfortably in the Spark's 128 GB. Check exact model tags in the Ollama library or on Hugging Face when you pull, and keep whichever Nemotron variant actually runs.

**Bake-offs in hours 1–2.** Each takes about 20 minutes:

- **Vision:** 10 handwritten crops, some deliberately messy. Keep the most accurate model.
- **TTS:** the same three sentences from Magpie, Kokoro and S1-mini. Choose by ear and by time to first audio.
- **LLM:** five Guide-mode prompts. Does it leak answers? Does it stay under 25 words? Does it return valid JSON?

```bash
export OLLAMA_KEEP_ALIVE=-1          # never unload
export OLLAMA_MAX_LOADED_MODELS=3    # LLM + VLM stay hot together
export OLLAMA_NUM_PARALLEL=4         # transcribe lines in parallel
ollama pull <nemotron-tag>; ollama pull <vlm-tag>
```

On the GB10, `nvidia-smi` shows N/A for memory because the memory is unified. That's normal. Use `free -h` and `docker stats` instead.

## 6. Backend spec (orchestrator on the Spark)

**Stack:** Python 3.11 in a `uv` virtual environment, plus FastAPI, uvicorn, `openai` (pointed at Ollama), SymPy, `HersheyFonts`, `httpx` and `pydantic`. State is in memory with one active session, and every turn is appended to a JSONL log.

```
backend/
  app.py           # FastAPI routes + WebSocket hub
  session.py       # state machine (modes, phases, misses, skills)
  planner/         # SymPy -> list[Step]
    linear.py  quadratic.py  shapes.py  classify.py
  narrate.py       # one batched LLM call -> narration for every step
  judge.py         # check-question answers: numbers first, LLM fallback
  vision.py        # line PNG -> expression string (VLM, temperature 0)
  checker.py       # SymPy line validation for Guide mode
  tutor.py         # Guide-mode Socratic moves (LLM, JSON out)
  speech.py        # /tts and /asr clients, sentence splitter, phrase cache, maths-to-speech
  strokes.py       # text/shape -> polylines in board-slot coordinates
  arm_client.py    # arm service client (URL from .env)
  prompts/socratic-tutor.md  prompts/addendum_teach.md  prompts/addendum_guide.md
  data/problems.json  data/skills.json  tests/
```

### The planner: correct steps, always

Each problem type becomes a list of `Step(kind, before, after, arm_text, skill, facts)`.

- **Quadratic.** Read `a, b, c` from `Poly(lhs - rhs, x)`. For monic quadratics, search the integer pairs where `p*q == c` and `p + q == b`. Then the factored form via `factor()`, the zero-product split, the roots via `solve()`, and a substitution check. The `facts` field carries the numbers the narration may mention, for example `{pair: [2, 3], product: 6, sum: 5}`.
- **Linear.** Isolate x one SymPy operation at a time: subtract the constant, then divide by the coefficient.
- **Shapes.** A JSON template per shape: strokes, labels and a list of properties.
- **The twist.** Take the correct plan and apply one canned mutation, such as flipping a sign in one bracket or dropping a root. Never ask the LLM to be wrong on purpose.
- **Unit tests.** Ten or more problems per type, run with `pytest` on every commit. This is the most testable part of the system, so test it properly.

### Narration: one LLM call per problem

`narrate.py` sends the full plan and the facts, and asks for JSON with one entry per step: `{intent, explain, check_q, expected}`. Each field is spoken text: maths written as words, no symbols, at most 30 words. `explain` is sized to the arm's estimated drawing time, at about 2.5 words per second. Validate the result with pydantic, and retry once if it fails. If the retry fails too, use templated narration (one template per step kind), so Teach mode **always works even if the LLM misbehaves**.

### Answer judging

`judge.py` works in two stages:

1. **Deterministic.** Pull numbers and signs out of the transcript ("minus two" becomes −2) and compare them with `expected`.
2. **LLM fallback** for why-questions. It returns `{correct, misconception, follow_up}`.

Two wrong answers in a row means the tutor explains and moves on. Never trap the student in a loop.

### Guide mode

Guide mode is unchanged from the earlier spec:

- Each line is transcribed by the vision model.
- SymPy checks whether the line's solution set equals the problem's.
- The first invalid line is classified with misconception rules.
- `tutor.py` phrases one Socratic question, at most 25 words, and never states the answer.
- A post-check regenerates the reply once if it mentions the roots.

### HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Status and latency of every dependency (LLM, VLM, ASR, TTS, arm), plus the Tailscale and LAN IPs |
| POST | `/session` | `{problem_id?}` → `{session_id}` |
| POST | `/session/{id}/question` | A PNG of the question box → the read-back over the WebSocket |
| POST | `/session/{id}/lines` | Multipart line PNGs and bounding boxes for Guide-mode checking |
| POST | `/session/{id}/audio` | A 16 kHz WAV → transcript, which goes into the state machine as an utterance |
| GET | `/audio/{clip}.wav` | Rendered TTS clips |
| POST | `/admin/{action}` | `next_problem`, `skip_step`, `force_arm_step`, `ghost_mode`, `tts_backend`, `reset` |

### WebSocket `/ws/{session_id}`

Server → iPad:

```json
{"type":"problem","display":"x² + 5x + 6 = 0","kind":"quadratic","needs_confirm":true}
{"type":"phase","mode":"teach|guide|check","phase":"STEP_WRITE"}
{"type":"step","index":2,"of":5,"slot":2,"display":"(x+2)(x+3)=0",
 "clips":[{"id":"s2i","url":"/audio/s2i.wav","caption":"..."},{"id":"s2e","url":"/audio/s2e.wav","caption":"..."}],
 "arm":{"job":"j7","strokes":[[[x,y],[x,y]]],"est_s":11}}
{"type":"ask","clip":{"id":"s2c","url":"/audio/s2c.wav","caption":"Why plus 2 and not minus 2?"},"answer":"voice|write|either"}
{"type":"tutor_say","caption":"...","url":"/audio/x.wav"}
{"type":"read_back","lines":[{"index":1,"expr":"(x+2)*(x+3)=0","ok":false}]}
{"type":"highlight","line_index":1,"bbox":[40,180,520,70]}
{"type":"arm","job":"j7","state":"drawing|done|error"}
{"type":"skills","skills":{"factor_pair":3,"signs":1}}
```

iPad → server:

- `confirm_problem` with `{ok}`
- `choose_mode` with `teach`, `guide` or `check`
- `audio_done` with `{clip}`
- `answer_text` with `{text}`
- `check`
- `stuck`
- `arm_touches` with `{job, count}`
- `mistake_circled` with `{line_index}`
- `clear_board`

### State machine

```
IDLE -> POSE -> CONFIRM -> MODE_SELECT
TEACH:  STEP_INTRO -> STEP_WRITE (arm + explain audio, wait for both)
        -> STEP_CHECK -> (correct: next step | wrong: FOLLOW_UP -> next step)
        -> RECAP -> offer GUIDE with a sibling problem
GUIDE:  WORKING -check-> HINTING (misses[s] += 1)
        misses[s] >= 2 -> ARM_STEP (one-step walkthrough) -> WORKING
        "stuck" -> TEACH from the current step
        all lines valid + roots found -> SOLVED
CHECK:  SPOT_MISTAKE -circled-> FINAL (unaided) -> DONE (skills, optional summary)
```

Every transition is logged with timings. The admin panel can force any transition, which is your get-out-of-jail card on stage.

### Prompt layering

1. The Jane Street socratic-tutor SKILL.md body, unchanged.
2. A mode addendum. **Teach:** "You are narrating a worked solution aloud. Explain why each step is taken, not just what. Spoken English only, at most 30 words per field, return JSON." **Guide:** the earlier rules: one question, at most 25 words, never state the answer or the next step.
3. Context: the problem, the plan, the student's lines, the misconception label, and the facts the model is allowed to mention.

Use temperature 0.3 for narration and 0 for judging and vision.

## 7. Frontend spec (SwiftUI + PencilKit iPad app)

**One screen, locked to landscape.**

```
┌──────────────────────────────────────────────────────────────┐
│ Solve: x² − 5x + 6 = 0      phase chip     skill bars ▓▓░░░ │
├────────────────────────────────────────────────┬─────────────┤
│                                                │  TUTOR      │
│        student canvas (PKCanvasView)           │  WRITING    │
│        fixed size, no scroll, no zoom          │  STRIP      │
│                                                │  (arm only) │
├────────────────────────────────────────────────┴─────────────┤
│ 💬 caption of what the tutor just said   [🎙 hold] [Check] │
└──────────────────────────────────────────────────────────────┘
```

**The tutor writing strip is the key simplification.** The arm only ever writes in one small, fixed region that sits in its most accurate reach. Calibrate that region well and ignore the rest of the screen.

### Canvas rules

- `PKCanvasView` in a `UIViewRepresentable`. Set a **fixed frame in points, disable scrolling and zoom, and lock the orientation.** Calibration maps canvas points to arm space, so anything that moves the canvas breaks the arm.
- **Drawing policy:** use `.pencilOnly` while the student works, so palms don't leave ink. Switch to `.anyInput` during ARM\_STEP and SPOT\_MISTAKE so the arm's passive stylus inks, then switch back on `arm: done`.
- **Ink colours:** the student uses black. During arm phases the app sets the tool to blue "tutor ink". Line segmentation filters strokes by `stroke.ink.color`, so the tutor's writing never counts as the student's work.
- **Ghost ink fallback:** on `arm: error`, or on `arm: done` with zero touches received, the app animates the planned `strokes` itself as `PKStroke`s in blue.

### Line segmentation, on device, on Check

1. Take the student strokes and sort them by `renderBounds.midY`.
2. Group them into lines: a stroke starts a new line when its top is more than about 35 pt below the current line's bottom. Merge strokes that overlap vertically.
3. Render each line with `PKDrawing(strokes: lineStrokes).image(from: bounds.insetBy(dx: -12, dy: -12), scale: 2)`.
4. **Composite onto a white background before PNG encoding.** PencilKit images are transparent, and vision models misread black-on-transparent.
5. POST the lines to `/session/{id}/lines`, then send `{"type":"check"}`.

### Other app pieces

- **Networking:** `URLSessionWebSocketTask` with auto-reconnect and a ping every 10 s. Use an `@Observable` `SessionModel` that the views bind to.
- **Highlight:** a translucent red rounded rect overlaid at `bbox`, fading after 4 s. Show the `read_back` text small under each line.
- **Audio:** download the mp3 and play it with `AVAudioPlayer`. **Mute the mic while it plays** (half-duplex), and always show the caption.
- **Voice in:** push-to-talk with `AVAudioRecorder` at 16 kHz mono WAV, POSTed to `/audio`. The fallback is `SFSpeechRecognizer` with `requiresOnDeviceRecognition`.
- **Calibration mode**, a hidden long-press on the title: shows a 3×3 grid of targets in the tutor strip and logs `touchesBegan` locations from any input. Section 7 explains how it's used.
- **Spot-the-mistake:** in this phase, a lasso or circle stroke over a line sends `mistake_circled`, using the line whose bbox overlaps most.
- **Demo hardening:** Guided Access on, auto-lock off, Do Not Disturb on, and brightness at maximum.

**Build and deploy:** a free Apple ID personal team is enough. Turn on Developer Mode on the iPad, connect it by cable, and build from Xcode. Free provisioning profiles expire after 7 days, which covers the hackathon. **Do this in hour one.** Signing problems are the classic time sink.

### Additions for Teach mode

- **Question box.** A small `PKCanvasView` at the top with a **Read it** button. It POSTs a PNG to `/question` and shows the read-back sheet: "I read x² + 5x + 6 = 0", with ✓ or Rewrite. Next to it, a preset picker that always works.
- **Mode buttons:** Teach me, Let me try, and I'm stuck. The last appears during Guide mode.
- **Tutor board.** The right-hand panel becomes **five numbered line slots** where the arm writes. Step *k* goes into slot *k*. The arm is calibrated to slot positions, not free positions. A step-progress indicator shows "Step 2 of 5".
- **Clear board.** The ink is digital, so wiping the tutor board after a problem is free: remove the blue strokes. The arm never has to erase anything.
- **Audio queue.** An `AVQueuePlayer` plays the clips in order (intent, then explanation) and sends `audio_done` for each one. The caption follows whichever clip is playing.
- **Answer input.** Push-to-talk, or a small answer box the student writes in. Both go to the server, and the student picks whichever they like.
- **Preview ink.** While the arm draws, show a faint dotted preview of the planned strokes. It helps the audience follow, and it becomes the ghost-ink fallback if the arm fails.
- **Settings sheet.** Three server profiles (MagicDNS, 100.x IP and LAN), a dot for each service from `/health`, and a TTS backend toggle.

## 8. Robot arm spec (SO-101)

**Approach:** scripted kinematics. Text or a shape becomes polylines in canvas points, then arm-space XYZ through calibration, then joint angles through IK, then streamed joint targets through LeRobot's `SOFollower`. Skip the learned policy and the sim.

### Physical setup

- **Fix the geometry.** Clamp the arm base to the table. Fix the iPad in a taped corner jig or 3D-printed frame. If either one moves, you have to recalibrate. That takes about 2 minutes, but don't do it on stage.
- Put the **tutor writing strip** in the arm's sweet spot, at mid reach. Avoid full extension and positions right next to the base.
- **End effector:** the hadrien-cornier sleeve that slides over the fixed gripper finger. It holds pens 8–13 mm thick in two V clamps, angled at 20°. Use it with a **passive capacitive stylus** that has a soft rubber or mesh tip. The soft tip absorbs about 1–2 mm of height error, the same job the article's soft pad does.

### The stylus grounding catch: test this in hour one

A passive capacitive stylus usually registers only because a human hand provides capacitance. **Held by a plastic robot, it may not register at all.** Fixes, in order:

1. Choose a stylus with a conductive metal barrel. Run a thin wire from the barrel to a larger conductive mass, or to a person standing nearby with the wire taped to their wrist.
2. Wrap the barrel in aluminium foil connected the same way.
3. Last resort: **the arm holds the Apple Pencil** (it's active and registers without a hand), and the student writes with a passive stylus. Use `.anyInput` throughout and tolerate some palm marks.

### Calibration: use the iPad as the sensor

1. Run LeRobot's motor calibration once (`lerobot-calibrate`, robot type `so101_follower`).
2. Open the app's calibration mode. It shows 9 targets across the tutor strip.
3. For each target, jog the arm with keyboard teleop or the leader arm until the stylus touches the screen. The app reports the **actual touch point** in canvas coordinates. Record the joint angles, then run forward kinematics on the URDF to get the tip XYZ.
4. Fit a least-squares affine map from canvas (u, v) to the table plane (x, y), plus a plane z(u, v). The plane absorbs a table or iPad that isn't level. Save the result to `calib.json`.
5. **Pen-down z** is the plane value minus 1.5 mm, pressing into the soft tip. **Pen-up z** is the plane value plus 12 mm.
6. Verify by commanding a 2 cm square in the strip, then compare the inked square with the expected one in the app. You're aiming for under 3 mm of error.

The hadrien-cornier article gives tip offsets by pen diameter, and notes that a thinner pen sits deeper in the V. Because this calibration measures the real tip position, it absorbs those offsets automatically.

### Stroke generation (`strokes.py`)

- **Text:** use a Hershey simplex single-stroke font from the `HersheyFonts` pip package. Each glyph becomes polylines in font units, which are scaled and laid out left to right in the strip.
- **Size: characters at least 15 mm tall** (about 80 pt on an 11-inch iPad). Arm precision is a few millimetres, so small text turns into scribble.
- **Keep strings short.** Only the characters `0–9 x + − = ( ) ²` and "or". Write x² as a raised small 2. Avoid writing words.
- **Shapes:** polyline generators for a triangle, square, circle (as a 48-gon) and a labelled right angle.
- Resample every polyline at 1.5 mm spacing. Between strokes: pen up, travel, then pen down.

### IK and control (`arm/`)

- Use the SO-101 URDF from the SO-ARM100 repo with `ikpy`, or placo if you're already in the LeRobot kinematics utilities. Five arm DOF: solve for position and keep the wrist pitch near a fixed "pen down" angle as a soft constraint. Fix wrist roll.
- **Precompute** the IK for the whole stroke plan before moving. Reject the plan if any point fails, since then the strip is out of reach. Warm-start each solve from the previous point to avoid joint flips.
- **Stream** joint targets to `robot.send_action({...})` at 30–50 Hz. Speed is about 25 mm/s while drawing and 80 mm/s while travelling. Slow and legible beats fast. Keep LeRobot's `max_relative_target` safety limit on.
- `arm_server.py` (FastAPI :8002): `POST /draw {strokes, rect}` → job id, `GET /status`, `POST /home`, `POST /stop`. It's a single worker thread, and it rejects a new job while one is drawing.
- **Closed loop for free:** the app counts touches while the arm draws and sends `arm_touches`. Zero touches after the first pen-down means a grounding or z problem. The backend then switches to ghost ink and logs it.
- Between runs: home the arm and **turn torque off.** STS3215 servos get hot if they hold a pose for a long time.

ACT-SO100-Draw and the Isaac Lab, Eureka and GR00T path are all post-hackathon work. Mention them on the "what's next" slide.

### Additions for Teach mode and the two layouts

- **Where the USB goes.**
  - Layout A, the Spark: the port is `/dev/ttyACM*`, and your user needs to be in the `dialout` group.
  - Layout B, the Mac: the port is `/dev/tty.usbmodem*`.
  - Install `lerobot[feetech]` in a venv either way. The Feetech motor bus is plain serial, so it has no GPU dependencies and installs fine on the Spark's ARM64 Linux.
- **Slots, not free placement.** Calibrate the five tutor-board slots, which means nine targets spread over the slot area. `strokes.py` lays text out inside slot *k*'s rectangle.
- **Time budget.** At legible speed, expect roughly 1–2 seconds per character. Keep `arm_text` to **10 characters or fewer per slot**, and split longer steps across two slots. Examples: "2×3=6", "2+3=5", "(x+2)(x+3)=0", "x=−2, x=−3". Write x² as a raised, smaller 2.
- **The estimate drives the narration.** `POST /plan` on the arm service returns `est_s` without moving. `narrate.py` uses that number to size the explanation, so the voice and the pen finish at about the same time.
- **Pre-flight on start.** The arm homes, then draws a 1 cm tick in a hidden corner of slot 5. The app confirms it received touches, which proves calibration and stylus grounding before the first real step.

## 9. Voice pipeline (fully local)

### Speech out (TTS)

- **Primary: NVIDIA Magpie TTS Multilingual 357M.** It has open weights under the NVIDIA Open Model License and is loaded through NeMo: `MagpieTTSModel.from_pretrained("nvidia/magpie_tts_multilingual_357m")`, then `model.do_tts(text, language="en", speaker_index=k)`.
  - Start from NVIDIA's recommended sampling settings: `cfg_scale=2.5`, `temperature=0.6`, `top_k=80`.
  - It isn't designed for voice cloning, so choose one of its built-in speakers.
  - Wrap it as `POST /tts {text, voice}`, which returns WAV.
  - It's an NVIDIA model, which helps your NVIDIA story.
- **How to build it on the Spark.** Start from an `nvcr.io/nvidia/pytorch` container. Spark users on NVIDIA's forum have published Dockerfiles that run Parakeet and Magpie on the Spark's ARM64 / CUDA 13 setup this way.
  - **Timebox it to two hours.** If it fights you, flip `TTS_BACKEND=kokoro` and move on.
- **Fallback: Kokoro-82M** (Apache 2.0). The Kokoro-FastAPI GPU image has been built and run on the Spark (`linux/arm64`), and it exposes an OpenAI-style speech endpoint. Put it behind the same `/tts` interface so switching is just a config flag. It's tiny and fast, and the most reliable choice.
- **Optional: OpenAudio S1-mini** (Fish Audio's open 0.5B model), run locally with `fish-speech`'s `api_server`. Its emotion tags, such as `(excited)` for praise lines, are a nice touch. The licence is non-commercial, which is fine for a hackathon.
- **Latency tricks.**
  - Split replies into sentences and send each clip as soon as it's ready.
  - In Teach mode, pre-render every step's clips while step 1 plays.
  - Keep a phrase cache of about 20 stock lines.
  - Warm the model up at startup.
- **Maths to speech.** One normaliser runs before every TTS call: `x^2` → "x squared", `-3` → "negative three", `(x+2)(x+3)` → "x plus two, times x plus three". The LLM is also told to write spoken maths, so the normaliser is a safety net.

### Speech in (ASR)

- **Parakeet TDT 0.6B**: v2 for English, v3 for multilingual. It runs in a NeMo container on the Spark behind an OpenAI-style `/v1/audio/transcriptions` wrapper.
- **Push-to-talk, half-duplex.** The iPad records 16 kHz mono WAV while the button is held, and the mic stays off while the tutor speaks. Push-to-talk avoids needing voice activity detection and copes with a noisy hall.
- **Speech to maths.** "x squared plus five x" becomes `x**2+5*x`, and "minus two" becomes −2, using a small rule table. `judge.py` compares numbers deterministically.

### Fallbacks that stay on-device

If the Spark's speech services go down, the iPad uses `SFSpeechRecognizer` with `requiresOnDeviceRecognition = true` and `AVSpeechSynthesizer`. Both run on the iPad itself, so audio still never leaves your hardware.

## 10. Deployment and operations on the Spark

- **Monorepo layout:** `ios/`, `backend/`, `arm/`, `services/asr/`, `services/tts/` and `ops/`.
- **Processes:**
  - Ollama runs as the system service.
  - The ASR and TTS containers run under `docker compose` with `restart: unless-stopped`.
  - The orchestrator runs in a `uv` venv inside a named `tmux` session, with `tutor` as the session name.
- **Three scripts:**
  - `ops/up.sh` starts everything and warms the models.
  - `ops/down.sh` stops everything.
  - `ops/health.sh` curls every service and prints the Tailscale and LAN IPs.
- **`/health` is your dashboard.** It reports per-service status and latency, and the iPad settings sheet shows it as coloured dots. That turns most "it's broken" moments into a 10-second diagnosis.
- **Config in `.env`:** model tags, service URLs, `ARM_URL`, `TTS_BACKEND=magpie|kokoro|fish` and `HF_HUB_OFFLINE=1` once the models are downloaded.
- **Logs:** each turn goes to `logs/session-*.jsonl` with inputs, transcriptions, SymPy verdicts, LLM outputs and per-stage timings. Keep `tail -f` running during rehearsals.
- **Ownership:** one person owns the Spark's services, and nobody restarts processes they don't own. Everyone SSHs in over the tailnet with their own key.
- **Git:** `main` is always demo-safe. Use feature branches, and tag `demo-v1` before judging.

## 11. Privacy: what leaves the machine

| Data | Processed on | Leaves your hardware? |
| --- | --- | --- |
| Handwriting images | Spark (vision model) | No |
| Voice recordings | Spark (Parakeet), or the iPad in fallback | No |
| Tutor text and speech audio | Spark | No |
| Session logs | The Spark's disk | No. Delete them after the event |
| Model weights | Downloaded once from Hugging Face, Ollama or NGC before the demo | A download only |
| Network traffic | Tailscale (WireGuard, end to end) or the plain LAN | Encrypted only. The LAN profile avoids it entirely |

**Pitch line:** no cloud model calls anywhere. If the Spark is on your desk, demo on the LAN profile with `HF_HUB_OFFLINE=1`, and **unplug the venue internet live**. The tutor keeps working, and that's very persuasive to NVIDIA judges.

## 12. Build plan and timeline

| Owner | Owns |
| --- | --- |
| **A: iOS** | App, canvas, line segmentation, question box, audio queue, settings and health view, calibration mode |
| **B: Backend and lesson engine** | Planner, narration, judge, Guide checker, state machine, prompts, tests |
| **C: Robotics** | Pen holder, stylus grounding, calibration, IK, arm service, slot layout |
| **D: Models, voice and infrastructure** | Tailscale and the LAN fallback, Ollama, ASR and TTS containers, bake-offs, ops scripts, pitch and backup video |

**Phase 0, hours 0–2: de-risk.**

- **D:** Tailscale on all three devices, `/health` reachable from the iPad, and the LAN profile tested once.
- **C:** the arm moves, and **a stylus held in the sleeve registers a touch on the iPad.**
- **D:** both Ollama models pulled and hot, the vision bake-off done, and the Parakeet and Kokoro containers running. Start the Magpie build in the background.
- **Everyone:** decide the layout (A or B), and sign off the WebSocket contract from section 6.

**Phase 1, hours 2–8: slices against mocks.**

- **A:** builds against a mock server that replays canned `step` and `ask` events.
- **B:** linear and quadratic planners with passing tests, and narration with its template fallback.
- **C:** writes into slots 1–3 from hard-coded strokes.
- **D:** puts `/tts` and `/asr` behind one interface.

**Phase 2, hours 8–14: Teach mode end to end. This is the hero feature.**

The full chain is planner → narration → pre-rendered TTS → arm and audio in sync → check question → judge. Build Teach mode first: it's deterministic and doesn't depend on reading handwriting, so it's the safest thing to demo. Then add Guide mode with the vision model and SymPy.

**Phase 3, hours 14–18: connect the modes.** "I'm stuck" switching from Guide to Teach, the twist, skill bars, shapes, and Magpie if it built.

**Phase 4, hours 18 onwards: freeze.** Bug fixes only. Rehearse ten times, including once fully offline on the LAN profile, record the backup video and build the slides.

## 13. Risks and fallbacks

| Risk | Likelihood | Mitigation or fallback |
| --- | --- | --- |
| A passive stylus held by the arm doesn't register | High | Ground the barrel with a wire or foil. Otherwise the arm holds the Apple Pencil and the student uses a passive stylus. Test in hour one. |
| Tailscale is relayed or flaky on venue Wi-Fi | High | Phone hotspot, then the LAN profile (layout A). Follow the runbook in section 4. |
| Tailscale is down and the Spark is remote (layout B) | Medium | No network fallback, so get the Spark onto your desk. The backup video is the last resort. |
| Magpie won't build on ARM64 in time | Medium | Kokoro behind the same `/tts` interface, by flipping one config flag. |
| The arm writes too slowly in Teach mode | Medium | 10 characters or fewer per slot, narration sized to `est_s`, and a faster travel speed. |
| The arm's handwriting is illegible | Medium | 15 mm or larger characters, a soft tip, and the calibration check square. Ghost ink is the last resort. |
| The narration LLM returns bad JSON | Medium | Validate, retry once, then fall back to templated narration. |
| The vision model misreads handwriting | Medium | Per-line crops on a white background, temperature 0, the read-back shown to the student, and a "please rewrite" path. |
| ASR mishears spoken maths | Medium | Deterministic number comparison, plus the option to write the answer instead. |
| The LLM gives away answers in Guide mode | Medium | The policy chooses the move, the prompt forbids answers, and a post-check regenerates the reply. |
| Slow turns | Medium | Models kept hot, pre-rendered Teach audio, sentence-level TTS and the phrase cache. |
| The iPad or arm gets bumped | Medium | A taped jig, a clamped base, and the 2-minute quick recalibration. |
| Xcode signing problems | Medium | Set up in hour one, with a second Apple ID ready. |
| Servos overheat | Low | Home the arm and turn torque off between runs. |
| Anything else on stage | – | The admin panel (`skip_step`, `force_arm_step`, `ghost_mode`) and the backup video. |

## 14. Demo-day pre-flight checklist

- [ ] `ops/health.sh` is all green: LLM, VLM, ASR, TTS and the arm
- [ ] The iPad settings sheet shows all dots green on the profile you'll actually use
- [ ] `tailscale ping tutor-spark` is direct, not relayed (or you've already switched to the LAN profile)
- [ ] The arm is homed, the pre-flight tick registered touches, and the calibration square is within 3 mm
- [ ] The iPad is in its jig, with Guided Access on, auto-lock off, Do Not Disturb on, maximum brightness and the charger plugged in
- [ ] Models are warmed and `HF_HUB_OFFLINE=1` is set
- [ ] Speaker volume has been checked in the actual room, with captions on
- [ ] The demo problems are preset, the tutor board is cleared and the session is reset
- [ ] The backup video is on the Mac's desktop, and the admin panel is open on a phone
- [ ] The servos have rested with torque off since the last run
