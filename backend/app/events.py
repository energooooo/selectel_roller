"""Журнал: запись события в БД + публикация в шину."""
from __future__ import annotations

import logging
from typing import Any

from app import bus
from app.db.models import Event, utcnow
from app.db.session import session_scope

log = logging.getLogger("roller.events")
_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "success": logging.INFO,
           "warn": logging.WARNING, "error": logging.ERROR}


async def emit(
    message: str,
    *,
    level: str = "info",
    kind: str = "log",
    key_id: int | None = None,
    region: str = "",
    data: dict[str, Any] | None = None,
    persist: bool = True,
) -> None:
    log.log(_LEVELS.get(level, logging.INFO), "[key=%s %s] %s", key_id, region, message)
    payload = {
        "ts": utcnow().isoformat(),
        "level": level,
        "kind": kind,
        "key_id": key_id,
        "region": region,
        "message": message,
        "data": data or {},
    }
    if persist:
        async with session_scope() as db:
            event = Event(level=level, kind=kind, key_id=key_id, region=region, message=message, data=data or {})
            db.add(event)
            await db.flush()
            payload["id"] = event.id
    bus.publish("event", payload)
