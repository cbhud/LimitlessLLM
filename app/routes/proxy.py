"""OpenAI-compatible proxy endpoints.

POST /v1/chat/completions  — with automatic fallover (up to 20 attempts)
GET  /v1/models            — model listing from config
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import get_config
from app.models import ChatCompletionRequest
from app.services.ratelimit import (
    get_next_cooldown_duration,
    record_request,
    record_tokens,
    set_cooldown,
)
from app.services.router import (
    RoutingError,
    get_sticky_model,
    record_rate_limit_hit,
    record_success,
    route_request,
    set_sticky_model,
)
from app.utils import content_to_string

logger = logging.getLogger("proxy")

router = APIRouter(prefix="/v1")

AUTO_MODEL_ID = "auto"
MAX_RETRIES = 20


# ── Auth ─────────────────────────────────────────────────────────────────────


def _authenticate(request: Request) -> bool:
    """Constant-time comparison of the bearer token against the unified key."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    token = auth[7:]
    expected = get_config().unified_api_key
    return hmac.compare_digest(token.encode(), expected.encode())


# ── Retryable error detection ────────────────────────────────────────────────

_RETRYABLE_PATTERNS = (
    "429", "rate limit", "too many requests",
    "quota", "resource_exhausted",
    "aborted", "timeout", "etimedout", "timed out",
    "econnrefused", "econnreset", "connect",
    "503", "unavailable",
    "500", "internal server error",
    "413", "payload too large", "request body too large",
    "request entity too large", "content too large",
    "404", "not found", "no endpoints found",
    "api error 400",
)


def _is_retryable(err: Exception) -> bool:
    # httpx transport errors are always retryable
    if isinstance(err, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    msg = str(err).lower()
    return any(p in msg for p in _RETRYABLE_PATTERNS)


# ── GET /v1/models ───────────────────────────────────────────────────────────


@router.get("/models")
async def list_models(request: Request):
    if not _authenticate(request):
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Invalid API key", "type": "authentication_error"}},
        )

    config = get_config()
    models = [
        {
            "id": AUTO_MODEL_ID,
            "object": "model",
            "created": 0,
            "owned_by": "limitlessllm",
            "name": "Auto (router picks the best available model)",
        },
    ]
    for entry in config.fallback_chain:
        if entry.enabled:
            models.append({
                "id": entry.model_id,
                "object": "model",
                "created": 0,
                "owned_by": entry.platform,
                "name": entry.display_name,
            })

    return {"object": "list", "data": models}


# ── POST /v1/chat/completions ────────────────────────────────────────────────


@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    start = time.time()

    # ── Auth ──
    if not _authenticate(request):
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Invalid API key", "type": "authentication_error"}},
        )

    messages = body.messages
    requested_model = body.model
    stream = body.stream or False

    # ── Build options dict ──
    options: dict[str, Any] = {}
    for key in ("temperature", "max_tokens", "top_p", "tools",
                "tool_choice", "parallel_tool_calls"):
        val = getattr(body, key, None)
        if val is not None:
            options[key] = val

    # ── Token estimation (~4 chars per token) ──
    estimated_input = sum(
        max(1, len(content_to_string(m.get("content"))) // 4)
        for m in messages
    )
    estimated_total = estimated_input + (body.max_tokens or 1000)

    # ── Model pinning / sticky ──
    config = get_config()
    preferred_model_idx: int | None = None

    if requested_model and requested_model != AUTO_MODEL_ID:
        found = False
        for i, entry in enumerate(config.fallback_chain):
            if entry.model_id == requested_model and entry.enabled:
                preferred_model_idx = i
                found = True
                break
        if not found:
            disabled = any(e.model_id == requested_model for e in config.fallback_chain)
            reason = "is disabled" if disabled else "is not in the catalog"
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": (
                            f"Model '{requested_model}' {reason}. "
                            "Use 'auto' or call /v1/models for the available list."
                        ),
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                    },
                },
            )
    else:
        preferred_model_idx = get_sticky_model(messages)

    # ── Retry loop ──
    skip_keys: set[str] = set()
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        # Route
        try:
            route = route_request(estimated_total, skip_keys or None, preferred_model_idx)
        except RoutingError as err:
            if last_error:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "message": f"All models rate-limited. Last error: {last_error}",
                            "type": "rate_limit_error",
                        },
                    },
                )
            return JSONResponse(
                status_code=503,
                content={"error": {"message": str(err), "type": "routing_error"}},
            )

        record_request(route.platform, route.model_id, route.key_idx)

        try:
            if stream:
                return await _handle_streaming(
                    route, messages, options, estimated_input, attempt,
                )
            else:
                result = await route.provider.chat_completion(
                    route.api_key, messages, route.model_id, options,
                )
                total_tokens = result.get("usage", {}).get("total_tokens", 0)
                record_tokens(route.platform, route.model_id, route.key_idx, total_tokens)
                record_success(route.model_idx)
                set_sticky_model(messages, route.model_idx)

                headers = {"X-Routed-Via": f"{route.platform}/{route.model_id}"}
                if attempt > 0:
                    headers["X-Fallback-Attempts"] = str(attempt)
                return JSONResponse(content=result, headers=headers)

        except Exception as err:
            if _is_retryable(err):
                skip_id = f"{route.platform}:{route.model_id}:{route.key_idx}"
                skip_keys.add(skip_id)
                cd = get_next_cooldown_duration(route.platform, route.model_id, route.key_idx)
                set_cooldown(route.platform, route.model_id, route.key_idx, cd)
                record_rate_limit_hit(route.model_idx)
                last_error = err
                logger.info(
                    "%.60s from %s, falling back (%d/%d)",
                    str(err), route.display_name, attempt + 1, MAX_RETRIES,
                )
                continue

            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "message": f"Provider error ({route.display_name}): {err}",
                        "type": "provider_error",
                    },
                },
            )

    # Exhausted all retries
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "message": f"All models rate-limited after {MAX_RETRIES} attempts. Last: {last_error}",
                "type": "rate_limit_error",
            },
        },
    )


# ── Streaming helper ─────────────────────────────────────────────────────────


async def _handle_streaming(
    route,
    messages: list[dict[str, Any]],
    options: dict[str, Any],
    estimated_input: int,
    attempt: int,
) -> StreamingResponse:
    """Start a streaming response.

    We pull the *first* chunk eagerly so that pre-stream errors (HTTP failures,
    auth errors) propagate as exceptions to the retry loop.  Once we have a
    valid first chunk, we commit to this provider and return a
    ``StreamingResponse``.  Mid-stream errors emit an ``error`` SSE frame.
    """
    gen = route.provider.stream_chat_completion(
        route.api_key, messages, route.model_id, options,
    )

    # Pull first chunk — may raise (propagates to caller's retry loop)
    try:
        first_chunk = await gen.__anext__()
    except StopAsyncIteration:
        first_chunk = None

    # Capture values for the closure (route won't change, but be explicit)
    _platform = route.platform
    _model_id = route.model_id
    _key_idx = route.key_idx
    _model_idx = route.model_idx
    _display_name = route.display_name
    total_output_tokens = 0

    async def sse_generator():
        nonlocal total_output_tokens

        # Yield the buffered first chunk
        if first_chunk is not None:
            text = (first_chunk.get("choices", [{}])[0]
                    .get("delta", {}).get("content") or "")
            total_output_tokens += max(1, len(text) // 4) if text else 0
            yield f"data: {json.dumps(first_chunk)}\n\n"

        # Continue streaming
        try:
            async for chunk in gen:
                text = (chunk.get("choices", [{}])[0]
                        .get("delta", {}).get("content") or "")
                total_output_tokens += max(1, len(text) // 4) if text else 0
                yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as mid_err:
            logger.error("Mid-stream error from %s: %s", _display_name, mid_err)
            error_payload = {
                "error": {
                    "message": f"Provider error ({_display_name}): stream interrupted",
                    "type": "stream_error",
                },
            }
            yield f"data: {json.dumps(error_payload)}\n\n"

        yield "data: [DONE]\n\n"

        # Post-stream bookkeeping
        record_tokens(_platform, _model_id, _key_idx, estimated_input + total_output_tokens)
        record_success(_model_idx)
        set_sticky_model(messages, _model_idx)

    headers: dict[str, str] = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Routed-Via": f"{_platform}/{_model_id}",
    }
    if attempt > 0:
        headers["X-Fallback-Attempts"] = str(attempt)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers=headers,
    )
