# Issue: No single place to see all running agents and subagents

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **OPEN**
> Branch: `feat/global-agents-panel`

- **Filed:** 2026-09-17
- **Severity:** medium — running work is invisible unless you hunt per-chat

## Problem

Running agents and their subagents are only visible inside their own
conversation (the per-chat `SubagentsBar`). With work spread across
workspaces and chats, there is no single surface showing everything
currently working, and no quick way to jump to it or stop it.

## Motivation

Long-running agents + background subagents are core to the product thesis
(describe → wait → review from anywhere). If the user can't see what's
running from one place, they can't trust leaving work unattended.

## Approach (scope: active only + workspace jump)

- New backend `GET /api/chats/active`: active chats (via
  `get_active_chat_ids()`) plus starting/running subagents (via
  `list_async_subagents()`, serializable projection already exists).
- New entry in the `SidebarFooter` account menu opening a modal: each active
  chat with its running subagents.
- Row actions: **Open** → `goto('/?workspace=...&chatId=...')`;
  **Stop** → `cancelTask(chatId, messageId)` for chats,
  `cancelChatSubagent(chatId, delegationId)` for subagents.
- Live-update via the existing `events:chat` socket (`chat:active`,
  `chat:subagents`, `done`).
