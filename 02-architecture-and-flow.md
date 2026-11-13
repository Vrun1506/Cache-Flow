# Socrates software tutor: how a turn works

This is the readable companion to [final-plan.md](final-plan.md). [prd.md](prd.md) owns scope, [architecture.md](architecture.md) and [design.md](design.md) own component contracts, and [Interface.md](Interface.md) owns the v2 wire contract.

## Tutor my working

1. The learner writes on the PencilKit student layer and taps **Tutor me**. The iPad sends a bounded image, board revision and request ID to the Spark. Local vLLM Nemotron reads it and asks the learner to confirm an ambiguous sign, exponent or line.
2. The Jane Street-derived Socratic skill is active in the NemoClaw/OpenShell tutor agent. It asks one diagnostic question, waits for the student's written or typed answer, and uses the learner's evidence and help history to select one next move.
3. On **Check**, the agent calls the approved local `check_math_step` MCP tool. A safe grammar constructs bounded SymPy objects. The tool returns `valid`, `invalid`, `partial` or `unknown`, with a reason and evidence ID. A single valid root is partial progress; the model cannot upgrade it to complete.
4. NeMo Guardrails checks input, tool traffic and proposed output. The gateway verifies schema, evidence, permission and current board revision. It sends caption first, then matching local speech and bounded tutor ink or a diagram preview. The iPad commits only for the current revision and keeps student ink separate.
5. **Show the answer** lets the tutor write and explain the exact answer on explicit request. The session marks that item assisted and gives a fresh unassisted check before raising the skill level. A local summary records both.

## Other subjects and voice

The same agent can ask questions, explain and draw in physics, history or another domain. Only a registered subject verifier can make a correctness claim; approved local sources can support narrower cited claims. Missing evidence is labelled unknown. The first demo includes local Magpie output with immediate captions and an on-device voice fallback, plus one spoken learner question through Spark-local Parakeet push-to-talk ASR. Omni audio input is the local substitute if Parakeet fails the device gate. Continuous microphone listening and full-duplex barge-in are later work.

## Failure behavior

- Unclear handwriting: confirm before grading.
- Unsupported or timed-out math: return unknown and avoid a correctness claim.
- Stale board or cancelled turn: discard old tutor ink, scene and speech.
- Audio unavailable: preserve caption and use local iPad speech fallback.
- Guardrail or sandbox denial: show a blocked result; do not bypass it.
- Spark unreachable: keep the board usable; do not invent a tutoring result.

Every displayed NVIDIA component must correspond to an actual local call with outcome and latency. A mock or imported SDK does not count as a live integration.
