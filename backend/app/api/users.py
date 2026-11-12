"""Пользователи панели — для владельца: заявки, одобрение, отклонение, блокировка.

Здесь только учётные данные пользователя (логин, IP заявки, описание, статус); его ключи,
IP и деньги владелец не видит — у каждого свой кабинет.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Current, audit
from app.auth.scope import owner_only
from app.db.models import Admin
from app.db.session import get_db
from app.notify import admin_bot

router = APIRouter(prefix="/users", tags=["users"])


def user_view(u: Admin) -> dict[str, Any]:
    return {"id": u.id, "login": u.login, "role": u.role, "status": u.status, "description": u.description,
            "reg_ip": u.reg_ip, "browser": admin_bot.browser_of(u.reg_user_agent), "created_at": u.created_at,
            "decided_at": u.decided_at, "decided_by": u.decided_by}


@router.get("")
async def list_users(_: Current = Depends(owner_only), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Admin).order_by(Admin.created_at.desc()))).scalars().all()
    order = {"pending": 0, "active": 1, "blocked": 2, "rejected": 3}
    return [user_view(u) for u in sorted(rows, key=lambda u: (u.role != "owner", order.get(u.status, 9)))]


class DecisionIn(BaseModel):
    action: Literal["approve", "reject", "block"]


@router.post("/{user_id}/decision")
async def decide(user_id: int, body: DecisionIn, current: Current = Depends(owner_only),
                 db: AsyncSession = Depends(get_db)) -> dict:
    target = await db.get(Admin, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого пользователя")
    if target.role == "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "Владельца панели нельзя заблокировать")
    await db.commit()
    user = await admin_bot.decide(user_id, body.action, current.admin.login)
    if user is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Не получилось")
    await audit(current, f"user.{body.action}", user.login)
    await admin_bot.refresh_message(user)  # заявка в боте показывает то же решение
    return user_view(user)
