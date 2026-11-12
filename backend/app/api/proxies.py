"""Прокси: список, массовое добавление, проверка, удаление."""
from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bus
from app.auth.deps import Current, audit, current_admin
from app.core.crypto import encrypt
from app.db.models import Proxy, SelectelKey
from app.db.session import get_db
from app.engine.manager import manager
from app.engine.proxy import ProxyParseError, parse_proxy_line

router = APIRouter(prefix="/proxies", tags=["proxies"])


def proxy_view(p: Proxy, keys: int = 0) -> dict[str, Any]:
    return {"id": p.id, "display": p.display, "label": p.label, "kind": p.kind, "session_mode": p.session_mode,
            "status": p.status, "exit_ip": p.exit_ip, "exit_country": p.exit_country, "latency_ms": p.latency_ms,
            "regions_ok": p.regions_ok, "regions_failed": p.regions_failed, "last_error": p.last_error,
            "checked_at": p.checked_at, "created_at": p.created_at, "keys": keys}


@router.get("")
async def list_proxies(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    load = Counter(k.proxy_id for k in (await db.execute(select(SelectelKey))).scalars() if k.proxy_id)
    proxies = (await db.execute(select(Proxy).order_by(Proxy.id))).scalars().all()
    return [proxy_view(p, load.get(p.id, 0)) for p in proxies]


class ProxiesIn(BaseModel):
    text: str = Field(max_length=50_000)
    default_kind: Literal["http", "https", "socks5", "socks5h"] = "http"
    session_mode: Literal["none", "password_suffix"] = "none"


@router.post("")
async def add_proxies(body: ProxiesIn, current: Current = Depends(current_admin),
                      db: AsyncSession = Depends(get_db)) -> dict:
    added: list[Proxy] = []
    errors: list[dict[str, Any]] = []
    duplicates = 0
    seen: set[str] = set()
    for n, line in enumerate(body.text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            parsed = parse_proxy_line(line, body.default_kind)
        except ProxyParseError as exc:
            errors.append({"line": n, "error": str(exc)})
            continue
        exists = (await db.execute(select(Proxy.id).where(Proxy.fingerprint == parsed.fingerprint))).scalar()
        if exists or parsed.fingerprint in seen:
            duplicates += 1
            continue
        seen.add(parsed.fingerprint)
        proxy = Proxy(url_enc=encrypt(parsed.url), fingerprint=parsed.fingerprint, display=parsed.display,
                      label=parsed.label, kind=parsed.kind, session_mode=body.session_mode)
        db.add(proxy)
        added.append(proxy)
    await db.commit()
    if added:
        await audit(current, "proxy.add", f"{len(added)} шт.")
        # Проверка сразу в фоне — статус прилетит на страницу по SSE.
        for proxy in added:
            bus.publish("proxy", proxy_view(proxy))
            asyncio.create_task(manager.check_proxy(proxy.id))
        await manager.reconcile()
    return {"added": len(added), "duplicates": duplicates, "errors": errors}


class ProxyPatch(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    session_mode: Literal["none", "password_suffix"] | None = None


@router.patch("/{proxy_id}")
async def update_proxy(proxy_id: int, body: ProxyPatch, current: Current = Depends(current_admin),
                       db: AsyncSession = Depends(get_db)) -> dict:
    proxy = await db.get(Proxy, proxy_id)
    if proxy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого прокси")
    if body.label is not None:
        proxy.label = body.label.strip()
    if body.session_mode is not None:
        proxy.session_mode = body.session_mode
    await db.commit()
    await audit(current, "proxy.update", proxy.display)
    view = proxy_view(proxy)
    bus.publish("proxy", view)
    return view


@router.post("/{proxy_id}/check")
async def check_one(proxy_id: int, _: Current = Depends(current_admin)) -> dict:
    data = await manager.check_proxy(proxy_id)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого прокси")
    return data


@router.post("/check-all")
async def check_all(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    ids = list((await db.execute(select(Proxy.id))).scalars())

    async def run() -> None:
        sem = asyncio.Semaphore(6)

        async def one(pid: int) -> None:
            async with sem:
                await manager.check_proxy(pid)

        await asyncio.gather(*(one(pid) for pid in ids))

    asyncio.create_task(run())
    return {"queued": len(ids)}


@router.delete("/{proxy_id}")
async def delete_proxy(proxy_id: int, current: Current = Depends(current_admin),
                       db: AsyncSession = Depends(get_db)) -> dict:
    proxy = await db.get(Proxy, proxy_id)
    if proxy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого прокси")
    affected = [k.id for k in (await db.execute(select(SelectelKey).where(
        SelectelKey.proxy_id == proxy_id, SelectelKey.enabled.is_(True)))).scalars()]
    display = proxy.display
    await db.delete(proxy)
    await db.commit()
    await audit(current, "proxy.delete", display)
    bus.publish("proxy_deleted", {"id": proxy_id})
    # Ключи, сидевшие на нём, пересаживаются на другой прокси.
    for key_id in affected:
        await manager.restart_key(key_id)
    return {"ok": True, "moved_keys": len(affected)}
