"""Search dispatcher: picks provider based on config.

Priority in auto mode:
1. Exa        (EXA_API_KEY or web.exa_api_key)
2. Perplexity (PERPLEXITY_API_KEY or web.perplexity_api_key, optional PERPLEXITY_BASE_URL or web.perplexity_base_url)
3. Parallel   (PARALLEL_API_KEY or web.parallel_api_key)
4. Tavily     (TAVILY_API_KEY or web.tavily_api_key)
5. Brave      (BRAVE_API_KEY or web.brave_api_key)
6. Firecrawl  (FIRECRAWL_API_KEY or web.firecrawl_api_key)
7. SearXNG    (SEARXNG_BASE_URL or web.searxng_base_url)
8. DuckDuckGo (zero-config fallback)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


async def _get_key(env_var: str, config_key: str) -> str:
    """Check env var first, then DB config."""
    val = os.environ.get(env_var, "")
    if val:
        return val
    try:
        from cptr.models import Config

        return (await Config.get(config_key)) or ""
    except Exception:
        return ""


async def _get_config(key: str) -> str:
    """Read a single config value from DB."""
    try:
        from cptr.models import Config

        return (await Config.get(key)) or ""
    except Exception:
        return ""


async def web_search_handler(query: str) -> str:
    """Search the web using the configured or best available provider."""
    from cptr.utils.web import (
        exa,
        perplexity,
        parallel,
        tavily,
        brave,
        duckduckgo,
        chat_completions,
        firecrawl,
        searxng,
    )

    # Check if web access is disabled by admin
    enabled = await _get_config("web.enabled")
    if enabled == "false" or enabled is False:
        return "Error: web search is disabled by the administrator."

    provider = await _get_config("web.search_provider") or "auto"

    exa_key = await _get_key("EXA_API_KEY", "web.exa_api_key")
    perplexity_key = await _get_key("PERPLEXITY_API_KEY", "web.perplexity_api_key")
    perplexity_url = (await _get_config("web.perplexity_base_url")) or os.environ.get(
        "PERPLEXITY_BASE_URL", ""
    )
    parallel_key = await _get_key("PARALLEL_API_KEY", "web.parallel_api_key")
    tavily_key = await _get_key("TAVILY_API_KEY", "web.tavily_api_key")
    brave_key = await _get_key("BRAVE_API_KEY", "web.brave_api_key")
    firecrawl_key = await _get_key("FIRECRAWL_API_KEY", "web.firecrawl_api_key")
    if not firecrawl_key:
        firecrawl_key = await _get_config("browser.firecrawl_api_key")
    firecrawl_url = (await _get_config("web.firecrawl_base_url")) or os.environ.get(
        "FIRECRAWL_BASE_URL", ""
    )
    if not firecrawl_url:
        firecrawl_url = await _get_config("browser.firecrawl_base_url")
    searxng_url = (await _get_config("web.searxng_base_url")) or os.environ.get(
        "SEARXNG_BASE_URL", ""
    )
    cc_key = await _get_key("CHAT_COMPLETIONS_SEARCH_API_KEY", "web.chat_completions_api_key")
    cc_url = (await _get_config("web.chat_completions_base_url")) or os.environ.get(
        "CHAT_COMPLETIONS_SEARCH_BASE_URL", ""
    )
    cc_model = (await _get_config("web.chat_completions_model")) or os.environ.get(
        "CHAT_COMPLETIONS_SEARCH_MODEL", ""
    )

    # Explicit provider mode
    if provider != "auto":
        try:
            if provider == "exa":
                if not exa_key:
                    return "Error: Exa API key not configured."
                return await exa.search(query, exa_key)
            elif provider == "perplexity":
                if not perplexity_key:
                    return "Error: Perplexity API key not configured."
                return (
                    await perplexity.search(query, perplexity_key, base_url=perplexity_url)
                    if perplexity_url
                    else await perplexity.search(query, perplexity_key)
                )
            elif provider == "parallel":
                if not parallel_key:
                    return "Error: Parallel API key not configured."
                return await parallel.search(query, parallel_key)
            elif provider == "tavily":
                if not tavily_key:
                    return "Error: Tavily API key not configured."
                return await tavily.search(query, tavily_key)
            elif provider == "brave":
                if not brave_key:
                    return "Error: Brave API key not configured."
                return await brave.search(query, brave_key)
            elif provider == "firecrawl":
                if not firecrawl_key:
                    return "Error: Firecrawl API key not configured."
                if firecrawl_url:
                    return await firecrawl.search(query, firecrawl_key, base_url=firecrawl_url)
                return await firecrawl.search(query, firecrawl_key)
            elif provider == "searxng":
                if not searxng_url:
                    return "Error: SearXNG base URL not configured."
                return await searxng.search(query, searxng_url)
            elif provider == "duckduckgo":
                return await duckduckgo.search(query)
            elif provider == "chat_completions":
                if not cc_key or not cc_url or not cc_model:
                    return "Error: Chat Completions search requires API key, base URL, and model."
                return await chat_completions.search(query, cc_key, cc_url, cc_model)
            else:
                return f"Error: unknown search provider '{provider}'."
        except Exception as e:
            logger.error("Search provider %s failed: %s", provider, e)
            return f"Error: {provider} search failed. {e}"

    # Auto mode: try each provider with a key, fall back to DuckDuckGo
    providers = []
    if exa_key:
        providers.append(("exa", lambda: exa.search(query, exa_key)))
    if perplexity_key:
        _pplx_kw = {"base_url": perplexity_url} if perplexity_url else {}
        providers.append(
            ("perplexity", lambda: perplexity.search(query, perplexity_key, **_pplx_kw))
        )
    if parallel_key:
        providers.append(("parallel", lambda: parallel.search(query, parallel_key)))
    if tavily_key:
        providers.append(("tavily", lambda: tavily.search(query, tavily_key)))
    if brave_key:
        providers.append(("brave", lambda: brave.search(query, brave_key)))
    if firecrawl_key:
        _fc_kw = {"base_url": firecrawl_url} if firecrawl_url else {}
        providers.append(("firecrawl", lambda: firecrawl.search(query, firecrawl_key, **_fc_kw)))
    if searxng_url:
        providers.append(("searxng", lambda: searxng.search(query, searxng_url)))
    providers.append(("duckduckgo", lambda: duckduckgo.search(query)))

    for name, fn in providers:
        try:
            return await fn()
        except Exception as e:
            logger.warning("Search provider %s failed: %s", name, e)
            continue

    return "Error: all search providers failed."
