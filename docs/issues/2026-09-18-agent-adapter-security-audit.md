# 2026-09-18 — CLI agent adapter security & robustness audit

## Summary

A three-way adversarial review (one subagent per driver: Kilo/OpenCode,
Antigravity, Command Code) of the CLI agent adapters found process leaks,
auth failures, false-success paths, and an option-injection vector.
Implemented in commit `dbb0c91` (merged to `main` as `9068077`).

## Findings and disposition

### Kilo / OpenCode adapter (`cptr/utils/agents/opencode.py`, `detection.py`)

- **[HIGH] H1 — Basic-auth username mismatch.** Kilo's server defaults
  `KILO_SERVER_USERNAME` to `kilo` (explicit `kilocode_change` in their
  `server/auth.ts`); we authenticated as `opencode`, so any Kilo profile with
  `server_password` got 401 on every request. **Fixed**: username now selected
  per profile family in both driver and detection.
- **[HIGH] H2 — false `AgentDone` on slow-start or mid-turn stream death.**
  The status-poll fallback treated a missing status entry as idle, and a dead
  SSE stream silently ended the turn as a successful empty run (persisting a
  bogus session id). **Fixed**: idle requires prior activity; stream failure
  verifies session status or raises.
- **[MEDIUM] M1 — spawned server unsecured; `server_password` ignored in
  spawned mode.** Any local process could drive the agent during the turn
  window; an inherited `KILO_SERVER_PASSWORD` env made the spawned server
  require auth we never sent. **Fixed**: fresh per-spawn credential via env,
  overriding inherited values.
- **[MEDIUM] M3 — 1s per-line stdout timeout uncaught; 5s startup deadline too
  short for cold Node starts.** **Fixed**: timeouts caught, 15s deadline,
  connect-wait before first use.
- **[MEDIUM] M4 — stderr drained and discarded.** **Fixed**: 20-line tail
  (2000 chars/line) surfaced in error messages.
- **[MEDIUM] M2 — `server_password` plaintext at rest and unmasked via admin
  API.** **Parked**: needs a policy decision on adopting `cptr.utils.crypto`
  for agent profiles; separate issue.
- **[MEDIUM] M5 — unverified protocol payload shapes.** Route names verified
  against Kilo upstream; event payload shapes remain unverified (kilo binary
  not installed here). Tracked for a live smoke test.

### Antigravity adapter (`cptr/utils/agents/antigravity.py`)

- **[HIGH] — 64KB readline limit.** NDJSON tool events embed full tool output;
  a single line over asyncio's StreamReader limit raised `ValueError`, failed
  the turn, and leaked the child. **Fixed**: chunked reads + incremental
  decoder (mirrors codex.py).
- **[HIGH] — GeneratorExit leak.** No outer cleanup; abandoning the generator
  (client disconnect, GC) orphaned the child for up to 30m. **Fixed**: cleanup
  on GeneratorExit, generic exceptions, and cancellation; SIGKILL escalation.
- **[MEDIUM] — unsandboxed `full` approval mapping.** Unlike codex (which adds
  `sandbox: workspace-write`), `tool_approval_mode: "full"` here is fully
  unsandboxed and settable by any chat owner on their own chat (self-consent,
  not privilege escalation). **Parked**: systemic `_auto_approve` policy
  question across 6 adapters — needs an owner decision (admin cap or docs).
- **[MEDIUM] — `profile.home` `~` expansion against the server user's env** for
  PAM identities. **Fixed** (uses `identity.expand_user_path`).
- **[MEDIUM] — ARG_MAX/MAX_ARG_STRLEN**: single-argv prompt fails with E2BIG
  on long chats. **Handled**: clear error above 100k chars (full stdin-based
  prompt delivery parked — CLI flag support unverified).
- **[LOW] — WAITING results misreported as generic errors; dead
  `_TERMINAL_STATUSES`.** **Fixed**: explanatory hint; dead set removed.
- **[LOW] — full `os.environ` passthrough for non-PAM runs.** **Parked**
  (systemic; allowlist needs design).

### Command Code adapter (`cptr/utils/agents/command_code.py`)

- **[HIGH] — exception-path and GeneratorExit process leaks** (same pattern as
  antigravity). **Fixed** via the shared hygiene changes.
- **[HIGH] — `--model` option injection.** Model ids reach CLI argv unchecked;
  `agent:x/--yolo` could disable permission gating. **Fixed** centrally in
  `resolve_agent_model_target` (rejects leading `-`; enforces profile model
  allowlists when declared) — covers all argv-based adapters.
- **[MEDIUM] — double-render.** `result.finalText` re-emitted the full answer
  after deltas already streamed it. **Fixed**: emit only if nothing streamed.
- **[MEDIUM] — `max_turns` discarded partial work and the session.** **Fixed**:
  partial text + visible truncation notice + persisted `resume_state`.
- **[LOW] — fallback call_id collisions** (same-named id-less tool calls
  merged in UI). **Fixed**: per-name counter.
- **[LOW] — usage passthrough pollution.** **Fixed**: unknown keys dropped.
- **[LOW] — dash-leading prompt confusion; cross-workspace resume.** **Fixed**:
  prompt guard; resume ignored when stored workspace differs.
- **[LOW] — no overall turn timeout.** **Parked**: prompt-via-stdin +
  timeout needs CLI capability verification (systemic for subprocess
  adapters).

## Validation

- `pytest cptr/tests/` — 79 passed (whatsapp-bridge tests excluded: 4
  pre-existing failures at HEAD `582abc0`, unrelated to this change; verified
  by running them with the changes stashed).
- `ruff check` / `ruff format --check` clean on all touched files
  (detection.py has 8 pre-existing findings, down from 9 at HEAD).
- Regression tests added: auth username selection, spawn-credential override,
  oversized-line reassembly, call-id counter, SIGTERM-immune child
  termination, usage normalization.

## Not deployed

The live cptr instance was not touched. Deploying fork changes to the running
instance requires an explicit owner instruction.
