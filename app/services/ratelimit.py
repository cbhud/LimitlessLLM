"""In-memory sliding-window rate-limit tracker with escalating cooldowns.

All state lives in module-level dicts — zero I/O on the hot path.
State resets on process restart, which is acceptable because the
rate-limit windows are short (1 min / 1 day) and a restart naturally
resets our view of provider quotas.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

# ── Constants ────────────────────────────────────────────────────────────────

MINUTE: float = 60.0
DAY: float = 24 * 60 * MINUTE

# Escalating cooldown durations (indexed by number of 429 hits in last 24 h)
COOLDOWN_DURATIONS: list[float] = [
    2 * MINUTE,       # 1st hit
    10 * MINUTE,      # 2nd
    60 * MINUTE,      # 3rd  (1 hour)
    DAY,              # 4th and beyond
]

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class Window:
    """Sliding-window counters for a single (platform, model, key, kind) tuple."""
    timestamps: list[float] = field(default_factory=list)
    token_entries: list[tuple[float, int]] = field(default_factory=list)


# Key format: "platform:modelId:keyIdx:kind"  where kind ∈ {rpm, rpd, tpm, tpd}
_windows: dict[str, Window] = {}

# Cooldown expiry:  "platform:modelId:keyIdx" → expiry timestamp
_cooldowns: dict[str, float] = {}

# Escalating cooldown hit tracking: same key → list of hit timestamps (last 24 h)
_cooldown_hits: dict[str, list[float]] = {}


# ── Internal helpers ─────────────────────────────────────────────────────────


def _get_window(key: str) -> Window:
    w = _windows.get(key)
    if w is None:
        w = Window()
        _windows[key] = w
    return w


def _request_count(platform: str, model_id: str, key_idx: int, window_s: float) -> int:
    now = time.time()
    kind = "rpm" if window_s == MINUTE else "rpd"
    key = f"{platform}:{model_id}:{key_idx}:{kind}"
    w = _get_window(key)
    cutoff = now - window_s
    w.timestamps = [ts for ts in w.timestamps if ts > cutoff]
    return len(w.timestamps)


def _token_count(platform: str, model_id: str, key_idx: int, window_s: float) -> int:
    now = time.time()
    kind = "tpm" if window_s == MINUTE else "tpd"
    key = f"{platform}:{model_id}:{key_idx}:{kind}"
    w = _get_window(key)
    cutoff = now - window_s
    w.token_entries = [(ts, t) for ts, t in w.token_entries if ts > cutoff]
    return sum(t for _, t in w.token_entries)


# ── Public API ───────────────────────────────────────────────────────────────


def can_make_request(
    platform: str,
    model_id: str,
    key_idx: int,
    limits: dict[str, Optional[int]],
) -> bool:
    """Return ``True`` if a request fits within RPM / RPD limits."""
    rpm = limits.get("rpm")
    if rpm is not None and _request_count(platform, model_id, key_idx, MINUTE) >= rpm:
        return False
    rpd = limits.get("rpd")
    if rpd is not None and _request_count(platform, model_id, key_idx, DAY) >= rpd:
        return False
    return True


def can_use_tokens(
    platform: str,
    model_id: str,
    key_idx: int,
    estimated_tokens: int,
    limits: dict[str, Optional[int]],
) -> bool:
    """Return ``True`` if *estimated_tokens* fit within TPM / TPD limits."""
    tpm = limits.get("tpm")
    if tpm is not None and _token_count(platform, model_id, key_idx, MINUTE) + estimated_tokens > tpm:
        return False
    tpd = limits.get("tpd")
    if tpd is not None and _token_count(platform, model_id, key_idx, DAY) + estimated_tokens > tpd:
        return False
    return True


def record_request(platform: str, model_id: str, key_idx: int) -> None:
    """Append a request timestamp to the RPM and RPD windows."""
    now = time.time()
    for kind in ("rpm", "rpd"):
        _get_window(f"{platform}:{model_id}:{key_idx}:{kind}").timestamps.append(now)


def record_tokens(platform: str, model_id: str, key_idx: int, tokens: int) -> None:
    """Append a token count to the TPM and TPD windows."""
    now = time.time()
    for kind in ("tpm", "tpd"):
        _get_window(f"{platform}:{model_id}:{key_idx}:{kind}").token_entries.append((now, tokens))


# ── Cooldowns ────────────────────────────────────────────────────────────────


def get_next_cooldown_duration(platform: str, model_id: str, key_idx: int) -> float:
    """Return an escalating cooldown duration based on recent 429 history."""
    key = f"{platform}:{model_id}:{key_idx}"
    now = time.time()
    hits = [t for t in _cooldown_hits.get(key, []) if t > now - DAY]
    hits.append(now)
    _cooldown_hits[key] = hits
    idx = min(len(hits) - 1, len(COOLDOWN_DURATIONS) - 1)
    return COOLDOWN_DURATIONS[idx]


def set_cooldown(platform: str, model_id: str, key_idx: int, duration_s: float = 60.0) -> None:
    """Put a ``(platform, model, key)`` tuple on cooldown for *duration_s* seconds."""
    _cooldowns[f"{platform}:{model_id}:{key_idx}"] = time.time() + duration_s


def is_on_cooldown(platform: str, model_id: str, key_idx: int) -> bool:
    """Return ``True`` if the tuple is currently on cooldown."""
    key = f"{platform}:{model_id}:{key_idx}"
    expiry = _cooldowns.get(key)
    if expiry is None:
        return False
    if time.time() > expiry:
        del _cooldowns[key]
        return False
    return True
