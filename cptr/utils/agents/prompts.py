"""Prompt helpers shared by coding agent adapters."""

from __future__ import annotations

from typing import Any

from cptr.env import AGENT_SEED_TRANSCRIPT_MAX_CHARS


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return str(content or "")


def session_turn_prompt_text(messages: list[dict[str, Any]], resumed: bool) -> str:
    """Return the prompt for a native-session agent turn."""

    last_user_index = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            last_user_index = index
            break
    if last_user_index is None:
        return ""

    prompt = message_text(messages[last_user_index]).strip()
    if resumed or AGENT_SEED_TRANSCRIPT_MAX_CHARS <= 0:
        return prompt

    parts = []
    for message in messages[:last_user_index]:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = message_text(message).strip()
        if text:
            parts.append(f"<{role}>\n{text}\n</{role}>")

    selected = []
    total = 0
    truncated = False
    for part in reversed(parts):
        if len(part) > AGENT_SEED_TRANSCRIPT_MAX_CHARS:
            selected.append(
                "[Earlier conversation truncated.]\n" + part[-AGENT_SEED_TRANSCRIPT_MAX_CHARS:]
            )
            truncated = True
            break
        next_total = total + len(part) + (2 if selected else 0)
        if selected and next_total > AGENT_SEED_TRANSCRIPT_MAX_CHARS:
            truncated = True
            break
        selected.append(part)
        total = next_total
    if truncated and not selected[-1].startswith("[Earlier conversation truncated.]"):
        selected.append("[Earlier conversation truncated.]")
    transcript = "\n\n".join(reversed(selected))
    if not transcript:
        return prompt
    return (
        "The following is prior conversation context from before this native agent "
        "session began. Use it for context, then answer only the latest user request.\n\n"
        f"<prior_conversation>\n{transcript}\n</prior_conversation>\n\n"
        f"<latest_user_request>\n{prompt}\n</latest_user_request>"
    )


def turn_prompt_text(messages: list[dict[str, Any]], system_prompt: str, resumed: bool) -> str:
    prompt = session_turn_prompt_text(messages, resumed)
    if system_prompt and not resumed:
        return f"{system_prompt}\n\n{prompt}" if prompt else system_prompt
    return prompt
