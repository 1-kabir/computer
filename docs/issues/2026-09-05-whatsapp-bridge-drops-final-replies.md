# Issue: WhatsApp bridge silently drops final agent replies

> Design record. `1-kabir/computer` has GitHub Issues disabled, so fork issues
> are recorded here in-repo. Status: **OPEN → fixed by branch
> `fix/whatsapp-bridge-delivery`**.
> Cross-filed upstream: [open-webui/computer#264](https://github.com/open-webui/computer/issues/264).

- **Filed:** 2026-09-05
- **Platform:** WhatsApp Cloud API (Meta), Graph API v21.0 as shipped
- **Severity:** high — feature completely broken (replies never delivered), silent

## Problem

With a bot on `platform: "whatsapp"`, inbound messages and the agent task both
work, but the **final reply is never delivered**. The user only ever sees the
"⏳ Thinking..." placeholder. The reply text exists in the cptr chat thread —
only the platform delivery is missing.

## Root cause

`cptr/utils/bridge.py` `_stream_loop()` delivers the final reply by calling
`adapter.edit()` on the placeholder message. `WhatsAppAdapter.edit()` is a
hard no-op: WhatsApp Cloud API is send-only (the Messages API exposes exactly
one endpoint, `POST /{Phone-Number-ID}/messages`; Meta's "edit messages"
documentation covers inbound user-edit webhook events only). Since the
placeholder send always succeeds, `platform_msg_id` is always set, so the
send-based fallback branch is unreachable for normal-length replies. The
resulting drop is logged at DEBUG only.

## Secondary issues

1. `send_typing()` posts a hardcoded fake `message_id` — Cloud API requires
   the real inbound `wamid`, so every call returns HTTP 400.
2. `_stream_loop` overflow branch has an unconditional `break` after the
   first chunk → multi-chunk replies lose everything after chunk 1 (affects
   all edit-fallback platforms).
3. "Task complete, no text output" branch calls `edit()` directly → no-op on
   WhatsApp.
4. Webhook: no `X-Hub-Signature-256` verification; GET verification accepts
   any `hub.verify_token`; no redelivery dedupe.
5. Inbound `interactive` messages (button/list replies) dropped silently.

## Approach

- Adapter capability flag `supports_edit` (False for WhatsApp).
- Bridge: skip placeholder + edits when adapter can't edit; final reply via
  `adapter.send()` chunks; fix overflow `break`; WARNING on delivery failure.
- Adapter: real typing indicator (`status: "read"` + real inbound
  `message_id` + `typing_indicator: {"type": "text"}`; auto-dismisses after
  25s or on reply); track last inbound message id; Graph API v23.0.
- Webhook: opt-in HMAC-SHA256 signature verification, opt-in
  `hub.verify_token` check, redelivery dedupe by message id.
- Inbound `interactive` button/list replies handled as text.
