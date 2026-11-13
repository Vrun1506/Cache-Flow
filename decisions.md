# Socrates engineering decisions

**13 November 2026.** The product owner changed the direction to a software-only, fully local iPad tutor. [prd.md](prd.md) is authoritative for scope, [final-plan.md](final-plan.md) is the build handoff, and [Interface.md](Interface.md) owns the wire contract. These decisions describe the target; [status.md](status.md) records what exists. The Ollama/NAT proposal in the former `final-plan.md` revision is superseded by D14 below.

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
- **Choice.** Run a versioned, attributed adaptation of the [Jane Street `socratic-tutor` skill](https://github.com/janestreet/agent_skills/blob/master/socratic-tutor/SKILL.md) in NemoClaw, routed to the existing local vLLM service. Preserve its [MIT license](https://github.com/janestreet/agent_skills/blob/master/LICENSE.md). Use OpenShell to allow only a local MCP math tool and explicitly permitted services. Prove the skill is active in a real turn. The session API remains the only iPad entry point.
- **Tradeoff/extension.** The harness adds setup and bridge latency. Prove it with a real trace and denial test before claiming integration. The math tool is replaceable by subject strategy behind the same structured evidence contract. [NemoClaw local vLLM](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/inference/local-inference/set-up-vllm) · [managed MCP](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/mcp-servers/about-managed-mcp-servers).

## D09 · Guardrails and deterministic authority

- **Choice.** SymPy and subject-specific evidence decide mathematical validity. NeMo Guardrails checks input, tool call/result and output. Neither model eloquence nor a guardrail pass proves the math.
- **Tradeoff/extension.** Additional rails add latency; a failed or missing required rail blocks the affected action. Version policies and test both benign and adversarial fixtures. The skill may change its question strategy without changing verifier code. [Tool rail configuration](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/tool-calling).

## D10 · Digital ink protocol v2

- **Choice.** Student Pencil ink and tutor-generated ink are separate layers. A tutor turn binds caption, audio and bounded ink to one board revision and turn ID. Stale turns cannot commit.
- **Tradeoff/extension.** This requires coordinated Swift contract and mock changes, but eliminates unsafe arm semantics. Text/glyph rendering can change behind one preview/commit contract. The v1 arm events are legacy only; see [Interface.md](Interface.md).

## D11 · Local voice first, streaming after measurement

- **Choice.** Keep current push-to-talk WAV and clip playback as the first voice-input transport. One spoken learner follow-up is in the first demo. Start with [Spark-supported Parakeet 1.1B CTC English ASR](https://docs.nvidia.com/nim/speech/26.10.0/reference/support-matrix/asr.html); if it fails the co-residency/quality gate, use Omni's local audio input and report that substitution. Local Magpie speech output and matching captions are also in the first demo, with a documented local equivalent if Magpie cannot run. Use on-device speech as a local output fallback.
- **Tradeoff/extension.** Half-duplex is less natural than barge-in but matches the shipped app. True streaming needs a new transport, cancellation and turn-boundary tests. [NVIDIA speech model guidance](https://docs.nvidia.com/nemo/speech/nightly/starthere/choosing_a_model.html).

## D12 · Retrieval is conditional

- **Choice.** Add curated local knowledge only when expanding beyond verifiable algebra. Use local NeMo Retriever embedding and reranking only if Spark co-residency and quality tests pass. Do not invoke them for every turn merely to increase tool count.
- **Tradeoff/extension.** Missing retrieval means some general questions have a narrower evidence base; the tutor must say so. A new source or retriever adapts to one citation contract. Nemotron Parse is deferred because [its current support matrix](https://docs.nvidia.com/nim/ingestion/parse/2.4/support-matrix.html) omits Spark.

## D13 · Measure independent learning

- **Choice.** The session closes with a new item attempted without help; store assistance and verifier evidence separately. Do not claim human-tutor replacement or learning gains before a real evaluation.
- **Evidence.** [Bastani et al., PNAS 2025](https://www.pnas.org/doi/10.1073/pnas.2422633122) found stronger practice performance can coexist with weaker unaided performance for an unrestricted AI assistant. [MRBench](https://aclanthology.org/2025.naacl-long.57/) gives pedagogical dimensions beyond answer correctness.

## D14 · One runtime path for the first demo

- **Context/alternatives.** The later `final-plan.md` revision proposed Ollama native transport, NAT orchestration and three model tiers. That conflicts with the owner's Spark-local vLLM/NemoClaw direction and adds swaps and a second agent-runtime boundary.
- **Choice.** One resident multimodal Nemotron served by local vLLM, one NemoClaw/OpenShell tutor agent, one FastAPI gateway and one local math MCP tool. Do not put Ollama or NAT in the critical loop. Gate the exact vLLM model on Spark handwriting, tool-call, memory and speech tests; a replacement stays behind the same local vLLM adapter.
- **Tradeoff/evidence.** A single model may trail a specialist on handwriting or reasoning. Phase 0 records measured failures and the chosen replacement; no Spark integration has yet been observed.

## D15 · General-purpose tutoring, bounded grading

- **Context/alternatives.** The owner wants an agent that can tutor across subjects and also give the exact answer on request. A purely algebraic demo is too narrow; universal correctness claims would be unsupported.
- **Choice.** P0 includes handwriting, one-question-at-a-time teaching, local two-way push-to-talk speech, separate tutor writing, a subject-relevant diagram, explicit Show the answer, an independent transfer item and a second-domain teaching turn. Only registered verifiers may say “verified correct.” Other explanations carry source or uncertainty labels. A single-problem coaching adaptation shortens the upstream Jane Street diagnostic and is labelled as such.
- **Tradeoff/evidence.** Demo breadth increases integration work. Cut optional animation and extra model tiers before cutting the core interaction; verify the full journey on a real iPad.

## D16 · Step-aware, safe math verification

- **Context/alternatives.** The superseded plan compared every equation step by equal `solveset` and used `parse_expr` on untrusted strings. Equal sets reject a valid single root as partial progress, and [SymPy says `parse_expr` uses `eval`](https://docs.sympy.org/latest/modules/parsing.html).
- **Choice.** Parse a bounded whitelist grammar into SymPy objects without `parse_expr`. Require `step_kind`: equivalent equation, factorization/identity, candidate root, complete solution set or numeric result. A candidate root is checked against the original equation and labelled `partial` until all roots are found. Exact symbolic evidence is required for `valid`; timeout or unsupported input is `unknown`.
- **Tradeoff/evidence.** The grammar initially supports fewer expressions. Add syntax through parser cases and fixtures, and domain verifiers through the subject Strategy; test domain changes, extraneous roots, parser attacks and resource limits.

## D17 · One revisioned board and scene contract

- **Choice.** [Interface.md](Interface.md) v2 owns all iPad endpoints and events. Add semantic diagram/plot events to the same revisioned preview/commit and cancellation flow as tutor ink, with no executable model code on iPad. No parallel `/session/{id}/solve` API is introduced.
- **Tradeoff/evidence.** Swift v1 arm events and the mock need coordinated migration. Shared JSON fixtures and reconnect/stale-revision tests prove compatibility.

## Pattern choices for implementation

Use a **Strategy** for subject verifiers so algebra can be extended without changing session sequencing. Use **Adapters** for local vLLM, ASR, TTS and optional retrieval APIs, preserving their distinct error and latency semantics. Register bounded board/scene handlers at the iPad rendering boundary. Model tutor/check/hint/show-answer requests as idempotent **Commands** bound to board revisions. Keep finite lesson phases as an explicit **State** transition table; a class per phase is unnecessary unless behavior grows. The session API is a focused **Facade** over the local agent and services. These choices create seams only where model, subject, speech or visual variation is expected; no speculative plugin layer is needed for the evidence store. Verify each new adapter or strategy through the composition root, including failures and cancellation, before claiming the open–closed principle holds.
