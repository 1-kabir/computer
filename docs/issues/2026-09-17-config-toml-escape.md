# Issue: Config serializer writes raw newlines into TOML basic strings, corrupting config.toml

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **OPEN**
> Branch: `fix/config-toml-escape`

- **Filed:** 2026-09-17
- **Severity:** high — one bad write bricked the whole UI after the next restart

## Problem

`save_config()` in `cptr/utils/config.py` serializes string values as TOML
basic strings but only escapes backslashes and quotes — not control
characters. Any multi-line string value (in practice `gateway.system_prompt`,
edited through the prompt/settings UI) is written with literal newline
characters inside `"..."` quotes, which is invalid TOML:

```toml
"gateway.system_prompt" = "You are Computer (cptr)...
You are OI/Open-WebUI Computer..."
```

Nothing complains at write time. The bomb detonates on the next full parse
of the file (service restart, or any cache invalidation): `tomllib` raises
`TOMLDecodeError: Illegal character '\n'`, and because the config file is
read on startup and on auth/config-touching paths, the whole UI goes down.
The failure surfaces hours after the actual bad write, which makes it look
like "500s immediately after restart".

Worse, the current `load_config()` catches the parse error and silently
returns `{}` — so a corrupt file doesn't just fail loudly, it silently
discards every file-based setting (server section, auth, app_config mirror).

The repair of the *file* fixes the running app, but the serializer bug lives
in the installed code: the next UI edit of the system prompt re-writes the
same invalid TOML. This has recurred (fixed on Sep 11, re-corrupted Sep 17).

## Motivation

One bad write should never be able to take down the entire interface —
especially when the web UI is the operator's only way to reach agents.

## Approach

1. **Escape control characters when writing basic strings** in
   `save_config()`: after the existing backslash/quote escaping, also escape
   `\n`, `\r`, `\t` as the two-character escapes.
2. **Mirror the unescape** in `_parse_simple_toml()` (the Python <3.11
   fallback parser), which currently only handles `\"` and `\\`.
3. **Load-time sanity**: if the file fails to parse, back it up
   (`config.toml.bak-<timestamp>`), log loudly (error, with the exception),
   and continue with `{}` instead of silently discarding.
4. **Round-trip tests**: a config containing newlines/tabs/quotes/backslashes
   in `gateway.system_prompt` must survive save → parse → save → parse
   identically, via both `tomllib` and the fallback parser. Also cover the
   corrupt-file backup path.

Out of scope: emitting `"""` multi-line blocks (escaping is sufficient and
simpler); migrating the already-corrupt file (the operator repairs that
manually per the restart runbook, and the backup in step 3 makes the next
occurrence recoverable).
