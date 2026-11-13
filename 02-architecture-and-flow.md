# Socrates software tutor: how a turn works

This is the readable companion to [01-plan.md](01-plan.md). The concrete contracts are [architecture.md](architecture.md), [design.md](design.md) and [Interface.md](Interface.md). The prior arm, Isaac, physical stylus, calibration and preflight flows are retired.

## Check my working

1. The student writes on the PencilKit student layer and taps Check. The iPad snapshots the relevant lines and sends board_revision plus request_id to the Spark.
2. The local vLLM Nemotron model reads the image. The student sees a read-back before any verdict if the model is uncertain about a sign, exponent or line order.
3. The session service gives confirmed text to the bounded SymPy math tool. The tool returns valid, invalid, partial or unknown, a reason code and an evidence ID. The model cannot override that verdict.
4. The Socratic skill in NemoClaw chooses a short next question from that evidence. OpenShell allows only approved local services and tool names. NeMo Guardrails checks input, tool traffic and output.
5. The service sends one tutor turn with caption, a locally generated Magpie clip and a tutor-ink preview. The iPad writes to a separate blue layer only if the board revision still matches. The student may cancel.
6. The student fixes the line. The next Check sees a new revision; the summary later records both the help and an independent transfer attempt.

## Ask or teach one step

A spoken question arrives as a short local WAV upload in the first release. Local ASR or Omni audio input creates a transcript candidate. The agent responds with one question or one worked step, according to the learner's request and help history. Speech and digital writing share a turn ID; cancelling the turn stops both. Continuous audio with barge-in is a later integration, not a claim about the present app.

## Other subjects

A teacher imports approved material on the Spark. Local embedding finds possible passages and a locally qualified reranker may reorder them. The agent cites source IDs. A subject without a deterministic or teacher-approved verifier says “I can help reason through this, but I cannot confirm it is correct.” Nemotron Parse can join this import path only after a supported local Spark recipe is proved. Retrieval is not invoked for the algebra fixture.

## Failure behavior

- Unclear handwriting: ask for confirmation, no grade.
- Unsupported step: explain the uncertainty, no false correctness claim.
- Model or harness down: deterministic local template and caption if verified evidence exists; otherwise retry or stop.
- Speech down: caption and local iPad speech fallback.
- Tutor ink stale: discard preview and request a new turn.
- Guardrail or sandbox denial: visible blocked result, no bypass.
- Spark unreachable: local board remains usable; no pretend tutoring result.

The target is a measured, complete loop on one Spark, not a count of decorative SDK imports. Trace each actual NVIDIA component call with its useful input, result and latency.
