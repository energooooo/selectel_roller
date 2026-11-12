"""Зависимости FastAPI: текущий админ, CSRF, аудит."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import client_ip, token_hash
from app.db.models import Admin, AuditLog, Session, utcnow
from app.db.session import get_db, session_scope

COOKIE_NAME = "tr_session"
SESSION_TTL = timedelta(days=7)
# Продлеваем сессию не на каждый запрос, а когда прошло столько с последнего продления.
TOUCH_EVERY = timedelta(minutes=5)
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "toffee-roller"


@dataclass
class Current:
    admin: Admin
    session: Session
    ip: str


def csrf_guard(request: Request) -> None:
    """Изменяющие запросы обязаны нести свой заголовок: чужая страница его не поставит
    без CORS-preflight, который мы не разрешаем. Плюс cookie SameSite=Strict."""
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF: нет заголовка X-Requested-With")


async def current_admin(request: Request, db: AsyncSession = Depends(get_db)) -> Current:
    csrf_guard(request)
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужно войти")
    session = (await db.execute(select(Session).where(Session.token_hash == token_hash(token)))).scalar_one_or_none()
    now = utcnow()
    if session is None or session.revoked or session.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла — войдите заново")
    admin = await db.get(Admin, session.admin_id)
    if admin is None or admin.status != "active":
        # Заблокированный или ещё не одобренный пользователь не работает даже со старой сессией.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужно войти")
    ip = client_ip(request)
    if now - session.last_seen_at >= TOUCH_EVERY:
        session.last_seen_at = now
        session.expires_at = now + SESSION_TTL
        session.ip = ip
    # Даже после чтения нужно завершить транзакцию, иначе SSE и медленные
    # запросы к Selectel держат подключение к БД до конца ответа.
    await db.commit()
    return Current(admin=admin, session=session, ip=ip)


async def audit(current: Current | None, action: str, target: str = "", details: dict[str, Any] | None = None,
                ip: str = "") -> None:
    async with session_scope() as db:
        db.add(AuditLog(
            admin_id=current.admin.id if current else None,
            ip=current.ip if current else ip,
            action=action,
            target=target[:200],
            details=details or {},
        ))
