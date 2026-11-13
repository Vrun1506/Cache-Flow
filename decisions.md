# Socrates engineering decisions

These are proposed decisions for the first supervised demo, recorded on 13 November 2026. Update the status and add evidence when the team adopts or revises them. See the [PRD](prd.md), [architecture](architecture.md), and [implementation contracts](design.md).

| ID | Decision | Status | Components |
| --- | --- | --- | --- |
| D01 | One lesson and one station | Proposed | C1, C2, C6 |
| D02 | Separate recognition, symbolic checking, and tutoring | Proposed | C3, C4, C5 |
| D03 | Local inference with provider adapters and deterministic fallback | Proposed | C2, C4, C5 |
| D04 | Preview and execute one versioned geometry plan | Proposed | C1, C2, C7, C8 |
| D05 | Prove motion on paper before any screen contact | Proposed | C7, C8 |
| D06 | Keep learner evidence local and auditable | Proposed | C1, C2, C6 |

## D01 · One lesson and one station

- **Decision.** Use a Next.js iPad board and caregiver dashboard, a FastAPI lesson app, and SQLite on one DGX Spark. The first lesson covers factoring a monic quadratic, finding its roots, and solving one new problem. A modular backend coordinates local adapters, with an independent robot process owning hardware.
- **Tradeoff.** A cloud dashboard and distributed workers could serve more students, but add identity, network and consistency work ahead of the first working lesson. A native iPad app could access more Pencil-specific APIs, but first qualify the existing web stack on the demo iPad.
- **Extension and revisit.** Input modalities share the confirmation flow, and HTTP is separate from lesson policy. New lessons need explicit strategies and fixtures. Revisit native capture if Safari misses strokes or audio requirements, or database deployment if multiple stations or concurrent writers become necessary. Device checks are pending.

## D02 · Separate recognition, symbolic checking, and tutoring

- **Decision.** Vision and ASR produce editable candidates. A bounded parser builds SymPy objects, and task strategies produce reason codes and numeric evidence. The tutor works from that evidence. Require confirmation of recognized input for the initial lesson.
- **Tradeoff.** An end-to-end multimodal tutor is quicker to prototype but can confuse a bad transcription with a bad algebra step. An explicit confirmation costs a tap and gives the student a way to correct a sign before grading. General symbolic equivalence would accept more forms, but the first lesson is assessing specific factoring and root skills.
- **Extension and revisit.** Swap recognition adapters or add a task strategy without changing lesson sequencing or granting model output grading authority. Revisit auto-confirmation only after an iPad dataset establishes a safe error rate and correction path. The parser, negative cases and answer-leakage checks are pending. [SymPy parser documentation](https://docs.sympy.org/latest/modules/parsing.html)

## D03 · Local inference with provider adapters and deterministic fallback

- **Decision.** Use an NVIDIA Spark-compatible vLLM image, test Nemotron Nano 12B v2 VL first for row recognition, and evaluate Nemotron 3 Nano 30B A3B NVFP4 for tutoring. Qualify Parakeet and Magpie in a local NeMo-Speech.cpp sidecar. Use an in-app Socratic policy with one question per turn. Add a NemoClaw/OpenShell skill and NeMo Guardrails integration after proving the single-turn contract and resource budget.
- **Tradeoff.** Separate vision, tutor, and voice components can be tested independently but share Spark memory. Evaluate a single model for vision and tutoring if two resident models compete for memory. If the tutor model is unavailable, ship the vetted question templates with text feedback. Defer cloud inference in the demo deployment.
- **Extension and revisit.** Providers implement bounded interfaces and are selected at startup. Any provider must pass the same output and timeout tests. Pin images and model revisions once the smoke and rehearsal tests pass. Change selection if measured latency, input quality, memory, or policy results miss PRD gates. Upstream documentation describes the components; the combined runtime is pending verification. [Stack references](architecture.md#stack-references)

## D04 · Preview and execute one versioned geometry plan

- **Decision.** Treat a plan as a command with an immutable version, digest, row revision and fixture profile. Render Ghost Ink from the same path the executor consumes. Require display acknowledgement, an approval tied to that version and local executor checks. Enforce hardware limits and stop independently of model and UI code.
- **Tradeoff.** A model emitting unrestricted SVG or motor commands is convenient for demonstrations but harder to bound and preview reliably. Restrict the first planner to circles and known stroke glyphs. Preflight and local approval add time and prevent replay of a stale or partially executed job.
- **Extension and revisit.** Add a planner intent only through preflight, preview and driver contract checks. The simulator and physical driver use the same command contract. Change the protocol version and preserve old fixtures if path semantics change. Fault injection and stop-distance evidence are pending.

## D05 · Prove motion on paper before any screen contact

- **Decision.** Use SO-101 plus LeRobot and evaluate the MIT-licensed `lerobot-artist` geometry path against the calibrated paper fixture. Qualify corners, line, circle, square, digits, and the equation in order. Provide digital-only and mirrored-paper modes. Treat iPad contact as a separate fixture with a compatible Apple Pencil, compliant mount and independently verified stop. Use offline LeIsaac when the Isaac Sim/Lab pairing is verified.
- **Tradeoff.** Paper does not reproduce screen contact, but proves positioning and lets the team demo the learning flow while investigating pressure and clearance. Simulation helps inspect geometry and reachability. Bench evidence is still needed for backlash, contact and stop behavior. Keep simulator workloads off the live demo critical path.
- **Extension and revisit.** A new surface requires its own fixture calibration and operator sign-off. Preserve the shared normalized path and calibration contracts. No local paper or screen measurement exists yet. [lerobot-artist hardware and calibration](https://github.com/sorob/lerobot-artist) · [LeIsaac](https://github.com/LightwheelAI/leisaac)

## D06 · Keep learner evidence local and auditable

- **Decision.** Persist only the local evidence needed to explain the lesson result and summary. Distinguish independent transfer from assisted correction. Use separate session-scoped student, caregiver and operator roles. Keep raw microphone audio and recognition rasters transient.
- **Tradeoff.** Cross-session mastery tracking and remote parent notifications need a broader identity, retention, and consent design. A local summary answers the MVP question of what happened and what to practice next. Start with one database and transactional attempt and evidence writes.
- **Extension and revisit.** Add new summary views through the authorized evidence contract. Add a schema migration for new durable fields and verify replay and deletion. Agree on learner age, consent, retention and parent access before a real pilot. Recovery and permission tests are pending.
