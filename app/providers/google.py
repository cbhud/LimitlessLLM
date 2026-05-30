"""Google Gemini provider adapter.

Translates the OpenAI chat-completions wire format to/from Google's
``generateContent`` / ``streamGenerateContent`` API, including
``functionDeclarations`` ↔ ``tools`` and ``functionResponse`` ↔ ``tool`` role
round-trips.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator, Optional

import httpx

from app.providers.base import BaseProvider
from app.utils import content_to_string

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# ── Schema sanitisation ─────────────────────────────────────────────────────
# Google Gemini accepts only a subset of JSON Schema (~OpenAPI 3.0).
# Strip fields that strict-JSON-Schema clients send but Gemini rejects.

_GEMINI_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "$schema", "$id", "$ref", "$defs", "$comment",
    "definitions",
    "exclusiveMinimum", "exclusiveMaximum",
    "patternProperties", "unevaluatedProperties", "unevaluatedItems",
    "if", "then", "else",
    "contentEncoding", "contentMediaType", "contentSchema",
    "dependentRequired", "dependentSchemas",
    "additionalProperties",
})


def sanitize_for_gemini(schema: Any) -> Any:
    """Recursively strip unsupported JSON-Schema keys."""
    if isinstance(schema, list):
        return [sanitize_for_gemini(item) for item in schema]
    if isinstance(schema, dict):
        return {
            k: sanitize_for_gemini(v)
            for k, v in schema.items()
            if k not in _GEMINI_UNSUPPORTED_SCHEMA_KEYS
        }
    return schema


# ── Helpers ──────────────────────────────────────────────────────────────────


def _safe_parse_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"value": raw}


def _normalize_gemini_args(args: Any) -> str:
    if isinstance(args, str):
        return args
    return json.dumps(args or {})


def _to_gemini_finish_reason(reason: Optional[str]) -> str:
    if not reason:
        return "stop"
    r = reason.upper()
    if r == "MAX_TOKENS":
        return "length"
    if r in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
        return "content_filter"
    return "stop"


def _to_gemini_tools(tools: Optional[list[dict[str, Any]]]) -> Optional[list[dict]]:
    """Convert OpenAI ``tools`` → Gemini ``functionDeclarations``."""
    if not tools:
        return None
    return [{
        "functionDeclarations": [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description"),
                "parameters": sanitize_for_gemini(t["function"].get("parameters")),
            }
            for t in tools
        ],
    }]


def _to_gemini_tool_config(tool_choice: Any) -> Optional[dict]:
    """Convert OpenAI ``tool_choice`` → Gemini ``functionCallingConfig``."""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        mode_map = {"none": "NONE", "required": "ANY", "auto": "AUTO"}
        return {"functionCallingConfig": {"mode": mode_map.get(tool_choice, "AUTO")}}
    if isinstance(tool_choice, dict):
        return {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": [tool_choice["function"]["name"]],
            },
        }
    return None


def _to_gemini_contents(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Translate OpenAI messages → Gemini ``contents`` + ``systemInstruction``."""
    system_texts = [
        content_to_string(m.get("content"))
        for m in messages if m.get("role") == "system"
    ]
    system_texts = [s for s in system_texts if s]

    # Build tool-name lookup from assistant tool_calls
    tool_name_by_call_id: dict[str, str] = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            tool_name_by_call_id[tc["id"]] = tc["function"]["name"]

    contents: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue

        if role == "assistant":
            parts: list[dict[str, Any]] = []
            text = content_to_string(m.get("content"))
            if text:
                parts.append({"text": text})
            for tc in m.get("tool_calls") or []:
                part: dict[str, Any] = {
                    "functionCall": {
                        "id": tc.get("id"),
                        "name": tc["function"]["name"],
                        "args": _safe_parse_object(tc["function"]["arguments"]),
                    },
                }
                if tc.get("thought_signature"):
                    part["thoughtSignature"] = tc["thought_signature"]
                parts.append(part)
            if parts:
                contents.append({"role": "model", "parts": parts})

        elif role == "tool":
            tool_call_id = m.get("tool_call_id")
            if not tool_call_id:
                continue
            tool_name = m.get("name") or tool_name_by_call_id.get(tool_call_id, "tool")
            response = _safe_parse_object(content_to_string(m.get("content")))
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "id": tool_call_id,
                        "name": tool_name,
                        "response": response,
                    },
                }],
            })

        else:  # user
            contents.append({
                "role": "user",
                "parts": [{"text": content_to_string(m.get("content"))}],
            })

    result: dict[str, Any] = {"contents": contents}
    if system_texts:
        result["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}
    return result


def _extract_tool_calls(parts: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if not parts:
        return []
    calls: list[dict[str, Any]] = []
    fallback_idx = 0
    for part in parts:
        fc = part.get("functionCall")
        if not fc or not fc.get("name"):
            continue
        call_id = fc.get("id") or f"call_{int(time.time())}_{fallback_idx}"
        fallback_idx += 1
        call: dict[str, Any] = {
            "id": call_id,
            "type": "function",
            "function": {
                "name": fc["name"],
                "arguments": _normalize_gemini_args(fc.get("args")),
            },
        }
        if part.get("thoughtSignature"):
            call["thought_signature"] = part["thoughtSignature"]
        calls.append(call)
    return calls


def _extract_text(parts: Optional[list[dict[str, Any]]]) -> Optional[str]:
    if not parts:
        return None
    text = "".join(p.get("text", "") for p in parts)
    return text if text else None


# ── Provider ─────────────────────────────────────────────────────────────────


class GoogleProvider(BaseProvider):
    """Google Gemini provider with full payload translation."""

    platform = "google"
    name = "Google AI Studio"

    def __init__(self, client: httpx.AsyncClient, timeout_s: float = 30.0) -> None:
        super().__init__(client)
        self.timeout_s = timeout_s

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        opts = options or {}
        gemini = _to_gemini_contents(messages)
        body: dict[str, Any] = {
            "contents": gemini["contents"],
            "generationConfig": {
                "temperature": opts.get("temperature"),
                "maxOutputTokens": opts.get("max_tokens"),
                "topP": opts.get("top_p"),
            },
        }
        if gemini.get("systemInstruction"):
            body["systemInstruction"] = gemini["systemInstruction"]
        tools = _to_gemini_tools(opts.get("tools"))
        if tools:
            body["tools"] = tools
        tool_config = _to_gemini_tool_config(opts.get("tool_choice"))
        if tool_config:
            body["toolConfig"] = tool_config
        return body

    # ── non-streaming ────────────────────────────────────────────────────────

    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self._build_body(messages, options)
        url = f"{API_BASE}/models/{model_id}:generateContent?key={api_key}"

        resp = await self._client.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
        )
        if resp.status_code != 200:
            try:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:300])
            except Exception:
                err_msg = resp.text[:300]
            raise Exception(f"Google API error {resp.status_code}: {err_msg}")

        data = resp.json()
        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts")
        tool_calls = _extract_tool_calls(parts)
        text = _extract_text(parts)

        usage_meta = data.get("usageMetadata", {})
        usage = {
            "prompt_tokens": usage_meta.get("promptTokenCount", 0),
            "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
            "total_tokens": usage_meta.get("totalTokenCount", 0),
        }

        message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {
            "id": self.make_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_id,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else _to_gemini_finish_reason(
                    candidate.get("finishReason")
                ),
            }],
            "usage": usage,
            "_routed_via": {"platform": "google", "model": model_id},
        }

    # ── streaming ────────────────────────────────────────────────────────────

    async def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        body = self._build_body(messages, options)
        url = f"{API_BASE}/models/{model_id}:streamGenerateContent?alt=sse&key={api_key}"

        async with self._client.stream(
            "POST",
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
        ) as resp:
            if resp.status_code != 200:
                err_text = (await resp.aread()).decode(errors="replace")
                try:
                    err_msg = json.loads(err_text).get("error", {}).get("message", err_text[:300])
                except Exception:
                    err_msg = err_text[:300]
                raise Exception(f"Google API error {resp.status_code}: {err_msg}")

            completion_id = self.make_id()
            emitted_finish = False
            saw_tool_calls = False
            seen_tc_keys: set[str] = set()

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                raw = line[6:]

                if raw == "[DONE]":
                    if not emitted_finish:
                        emitted_finish = True
                        yield {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_id,
                            "choices": [{"index": 0, "delta": {},
                                         "finish_reason": "tool_calls" if saw_tool_calls else "stop"}],
                        }
                    return

                try:
                    chunk_data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                candidate = (chunk_data.get("candidates") or [{}])[0]
                parts = (candidate.get("content") or {}).get("parts", [])

                text = _extract_text(parts)
                tool_calls = _extract_tool_calls(parts)

                # De-duplicate tool calls across SSE frames
                deduped: list[dict[str, Any]] = []
                for call in tool_calls:
                    key = f"{call['id']}:{call['function']['name']}:{call['function']['arguments']}"
                    if key not in seen_tc_keys:
                        seen_tc_keys.add(key)
                        deduped.append(call)

                if (text and len(text) > 0) or deduped:
                    saw_tool_calls = saw_tool_calls or bool(deduped)
                    delta: dict[str, Any] = {}
                    if text:
                        delta["content"] = text
                    if deduped:
                        delta["tool_calls"] = deduped
                    yield {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_id,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    }

                finish_reason = candidate.get("finishReason")
                if finish_reason and not emitted_finish:
                    emitted_finish = True
                    yield {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_id,
                        "choices": [{"index": 0, "delta": {},
                                     "finish_reason": "tool_calls" if saw_tool_calls else _to_gemini_finish_reason(finish_reason)}],
                    }
                    return

            # End of stream without an explicit finish frame
            if not emitted_finish:
                yield {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_id,
                    "choices": [{"index": 0, "delta": {},
                                 "finish_reason": "tool_calls" if saw_tool_calls else "stop"}],
                }
