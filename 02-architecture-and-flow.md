# Socratic Robot Tutor: Architecture and Flow Explained

This is the companion to `01-plan.md`. The plan says *what* to build and *when*. This file explains *how the system works* from the moment a student picks up the Pencil to the moment the arm puts it down.

---

## 1. The big picture

```
┌──────────────────────── iPad (SwiftUI + PencilKit) ────────────────────────┐
│  question box · student canvas · tutor board (5 arm slots) · captions      │
│  mode buttons · push-to-talk · Check · settings (server profiles, health)  │
└───────────────┬────────────────────────────────────────────────────────────┘
                │  WebSocket (events)  +  HTTP (PNGs, WAVs, audio clips)
                │  over Tailscale (MagicDNS / 100.x) or plain LAN
                ▼
┌──────────────────────────── DGX Spark "tutor-spark" ───────────────────────┐
│  orchestrator  FastAPI :8000          ← the ONLY port the iPad ever talks to│
│   ├─ session.py   state machine (modes, phases, misses, skills)            │
│   ├─ planner/     SymPy → correct solution steps                           │
│   ├─ checker.py   SymPy → is each student line valid?                      │
│   ├─ narrate.py / tutor.py / judge.py  ── Ollama :11434 (Nemotron Nano)    │
│   ├─ vision.py    ──────────────────────── Ollama :11434 (vision model)    │
│   ├─ speech.py    ── ASR :8001 (Parakeet)   ── TTS :8002 (Magpie / Kokoro) │
│   └─ arm_client   ── arm service :8003 ──USB──► SO-101 holding a stylus    │
└────────────────────────────────────────────────────────────────────────────┘
Mac: builds the iPad app in Xcode, mirrors the iPad screen, SSHs into the Spark.
```

### The one rule

> **SymPy decides the maths. The state machine decides what happens next. The LLM only explains.**

Everything else follows from this:

- The arm writes text that SymPy produced, so it can never write a wrong step by accident. The only exception is the deliberate twist, which applies a scripted mutation.
- The decision about when to hint and when the arm steps in is plain Python. It's predictable, testable and easy to override from the admin panel.
- The LLM turns decisions into friendly spoken English. If it misbehaves, templates take over.

### Who owns what

| Component | Job | Never does |
|---|---|---|
| iPad app | Captures ink, splits it into lines, plays audio, shows captions and highlights, runs ghost ink | Any maths or AI |
| Orchestrator | Holds the session, routes every request, enforces the state machine | Talks to the internet |
| SymPy | Generates steps, validates lines, creates the twist mutation | Generates language |
| Nemotron (LLM) | Narration, Socratic questions, judging "why" answers | Decides the next move, or decides correctness when numbers can be compared |
| Vision model | Handwriting → expression string | Judges whether the line is right |
| Parakeet | Speech → text | – |
| Magpie / Kokoro | Text → speech | – |
| Arm service | Plans and executes strokes, estimates drawing time | Decides *what* to write |

---

## 2. Physical setup and layouts

```
          ┌────────── iPad in a taped jig ──────────┐
          │  student canvas       │  tutor board    │◄── arm writes only here,
          │  (Apple Pencil)       │  slots 1–5      │    in calibrated slots
          └──────────────────────────────────────────┘
                                         ▲
                     SO-101 clamped to table, passive stylus in pen sleeve
```

- **Student** writes with the **Apple Pencil**. The canvas is in `.pencilOnly` mode, so resting palms leave no marks.
- **Arm** holds a **passive capacitive stylus**, grounded with a wire or foil. During arm phases the canvas switches to `.anyInput` and the ink colour switches to **blue tutor ink**.
- The canvas has a fixed size, with no scroll, no zoom and a locked orientation. Calibration maps canvas points to arm coordinates, so the canvas must never move.

**Layout A (preferred):** the Spark is on the desk and the arm's USB goes into the Spark. All traffic stays on the desk.
**Layout B:** the Spark is remote, so the arm's USB goes into the Mac and the arm service runs there. The Spark sends the Mac whole drawing *jobs*. The 50 Hz servo loop never crosses the network.

---

## 3. Session start-up flow

```
iPad                         Orchestrator                    Services
 │  GET /health  ───────────────►│ ping LLM, VLM, ASR, TTS, arm ──► each
 │◄─────────── status + latency ─│
 │  POST /session ──────────────►│ create session, state = IDLE
 │◄──────────────── session_id ──│
 │  open WS /ws/{id} ───────────►│
 │                               │ arm: home → pre-flight tick in slot 5
 │◄── arm_touches count ─────────│ (iPad reports touches → stylus + calibration OK)
```

The settings sheet shows one green or red dot per service. If anything is red you know which service to fix before a student ever touches the screen.

---

## 4. Posing the question

```
Student writes "x² + 5x + 6 = 0" in the question box → taps "Read it"
 iPad ── POST /session/{id}/question (PNG on white) ──► orchestrator
 orchestrator ── vision model (temperature 0) ──► "x**2+5*x+6=0"
 orchestrator ── classify.py: has x**2 and "=" → quadratic (rules first, LLM fallback)
 iPad ◄── {"type":"problem","display":"x² + 5x + 6 = 0","kind":"quadratic","needs_confirm":true}
 Student taps ✓   → confirm_problem   (or Rewrite → repeat)
 Student taps "Teach me" or "Let me try" → choose_mode
```

There is also a preset picker that skips the vision step entirely. It's the safe option for the demo.

Read-back matters. The student sees what the system thinks they wrote *before* anything depends on it, which hides most vision-model mistakes and is honest about them.

---

## 5. Teach mode: the full walkthrough

This is the hero feature. The trick is to **do all the slow work up front**, so the walkthrough never pauses for the AI.

### 5.1 Preparation (once per problem, about 3–5 s, hidden behind an intro line)

```
confirm_problem
   │
   ├─► planner/quadratic.py (SymPy)
   │      a,b,c = 1,5,6 → pair (2,3): 2×3=6, 2+3=5 → (x+2)(x+3)=0
   │      → x+2=0 or x+3=0 → x=−2, x=−3 → check by substitution
   │      → list[Step] with arm_text per slot + facts the LLM may mention
   │
   ├─► arm service POST /plan for each step's arm_text → est_s (drawing seconds)
   │
   ├─► narrate.py: ONE LLM call, JSON per step {intent, explain, check_q, expected}
   │      explain sized to est_s (~2.5 words/sec) · validate with pydantic
   │      retry once → else templated narration (Teach always works)
   │
   └─► speech.py: TTS renders step 1 immediately; steps 2..n in the background
```

While this runs, the iPad plays a cached line such as "Great, let's work through this together."

### 5.2 One step, end to end

```
Orchestrator                 iPad                         Arm
 │── step k (clips, strokes, slot) ──►│                           │
 │                                    │ play INTENT clip          │
 │── POST /draw (job k) ─────────────────────────────────────────►│ pen down in slot k
 │                                    │ play EXPLAIN clip         │ writes "(x+2)(x+3)=0"
 │                                    │ show dotted preview       │ (blue tutor ink appears)
 │                                    │ count arm touches         │
 │◄── audio_done(explain) ────────────│                           │
 │◄── arm done ───────────────────────────────────────────────────│
 │   (waits for BOTH)                 │                           │
 │── ask (check clip) ───────────────►│ play "Why plus two and not minus two?"
 │                                    │ student holds mic / writes answer
 │◄── audio WAV → Parakeet → text ────│
 │ judge.py: numbers first, LLM for "why" questions
 │   correct → step k+1
 │   wrong   → one Socratic follow-up → explain → step k+1 (never loops)
```

Why it feels smooth:

- The voice and the pen finish together, because the narration was sized to the arm's own time estimate.
- There's no LLM call during the walkthrough except for judging free-text "why" answers, and those are short.
- If the arm reports done but the iPad saw **zero touches**, the stylus missed the screen. The app animates the planned strokes itself as ghost ink, and the lesson carries on.

### 5.3 Ending

Teach mode ends with a recap ("three moves: find the pair, write the brackets, set each bracket to zero"). It then offers a sibling problem, x² − 5x + 6 = 0, in **Guide mode**: "Your turn."

---

## 6. Guide mode: the Socratic loop

The student does the work, and the system reads it.

```
Student writes lines on the canvas → taps Check (or says "check")
 iPad: group student strokes by vertical position → lines
       render each line → PNG on white + bbox → POST /lines
 Orchestrator:
   1. vision model on every line IN PARALLEL → ["x**2-5*x+6=0", "(x+2)*(x+3)=0"]
   2. checker.py: a line is valid if solveset(line) == solveset(problem)
        line 0 ✓   line 1 ✗  → first wrong line = 1
   3. misconception rules: right product, wrong sign → label "sign_error"
   4. session.py policy: misses[factor_step] = 1 → move = HINT
   5. tutor.py (LLM + Socratic SKILL.md + Guide addendum):
        {"say": "Which two numbers multiply to 6 but add to negative 5?"}
        post-check: reply mentions the roots? → regenerate once
   6. TTS first sentence → clip
 iPad ◄── read_back (what we read) · highlight (red box on line 1)
      ◄── tutor_say (caption + audio) · skills
```

**Second miss on the same step:** the policy switches to an ARM_STEP. It's a one-step walkthrough: the arm writes the correct line into the next tutor-board slot while the tutor explains it, then hands back to the student.

**"I'm stuck"** at any point jumps to Teach mode *from the current step*. It reuses the same plan and narration, so the switch is instant.

**Solved:** every line is valid and the roots have been found. The skills update and the session moves on to Check mode.

Target timing, from tapping Check to the first spoken word, is **under 3.5 seconds**. Vision is about 1 s (in parallel), SymPy is milliseconds, the LLM is about 1 s, and the first TTS sentence comes well under a second after that. Every stage is timed in the JSONL log, so you can see where the time goes.

---

## 7. Check mode and the twist

```
SPOT_MISTAKE: planner takes the correct plan → applies ONE canned mutation
              e.g. (x−2)(x+3)=0 → arm writes the worked solution with that error
              student circles the bad line → mistake_circled(line_index)
              iPad picks the line whose bbox overlaps the circle most
FINAL:        new problem, no hints; checked exactly like Guide mode
DONE:         skill bars filled; optional end-of-session summary
```

The mistake comes from code, never from asking the LLM to be wrong. LLMs are unreliable at being wrong on purpose: sometimes they're accidentally right, and sometimes wrong in two places.

---

## 8. The state machine in one picture

```
IDLE → POSE → CONFIRM → MODE_SELECT
                         │
        ┌────────────────┴───────────────┐
      TEACH                            GUIDE
  STEP_INTRO                        WORKING ◄──────────┐
  STEP_WRITE (arm + audio)            │ check          │
  STEP_CHECK ── wrong → FOLLOW_UP     ▼                │
      │  correct                    HINTING (misses+1) │
      ▼                               │ misses ≥ 2     │
  next step … RECAP ──► GUIDE         ▼                │
                                    ARM_STEP ──────────┘
            "I'm stuck" (GUIDE → TEACH at current step)
                                      │ all valid
                                      ▼
                         CHECK: SPOT_MISTAKE → FINAL → DONE
```

Every transition is logged. The hidden admin panel (`skip_step`, `force_arm_step`, `ghost_mode`, `next_problem`, `reset`) can force any transition, which is the escape hatch on stage.

---

## 9. How the arm turns text into ink

```
arm_text "(x+2)(x+3)=0", slot 2
   │
   ▼ strokes.py: Hershey single-stroke font → polylines (font units)
   ▼ scale to ≥15 mm characters, lay out inside slot 2's rectangle (canvas points)
   ▼ resample every 1.5 mm; add pen-up travel moves between strokes
   ▼ calib.json: canvas (u,v) → table (x,y) affine fit + plane z(u,v)
   ▼ pen-down z = plane − 1.5 mm (into the soft tip); pen-up z = plane + 12 mm
   ▼ IK (URDF, ikpy/placo): position solve, wrist pitch held near "pen down",
     warm-started point to point; reject the whole plan if any point fails
   ▼ stream joint targets to LeRobot SOFollower at 30–50 Hz
     (~25 mm/s drawing, ~80 mm/s travel, max_relative_target safety on)
   ▼ iPad counts touches → closed-loop sanity check
```

**Calibration uses the iPad as the sensor.** You jog the stylus onto nine targets in the tutor-board area, and the iPad reports exactly where each touch landed. Forward kinematics gives the arm's view of each point, and a least-squares fit maps one to the other. The whole process takes about 2 minutes and absorbs pen-holder offsets and a table that isn't level.

---

## 10. How voice flows

**Out (TTS):**

```
LLM text → maths-to-speech normaliser ("x^2" → "x squared")
         → sentence splitter → /tts (Magpie, or Kokoro if TTS_BACKEND=kokoro)
         → WAV clip → /audio/{clip}.wav → iPad AVQueuePlayer → caption follows clip
```

There's a phrase cache for about 20 stock lines, and all Teach-mode clips are pre-rendered.

**In (ASR):**

```
student holds mic → iPad records 16 kHz mono WAV (mic off while tutor speaks)
                 → POST /audio → Parakeet → "minus two and minus three"
                 → speech-to-maths rules → [-2, -3] → judge.py compares to expected
```

**On-device fallback:** if the Spark's speech services fail, the iPad switches to on-device `SFSpeechRecognizer` and `AVSpeechSynthesizer`. Audio still never leaves your hardware.

---

## 11. How the network flows, and how it fails safely

```
Normal:    iPad ──Tailscale (WireGuard, direct)──► tutor-spark:8000
Degraded:  iPad ──Tailscale via DERP relay──► tutor-spark:8000   (works, slower)
Fallback:  all devices on one hotspot/router ──plain LAN──► <spark-lan-ip>:8000
```

- **One port only.** Every model listens on the Spark's localhost, and only the orchestrator is reachable from other devices. There's one thing to debug.
- **Profiles, not hard-coded IPs.** The app has `Tailscale (MagicDNS)`, `Tailscale (100.x)` and `LAN` profiles. Switching is a tap, not a rebuild.
- **Diagnosis order when something breaks:**
  1. iPad settings dots: which service is red?
  2. `tailscale status` and `tailscale ping tutor-spark`: is the Spark online, and is the link direct or relayed?
  3. `curl localhost:8000/health` on the Spark: is it the server or the network?
  4. Switch the profile to the 100.x IP, then the hotspot, then LAN.
- **Privacy:** Tailscale traffic is end-to-end encrypted, and relays forward packets they can't read. On the LAN profile, with `HF_HUB_OFFLINE=1`, you can unplug the internet and everything still works.

---

## 12. What happens when things break

| Failure | What the system does automatically | What you do |
|---|---|---|
| Arm draws but no touches register | Ghost ink animates the planned strokes; the lesson continues | Fix the stylus grounding between runs |
| Arm service down or IK plan rejected | Ghost ink only; the tutor still narrates | Re-home or recalibrate |
| Narration JSON invalid twice | Templated narration | Nothing |
| LLM reply leaks the answer in Guide mode | Regenerates once, then uses a templated hint | Nothing |
| A line is unreadable | Tutor says "Can you rewrite line two more clearly?" | Nothing |
| TTS service down | iPad speaks with `AVSpeechSynthesizer` | Flip `TTS_BACKEND` |
| ASR down | iPad on-device speech recognition | Restart the ASR container |
| Network degrades | Higher latency, with a "thinking" animation | Switch profile or move to the hotspot |
| Anything else | – | Admin panel, then the backup video |

---

## 13. One complete demo run, end to end

1. **Start-up.** Health is all green, and the arm draws its pre-flight tick in slot 5.
2. **Question.** The student writes x² + 5x + 6 = 0. The vision model reads it, the app reads it back, and the student confirms and taps **Teach me**.
3. **Prep.** SymPy builds the steps, the arm estimates its drawing time, one LLM call writes the narration, and the TTS for step 1 is ready.
4. **Step 1.** "We need two numbers that multiply to 6 and add to 5." The arm writes `2×3=6` in slot 1 and `2+3=5` in slot 2.
5. **Step 2.** The arm writes `(x+2)(x+3)=0` in slot 3. The tutor asks "Why plus two?", the student answers aloud, and Parakeet and the judge mark it correct.
6. **Step 3.** The arm writes `x=−2, x=−3`, followed by the recap.
7. **Guide.** On x² − 5x + 6 = 0, the student writes (x+2)(x+3) = 0. The line goes red and the tutor asks about the signs. After a second miss, the arm writes `(x−2)(x−3)=0`.
8. **Twist.** The arm writes a worked solution with a flipped sign, and the student circles it.
9. **Close.** The skill bars fill. Final slide: Nemotron, Parakeet and Magpie all ran on the DGX Spark, with nothing sent to the cloud.
