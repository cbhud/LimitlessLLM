"""Request router — the brain of the proxy.

Picks the best available (model, key) pair per request based on:
  1. Fallback-chain priority order from config.yaml.
  2. Dynamic penalty that demotes models hit by recent 429s.
  3. Round-robin key rotation within each model.
  4. Sticky sessions (30 min) that keep multi-turn conversations on the
     same model to prevent hallucination from mid-conversation switches.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Optional, Set

from app.config import get_config
from app.providers import get_provider
from app.providers.base import BaseProvider
from app.services.ratelimit import can_make_request, can_use_tokens, is_on_cooldown


# ── Route result ─────────────────────────────────────────────────────────────


@dataclass
class RouteResult:
    """Everything the proxy endpoint needs to execute one attempt."""
    provider: BaseProvider
    model_id: str
    model_idx: int       # Index in fallback_chain (used for penalties/sticky)
    api_key: str
    key_idx: int         # Index into config.providers[platform].keys
    platform: str
    display_name: str


class RoutingError(Exception):
    """Raised when no model/key combination is available."""
    pass


# ── Dynamic penalty tracking ────────────────────────────────────────────────

PENALTY_PER_429 = 3
MAX_PENALTY = 10
DECAY_INTERVAL_S = 2 * 60      # 2 minutes
DECAY_AMOUNT = 1

# model_idx → {count, last_hit, penalty}
_penalties: dict[int, dict] = {}


def record_rate_limit_hit(model_idx: int) -> None:
    """Record a 429 — increases the model's priority penalty so it sinks."""
    now = time.time()
    entry = _penalties.get(model_idx)
    if entry:
        entry["count"] += 1
        entry["last_hit"] = now
        entry["penalty"] = min(entry["penalty"] + PENALTY_PER_429, MAX_PENALTY)
    else:
        _penalties[model_idx] = {"count": 1, "last_hit": now, "penalty": PENALTY_PER_429}


def record_success(model_idx: int) -> None:
    """Record a success — reduces the model's priority penalty."""
    entry = _penalties.get(model_idx)
    if entry:
        entry["penalty"] = max(0, entry["penalty"] - 1)
        if entry["penalty"] == 0:
            del _penalties[model_idx]


def _get_penalty(model_idx: int) -> int:
    """Get current penalty with time-based decay applied."""
    entry = _penalties.get(model_idx)
    if not entry:
        return 0

    now = time.time()
    elapsed = now - entry["last_hit"]
    decay_steps = int(elapsed / DECAY_INTERVAL_S)
    if decay_steps > 0:
        entry["penalty"] = max(0, entry["penalty"] - decay_steps * DECAY_AMOUNT)
        entry["last_hit"] = now
        if entry["penalty"] == 0:
            del _penalties[model_idx]
            return 0

    return entry["penalty"]


# ── Round-robin state ────────────────────────────────────────────────────────

_round_robin: dict[str, int] = {}      # "platform:model_id" → next key index


# ── Sticky sessions ─────────────────────────────────────────────────────────

STICKY_TTL_S = 30 * 60  # 30 minutes

# SHA-1(first user message) → {model_idx, last_used}
_sticky: dict[str, dict] = {}


def _get_session_key(messages: list[dict]) -> str:
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    if not first_user or not isinstance(first_user.get("content"), str):
        return ""
    h = hashlib.sha1(first_user["content"].encode()).hexdigest()
    multi = "multi" if len(messages) > 2 else "single"
    return f"{h}:{multi}"


def get_sticky_model(messages: list[dict]) -> Optional[int]:
    """Return the preferred model_idx for multi-turn conversations."""
    if not any(m.get("role") == "assistant" for m in messages):
        return None

    key = _get_session_key(messages)
    if not key:
        return None

    entry = _sticky.get(key)
    if not entry:
        return None

    if time.time() - entry["last_used"] > STICKY_TTL_S:
        del _sticky[key]
        return None
    return entry["model_idx"]


def set_sticky_model(messages: list[dict], model_idx: int) -> None:
    """Bind this conversation to *model_idx* for the sticky-session window."""
    key = _get_session_key(messages)
    if not key:
        return
    _sticky[key] = {"model_idx": model_idx, "last_used": time.time()}

    # Evict expired entries when the map grows too large
    if len(_sticky) > 500:
        now = time.time()
        expired = [k for k, v in _sticky.items() if now - v["last_used"] > STICKY_TTL_S]
        for k in expired:
            del _sticky[k]


# ── Main routing function ───────────────────────────────────────────────────


def route_request(
    estimated_tokens: int = 1000,
    skip_keys: Optional[Set[str]] = None,
    preferred_model_idx: Optional[int] = None,
) -> RouteResult:
    """Pick the best available model and key.

    Raises :class:`RoutingError` if every model/key combination is exhausted.
    """
    config = get_config()

    # Build sorted fallback chain: (original_idx, entry, effective_priority)
    chain: list[tuple[int, object, int]] = []
    for idx, entry in enumerate(config.fallback_chain):
        if not entry.enabled:
            continue
        chain.append((idx, entry, idx + _get_penalty(idx)))

    chain.sort(key=lambda x: x[2])

    # Sticky session — move preferred model to the front
    if preferred_model_idx is not None:
        for i, (idx, _entry, _prio) in enumerate(chain):
            if idx == preferred_model_idx:
                if i > 0:
                    chain.insert(0, chain.pop(i))
                break

    for idx, entry, _ in chain:
        platform = entry.platform
        model_id = entry.model_id

        provider = get_provider(platform)
        if not provider:
            continue

        provider_cfg = config.providers.get(platform)
        if not provider_cfg or not provider_cfg.keys:
            continue

        keys = provider_cfg.keys
        limits = {
            "rpm": entry.limits.rpm,
            "rpd": entry.limits.rpd,
            "tpm": entry.limits.tpm,
            "tpd": entry.limits.tpd,
        }

        # Round-robin across keys for this model
        rr_key = f"{platform}:{model_id}"
        start = _round_robin.get(rr_key, 0)

        for attempt in range(len(keys)):
            key_idx = (start + attempt) % len(keys)
            api_key = keys[key_idx]

            skip_id = f"{platform}:{model_id}:{key_idx}"
            if skip_keys and skip_id in skip_keys:
                continue

            if is_on_cooldown(platform, model_id, key_idx):
                continue
            if not can_make_request(platform, model_id, key_idx, limits):
                continue
            if not can_use_tokens(platform, model_id, key_idx, estimated_tokens, limits):
                continue

            # Found a working key!
            _round_robin[rr_key] = (start + attempt + 1) % len(keys)
            return RouteResult(
                provider=provider,
                model_id=model_id,
                model_idx=idx,
                api_key=api_key,
                key_idx=key_idx,
                platform=platform,
                display_name=entry.display_name,
            )

        # All keys for this model failed — advance round-robin anyway
        _round_robin[rr_key] = (start + len(keys)) % len(keys)

    raise RoutingError(
        "All models exhausted. Add more API keys or wait for rate limits to reset."
    )
