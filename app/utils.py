"""Content utilities for OpenAI message handling."""

from __future__ import annotations

from typing import Any


def content_to_string(content: Any) -> str:
    """Convert OpenAI message content (string | null | ContentBlock[]) to plain string.

    Accepts the multimodal array envelope that clients like opencode / continue.dev
    send even for text-only messages.  Non-text blocks are dropped silently.
    """
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def flatten_message_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of *messages* with every ``content`` flattened to a string.

    Needed for providers that reject the array-content envelope
    (Cohere, Cloudflare).
    """
    return [{**m, "content": content_to_string(m.get("content"))} for m in messages]
