"""Уведомления в Telegram. Только отправка — у бота нет меню и команд.

Токен и chat_id берутся из настроек панели (БД) в момент отправки, так что
смена бота в настройках действует сразу. Ошибки отправки не роняют движок.
"""
from __future__ import annotations

import html
import logging

import httpx

from app import settings_store

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"

# Какой флажок настроек включает какой тип сообщения.
KIND_FLAGS = {
    "caught": "notify_caught",
    "alert": "notify_alerts",
    "login": "notify_logins",
    "rotation": "notify_rotations",
    "daily": "notify_daily",
    "money": "notify_money",
}


def esc(value: object) -> str:
    return html.escape(str(value))


async def _send(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                API.format(token=token, method="sendMessage"),
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            )
        data = resp.json()
        if not data.get("ok"):
            return False, str(data.get("description") or resp.status_code)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


async def notify(kind: str, text: str, *, admin_copy: bool = False) -> bool:
    """Отправить сообщение типа kind, если он включён в настройках."""
    flag = KIND_FLAGS.get(kind)
    if flag and not settings_store.get(flag):
        return False
    token = settings_store.secret("tg_bot_token")
    chat_id = str(settings_store.get("tg_chat_id") or "").strip()
    if not token or not chat_id:
        return False
    ok, error = await _send(token, chat_id, f"🍬 <b>Toffee Roller</b>\n{text}")
    if not ok:
        log.warning("Telegram: не отправлено (%s)", error)
    admin_chat = str(settings_store.get("tg_admin_chat_id") or "").strip()
    if admin_copy and admin_chat and admin_chat != chat_id:
        await _send(token, admin_chat, f"🍬 <b>Toffee Roller</b> · журнал\n{text}")
    return ok


_owner_ids: set[int] = set()
_owner_ids_at = 0.0


async def panel_owner_ids() -> set[int]:
    """id владельцев панели (role=owner); кэш на минуту."""
    global _owner_ids_at
    import time  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from app.db.models import Admin  # noqa: PLC0415
    from app.db.session import session_scope  # noqa: PLC0415

    if time.monotonic() - _owner_ids_at > 60:
        async with session_scope() as db:
            ids = set((await db.execute(select(Admin.id).where(Admin.role == "owner"))).scalars())
        _owner_ids.clear()
        _owner_ids.update(ids)
        _owner_ids_at = time.monotonic()
    return _owner_ids


async def notify_for(owner_id: int | None, kind: str, text: str, *, admin_copy: bool = False) -> bool:
    """Уведомление о записи кабинета: в бот панели — только если это кабинет владельца панели.
    Пользователи получат свои уведомления через основного бота (позже); чужие IP и балансы
    владельцу не уходят."""
    if owner_id is None or owner_id not in await panel_owner_ids():
        return False
    return await notify(kind, text, admin_copy=admin_copy)


async def send_test(token: str, chat_id: str) -> tuple[bool, str]:
    return await _send(token, chat_id, "🍬 <b>Toffee Roller</b>\nТестовое сообщение: уведомления работают.")


async def find_chat_ids(token: str) -> list[dict[str, str]]:
    """Чаты, которые писали боту (/start) — чтобы выбрать chat_id в настройках."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(API.format(token=token, method="getUpdates"))
    seen: dict[str, dict[str, str]] = {}
    for update in resp.json().get("result", []):
        message = update.get("message") or update.get("my_chat_member") or {}
        chat = message.get("chat") or {}
        if "id" in chat:
            title = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            seen[str(chat["id"])] = {"id": str(chat["id"]), "title": title or chat.get("username", "")}
    return list(seen.values())
