"""Cloudflare Workers AI provider adapter.

API key format: ``"account_id:api_token"``  — the account ID is extracted
from the key to build the per-account URL.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import httpx

from app.providers.base import BaseProvider
from app.utils import content_to_string


class CloudflareProvider(BaseProvider):
    """Cloudflare Workers AI with account-specific URL construction."""

    platform = "cloudflare"
    name = "Cloudflare Workers AI"

    def __init__(self, client: httpx.AsyncClient, timeout_s: float = 30.0) -> None:
        super().__init__(client)
        self.timeout_s = timeout_s

    @staticmethod
    def _parse_key(api_key: str) -> tuple[str, str]:
        sep = api_key.find(":")
        if sep == -1:
            raise Exception('Cloudflare key must be in format "account_id:api_token"')
        return api_key[:sep], api_key[sep + 1:]

    @staticmethod
    def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Flatten content to string and collapse ``null`` → ``""``."""
        return [{**m, "content": content_to_string(m.get("content"))} for m in messages]

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
            "messages": self._normalize_messages(messages),
        }
        if stream:
            body["stream"] = True
        for key in ("temperature", "max_tokens", "top_p", "tools",
                     "tool_choice", "parallel_tool_calls"):
            val = opts.get(key)
            if val is not None:
                body[key] = val
        return body

    def _extract_error(self, resp_data: Any, fallback: str) -> str:
        if isinstance(resp_data, dict):
            msg = resp_data.get("error", {}).get("message")
            if msg:
                return msg
            errors = resp_data.get("errors")
            if isinstance(errors, list) and errors:
                return errors[0].get("message", fallback)
        return fallback

    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        account_id, token = self._parse_key(api_key)
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
        body = self._build_body(messages, model_id, options)

        resp = await self._client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
        )
        if resp.status_code != 200:
            try:
                err_msg = self._extract_error(resp.json(), resp.text[:300])
            except Exception:
                err_msg = resp.text[:300]
            raise Exception(f"Cloudflare API error {resp.status_code}: {err_msg}")

        data = resp.json()
        data["_routed_via"] = {"platform": "cloudflare", "model": model_id}
        return data

    async def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        account_id, token = self._parse_key(api_key)
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
        body = self._build_body(messages, model_id, options, stream=True)

        async with self._client.stream(
            "POST",
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
        ) as resp:
            if resp.status_code != 200:
                err_text = (await resp.aread()).decode(errors="replace")
                try:
                    err_msg = self._extract_error(json.loads(err_text), err_text[:300])
                except Exception:
                    err_msg = err_text[:300]
                raise Exception(f"Cloudflare API error {resp.status_code}: {err_msg}")

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
