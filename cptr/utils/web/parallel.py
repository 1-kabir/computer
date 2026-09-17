"""Parallel search provider: web search built for AI agents.

https://parallel.ai — POST /v1/search with a natural-language objective
plus keyword queries; returns LLM-optimized excerpts per result.
"""

from __future__ import annotations

import httpx


async def search(query: str, api_key: str, count: int = 5) -> str:
    """Search using Parallel's AI-native search API."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.parallel.ai/v1/search",
            json={
                "objective": query,
                "search_queries": [query],
                "max_results": count,
                "max_chars_per_result": 3000,
            },
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("results", [])[:count]:
        title = item.get("title", "")
        url = item.get("url", "")
        excerpts = item.get("excerpts", [])
        # Guard: a bare string would join into newline-per-char.
        if isinstance(excerpts, str):
            excerpts = [excerpts]
        text = "\n".join(e for e in excerpts if e)[:1500]
        parts = []
        if title:
            parts.append(f"**{title}**")
        if url:
            parts.append(url)
        if text:
            parts.append(text)
        if parts:
            results.append("\n".join(parts))

    return "\n\n".join(results) if results else "No results found."
