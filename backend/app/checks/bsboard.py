"""Проверка пойманных IP через bsboard API.

Документации bsboard пока нет, поэтому клиент нарочно общий: GET {bsboard_url}
с параметром ip и ключом в заголовке Authorization: Bearer. Ответ сохраняется
как есть (check_payload), а итог «ок / плохо» берётся из распространённых полей
(ok / success / whitelisted / status). Когда будет документация — правится только
функция _verdict.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app import bus, settings_store
from app.db.models import CaughtIP, utcnow
from app.db.session import session_scope
from app.events import emit
from app.notify.telegram import esc, notify_for

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(str(settings_store.get("bsboard_url") or "").strip())


def _verdict(payload: Any) -> str:
    if isinstance(payload, dict):
        for name in ("whitelisted", "ok", "success", "valid", "white"):
            if isinstance(payload.get(name), bool):
                return "ok" if payload[name] else "bad"
        status = str(payload.get("status", "")).lower()
        if status in {"ok", "white", "whitelisted", "pass", "passed"}:
            return "ok"
        if status in {"bad", "black", "fail", "failed", "blocked"}:
            return "bad"
    return "ok" if payload else "error"


async def check_ip(caught_id: int, *, announce: bool = False) -> str:
    async with session_scope() as db:
        caught = await db.get(CaughtIP, caught_id)
        if caught is None:
            return "error"
        ip = caught.ip
    if not configured():
        verdict, payload = "skipped", {"reason": "bsboard не настроен"}
    else:
        url = str(settings_store.get("bsboard_url")).strip()
        key = settings_store.secret("bsboard_key")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url, params={"ip": ip},
                                        headers={"Authorization": f"Bearer {key}"} if key else {})
            try:
                payload = resp.json()
            except ValueError:
                payload = {"text": resp.text[:500]}
            verdict = _verdict(payload) if resp.is_success else "error"
            if not resp.is_success:
                payload = {"http_status": resp.status_code, "body": payload}
        except Exception as exc:  # noqa: BLE001
            verdict, payload = "error", {"error": f"{type(exc).__name__}: {exc}"}

    async with session_scope() as db:
        caught = await db.get(CaughtIP, caught_id)
        if caught:
            caught.check_status, caught.check_payload, caught.checked_at = verdict, payload, utcnow()
    bus.publish("caught_check", {"id": caught_id, "check_status": verdict})
    if verdict in {"ok", "bad"}:
        await emit(f"bsboard: {ip} — {'проверку прошёл' if verdict == 'ok' else 'НЕ прошёл проверку'}",
                   level="success" if verdict == "ok" else "warn", kind="check", data={"ip": ip})
        if announce:
            await notify_for(caught.owner_id if caught else None, "caught", f"🧪 bsboard: <code>{esc(ip)}</code> — "
                                   + ("✅ прошёл проверку" if verdict == "ok" else "❌ не прошёл проверку"))
    return verdict


async def on_caught(caught_id: int) -> None:
    await check_ip(caught_id, announce=True)


async def rechecker() -> None:
    """Периодическая перепроверка IP, которые всё ещё на аккаунтах."""
    while True:
        await asyncio.sleep(600)
        if not configured():
            continue
        hours = max(1, int(settings_store.get("bsboard_recheck_hours") or 6))
        try:
            async with session_scope() as db:
                ids = list((await db.execute(select(CaughtIP.id).where(
                    CaughtIP.on_account.is_(True),
                    (CaughtIP.checked_at.is_(None)) | (CaughtIP.checked_at < utcnow() - timedelta(hours=hours)),
                ))).scalars())
            for caught_id in ids:
                await check_ip(caught_id)
        except Exception:  # noqa: BLE001
            log.exception("bsboard recheck")
