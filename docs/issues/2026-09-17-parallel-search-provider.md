# Issue: Add Parallel.ai as a web search provider

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **OPEN**
> Branch: `feat/parallel-search-provider`

- **Filed:** 2026-09-17
- **Severity:** low (feature) — new provider option

## Problem

The admin panel offers Exa, Tavily, Brave, Firecrawl, SearXNG, Perplexity,
DuckDuckGo, and generic Chat Completions — but not
[Parallel.ai](https://parallel.ai), an AI-agent-oriented search API whose
LLM-optimized excerpts fit the agent use case well.

## Motivation

More provider choice with no lock-in (product thesis: interoperable, avoid
vendor lock-in). Parallel's objective + keyword-query API maps cleanly onto
the existing provider interface.

## Approach

- New `cptr/utils/web/parallel.py` mirroring `tavily.py`/`exa.py`:
  `POST https://api.parallel.ai/v1/search` with `x-api-key`, body
  `{objective, search_queries, max_results, max_chars_per_result}`, formatted
  to the shared `**title**\nurl\nexcerpt` shape.
- Wire into `utils/web/search.py`: `PARALLEL_API_KEY` /
  `web.parallel_api_key`, explicit `provider == "parallel"` + auto-chain slot.
- Admin `Web.svelte`: dropdown option + key field, saved via `updateConfig`;
  i18n `admin.webParallelKey`/`admin.webParallelHint` in `en.json`.
- Unit test with mocked httpx; live validation needs a user-supplied key.
