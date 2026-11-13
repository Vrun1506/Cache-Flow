"""Runtime configuration for the model layer (final-plan.md §8)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .llm.client import OllamaClient
from .llm.openai_compat import OpenAICompatClient
from .llm.types import Tier


def _default_models() -> dict[Tier, str]:
    return {
        "reasoner": "nemotron-3-nano:30b",
        "router": "nemotron-3.5-lightning:30b",
        "vision": "qwen3.6:35b-a3b",
    }


@dataclass
class LLMConfig:
    base_url: str = "http://127.0.0.1:11434"
    models: dict[Tier, str] = field(default_factory=_default_models)
    num_ctx: int = 8192
    keep_alive: str = "5m"
    http_timeout: float = 90.0  # per-call read limit; raise it on a slow dev machine
    api: str = "ollama"  # "ollama" (native /api/chat) or "openai" (vLLM / NIM style /v1/chat/completions)
    api_key: Optional[str] = field(default=None, repr=False)  # optional bearer; never printed

    @classmethod
    def from_env(cls) -> "LLMConfig":
        env = os.environ.get
        defaults = _default_models()
        api = env("TUTOR_LLM_API", "ollama").strip().lower()
        if api not in ("ollama", "openai"):
            raise ValueError(f"TUTOR_LLM_API must be 'ollama' or 'openai', got {api!r}")
        fallback_url = "http://127.0.0.1:11434" if api == "ollama" else "http://127.0.0.1:8000"
        return cls(
            base_url=env("TUTOR_LLM_BASE_URL") or env("OLLAMA_BASE_URL", fallback_url),
            api=api,
            api_key=env("TUTOR_LLM_API_KEY") or None,
            models={
                "reasoner": env("TUTOR_MODEL_REASONER", defaults["reasoner"]),
                "router": env("TUTOR_MODEL_ROUTER", defaults["router"]),
                "vision": env("TUTOR_MODEL_VISION", defaults["vision"]),
            },
            keep_alive=env("TUTOR_KEEP_ALIVE", "5m"),
            http_timeout=float(env("TUTOR_HTTP_TIMEOUT", "90")),
        )


def build_client(config: LLMConfig) -> OllamaClient | OpenAICompatClient:
    if config.api == "openai":
        return OpenAICompatClient(
            base_url=config.base_url,
            models=config.models,
            num_ctx=config.num_ctx,
            timeout=config.http_timeout,
            api_key=config.api_key,
        )
    return OllamaClient(
        base_url=config.base_url,
        models=config.models,
        num_ctx=config.num_ctx,
        keep_alive=config.keep_alive,
        timeout=config.http_timeout,
    )


@dataclass
class SchedulerConfig:
    """Scheduler limits (final-plan.md §16.12). Phase 0 sets the real concurrency."""

    max_concurrency: int = 2
    swap_threshold_s: float = 8.0

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        env = os.environ.get
        return cls(
            max_concurrency=int(env("TUTOR_MAX_CONCURRENCY", "2")),
            swap_threshold_s=float(env("TUTOR_SWAP_THRESHOLD_S", "8")),
        )


def build_scheduled_client(config: LLMConfig, scheduler: SchedulerConfig | None = None):
    """An OllamaClient behind the swap-aware scheduler. Returns a ScheduledBackend."""
    from .llm.scheduler import ScheduledBackend  # lazy: keeps config importable without the scheduler

    scheduler = scheduler or SchedulerConfig.from_env()
    client = build_client(config)

    async def ps() -> set[str]:
        return set(await client.loaded())

    return ScheduledBackend(
        client,
        config.models,
        max_concurrency=scheduler.max_concurrency,
        ps=ps,
        swap_threshold_s=scheduler.swap_threshold_s,
    )
