"""Prompt helpers shared by coding agent adapters."""

from __future__ import annotations

from typing import Any


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict)
        )
    return str(content or "")


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    """Return only the current user turn for native-session agents."""

    for message in reversed(messages):
        if message.get("role") == "user":
            return message_text(message).strip()
    return ""


def turn_prompt_text(messages: list[dict[str, Any]], system_prompt: str, resumed: bool) -> str:
    prompt = latest_user_text(messages)
    if system_prompt and not resumed:
        return f"{system_prompt}\n\n{prompt}" if prompt else system_prompt
    return prompt
