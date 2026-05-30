"""Configuration loader — reads config.yaml + .env, resolves env-var references."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


# ── Pydantic config models ──────────────────────────────────────────────────

class RateLimits(BaseModel):
    rpm: Optional[int] = None
    rpd: Optional[int] = None
    tpm: Optional[int] = None
    tpd: Optional[int] = None


class FallbackEntry(BaseModel):
    platform: str
    model_id: str
    display_name: str
    enabled: bool = True
    limits: RateLimits = RateLimits()


class ProviderConfig(BaseModel):
    keys: list[str] = []


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 3001
    log_level: str = "info"


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    unified_api_key: str = "change-me"
    providers: dict[str, ProviderConfig] = {}
    fallback_chain: list[FallbackEntry] = []


# ── Env-var resolution ───────────────────────────────────────────────────────

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: object) -> object:
    """Recursively replace ``${VAR}`` with ``os.environ[VAR]`` (empty if unset)."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


# ── Module-level singleton ───────────────────────────────────────────────────

_config: Optional[AppConfig] = None


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load, resolve, and validate the application configuration.

    Call once at startup.  After that, use :func:`get_config`.
    """
    global _config

    if config_path is None:
        config_path = str(Path(__file__).parent.parent / "config.yaml")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    resolved = _resolve_env_vars(raw)

    # Env-var override for the unified API key
    env_key = os.environ.get("UNIFIED_API_KEY")
    if env_key:
        resolved["unified_api_key"] = env_key  # type: ignore[index]

    config = AppConfig(**resolved)  # type: ignore[arg-type]

    # Strip providers that ended up with no usable keys after env resolution
    cleaned: dict[str, ProviderConfig] = {}
    for platform, prov in config.providers.items():
        valid_keys = [k for k in prov.keys if k.strip()]
        if valid_keys:
            prov.keys = valid_keys
            cleaned[platform] = prov
    config.providers = cleaned

    _config = config
    return config


def get_config() -> AppConfig:
    """Return the loaded config singleton (raises if :func:`load_config` hasn't been called)."""
    if _config is None:
        raise RuntimeError("Configuration not loaded. Call load_config() first.")
    return _config
