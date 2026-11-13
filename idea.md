# Socrates: a local tutor that shares the page

Socrates is a general-purpose learning agent on a DGX Spark. A student writes on a SwiftUI/PencilKit iPad board, asks for a check or a hint, and hears and sees the tutor respond. The tutor can add its own editable, visibly separate digital ink beside the student's work. The first demonstrable subject is algebra; the architecture admits other subjects only through subject-specific evidence and assessment rules.

## A student turn

1. The student writes a problem or selects a preset. The app sends a bounded image of the relevant region when the student asks to read or check it; background upload after an ink pause is a later, opt-in enhancement.
2. A locally served Nemotron 3 Nano Omni model proposes a transcription. The app shows the reading and asks for correction when symbols or layout are uncertain.
3. A domain tool checks the step. In algebra, a constrained parser and SymPy return a structured verdict, the first questionable location, and a reason. The model cannot mark its own answer correct.
4. The Socratic skill in a NemoClaw-managed, OpenShell-restricted agent chooses one next move: ask a question, give a short hint, demonstrate a single step, or request clarification. NeMo Guardrails screens inputs, tool calls/results and outgoing text.
5. The same tutor turn emits a caption, a local Magpie voice clip, and a digital ink plan. The iPad previews the tutor layer and commits it only if the board revision still matches. The student can interrupt or erase the tutor layer without changing their own ink.
6. The student tries a new item without help. The session summary separates assisted practice from independent evidence.

The experience is conversational and responsive, but the first release uses push-to-talk and deliberate Check/Hint actions. Continuous live audio and automatic background checking are later gates that need latency, privacy and interruption testing.

## Product principle

Help the student take the next step. A complete worked solution is available only through a deliberate explain or reveal action, and it does not count as independent mastery. Research on AI-assisted mathematics shows that success during practice can hide weaker unaided learning; see [research.md](research.md).

## Current boundary

The iPad scaffold and mock lesson are in the repository. No Spark tutor backend, NemoClaw harness, model runtime, guardrail, speech service, retrieval service or real digital-tutor integration is implemented yet. The SO-101, Isaac Sim/Lab, LeRobot and physical calibration paths are retired. See [prd.md](prd.md), [architecture.md](architecture.md), [Interface.md](Interface.md), and [status.md](status.md).
