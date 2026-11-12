"""Пойманные IP, журнал, метрики, обзор, настройки и живой поток событий."""
from __future__ import annotations

import asyncio
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bus, metrics, settings_store
from app.auth.deps import Current, audit, current_admin
from app.checks import bsboard
from app.db.models import CaughtIP, Event, MetricPoint, Proxy, Sale, SelectelKey, Subnet, Vds, utcnow
from app.db.session import get_db
from app.engine.manager import manager
from app.notify import telegram

router = APIRouter(tags=["data"])
MSK = timezone(timedelta(hours=3))


# ── Пойманные IP / история ─────────────────────────────────────────────────

def caught_view(c: CaughtIP, key_name: str | None) -> dict[str, Any]:
    return {"id": c.id, "ip": c.ip, "region": c.region, "key_id": c.key_id, "key_name": key_name,
            "account_id": c.account_id, "pool_cidr": c.pool_cidr, "floating_ip_id": c.floating_ip_id,
            "caught_at": c.caught_at, "attempts_spent": c.attempts_spent, "protected": c.protected,
            "on_account": c.on_account, "released_at": c.released_at, "check_status": c.check_status,
            "check_payload": c.check_payload, "checked_at": c.checked_at, "note": c.note, "source": c.source,
            "client": c.client or "", "tags": c.tags or [], "expires_at": c.expires_at}


async def _caught_query(db: AsyncSession, key_id: int | None, region: str | None, on_account: bool | None,
                        q: str | None, days: int | None) -> list[dict[str, Any]]:
    stmt = select(CaughtIP).order_by(CaughtIP.caught_at.desc())
    if key_id:
        stmt = stmt.where(CaughtIP.key_id == key_id)
    if region:
        stmt = stmt.where(CaughtIP.region == region)
    if on_account is not None:
        stmt = stmt.where(CaughtIP.on_account.is_(on_account))
    if q:
        stmt = stmt.where(CaughtIP.ip.contains(q.strip()) | CaughtIP.pool_cidr.contains(q.strip()))
    if days:
        stmt = stmt.where(CaughtIP.caught_at >= utcnow() - timedelta(days=days))
    names = {k.id: k.name for k in (await db.execute(select(SelectelKey))).scalars()}
    income: dict[int, float] = defaultdict(float)
    for cid, amount in (await db.execute(select(Sale.caught_id, Sale.amount).where(Sale.caught_id.is_not(None)))).all():
        income[cid] += amount
    servers = {v.caught_id: v for v in (await db.execute(select(Vds).where(Vds.status != "deleted"))).scalars()}
    out = []
    for c in (await db.execute(stmt.limit(2000))).scalars():
        view = caught_view(c, names.get(c.key_id))
        view["income"] = round(income.get(c.id, 0.0), 2)
        v = servers.get(c.id)
        view["vds"] = {"id": v.id, "status": v.status, "step": v.step, "flavor": v.flavor, "zone": v.zone,
                       "error": v.error, "month_cost": v.month_cost, "has_password": bool(v.password_enc)} if v else None
        out.append(view)
    return out


@router.get("/caught")
async def list_caught(key_id: int | None = None, region: str | None = None, on_account: bool | None = None,
                      q: str | None = None, days: int | None = None, _: Current = Depends(current_admin),
                      db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await _caught_query(db, key_id, region, on_account, q, days)


@router.get("/caught.csv")
async def export_caught(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    rows = await _caught_query(db, None, None, None, None, None)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["ip", "регион", "ключ", "аккаунт", "подсеть", "пойман (МСК)", "на аккаунте", "защита",
                     "проверка", "заметка"])
    for r in rows:
        writer.writerow([r["ip"], r["region"], r["key_name"] or "", r["account_id"], r["pool_cidr"],
                         r["caught_at"].astimezone(MSK).strftime("%d.%m.%Y %H:%M"), "да" if r["on_account"] else "нет",
                         "да" if r["protected"] else "нет", r["check_status"], r["note"]])
    data = "﻿" + buf.getvalue()  # BOM — чтобы Excel открыл кириллицу
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="toffee-roller-caught.csv"'})


class CaughtPatch(BaseModel):
    protected: bool | None = None
    note: str | None = Field(default=None, max_length=200)
    client: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = Field(default=None, max_length=12)
    expires_at: datetime | None = None


@router.patch("/caught/{caught_id}")
async def update_caught(caught_id: int, body: CaughtPatch, current: Current = Depends(current_admin),
                        db: AsyncSession = Depends(get_db)) -> dict:
    caught = await db.get(CaughtIP, caught_id)
    if caught is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такой записи")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field == "protected" and value is None:
            continue
        if field == "tags":
            value = sorted({t.strip()[:24] for t in value or [] if t.strip()})
        elif isinstance(value, str):
            value = value.strip()
        if field == "expires_at" and value != caught.expires_at:
            caught.expiry_notified_at = None  # новый срок — новое напоминание
        setattr(caught, field, value)
    await db.commit()
    await audit(current, "caught.update", caught.ip, {k: str(v) for k, v in changes.items()})
    bus.publish("caught_update", {"id": caught.id})
    return {"ok": True}


@router.post("/caught/{caught_id}/check")
async def recheck_caught(caught_id: int, _: Current = Depends(current_admin)) -> dict:
    return {"check_status": await bsboard.check_ip(caught_id)}


# ── Журнал ─────────────────────────────────────────────────────────────────

@router.get("/events")
async def list_events(level: str | None = None, key_id: int | None = None, kind: str | None = None,
                      before_id: int | None = None, limit: int = Query(100, le=500),
                      _: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Event).order_by(Event.id.desc()).limit(limit)
    if level:
        stmt = stmt.where(Event.level.in_(level.split(",")))
    if key_id:
        stmt = stmt.where(Event.key_id == key_id)
    if kind:
        stmt = stmt.where(Event.kind.in_(kind.split(",")))
    if before_id:
        stmt = stmt.where(Event.id < before_id)
    return [{"id": e.id, "ts": e.ts, "level": e.level, "kind": e.kind, "key_id": e.key_id, "region": e.region,
             "message": e.message, "data": e.data} for e in (await db.execute(stmt)).scalars()]


# ── Обзор ──────────────────────────────────────────────────────────────────

@router.get("/overview")
async def overview(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    keys = (await db.execute(select(SelectelKey))).scalars().all()
    proxies = (await db.execute(select(Proxy))).scalars().all()
    subnets = (await db.execute(select(Subnet))).scalars().all()
    today = datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    caught_total = (await db.execute(select(func.count(CaughtIP.id)))).scalar_one()
    caught_today = (await db.execute(select(func.count(CaughtIP.id)).where(CaughtIP.caught_at >= today))).scalar_one()
    held = (await db.execute(select(func.count(CaughtIP.id)).where(CaughtIP.on_account.is_(True)))).scalar_one()
    day_ago = utcnow() - timedelta(hours=24)
    attempts_24h = (await db.execute(select(func.coalesce(func.sum(MetricPoint.attempts), 0)).where(
        MetricPoint.bucket >= day_ago))).scalar_one()
    last = (await db.execute(select(CaughtIP).order_by(CaughtIP.caught_at.desc()).limit(6))).scalars().all()
    names = {k.id: k.name for k in keys}
    hunting = [k for k in keys if manager.runtime(k.id) and k.status in {"hunting", "sleeping", "starting"}]
    return {
        "keys": {"total": len(keys), "enabled": sum(k.enabled for k in keys), "hunting": len(hunting),
                 "problems": sum(k.status in {"error", "banned", "auth_failed"} for k in keys)},
        "proxies": {"total": len(proxies), "ok": sum(p.status == "ok" for p in proxies),
                    "partial": sum(p.status == "partial" for p in proxies),
                    "dead": sum(p.status == "dead" for p in proxies)},
        "subnets": {"total": len(subnets), "enabled": sum(s.enabled for s in subnets)},
        "caught": {"total": caught_total, "today": caught_today, "held": held},
        "rate_per_min": metrics.rate_per_minute(),
        "attempts_24h": int(attempts_24h),
        "last_caught": [caught_view(c, names.get(c.key_id)) for c in last],
    }


# ── Метрики ────────────────────────────────────────────────────────────────

PERIODS = {"1h": (timedelta(hours=1), 60), "24h": (timedelta(hours=24), 900),
           "7d": (timedelta(days=7), 3600), "30d": (timedelta(days=30), 6 * 3600)}


@router.get("/metrics")
async def get_metrics(period: Literal["1h", "24h", "7d", "30d"] = "24h", _: Current = Depends(current_admin),
                      db: AsyncSession = Depends(get_db)) -> dict:
    await metrics.flush()  # свежие счётчики из памяти — сразу в выборку
    span, step = PERIODS[period]
    now = utcnow()
    since = now - span
    start_ts = int(since.timestamp()) // step * step
    n_steps = int((now.timestamp() - start_ts) // step) + 1
    points = (await db.execute(select(MetricPoint).where(MetricPoint.bucket >= since))).scalars().all()
    keys = {k.id: k for k in (await db.execute(select(SelectelKey))).scalars()}

    fields = ("attempts", "busy", "misses", "errors", "timeouts", "proxy_errors", "caught")
    series = [{"t": (start_ts + i * step) * 1000, **{f: 0 for f in fields}, "lat_sum": 0.0, "lat_n": 0}
              for i in range(n_steps)]
    by_key_series: dict[int, list[int]] = defaultdict(lambda: [0] * n_steps)
    by_key: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_region: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    totals: dict[str, float] = defaultdict(float)
    for p in points:
        idx = min(max(int((p.bucket.timestamp() - start_ts) // step), 0), n_steps - 1)
        row = series[idx]
        for f in fields:
            value = getattr(p, f) or 0
            row[f] += value
            by_key[p.key_id][f] += value
            by_region[p.region][f] += value
            totals[f] += value
        row["lat_sum"] += p.latency_sum_ms or 0
        row["lat_n"] += p.latency_count or 0
        by_key[p.key_id]["lat_sum"] += p.latency_sum_ms or 0
        by_key[p.key_id]["lat_n"] += p.latency_count or 0
        by_key_series[p.key_id][idx] += p.attempts or 0
    for row in series:
        lat_sum, lat_n = row.pop("lat_sum"), row.pop("lat_n")
        row["latency_ms"] = round(lat_sum / lat_n) if lat_n else None
        row["rate_per_min"] = round(row["attempts"] * 60 / step, 2)

    minutes = span.total_seconds() / 60
    key_rows = []
    for key_id, agg in by_key.items():
        key = keys.get(key_id)
        key_rows.append({
            "key_id": key_id, "name": key.name if key else f"удалён #{key_id}",
            "account_id": key.account_id if key else "",
            **{f: int(agg[f]) for f in fields},
            "rate_per_min": round(agg["attempts"] / minutes, 2),
            "latency_ms": round(agg["lat_sum"] / agg["lat_n"]) if agg["lat_n"] else None,
            "attempts_per_ip": round(agg["attempts"] / agg["caught"]) if agg["caught"] else None,
            "status": key.status if key else "deleted",
        })
    key_rows.sort(key=lambda r: (-r["caught"], -r["attempts"]))

    caught_rows = (await db.execute(select(CaughtIP).where(CaughtIP.caught_at >= since))).scalars().all()
    by_subnet: dict[str, int] = defaultdict(int)
    by_account: dict[str, int] = defaultdict(int)
    for c in caught_rows:
        by_subnet[c.pool_cidr or "—"] += 1
        name = keys[c.key_id].name if c.key_id in keys else (c.account_id or "—")
        by_account[name] += 1

    # «Когда ловится»: попытки и поимки по дню недели × часу (МСК) за всё время хранения.
    heat_attempts = [[0] * 24 for _ in range(7)]
    heat_caught = [[0] * 24 for _ in range(7)]
    for p in (await db.execute(select(MetricPoint.bucket, MetricPoint.attempts))).all():
        local = p.bucket.astimezone(MSK)
        heat_attempts[local.weekday()][local.hour] += p.attempts or 0
    for c in (await db.execute(select(CaughtIP.caught_at).where(CaughtIP.source == "hunt"))).scalars():
        local = c.astimezone(MSK)
        heat_caught[local.weekday()][local.hour] += 1

    return {
        "period": period, "step_s": step, "series": series,
        "by_key_series": [{"key_id": k, "name": keys[k].name if k in keys else f"#{k}", "values": v}
                          for k, v in by_key_series.items()],
        "by_key": key_rows,
        "by_region": [{"region": r, **{f: int(a[f]) for f in fields}} for r, a in sorted(by_region.items())],
        "totals": {f: int(totals[f]) for f in fields},
        "rate_per_min": round(totals["attempts"] / minutes, 2),
        "caught_by_subnet": sorted(({"subnet": s, "caught": n} for s, n in by_subnet.items()),
                                   key=lambda r: -r["caught"]),
        "caught_by_account": sorted(({"name": s, "caught": n} for s, n in by_account.items()),
                                    key=lambda r: -r["caught"]),
        "heatmap": {"attempts": heat_attempts, "caught": heat_caught, "tz": "МСК"},
    }


# ── Настройки ──────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(_: Current = Depends(current_admin)) -> dict:
    return settings_store.public_view()


@router.put("/settings")
async def put_settings(body: dict[str, Any], current: Current = Depends(current_admin)) -> dict:
    errors = _validate_settings(body)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "; ".join(errors))
    view = await settings_store.update(body)
    await audit(current, "settings.update", "", {k: ("***" if k in ("tg_bot_token", "bsboard_key") else v)
                                                 for k, v in body.items()})
    manager.settings_changed()
    bus.publish("settings", view)
    return view


def _validate_settings(body: dict[str, Any]) -> list[str]:
    errors = []
    for lo, hi, title in (("busy_delay_min", "busy_delay_max", "пауза после «занято»"),
                          ("round_delay_min", "round_delay_max", "пауза между кругами"),
                          ("long_pause_min", "long_pause_max", "длинная пауза")):
        a = body.get(lo, settings_store.get(lo))
        b = body.get(hi, settings_store.get(hi))
        try:
            if float(a) < 0 or float(b) < float(a):
                errors.append(f"{title}: «до» должно быть не меньше «от»")
        except (TypeError, ValueError):
            errors.append(f"{title}: нужны числа")
    if "regions" in body and (not isinstance(body["regions"], list) or not body["regions"]):
        errors.append("выберите хотя бы один регион")
    return errors


class TgTestIn(BaseModel):
    token: str = ""
    chat_id: str = ""


@router.post("/settings/telegram/test")
async def telegram_test(body: TgTestIn, _: Current = Depends(current_admin)) -> dict:
    token = body.token or settings_store.secret("tg_bot_token")
    chat = body.chat_id or str(settings_store.get("tg_chat_id") or "")
    if not token or not chat:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Укажите токен бота и чат")
    ok, error = await telegram.send_test(token, chat)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Telegram: {error}")
    return {"ok": True}


@router.post("/settings/telegram/chats")
async def telegram_chats(body: TgTestIn, _: Current = Depends(current_admin)) -> list[dict]:
    token = body.token or settings_store.secret("tg_bot_token")
    if not token:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Сначала укажите токен бота")
    return await telegram.find_chat_ids(token)


# ── Живой поток ────────────────────────────────────────────────────────────

@router.get("/stream")
async def stream(request: Request, _: Current = Depends(current_admin)) -> StreamingResponse:
    async def gen():  # noqa: ANN202
        queue = bus.open_queue()
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {message}\n\n"
        finally:
            bus.close_queue(queue)

    # no-transform: прокси Next (next start / standalone) иначе сжимает поток gzip'ом
    # и копит события в буфере — «живой» поток приходил бы пачками.
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
