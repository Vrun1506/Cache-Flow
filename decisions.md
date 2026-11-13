# Socrates engineering decisions

**13 November 2026.** The product owner changed the direction to a software-only, fully local iPad tutor. [prd.md](prd.md) is now authoritative for scope. These decisions describe the target; [status.md](status.md) records what exists.

## Superseded decisions

| ID | Earlier decision | Why superseded / migration |
| --- | --- | --- |
| D01 | Next.js or native iPad plus one station. | The merged SwiftUI/PencilKit client is the sole target surface; the Next.js starter has been removed. Preserve the one-station starting workload. |
| D02 | Separate recognition, symbolic checking and tutor. | Retained. Confirmation and structured math evidence remain mandatory. |
| D03 | Separate Nemotron Nano VL, Nemotron reasoning model, Parakeet and Magpie candidates. | Replace the two-model default with one local multimodal Nemotron 3 Nano Omni in vLLM; qualify separate local ASR and TTS under a shared memory budget. |
| D04 | Versioned physical stroke plan. | Replace with versioned digital tutor-ink preview/commit. No actuator command remains. |
| D05 | Paper-first SO-101, LeRobot, Isaac simulation and screen contact. | Retired. Do not build robot, simulation or calibration workflows. |
| D06 | Local learner evidence and independent transfer. | Retained and extended with deletion, role checks and explicit assistance level. |

The historical details remain in Git history before this rewrite.

## D07 · Local multimodal vLLM model

- **Context/alternatives.** The app needs image reading, dialogue and tool proposals. Two resident models increase memory pressure; hosted inference violates the owner's locality requirement.
- **Choice.** Start with Spark-local vLLM serving Nemotron 3 Nano Omni NVFP4, pinned after an actual device smoke test. It is the only model allowed to propose tutor turns. [NVIDIA's model card](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4) lists image, audio and text input, text output, NVFP4 weights and Spark support.
- **Tradeoff/extension.** One model simplifies residency but may be slower or weaker at handwritten algebra than a specialist. A local perception adapter can be added after a labelled benchmark without changing math policy. No hosted fallback.
- **Evidence/migration.** No model runtime has been observed in this repo. Record exact revision, parser, memory and latency before finalizing.

## D08 · NemoClaw harness with bounded math tool

- **Context/alternatives.** A direct FastAPI→vLLM call is simpler; the requested demonstrable agent harness needs real tool invocation and policy isolation.
- **Choice.** Plan a Socratic skill in NemoClaw, routed to the existing local vLLM service. Use OpenShell to allow only a local MCP math tool and explicitly permitted services. The session API remains the only iPad entry point.
- **Tradeoff/extension.** The harness adds setup and bridge latency. Prove it with a real trace and denial test before claiming integration. The math tool is replaceable by subject strategy behind the same structured evidence contract. [NemoClaw local vLLM](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/set-up-vllm) · [managed MCP](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/mcp-servers/about-managed-mcp-servers).

## D09 · Guardrails and deterministic authority

- **Choice.** SymPy and subject-specific evidence decide mathematical validity. NeMo Guardrails checks input, tool call/result and output. Neither model eloquence nor a guardrail pass proves the math.
- **Tradeoff/extension.** Additional rails add latency; a failed or missing required rail blocks the affected action. Version policies and test both benign and adversarial fixtures. The skill may change its question strategy without changing verifier code. [Tool rail configuration](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/tool-calling).

## D10 · Digital ink protocol v2

- **Choice.** Student Pencil ink and tutor-generated ink are separate layers. A tutor turn binds caption, audio and bounded ink to one board revision and turn ID. Stale turns cannot commit.
- **Tradeoff/extension.** This requires coordinated Swift contract and mock changes, but eliminates unsafe arm semantics. Text/glyph rendering can change behind one preview/commit contract. The v1 arm events are legacy only; see [Interface.md](Interface.md).

## D11 · Local voice first, streaming after measurement

- **Choice.** Keep current push-to-talk WAV and clip playback as the first integration. Qualify local Magpie TTS for output, and compare local Parakeet/Nemotron streaming ASR with the Omni model's audio input. Use on-device speech only as a local fallback.
- **Tradeoff/extension.** Half-duplex is less natural than barge-in but matches the shipped app. True streaming needs a new transport, cancellation and turn-boundary tests. [NVIDIA speech model guidance](https://docs.nvidia.com/nemo/speech/nightly/starthere/choosing_a_model.html).

## D12 · Retrieval is conditional

- **Choice.** Add curated local knowledge only when expanding beyond verifiable algebra. Use local NeMo Retriever embedding and reranking only if Spark co-residency and quality tests pass. Do not invoke them for every turn merely to increase tool count.
- **Tradeoff/extension.** Missing retrieval means some general questions have a narrower evidence base; the tutor must say so. A new source or retriever adapts to one citation contract. Nemotron Parse is deferred because [its current support matrix](https://docs.nvidia.com/nim/ingestion/parse/2.4/support-matrix.html) omits Spark.

## D13 · Measure independent learning

- **Choice.** The session closes with a new item attempted without help; store assistance and verifier evidence separately. Do not claim human-tutor replacement or learning gains before a real evaluation.
- **Evidence.** [Bastani et al., PNAS 2025](https://www.pnas.org/doi/10.1073/pnas.2422633122) found stronger practice performance can coexist with weaker unaided performance for an unrestricted AI assistant. [MRBench](https://aclanthology.org/2025.naacl-long.57/) gives pedagogical dimensions beyond answer correctness.

## Pattern choices for implementation

Use a **Strategy** for subject verifiers so algebra can be extended without changing session sequencing. Use **Adapters** for local vLLM, ASR, TTS, embedding and reranking APIs, preserving their distinct error and latency semantics. Model check/hint/teach requests as idempotent **Commands** bound to board revisions. Keep the finite lesson phases as an explicit **State** transition table; a class per phase is unnecessary unless behavior grows. The session API is a focused **Facade** over the local agent and services. These choices create seams only where model, subject or input variation is expected; no speculative plugin layer is needed for the evidence store. Verify each new adapter or strategy through the composition root, including failures and cancellation, before claiming the open–closed principle holds.
