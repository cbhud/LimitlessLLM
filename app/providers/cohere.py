"""Cohere provider adapter using the OpenAI compatibility endpoint."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import httpx

from app.providers.base import BaseProvider
from app.utils import flatten_message_content

API_BASE = "https://api.cohere.ai/compatibility/v1"


class CohereProvider(BaseProvider):
    """Cohere Command R / R+ via the compatibility endpoint."""

    platform = "cohere"
    name = "Cohere"

    def __init__(self, client: httpx.AsyncClient, timeout_s: float = 30.0) -> None:
        super().__init__(client)
        self.timeout_s = timeout_s

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        opts = options or {}
        body: dict[str, Any] = {
            "model": model_id,
            "messages": flatten_message_content(messages),
        }
        if stream:
            body["stream"] = True
        for key in ("temperature", "max_tokens", "top_p", "tools", "tool_choice"):
            val = opts.get(key)
            if val is not None:
                body[key] = val
        return body

    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self._build_body(messages, model_id, options)
        resp = await self._client.post(
            f"{API_BASE}/chat/completions",
            json=body,
            headers=self._headers(api_key),
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
        )
        if resp.status_code != 200:
            try:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:300])
            except Exception:
                err_msg = resp.text[:300]
            raise Exception(f"Cohere API error {resp.status_code}: {err_msg}")

        data = resp.json()
        data["_routed_via"] = {"platform": "cohere", "model": model_id}
        return data

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
            f"{API_BASE}/chat/completions",
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
                raise Exception(f"Cohere API error {resp.status_code}: {err_msg}")

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
                    pass
