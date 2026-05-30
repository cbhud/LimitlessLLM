"""Pydantic request models for the OpenAI-compatible proxy endpoint.

Only the *inbound* request is validated here.  Provider responses are forwarded
as raw dicts to avoid serialisation overhead on the hot path.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible ``/v1/chat/completions`` request body."""

    model: Optional[str] = None
    messages: list[dict[str, Any]] = Field(min_length=1)
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, gt=0)
    top_p: Optional[float] = Field(None, ge=0, le=1)
    stream: Optional[bool] = None
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    parallel_tool_calls: Optional[bool] = None

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        valid_roles = {"system", "user", "assistant", "tool"}
        for i, msg in enumerate(v):
            role = msg.get("role")
            if role is None:
                raise ValueError(f"Message {i} missing required 'role' field")
            if role not in valid_roles:
                raise ValueError(
                    f"Message {i} has invalid role '{role}'; "
                    f"expected one of {valid_roles}"
                )
        return v
