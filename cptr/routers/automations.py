"""Automations router: CRUD + run for scheduled automations."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from cptr.models.automations import Automation, AutomationRun
from cptr.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule
from cptr.utils.config import check_access, now_ms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automations", tags=["automations"])

COOKIE_NAME = "cptr_session"
PAGE_SIZE = 30


def _get_user(request: Request) -> str:
    """Extract user_id from cookie, raise 401 if not authenticated."""
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


def _hash_token(token: str) -> str:
    """SHA-256 hash a webhook token."""
    return hashlib.sha256(token.encode()).hexdigest()


def _automation_dict(
    a: Automation,
    last_run: AutomationRun | None = None,
    next_runs: list[int] | None = None,
    webhook_url: str | None = None,
) -> dict:
    """Serialize an Automation to a response dict."""
    meta = a.meta or {}
    has_webhook = bool(meta.get("webhook_token"))

    return {
        "id": a.id,
        "user_id": a.user_id,
        "name": a.name,
        "prompt": a.prompt,
        "model_id": a.model_id,
        "workspace": a.workspace,
        "rrule": a.rrule,
        "is_active": a.is_active,
        "last_run_at": a.last_run_at,
        "next_run_at": a.next_run_at,
        "meta": a.meta,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
        "last_run": _run_dict(last_run) if last_run else None,
        "next_runs": next_runs,
        "has_webhook": has_webhook,
        "webhook_url": webhook_url,
    }


def _run_dict(r: AutomationRun) -> dict:
    return {
        "id": r.id,
        "automation_id": r.automation_id,
        "chat_id": r.chat_id,
        "status": r.status,
        "error": r.error,
        "created_at": r.created_at,
    }


# ── List automations ────────────────────────────────────────


@router.get("")
async def list_automations(
    request: Request,
    workspace: Optional[str] = Query(None, description="Workspace path (omit for all)"),
    query: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
):
    user_id = _get_user(request)
    skip = (page - 1) * PAGE_SIZE

    items, total = await Automation.get_by_workspace(
        user_id=user_id,
        workspace=workspace,
        status=status,
        query=query,
        skip=skip,
        limit=PAGE_SIZE,
    )

    # Batch-fetch latest runs
    ids = [a.id for a in items]
    latest_runs = await AutomationRun.get_latest_batch(ids) if ids else {}

    return {
        "items": [_automation_dict(a, last_run=latest_runs.get(a.id)) for a in items],
        "total": total,
    }


# ── Create automation ───────────────────────────────────────


class CreateAutomationRequest(BaseModel):
    name: str
    prompt: str
    model_id: str
    workspace: str
    rrule: str
    is_active: bool = True


@router.post("")
async def create_automation(request: Request, body: CreateAutomationRequest):
    user_id = _get_user(request)

    try:
        validate_rrule(body.rrule)
    except ValueError as e:
        raise HTTPException(400, str(e))

    nxt = next_run_ns(body.rrule)
    now = now_ms()

    automation = await Automation.create(
        user_id=user_id,
        name=body.name.strip(),
        prompt=body.prompt.strip(),
        model_id=body.model_id.strip(),
        workspace=body.workspace,
        rrule=body.rrule,
        next_run_at=nxt,
        is_active=body.is_active,
        created_at=now,
    )

    return _automation_dict(automation, next_runs=next_n_runs_ns(body.rrule))


# ── Get automation by ID ────────────────────────────────────


@router.get("/{automation_id}")
async def get_automation(request: Request, automation_id: str):
    user_id = _get_user(request)
    automation = await Automation.get_by_id(automation_id)
    if not automation or automation.user_id != user_id:
        raise HTTPException(404, "automation not found")

    last_run = await AutomationRun.get_latest(automation_id)
    return _automation_dict(
        automation,
        last_run=last_run,
        next_runs=next_n_runs_ns(automation.rrule),
    )


# ── Update automation ───────────────────────────────────────


class UpdateAutomationRequest(BaseModel):
    name: str
    prompt: str
    model_id: str
    workspace: str
    rrule: str
    is_active: bool = True


@router.post("/{automation_id}")
async def update_automation(request: Request, automation_id: str, body: UpdateAutomationRequest):
    user_id = _get_user(request)
    automation = await Automation.get_by_id(automation_id)
    if not automation or automation.user_id != user_id:
        raise HTTPException(404, "automation not found")

    try:
        validate_rrule(body.rrule)
    except ValueError as e:
        raise HTTPException(400, str(e))

    nxt = next_run_ns(body.rrule)
    now = now_ms()

    await Automation.update_by_id(
        automation_id,
        updated_at=now,
        name=body.name.strip(),
        prompt=body.prompt.strip(),
        model_id=body.model_id.strip(),
        workspace=body.workspace,
        rrule=body.rrule,
        is_active=body.is_active,
        next_run_at=nxt,
    )

    updated = await Automation.get_by_id(automation_id)
    last_run = await AutomationRun.get_latest(automation_id)
    return _automation_dict(updated, last_run=last_run, next_runs=next_n_runs_ns(body.rrule))


# ── Toggle active/paused ───────────────────────────────────


@router.post("/{automation_id}/toggle")
async def toggle_automation(request: Request, automation_id: str):
    user_id = _get_user(request)
    automation = await Automation.get_by_id(automation_id)
    if not automation or automation.user_id != user_id:
        raise HTTPException(404, "automation not found")

    nxt = next_run_ns(automation.rrule) if not automation.is_active else None
    toggled = await Automation.toggle(automation_id, nxt, now_ms())
    return _automation_dict(toggled)


# ── Run now ─────────────────────────────────────────────────


@router.post("/{automation_id}/run")
async def run_automation_now(
    automation_id: str,
    request: Request,
    token: Optional[str] = Query(None, description="Webhook token for unauthenticated access"),
):
    if token:
        # Webhook path: validate token, no session required
        automation = await Automation.get_by_id(automation_id)
        if not automation:
            raise HTTPException(404, "automation not found")
        meta = automation.meta or {}
        expected_hash = meta.get("webhook_token", "")
        if not expected_hash or _hash_token(token) != expected_hash:
            raise HTTPException(403, "invalid token")
        if not automation.is_active:
            raise HTTPException(409, "automation is paused")

        # Inject webhook payload into prompt if request has a body
        webhook_payload = None
        try:
            body = await request.json()
            if body:
                import json

                webhook_payload = json.dumps(body, indent=2)
        except Exception:
            pass
    else:
        # Normal path: require session auth
        user_id = _get_user(request)
        automation = await Automation.get_by_id(automation_id)
        if not automation or automation.user_id != user_id:
            raise HTTPException(404, "automation not found")
        webhook_payload = None

    from cptr.utils.automations import execute_automation

    asyncio.create_task(execute_automation(request.app, automation, webhook_payload=webhook_payload))
    return _automation_dict(automation)


# ── Delete ──────────────────────────────────────────────────


@router.delete("/{automation_id}")
async def delete_automation(request: Request, automation_id: str):
    user_id = _get_user(request)
    automation = await Automation.get_by_id(automation_id)
    if not automation or automation.user_id != user_id:
        raise HTTPException(404, "automation not found")

    await Automation.delete(automation_id)
    return {"ok": True}


# ── Execution history ───────────────────────────────────────


@router.get("/{automation_id}/runs")
async def get_automation_runs(
    automation_id: str,
    request: Request,
    skip: int = 0,
    limit: int = 50,
):
    user_id = _get_user(request)
    automation = await Automation.get_by_id(automation_id)
    if not automation or automation.user_id != user_id:
        raise HTTPException(404, "automation not found")

    runs = await AutomationRun.get_by_automation(automation_id, skip=skip, limit=limit)
    return [_run_dict(r) for r in runs]


# ── Webhook management ──────────────────────────────────────


@router.post("/{automation_id}/webhook")
async def generate_webhook(request: Request, automation_id: str):
    """Generate or regenerate a webhook token for this automation.

    Returns the plaintext webhook URL once. The token is stored as a
    SHA-256 hash — the URL cannot be recovered after this response.
    """
    user_id = _get_user(request)
    automation = await Automation.get_by_id(automation_id)
    if not automation or automation.user_id != user_id:
        raise HTTPException(404, "automation not found")

    plaintext = f"wh_{secrets.token_hex(20)}"
    meta = dict(automation.meta or {})
    meta["webhook_token"] = _hash_token(plaintext)
    await Automation.update_by_id(automation_id, updated_at=now_ms(), meta=meta)

    base = str(request.base_url).rstrip("/")
    webhook_url = f"{base}/api/automations/{automation_id}/run?token={plaintext}"

    updated = await Automation.get_by_id(automation_id)
    return _automation_dict(updated, webhook_url=webhook_url)


@router.delete("/{automation_id}/webhook")
async def revoke_webhook(request: Request, automation_id: str):
    """Revoke the webhook token for this automation."""
    user_id = _get_user(request)
    automation = await Automation.get_by_id(automation_id)
    if not automation or automation.user_id != user_id:
        raise HTTPException(404, "automation not found")

    meta = dict(automation.meta or {})
    meta.pop("webhook_token", None)
    await Automation.update_by_id(automation_id, updated_at=now_ms(), meta=meta)

    updated = await Automation.get_by_id(automation_id)
    return _automation_dict(updated)
