"""Computer utility task model configuration."""

from __future__ import annotations

UTILITY_MODEL_CONFIG_KEYS: dict[str, str] = {
    "title_generation": "chat.title_generation.model",
    "summary_generation": "chat.context_compaction.model",
    "tool_approval_review": "tool_approval.review.model",
    "memory_background_review": "memory.background_review.model",
    "skills_background_review": "skills.background_review.model",
    "git_commit_message_generation": "git.commit_message_generation.model",
}


async def configured_utility_model(task: str) -> str | None:
    """Return the configured model id for a Computer utility task, if set."""
    key = UTILITY_MODEL_CONFIG_KEYS.get(task)
    if not key:
        return None

    from cptr.models import Config

    value = await Config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
