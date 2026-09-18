# Feature: Antigravity CLI agent profile (`agy`)

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **IN PROGRESS**
> Branch: `feat/agent-profile-antigravity`

- **Filed:** 2026-09-17
- **Type:** feat

## Summary

Add Google Antigravity CLI (`agy`) as a first-class agent profile type, on par
with the existing codex / claude_code / cursor / grok / opencode / cline /
gemini / pi drivers: profile schema + validation, binary detection, a
streaming driver speaking Antigravity's documented NDJSON headless protocol,
dispatch wiring, and admin UI support.

## Why Antigravity

Antigravity CLI is Google's terminal surface for the Antigravity agent harness
(Gemini models, multi-step reasoning, tool calling, async subagents,
checkpoints). Its headless mode is designed for exactly this kind of
programmatic driving: NDJSON events, explicit status values, conversation
resume by id, and loud failures on unknown models.

## Protocol (verified against official docs, September 2026)

- One-shot turn: `agy -p <prompt> --output-format stream-json [--model S]
  [--effort low|medium|high] [--conversation ID] [--print-timeout 30m]`
- NDJSON stream: one `init` event (cwd, tools, permission_mode,
  conversation_id), `step_update` events (`step_type`: `user_input`,
  `agent_response` [streamed `text_delta`], `tool` [`tool_info`: name,
  parameters, output, error; `subagent_info` for subagent steps],
  `checkpoint`), exactly one terminal `result`.
- `result` payload: `status` (SUCCESS | ERROR | CANCELED | INTERRUPTED |
  INVALID | WAITING | RUNNING), `response`, `error`, `duration_seconds`,
  `num_turns`, `usage{input_tokens, output_tokens, thinking_tokens,
  cache_read_tokens, total_tokens}`, `conversation_id`.
- Resume: `--conversation <id>` (or `--continue` for most recent).
- Permissions: headless soft-denies unapproved tools by default (run
  continues; notice on stderr); `--dangerously-skip-permissions` auto-approves
  everything. Scoped rules live in `~/.gemini/antigravity-cli/settings.json`.
- Unknown `--model` fails loudly (non-zero + ERROR envelope) — no silent
  fallback.
- Auth is cached-credential (interactive login first); unauthenticated
  headless runs exit immediately with `authentication required` instead of
  hanging.

## Design

- **Profile type**: `antigravity`, default command `agy`, default name
  "Antigravity". No extra profile fields beyond the common set (permissions
  policy lives in Antigravity's own settings file; approval follows the
  chat-level tool approval mode like other drivers).
- **Driver** (`cptr/utils/agents/antigravity.py`): one `agy -p` process per
  turn (stateless), streaming parsed to the normalized event contract:
  `text_delta` → `AgentTextDelta`; `tool` steps → `AgentToolUpdate` (call id
  derived from conversation + step index); `result` → `AgentDone(usage,
  resume_state={session_id: conversation_id})` or `AgentError` on non-SUCCESS
  status. Resume passes `--conversation`.
- **Approval mapping**: chat approval `full` → `--dangerously-skip-permissions`
  (explicit user opt-in per turn, same semantics as other drivers' bypass
  modes); `auto`/default → no flag (Antigravity's own scoped rules apply).
- **Timeout**: `--print-timeout 30m` (docs default is 5m — too short for
  agentic tool workloads).
- **Attachments**: Antigravity headless `content` blocks are text-only; image
  attachments are noted inline in the prompt and otherwise omitted (documented
  limitation).
- **Detection**: `--version` probe (generic path) + models left empty (the
  CLI's own `agy models` is the source of truth; headless fails loudly on
  unknown models anyway).

## Out of scope

- Persistent `--input-format stream-json` sessions (single long-lived
  process): a possible follow-up optimization; per-turn processes are simpler
  and match how the codex/pi drivers work.
- Subagent surfacing via `subagent_info` (data is available in the stream;
  mapping it to OI Computer's subagent model is a separate feature).
- Image attachment upload (protocol is text-block-only in headless mode).
