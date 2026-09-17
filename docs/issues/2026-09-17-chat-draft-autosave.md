# Issue: Chat input draft is lost on resize / reload / remount

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **OPEN**
> Branch: `feat/chat-draft-autosave`

- **Filed:** 2026-09-17
- **Severity:** medium — data loss of user-authored text (annoying, frequent)

## Problem

When resizing panels/tabs, moving the browser tab that views OI Computer, or
resizing panels/tabs inside OI Computer, the agent-conversation input box
(`ChatInput`, TipTap editor) is frequently remounted and **any text being
composed is wiped**. The user must retype from scratch.

## Motivation

Composed messages can be long (bug reports, specs, multi-part instructions).
Losing them to a layout change is a daily paper cut that punishes
phone/small-screen use, where resizes and tab switches are constant.

## Approach

- Autosave the in-progress input per chat to `localStorage`, debounced, keyed
  `cptr:draft:<workspace>::<chatId|tabId>` so drafts never merge across
  chats or workspaces. Clear the key on successful send.
- On mount, if the editor is empty and a fresh draft exists (recent reload /
  remount), show a small non-blocking restore nudge ("Restore draft from
  HH:MM? [Restore] [Discard]") — never autofocus-stealing, never modal.
- Migrate `new-`/`pending-` tab keys to the real `chatId` once assigned.
- New helper `lib/stores/drafts.ts`; changes in `ChatInput.svelte`,
  `ChatPanel.svelte`.
