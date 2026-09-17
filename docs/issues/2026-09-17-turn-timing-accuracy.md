# Issue: Turn timing measures queue wait (or unrelated turns), not actual work

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **FIXED** (branch `fix/turn-timing-accuracy`)

- **Filed:** 2026-09-17
- **Severity:** high (feature defect — the timing feature shipped misleading numbers)

## Problem

The turn-timing feature (`feat/turn-timing-tps`) computes duration as
`assistant.created_at − nearest-user-ancestor.created_at` on the frontend.
That model is wrong for this app's data shape:

1. **"0s" turns.** Assistant rows are created as `done=False` placeholders at
   task start with `created_at=now_ms()` — the same instant as the user row.
   Completion (`done=True` saves) never touches `created_at`. So the delta
   measures queue wait, which is ~0 in the normal path. Long agents (minutes
   of tool use) show `· 0s`.
2. **"2m" nonsense on regenerate.** Regeneration creates the assistant as a
   *sibling* of the existing response (`assistant_parent = body.parent_id`),
   so its nearest user ancestor is the ORIGINAL prompt. The delta measures
   how long ago the user sent that message — arbitrarily large.
3. Any path where the assistant's parent chain skips the triggering user
   message (combined/pending inputs create a NEW user row, but older rows
   predate the placeholder) is similarly off.

## Approach

Measure at the source of truth — the task lifecycle:

1. **Backend** (`run_chat_task`): stamp `message_meta["timing"] =
   {"started_at": now_ms()}` when the task starts, and add
   `"completed_at"` right before every terminal `_save_message(done=True)`
   (success, stream end, max-iterations, cancel, error). Terminal saves
   already persist `message_meta`, so the stamps ride existing writes —
   no schema change, no new column, no migration.
2. **Frontend** (`turnSecondsFor`): prefer
   `meta.timing.completed_at − meta.timing.started_at` (unit-normalized)
   when both exist; fall back to the old timestamp-walk only for rows
   without timing meta (historical messages, API/summary paths). The
   fallback retains its cycle guard; the timing path can't have one
   (single row, no chain walk) — which also makes regenerate correct by
   construction.
3. **Tests**: meta stamping at start; completed_at added on the terminal
   paths that are unit-testable; frontend logic covered by contract
   comments (frontend has no test runner in this repo).

Out of scope: backfilling timing for historical messages (impossible —
the data was never recorded).
