"""Белые подсети: список, вкл/выкл, массовое добавление. Изменения сразу уходят воркерам."""
from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bus
from app.auth.deps import Current, audit, current_admin
from app.db.models import RegionPool, Subnet
from app.db.session import get_db
from app.engine.heat import Heat, subnet_scores
from app.engine.manager import manager

router = APIRouter(prefix="/subnets", tags=["subnets"])


async def _pools(db: AsyncSession) -> list[tuple[str, ipaddress.IPv4Network]]:
    pools = []
    for p in (await db.execute(select(RegionPool))).scalars():
        try:
            pools.append((p.region, ipaddress.ip_network(p.cidr, strict=False)))
        except ValueError:
            pass
    return pools


def subnet_view(s: Subnet, offered: bool, heat: Heat | None = None) -> dict[str, Any]:
    return {"id": s.id, "cidr": s.cidr, "region": s.region, "white_count": s.white_count, "enabled": s.enabled,
            "source": s.source, "note": s.note, "caught_total": s.caught_total, "offered": offered,
            "attempts_total": s.attempts_total or 0,
            "heat": heat.heat if heat else 1.0, "tier": heat.tier if heat else "new",
            "weight": heat.weight if heat else 1.0, "created_at": s.created_at}


@router.get("")
async def list_subnets(_: Current = Depends(current_admin), db: AsyncSession = Depends(get_db)) -> list[dict]:
    pools = await _pools(db)
    subnets = (await db.execute(select(Subnet))).scalars().all()
    scores = await subnet_scores(db)
    views = []
    for s in subnets:
        net = ipaddress.ip_network(s.cidr, strict=False)
        views.append(subnet_view(s, any(net.overlaps(p) for _, p in pools), scores.get(s.id)))
    # Сначала включённые и выдаваемые, потом по числу белых.
    views.sort(key=lambda v: (not v["enabled"], not v["offered"], -(v["white_count"] or 0), v["cidr"]))
    return views


class SubnetsIn(BaseModel):
    text: str = Field(max_length=50_000)


@router.post("")
async def add_subnets(body: SubnetsIn, current: Current = Depends(current_admin),
                      db: AsyncSession = Depends(get_db)) -> dict:
    """По строке: «подсеть [белых]». Есть такая — обновляется число белых и она включается."""
    pools = await _pools(db)
    added, updated, errors = 0, 0, []
    for n, raw in enumerate(body.text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.replace(",", " ").split()
        try:
            net = ipaddress.ip_network(parts[0], strict=False)
            if net.version != 4:
                raise ValueError
        except ValueError:
            errors.append({"line": n, "error": f"не подсеть: {parts[0]}"})
            continue
        white = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        region = next((r for r, p in pools if net.overlaps(p)), "")
        existing = (await db.execute(select(Subnet).where(Subnet.cidr == str(net)))).scalar()
        if existing:
            existing.enabled = True
            if white is not None:
                existing.white_count = white
            existing.region = existing.region or region
            updated += 1
        else:
            db.add(Subnet(cidr=str(net), white_count=white, region=region, enabled=True, source="manual",
                          note="" if region else "в кэше пулов Selectel не найдена — проверится при охоте"))
            added += 1
    await db.commit()
    if added or updated:
        await audit(current, "subnet.add", f"+{added}, обновлено {updated}")
        manager.subnets_changed()
        bus.publish("subnets", {})
    return {"added": added, "updated": updated, "errors": errors}


class SubnetPatch(BaseModel):
    enabled: bool | None = None
    white_count: int | None = Field(default=None, ge=0, le=65536)
    note: str | None = Field(default=None, max_length=200)


@router.patch("/{subnet_id}")
async def update_subnet(subnet_id: int, body: SubnetPatch, current: Current = Depends(current_admin),
                        db: AsyncSession = Depends(get_db)) -> dict:
    subnet = await db.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такой подсети")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(subnet, field, value)
    await db.commit()
    await audit(current, "subnet.update", subnet.cidr, body.model_dump(exclude_none=True))
    manager.subnets_changed()
    bus.publish("subnets", {})
    return {"ok": True}


class BulkToggle(BaseModel):
    ids: list[int]
    enabled: bool


@router.post("/toggle")
async def toggle_subnets(body: BulkToggle, current: Current = Depends(current_admin),
                         db: AsyncSession = Depends(get_db)) -> dict:
    for subnet in (await db.execute(select(Subnet).where(Subnet.id.in_(body.ids)))).scalars():
        subnet.enabled = body.enabled
    await db.commit()
    await audit(current, "subnet.toggle", f"{len(body.ids)} шт.", {"enabled": body.enabled})
    manager.subnets_changed()
    bus.publish("subnets", {})
    return {"ok": True}


@router.delete("/{subnet_id}")
async def delete_subnet(subnet_id: int, current: Current = Depends(current_admin),
                        db: AsyncSession = Depends(get_db)) -> dict:
    subnet = await db.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такой подсети")
    cidr = subnet.cidr
    await db.delete(subnet)
    await db.commit()
    await audit(current, "subnet.delete", cidr)
    manager.subnets_changed()
    bus.publish("subnets", {})
    return {"ok": True}
