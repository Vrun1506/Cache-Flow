# Evidence and NVIDIA fit for the local Socrates plan

**Reviewed 13 November 2026.** This is a design synthesis, not a claim that these models or services are installed. Product choices are in [decisions.md](decisions.md); implementation evidence is in [status.md](status.md).

## Learning-science implications

| Primary study | What it found | Design consequence and limit |
| --- | --- | --- |
| [Bastani et al., PNAS 2025](https://www.pnas.org/doi/10.1073/pnas.2422633122) | In a high-school math field experiment, unrestricted AI raised practice scores but harmed subsequent unaided exam performance; a teacher-guided tutor design removed the measured negative effect without proving positive exam gains. | Require student attempts, staged hints, and an unaided transfer item. Do not infer learning from a completed assisted problem. |
| [Pardos and Bhandari, PLOS ONE 2024](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0304013) | AI-authored help achieved learning gains comparable to human-authored help in their tested math items and population. | Short, targeted help is a credible feature; the study does not establish equivalence to a live human tutor across subjects. |
| [Stepwise Verification and Remediation, 2024](https://arxiv.org/abs/2407.09136) | Grounding tutor replies in stepwise error verification improved error-focused feedback in the authors' evaluated setting. | Give the Socratic agent a structured first-error result, rather than asking it to grade its own prose. |
| [MRBench, NAACL 2025](https://aclanthology.org/2025.naacl-long.57/) and [MathTutorBench, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.11/) | Tutor quality spans misconception identification, scaffolding, actionability and student understanding as well as correctness. | Evaluate transcripts, tool evidence and next-step usefulness. Benchmark scores alone are not learner outcomes. |
| [GuideEval, EMNLP 2026](https://aclanthology.org/2026.findings-emnlp.1588/) | The studied LLM tutors often missed adaptive scaffolding during confusion/redirection. | Track learner state and assistance history; test multi-turn recovery and human review. |

These sources support cautious product hypotheses. A supervised learner study is still needed before claims about outcomes or “replacing tutors.”

## NVIDIA components and actual purpose

| Component | Proposed useful call | Local fit / gate |
| --- | --- | --- |
| [Nemotron 3 Nano Omni NVFP4](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4) through [Spark vLLM](https://build.nvidia.com/spark/nemotron/overview) | Read a bounded board image; propose a Socratic response and tool call. | Model card lists Spark, image/audio/text input and text output. Pin a working vLLM image and prove parser, handwriting accuracy and shared-load latency locally. |
| [NemoClaw](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/about/how-it-works) plus [OpenShell](https://docs.nvidia.com/openshell/about/architecture) | Run the Socratic skill with local vLLM and an allowed math MCP tool under sandbox policy. | Verify the app-to-harness bridge and denied egress on the actual Spark. A harness setup alone is not a student-facing tool call. |
| [NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/tool-calling) | Validate learner input, declared tool calls and results, and spoken/written output. | Pin the engine/config; assert required rails loaded. Rails do not replace server-side math, authentication or bounds. |
| [Magpie TTS Multilingual NIM](https://docs.nvidia.com/nim/speech/26.10.0/reference/support-matrix/tts.html) | Speak the exact caption for a tutor turn. | NVIDIA lists Spark and streaming output; batch_size=8 profile lists about 12.6 GiB GPU memory. Co-residency with vLLM remains unmeasured. |
| [Nemotron ASR Streaming / Parakeet](https://docs.nvidia.com/nim/speech/26.10.0/reference/support-matrix/asr.html) | Convert local microphone audio into text. | Spark support depends on the exact ASR model/profile. Existing iPad sends full WAV. Compare dedicated ASR with Omni audio input. |
| [NeMo Retriever Embedding](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/latest/getting-started.html) | Index approved local passages and retrieve top candidates. | A Spark Arm64 profile exists for a 1B multimodal embed model; prove residency and usefulness for non-math questions. |
| [NeMo Retriever Reranking](https://docs.nvidia.com/nim/nemo-retriever/text-reranking/latest/getting-started.html) | Reorder a short candidate list before citing. | A Spark 1B profile exists, but current 8B reranker measurements on GB10 are too large for an assumed 64 GB co-resident plan. Benchmark the small profile or omit. |
| [Nemotron Parse NIM](https://docs.nvidia.com/nim/ingestion/parse/2.4/overview.html) | Parse imported textbook pages into layout-aware text for indexing. | Current [support matrix](https://docs.nvidia.com/nim/ingestion/parse/2.4/support-matrix.html) lists A100/A10G/GB200/H100/L40S/RTX PRO 6000, not GB10 Spark. No local supported deployment has been established; defer it. |
| [NeMo Agent Toolkit evaluation](https://docs.nvidia.com/nemo/agent-toolkit/latest/improve-workflows/evaluate.html) | Offline evaluation of tutor traces and tool use. | Optional development tool, not part of a student turn; use only if it accepts the local harness trace. |

SymPy, FastAPI, SwiftUI and PencilKit are non-NVIDIA tools with essential roles. Count actual distinct, useful integrations in a demo trace rather than package names or simulated calls.

## Locality and feasibility gates

1. Inspect the real Spark's RAM, driver and container support; the current review could not authenticate to spark9.local.
2. Download model/container assets once and pin digests. Disconnect internet for the live rehearsal.
3. Run the vLLM model with an image, then verify one real harness-to-math-tool call, then add Magpie/ASR, and only then retrieval. Record available unified memory and p95 latency after each stage.
4. If any optional model fails the shared-load test, remove it from the live path. Preserve captions, math evidence and a completed lesson.
