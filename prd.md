# Socrates product requirements

**Status:** Proposed for implementation · **Updated:** 13 November 2026

**Scope:** One supervised, English-language factoring lesson on a paired iPad and one DGX Spark.

**Related:** [Product idea](idea.md) · [Architecture](architecture.md) · [Implementation contracts](design.md) · [Decisions](decisions.md) · [Delivery status](status.md)

## Problem and outcome

Students can produce a plausible-looking algebra step while missing a sign, factor, or root. A worksheet grade often arrives after the student has moved on. Socrates should help the learner notice the specific mismatch, revise it, and apply the same reasoning on a second problem. The parent or caregiver should be able to see what the student did independently and what still needs practice.

The initial audience is a learner already introduced to factoring, with an adult supervising the demo. Run initial evaluation with synthetic fixtures and consenting adult testers. Set the intended learner age, consent and retention policy, and access model before a pilot with minors.

## Scope and priorities

| Priority | Deliverable | Release evidence |
| --- | --- | --- |
| P0 · working lesson | iPad board with Apple Pencil and typed input, confirmed transcription, deterministic factoring checks, Mistake Glow, one-question tutoring with a text fallback, final check, skill map and local caregiver summary. Simulated Ghost Ink. | A student can complete the full flow on one iPad with the robot unplugged. |
| P1 · embodied demo | Push-to-talk with Parakeet, Magpie speech, local Nemotron Socratic skill, Ghost Ink followed by supervised SO-101 plot on paper, and a physical circle that corresponds to the digital annotation. | Voice and paper paths pass their independent gates. The app distinguishes simulated, paper, and screen modes. |
| P2 · shared-screen demo | The SO-101 traces the Ghost Ink path on the iPad with a compatible Apple Pencil and verified fixture. Isaac Lab/LeIsaac supports offline replay and clearance studies. | The operator signs off the screen-contact fixture, stop system, and measured alignment. Until then the product demos with paper or digital ink. |

Exclude open-ended chat, other subjects, arbitrary algebra transformations, automated emails, cloud sync, classroom dashboards, and unattended robot operation from the first release. The caregiver summary lives in the paired dashboard.

## Supported maths and lesson

- Seed problem `x² + 5x + 6 = 0`. Follow with a different problem from a small curated bank, such as `x² − x − 6 = 0`.
- Real-valued `x`. Curate monic quadratics with integer roots, including a repeated root and a zero root. The factoring task accepts two monic linear factors in either order, and the root task accepts the complete unordered set, with repeated roots counted once.
- Check factoring by expanding `(x + p)(x + q)` and comparing coefficients with the original polynomial. Give feedback for wrong sum, wrong product, or both. Grade final roots by comparing the entire submitted set with the expected set and report missing or extra roots. Keep the last accepted step when a submission is invalid.
- Canonical demo: `(x − 2)(x − 3)` has product `6` and sum `−5`. `(x + 2)(x + 3)` is correct, with roots `−2` and `−3`. For the transfer problem, `(x − 3)(x + 2)` is correct, with roots `3` and `−2`.
- The check result distinguishes a verified mathematical mistake, a recognition ambiguity, and unsupported input. Ask the student to confirm or edit ambiguous input before grading. An unsupported step gets a format hint and another try.

## Requirements and acceptance

| ID | Requirement | Acceptance test |
| --- | --- | --- |
| R1 | Capture a row with the Pencil, typed text, or push-to-talk. Show “Read as” for every handwriting or voice submission in the MVP. | On an actual iPad, write the seed, dictate it in a voice-enabled build, and change a misread `−` to `+` before checking. The visible draft survives a transient disconnect. Typed input remains available when mic or vision fails. |
| R2 | Check each confirmed factoring or roots submission and surface structured evidence from SymPy. | Verify both factor orders, missing/extra/duplicate roots, a repeated root, zero, an unsupported expression, and an ambiguous sign. Only an accepted, current-revision result can advance the lesson. |
| R3 | Mistake Glow identifies the cause and locates it on the submitted row when token mapping is available. | The wrong signs are underlined and marked with text on the seed mistake. Without reliable token boxes, highlight the row and give the same reason. Colour and audio have text equivalents. |
| R4 | Ask exactly one short, evidence-grounded question per incorrect submission. Let the student try again or request one more focused clue. | The seed sign error produces one question and no final roots. Invalid, unsafe, late, or unavailable model output uses a deterministic question keyed by the error. The visible text and optional audio match. After two unsuccessful prompts, offer a prerequisite example on a different equation or a way to pause. |
| R5 | Preview an annotation before any robot motion. The operator can approve or cancel a specific plan. | A preview is visible for at least 500 ms before motion eligibility. The displayed version, approved version, and executing version match. A stale preview, active student edit, reconnect, or missing hardware gate keeps the arm parked or stops the current plan safely. |
| R6 | Finish with an independent transfer check and update the skill map from recorded attempts. | A correction after a hint records “practiced with help”. A correct first transfer attempt with no hints records “checked independently” for observed skills. A retry, skipped check, or error leaves independence unearned. No score changes for OCR corrections or robot events. |
| R7 | Provide a local caregiver summary for the completed or incomplete session. | An authorized caregiver can see practiced skills, what the learner corrected, the transfer result, and one next practice suggestion. Reopening the dashboard shows the same summary. No microphone recording or raw handwriting appears in the summary. |
| R8 | Stop or disable robot motion without losing the lesson. | Test the operator stop, loss of robot or iPad heartbeat, stale calibration, serial fault, blocked motion, and an unreachable path. The robot latches a fault, suppresses future moves until local re-arm, and the lesson continues digitally. Physical power cut and independent operator supervision are verified before any contact test. |

## Pilot gates and measures

Targets below are proposed engineering gates. Record device/OS, runtime and model revisions, test fixture, sample count, and observed results before reporting a pass.

| Measure | Initial gate |
| --- | --- |
| Maths correctness | All ≥40 labelled accepted, incorrect, unsupported, and ambiguous cases match the reference outcomes. Include adversarial input and stale revisions. |
| Input quality | ≥90% exact normalized transcription before correction for ≥50 varied in-scope rows or utterances per enabled modality. Report sign/exponent misses and correction frequency separately. All ambiguous or unsupported cases can be corrected without a wrong grade. |
| Responsiveness | On the paired iPad and Spark, p95 ≤1 s from confirmation to deterministic feedback and p95 ≤5 s from confirmation to visible hint across ≥50 warm attempts on the demo LAN. Track recognition time separately (p95 ≤4 s for a single row or short voice clip) and time to first audible hint (p95 ≤7 s). Show a fallback question if generation exceeds its budget. |
| Lesson quality | Complete ≥10 supervised rehearsals, including ≥3 with a model, mic, or arm disabled. Verify one-question feedback, no answer leakage in the hint test corpus, and the independent transfer check in each completed session. Track first correction and transfer completion to establish a baseline. |
| Robot repeatability | Run the [calibration and safety gates](design.md#robot-execution-and-calibration) on the actual fixture before promoting any mode. A passed paper gate earns the paper demo. A passed screen-contact gate earns the shared-screen demo. |

## Build sequence

1. **Digital slice:** agree on versioned payloads and a golden fixture. Build typed seed → bad factors → one question → correction → roots → independent transfer → summary. Replay a fixed Ghost Ink plan in the board.
2. **Capture and voice:** add Pencil capture and local vision recognition. Validate on the actual iPad. Add local push-to-talk, transcription confirmation and audio output when the adapter passes its check.
3. **Tutor runtime:** evaluate the local Nemotron model and Socratic skill with bounded input and output, NeMo Guardrails where supported, and deterministic validation. Keep the prompt library usable without GPU inference.
4. **Motion ladder:** corner calibration → line → circle → square → digits → equation. Run dry runs before each physical rung and prove the stop path at the bench. Demonstrate Ghost Ink and matching paper marks before testing protected iPad contact.
5. **Demo rehearsal:** run both the full and degraded scenarios. Record measured readiness in [status.md](status.md) and decide whether the demo is digital, paper, or shared-screen.
