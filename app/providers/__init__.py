"""Provider registry — maps platform names to provider adapter instances.

Only providers whose API keys are present in the loaded config are registered.
"""

from __future__ import annotations

from typing import Optional

import httpx

from app.config import AppConfig
from app.providers.base import BaseProvider
from app.providers.cloudflare import CloudflareProvider
from app.providers.cohere import CohereProvider
from app.providers.google import GoogleProvider
from app.providers.openai_compat import OpenAICompatProvider

# ── Module-level registry ────────────────────────────────────────────────────

_registry: dict[str, BaseProvider] = {}

# Platform → constructor kwargs for the generic OpenAI-compatible adapter.
_OPENAI_COMPAT_PLATFORMS: dict[str, dict] = {
    "groq":         {"name": "Groq",              "base_url": "https://api.groq.com/openai/v1"},
    "cerebras":     {"name": "Cerebras",           "base_url": "https://api.cerebras.ai/v1"},
    "sambanova":    {"name": "SambaNova",           "base_url": "https://api.sambanova.ai/v1"},
    "nvidia":       {"name": "NVIDIA NIM",          "base_url": "https://integrate.api.nvidia.com/v1"},
    "mistral":      {"name": "Mistral",             "base_url": "https://api.mistral.ai/v1"},
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "extra_headers": {"HTTP-Referer": "http://localhost:3001", "X-Title": "LimitlessLLMProxy"},
    },
    "github":       {"name": "GitHub Models",       "base_url": "https://models.github.ai/inference"},
    "huggingface":  {"name": "HuggingFace Router",  "base_url": "https://router.huggingface.co/v1"},
    "ollama":       {"name": "Ollama Cloud",         "base_url": "https://ollama.com/v1", "timeout_s": 120.0},
}


def init_providers(config: AppConfig, client: httpx.AsyncClient) -> None:
    """Populate the registry with only the providers that have keys in *config*."""
    global _registry
    _registry.clear()

    configured = set(config.providers.keys())

    # Google — unique Gemini API format
    if "google" in configured:
        _registry["google"] = GoogleProvider(client)

    # Cohere — unique compatibility endpoint
    if "cohere" in configured:
        _registry["cohere"] = CohereProvider(client)

    # Cloudflare — unique URL structure (account_id:token)
    if "cloudflare" in configured:
        _registry["cloudflare"] = CloudflareProvider(client)

    # All OpenAI-compatible platforms
    for platform, spec in _OPENAI_COMPAT_PLATFORMS.items():
        if platform in configured:
            _registry[platform] = OpenAICompatProvider(
                client=client,
                platform=platform,
                name=spec["name"],
                base_url=spec["base_url"],
                extra_headers=spec.get("extra_headers"),
                timeout_s=spec.get("timeout_s", 30.0),
            )


def get_provider(platform: str) -> Optional[BaseProvider]:
    """Return the adapter for *platform*, or ``None`` if it wasn't registered."""
    return _registry.get(platform)


def get_all_providers() -> list[BaseProvider]:
    """Return every registered provider adapter."""
    return list(_registry.values())
