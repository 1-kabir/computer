# Issue: View and arrange tabs across workspaces in one UI

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **OPEN**
> Branch: `feat/cross-workspace-tabs`

- **Filed:** 2026-09-17
- **Severity:** low (feature) — new capability, large surface area

## Problem

Opening a workspace from the sidebar scopes the entire UI to that workspace:
panels and tabs (browsers, terminals, files, chats) can only be manipulated
within it. There is no way to see a chat from workspace A next to a terminal
from workspace B.

## Motivation

Real work spans repos: review an agent diff in one workspace while watching
tests in another, or keep a long-running agent chat visible beside the files
it edits elsewhere. Forcing one-workspace-at-a-time breaks that flow.

## Approach (MVP: cross-workspace tab strip)

Full shell restructure is out of scope. Instead:

- Extend `Tab` with `workspacePath?: string`; panes (`ChatPanel`, file,
  terminal) resolve data by the tab's workspace, not `currentWorkspace`.
- Tab strip renders mixed-workspace tabs with a small workspace badge.
- Sidebar stays per-workspace navigation; URL reflects the active tab's
  workspace (`?workspace=<active-tab-ws>&chatId=`).
- Keep per-workspace server-side layout persistence as-is; store
  cross-workspace pinned tab refs in user prefs.
- Out of scope: unified sidebar tree, cross-workspace drag of live
  terminal/browser sessions (sessions are path-bound).
