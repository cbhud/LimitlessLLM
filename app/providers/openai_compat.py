"""Generic adapter for platforms that speak the OpenAI chat-completions wire format.

Covers: Groq, Cerebras, SambaNova, NVIDIA NIM, Mistral, OpenRouter,
GitHub Models, HuggingFace, Ollama Cloud, Zhipu, Kilo, Pollinations, LLM7.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import httpx

from app.providers.base import BaseProvider


# ── Response normalisation ───────────────────────────────────────────────────


def _normalize_choices(data: dict[str, Any]) -> None:
    """Fix provider-specific quirks in response choices (mutates *data* in-place).

    * Flatten array ``content`` (Mistral magistral) → joined string.
    * Fold ``reasoning_content`` / ``reasoning`` into ``content`` when content
      is empty *and* there are no tool_calls (Z.ai, Ollama reasoning models).
    """
    for choice in data.get("choices", []):
        msg = choice.get("message", {})
        content = msg.get("content")

        # Array content → string
        if isinstance(content, list):
            msg["content"] = "".join(
                seg if isinstance(seg, str) else (seg.get("text", "") if isinstance(seg, dict) else "")
                for seg in content
            )

        # Fold reasoning fields into empty content (but NOT when tool_calls
        # are present — content=null is the correct OpenAI shape there).
        has_tool_calls = bool(msg.get("tool_calls"))
        if not has_tool_calls and (msg.get("content") in ("", None)):
            fold = None
            rc = msg.get("reasoning_content")
            if isinstance(rc, str) and rc:
                fold = rc
            else:
                r = msg.get("reasoning")
                if isinstance(r, str) and r:
                    fold = r
            if fold is not None:
                msg["content"] = fold


# ── Provider ─────────────────────────────────────────────────────────────────


class OpenAICompatProvider(BaseProvider):
    """Generic provider for any platform exposing an OpenAI-compatible API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        platform: str,
        name: str,
        base_url: str,
        extra_headers: Optional[dict[str, str]] = None,
        timeout_s: float = 30.0,
    ) -> None:
        super().__init__(client)
        self.platform = platform
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.extra_headers = extra_headers or {}
        self.timeout_s = timeout_s

    # ── helpers ──────────────────────────────────────────────────────────────

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        opts = options or {}
        body: dict[str, Any] = {"model": model_id, "messages": messages}
        if stream:
            body["stream"] = True
        for key in ("temperature", "max_tokens", "top_p", "tools",
                     "tool_choice", "parallel_tool_calls"):
            val = opts.get(key)
            if val is not None:
                body[key] = val
        return body

    # ── non-streaming ────────────────────────────────────────────────────────

    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self._build_body(messages, model_id, options)
        resp = await self._client.post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers=self._headers(api_key),
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
        )

        if resp.status_code != 200:
            try:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:300])
            except Exception:
                err_msg = resp.text[:300]
            raise Exception(f"{self.name} API error {resp.status_code}: {err_msg}")

        data = resp.json()
        _normalize_choices(data)
        data["_routed_via"] = {"platform": self.platform, "model": model_id}
        return data

    # ── streaming ────────────────────────────────────────────────────────────

    async def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        body = self._build_body(messages, model_id, options, stream=True)

        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=body,
            headers=self._headers(api_key),
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
        ) as resp:
            if resp.status_code != 200:
                err_text = (await resp.aread()).decode(errors="replace")
                try:
                    err_msg = json.loads(err_text).get("error", {}).get("message", err_text[:300])
                except Exception:
                    err_msg = err_text[:300]
                raise Exception(f"{self.name} API error {resp.status_code}: {err_msg}")

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    return
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    pass  # skip malformed chunks
