"""Abstract base class for LLM provider adapters."""

from __future__ import annotations

import random
import string
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import httpx


class BaseProvider(ABC):
    """Every provider adapter extends this class.

    The shared :class:`httpx.AsyncClient` is injected at construction time so
    all providers benefit from connection pooling.
    """

    platform: str
    name: str

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @abstractmethod
    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat completion.  Returns an OpenAI-compatible response dict."""
        ...

    @abstractmethod
    async def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming chat completion.  Yields OpenAI-compatible chunk dicts."""
        ...
        # The ``yield`` below is never reached but is required so Python treats
        # this method as an async-generator function in subclass signatures.
        yield {}  # pragma: no cover

    @staticmethod
    def make_id() -> str:
        """Generate a unique ``chatcmpl-…`` identifier."""
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        return f"chatcmpl-{int(time.time())}-{suffix}"
