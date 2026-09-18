# Feature: Kilo CLI agent profile (`kilo`)

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **IN PROGRESS**
> Branch: `feat/agent-profile-kilo`

- **Filed:** 2026-09-17
- **Type:** feat

## Summary

Add Kilo CLI (`kilo`, from `@kilocode/cli`) as a first-class agent profile
type. Kilo is an MIT-licensed fork of OpenCode; its docs state it "supports
the same configuration options" as OpenCode, and it ships `kilo serve` plus
`kilo acp`. OI Computer already has a proven OpenCode **server adapter**
(`cptr/utils/agents/opencode.py`), so Kilo support is that adapter with a
Kilo-specific config environment.

## Protocol basis (verified against official docs, September 2026)

- `kilo serve` starts a headless server (same shape as `opencode serve`).
- Config env: Kilo reads `KILO_CONFIG_CONTENT` / `KILO_CONFIG`; it **no
  longer** falls back to OpenCode config dirs (`.opencode/`), so the adapter
  must pass the isolation config through Kilo's variable.
- Fallback protocol if the serve API has drifted: `kilo acp` (first-class
  ACP server) against the existing generic ACP client.
- Exit codes for non-interactive runs: 0 success / 124 timeout / 1 error.
- Model ids are `provider/model` (e.g. `anthropic/claude-...`); the profile's
  `models` list passes them through.

## Design

- **Profile type**: `kilo`, default command `kilo`, default name "Kilo".
- **Driver**: reuse the OpenCode adapter's session/event flow with the
  environment adjusted: spawn `<command> serve --hostname=127.0.0.1
  --port=<free>` and set `KILO_CONFIG_CONTENT="{}"` **in addition to**
  `OPENCODE_CONFIG_CONTENT` (harmless for real OpenCode, required for Kilo).
  Implemented as a profile-level branch inside the existing adapter (a
  `kilo` agent type sharing the same code path) rather than a copied module —
  one adapter, two agent types.
- **Detection**: `--version` probe + model discovery via the existing
  OpenCode model probe; `auth_unknown` if no models are discoverable.
- **Attachments / subagents / session resume**: inherited from the OpenCode
  adapter unchanged.

## Out of scope

- `kilo acp` fallback driver (add only if the serve-API drift proves real).
- Kilo Gateway / Cloud Agent / remote-relay features (hosted services).
