# Issue: Show per-turn time taken and tokens-per-second

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **OPEN**
> Branch: `feat/turn-timing-tps`

- **Filed:** 2026-09-17
- **Severity:** low (QoL feature, opt-in)

## Problem

When an agent finishes a turn, the usage tooltip shows token counts but not
how long the turn took or how fast tokens arrived — useful signal for judging
provider/model speed and spotting stalls.

## Motivation

Small, honest operational feedback (product thesis: honest operational
boundaries). Helps compare models and notice regressions without leaving the
chat.

## Approach (scope: server timestamps only, toggleable)

- New `UserPreferences.showTurnTiming` (General settings toggle, default
  off), persisted via `stores.ts`.
- Turn seconds = `assistant.created_at − parent user.created_at` (both
  already on `ChatMessageRow`; includes queue/idle time — documented, not
  hidden). Render `· 12s` beside the date in `MessageTimestamp` styling.
- Usage tooltip (`AssistantMessage.svelte`) gains Time + TPS rows:
  `TPS = (output/completion tokens || total − input) / turn_seconds`,
  hidden when tokens or time are missing.
- Frontend-only; no backend change.
