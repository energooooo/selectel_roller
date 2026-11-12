"""Финансы: балансы аккаунтов, расходы на удержание IP, журнал доходов, серверы под IP."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bus
from app.auth.deps import Current, audit, current_admin
from app.core.crypto import decrypt
from app.db.models import CaughtIP, Sale, SelectelKey, Vds, utcnow
from app.db.session import get_db
from app.engine import billing, vds as vds_engine

router = APIRouter(tags=["finance"])
MSK = timezone(timedelta(hours=3))
SALE_KINDS = ("ip", "rent", "vds", "service", "other")
KIND_LABEL = {"ip": "Продажа IP", "rent": "Аренда IP", "vds": "Сервер", "service": "Услуга", "other": "Другое"}


def sale_view(s: Sale) -> dict[str, Any]:
    return {"id": s.id, "ts": s.ts, "kind": s.kind, "amount": s.amount, "buyer": s.buyer, "caught_id": s.caught_id,
            "ip": s.ip, "note": s.note}


def _month_start(dt: datetime) -> datetime:
    local = dt.astimezone(MSK)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("/finance/summary")
async def summary(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    balances = await billing.balances_view()
    sales = (await db.execute(select(Sale).order_by(Sale.ts))).scalars().all()
    now = utcnow()
    this_month = _month_start(now)
    prev_month = _month_start(this_month - timedelta(days=1))
    income_month = sum(s.amount for s in sales if s.ts >= this_month)
    income_prev = sum(s.amount for s in sales if prev_month <= s.ts < this_month)
    by_kind: dict[str, float] = defaultdict(float)
    for s in sales:
        by_kind[s.kind] += s.amount
    # Доход по месяцам за год — для графика.
    months: list[dict[str, Any]] = []
    cursor = this_month
    for _ in range(12):
        nxt = (cursor + timedelta(days=32)).replace(day=1)
        months.append({"month": cursor.strftime("%Y-%m"),
                       "income": round(sum(s.amount for s in sales if cursor <= s.ts < nxt), 2)})
        cursor = _month_start(cursor - timedelta(days=1))
    months.reverse()
    cost_month = round(sum(b["month"] for b in balances), 2)
    return {
        "balances": balances,
        "balance_total": round(sum(b["balance"] or 0 for b in balances), 2),
        "cost_month": cost_month,
        "cost_day": round(cost_month / 30.4, 2),
        "ips_held": sum(b["ips"] for b in balances),
        "income_month": round(income_month, 2),
        "income_prev_month": round(income_prev, 2),
        "income_total": round(sum(s.amount for s in sales), 2),
        "profit_month": round(income_month - cost_month, 2),
        "by_kind": [{"kind": k, "label": KIND_LABEL.get(k, k), "amount": round(v, 2)}
                    for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])],
        "months": months,
    }


@router.post("/finance/balances/refresh")
async def refresh(_: Current = Depends(current_admin)) -> list[dict]:
    return await billing.refresh_balances()


# ── журнал доходов ─────────────────────────────────────────────────────────

class SaleIn(BaseModel):
    ts: datetime | None = None
    kind: Literal["ip", "rent", "vds", "service", "other"] = "ip"
    amount: float = Field(gt=0, le=100_000_000)
    buyer: str = Field(default="", max_length=120)
    caught_id: int | None = None
    ip: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=300)


class SalePatch(BaseModel):
    ts: datetime | None = None
    kind: Literal["ip", "rent", "vds", "service", "other"] | None = None
    amount: float | None = Field(default=None, gt=0, le=100_000_000)
    buyer: str | None = Field(default=None, max_length=120)
    caught_id: int | None = None
    ip: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=300)


@router.get("/sales")
async def list_sales(days: int | None = None, caught_id: int | None = None, _: Current = Depends(current_admin),
                     db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Sale).order_by(Sale.ts.desc(), Sale.id.desc()).limit(2000)
    if days:
        stmt = stmt.where(Sale.ts >= utcnow() - timedelta(days=days))
    if caught_id:
        stmt = stmt.where(Sale.caught_id == caught_id)
    return [sale_view(s) for s in (await db.execute(stmt)).scalars()]


async def _fill_ip(db: AsyncSession, sale: Sale) -> None:
    if sale.caught_id:
        caught = await db.get(CaughtIP, sale.caught_id)
        if caught is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого IP")
        sale.ip = caught.ip


@router.post("/sales")
async def add_sale(body: SaleIn, current: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    sale = Sale(ts=body.ts or utcnow(), kind=body.kind, amount=round(body.amount, 2), buyer=body.buyer.strip(),
                caught_id=body.caught_id, ip=body.ip.strip(), note=body.note.strip())
    await _fill_ip(db, sale)
    db.add(sale)
    await db.commit()
    await audit(current, "sale.add", sale.ip or sale.buyer, {"amount": sale.amount, "kind": sale.kind})
    bus.publish("money", {})
    return sale_view(sale)


@router.patch("/sales/{sale_id}")
async def update_sale(sale_id: int, body: SalePatch, current: Current = Depends(current_admin),
                      db: AsyncSession = Depends(get_db)) -> dict:
    sale = await db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такой записи")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(sale, field, value.strip() if isinstance(value, str) else value)
    if "caught_id" in changes:
        await _fill_ip(db, sale)
    await db.commit()
    await audit(current, "sale.update", str(sale_id), {k: str(v) for k, v in changes.items()})
    bus.publish("money", {})
    return sale_view(sale)


@router.delete("/sales/{sale_id}")
async def delete_sale(sale_id: int, current: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    sale = await db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такой записи")
    await db.delete(sale)
    await db.commit()
    await audit(current, "sale.delete", str(sale_id), {"amount": sale.amount})
    bus.publish("money", {})
    return {"ok": True}


@router.get("/sales.csv")
async def export_sales(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["дата (МСК)", "что", "сумма, ₽", "кому", "IP", "заметка"])
    for s in (await db.execute(select(Sale).order_by(Sale.ts.desc()))).scalars():
        writer.writerow([s.ts.astimezone(MSK).strftime("%d.%m.%Y %H:%M"), KIND_LABEL.get(s.kind, s.kind),
                         f"{s.amount:.2f}".replace(".", ","), s.buyer, s.ip, s.note])
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="toffee-roller-income.csv"'})


# ── серверы под IP ─────────────────────────────────────────────────────────

def vds_view(v: Vds) -> dict[str, Any]:
    return {"id": v.id, "caught_id": v.caught_id, "key_id": v.key_id, "account_id": v.account_id, "ip": v.ip,
            "region": v.region, "zone": v.zone, "name": v.name, "flavor": v.flavor, "status": v.status,
            "step": v.step, "error": v.error, "month_cost": v.month_cost, "has_password": bool(v.password_enc),
            "created_at": v.created_at}


@router.get("/vds")
async def list_vds(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Vds).where(Vds.status != "deleted").order_by(Vds.id.desc()))).scalars()
    return [vds_view(v) for v in rows]


@router.get("/vds/quote")
async def vds_quote(caught_id: int, _: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    caught = await db.get(CaughtIP, caught_id)
    if caught is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого IP")
    return await vds_engine.quote(caught.region)


class VdsIn(BaseModel):
    caught_id: int
    confirm_ip: str


class ConfirmIn(BaseModel):
    confirm_ip: str


@router.post("/vds")
async def create_vds(body: VdsIn, current: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """Поднять сервер под пойманный IP. Тратит деньги аккаунта — подтверждение вводом адреса."""
    caught = await db.get(CaughtIP, body.caught_id)
    if caught is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого IP")
    if body.confirm_ip.strip() != caught.ip:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Адрес для подтверждения не совпал")
    if not caught.on_account or not caught.floating_ip_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этого IP уже нет на аккаунте")
    busy = (await db.execute(select(Vds).where(Vds.caught_id == caught.id,
                                               Vds.status.in_(("creating", "active", "deleting"))))).scalar()
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "Под этот IP сервер уже есть или создаётся")
    key = await db.get(SelectelKey, caught.key_id) if caught.key_id else None
    if key is None:
        key = (await db.execute(select(SelectelKey).where(SelectelKey.account_id == caught.account_id))).scalar()
    if key is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Нет ключа от аккаунта этого IP")
    name = "toffee-" + caught.ip.replace(".", "-")
    v = Vds(caught_id=caught.id, key_id=key.id, account_id=caught.account_id, ip=caught.ip, region=caught.region,
            name=name, status="creating", step="В очереди", error="")
    db.add(v)
    await db.commit()
    await audit(current, "vds.create", caught.ip, {"vds_id": v.id})
    vds_engine.spawn(vds_engine.create(v.id))
    bus.publish("vds", vds_view(v))
    return vds_view(v)


@router.post("/vds/{vds_id}/delete")
async def delete_vds(vds_id: int, body: ConfirmIn, current: Current = Depends(current_admin),
                     db: AsyncSession = Depends(get_db)) -> dict:
    v = await db.get(Vds, vds_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого сервера")
    if body.confirm_ip.strip() != v.ip:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Адрес для подтверждения не совпал")
    if v.status == "creating":
        raise HTTPException(status.HTTP_409_CONFLICT, "Сервер ещё создаётся — дождитесь окончания")
    if v.status == "error" and not v.server_id:
        v.status = "deleted"  # падение при создании уже убрало за собой — просто скрыть
        await db.commit()
        bus.publish("vds", vds_view(v))
        return vds_view(v)
    v.status, v.step = "deleting", "Удаляю"
    await db.commit()
    await audit(current, "vds.delete", v.ip, {"vds_id": v.id})
    vds_engine.spawn(vds_engine.destroy(v.id))
    bus.publish("vds", vds_view(v))
    return vds_view(v)


@router.post("/vds/{vds_id}/password")
async def vds_password(vds_id: int, current: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> dict:
    v = await db.get(Vds, vds_id)
    if v is None or not v.password_enc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пароля нет")
    await audit(current, "vds.password", v.ip)
    return {"user": "root", "password": decrypt(v.password_enc), "ip": v.ip}
