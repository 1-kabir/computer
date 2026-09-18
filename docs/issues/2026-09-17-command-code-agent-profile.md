# Feature: Command Code CLI agent profile (`cmd`)

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **IMPLEMENTED**
> Branch: `feat/agent-profile-command-code`
>
> Protocol notes from live verification (CLI 1.56.0, September 2026): the
> usage object uses camelCase keys (`inputTokens`, `outputTokens`,
> `cacheReadTokens`, `cacheWriteTokens`) — the docs' snake_case names do not
> match the wire format. Text streams via `text_delta` events; tool activity
> via `tool_running`/`tool_completed` events. `--resume <sessionId>` verified
> working headless. Live smoke test passed after a fresh `cmd login` (a stale
> stored key 401s; `cmd status` only checks local key presence, not validity).

- **Filed:** 2026-09-17
- **Type:** feat

## Summary

Add Command Code CLI (`cmd`, from `npm i -g command-code`) as a first-class
agent profile type. Command Code is a terminal coding agent ("taste-1"
neuro-symbolic engine) whose headless mode emits newline-delimited JSON
`AgentEvent` frames plus one final result line — a clean fit for the
normalized event contract.

## Protocol basis (verified against official docs, September 2026)

- Turn: `cmd -p <prompt> --output-format json [--model M] [--effort L]`
  [--continue | --resume <id>] [--skip-onboarding] [--max-turns N]
  [--yolo | --permission-mode ...]`
- NDJSON stream: `{"type": "event", "event": {"type": "tool_running",
  "toolCallId", "toolName", "description", ...}}` frames; final line
  `{"type": "result", "subtype": "success|error|max_turns", "sessionId"
  (optional), "stopReason" (optional), "usage", "durationMs", "finalText",
  "error" (on error)}`.
- Consumers must treat unknown `event.type` values as forward-compatible.
- Resume: `--continue` (latest in cwd) or `--resume <sessionId>`; headless
  sessions are kept separate from interactive history.
- Permissions: headless blocks writes/shell by default; `--yolo` (alias
  `--dangerously-skip-permissions`) enables everything; `--permission-mode
  standard|plan|auto-accept` and fail-closed allowlists also exist.
- Exit codes: 0 ok; 3 auth; 4 permission; 5 rate-limit; 8 max-turns; 10
  credits; 130 interrupted; 1 general.
- `--skip-onboarding` bypasses taste onboarding (required for automation).

## Design

- **Profile type**: `command_code`, default command `cmd`, default name
  "Command Code".
- **Driver** (`cptr/utils/agents/command_code.py`): one `cmd -p` process per
  turn. Event frames → `AgentToolUpdate` (call_id = `toolCallId`); the
  `finalText` accumulates the answer, so the driver emits text deltas from
  documented events where available and falls back to yielding the final
  text once. `result.subtype == "success"` → `AgentDone(usage,
  resume_state={session_id})`; `max_turns`/`error` → `AgentError` with the
  documented detail. Unknown event types are ignored (forward-compatible by
  the CLI's own contract).
- **Approval mapping**: chat approval `full` → `--yolo`; otherwise no flag
  (headless default: reads allowed, writes/shell blocked — safe default).
- **Automation flags**: always pass `--skip-onboarding`; `--max-turns` left
  at the CLI default (100).
- **Attachments**: documented headless input is prompt text (arg or piped
  stdin); no documented image-block protocol, so images are omitted
  (documented limitation).

## Out of scope

- Interactive-mode bridging (plan approvals, ask_user_question) — headless
  disables those tools by default; `--tools-enable` bridging is a follow-up.
- Taste-profile management (CLI-local feature).
