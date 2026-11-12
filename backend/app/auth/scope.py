"""Кабинеты: каждый пользователь видит только свои записи — владелец панели тоже.

Владелец панели (role=owner) отличается лишь правом управлять пользователями (одобрить,
отклонить, заблокировать) и общими настройками движка; чужие ключи, IP и деньги он не видит.
Любой запрос к таблице с owner_id проходит через owned()/get_owned(), а новая запись
получает owner_id = current.admin.id.
"""
from __future__ import annotations

from typing import Any, TypeVar

from fastapi import Depends, HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import Current, current_admin
from app.db.models import SelectelKey

T = TypeVar("T")


def is_owner(current: Current) -> bool:
    return current.admin.role == "owner"


def owned(stmt: Select[Any], model: Any, current: Current) -> Select[Any]:
    """Ограничить выборку кабинетом пользователя."""
    return stmt.where(model.owner_id == current.admin.id)


def can_see(obj: Any, current: Current) -> bool:
    return obj is not None and getattr(obj, "owner_id", None) == current.admin.id


async def get_owned(db: AsyncSession, model: type[T], obj_id: Any, current: Current, missing: str = "Не найдено") -> T:
    """Запись по id, только если она в кабинете пользователя. Чужая — как несуществующая (404)."""
    obj = await db.get(model, obj_id)
    if not can_see(obj, current):
        raise HTTPException(status.HTTP_404_NOT_FOUND, missing)
    return obj  # type: ignore[return-value]


async def owner_only(current: Current = Depends(current_admin)) -> Current:
    if not is_owner(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это доступно только владельцу панели")
    return current


async def visible_key_ids(db: AsyncSession, current: Current) -> set[int]:
    rows = await db.execute(select(SelectelKey.id).where(SelectelKey.owner_id == current.admin.id))
    return set(rows.scalars())


async def visible_account_ids(db: AsyncSession, current: Current) -> set[str]:
    rows = await db.execute(select(SelectelKey.account_id).where(SelectelKey.owner_id == current.admin.id))
    return set(rows.scalars())
