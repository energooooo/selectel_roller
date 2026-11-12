"""Админ-бот: заявки на регистрацию с кнопками «Принять / Отклонить».

Токен бота и список Telegram-id, которым можно решать, — в настройках (токен зашифрован).
Бот читает обновления long polling'ом (без вебхука и открытых портов). Нажатие кнопки
от чужого id ничего не делает. После решения сообщение заявки переписывается: видно, кто
и когда решил, а под одобренным пользователем остаётся кнопка «Заблокировать».
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select, update

from app import bus, settings_store
from app.db.models import Admin, SelectelKey, Session, utcnow
from app.db.session import session_scope
from app.notify.telegram import esc

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
MSK = timezone(timedelta(hours=3))
STATUS_LINE = {
    "active": "✅ <b>Принят</b>",
    "rejected": "❌ <b>Отклонён</b>",
    "blocked": "🚫 <b>Заблокирован</b>",
    "pending": "⏳ <b>Ждёт решения</b>",
}


def admin_ids() -> set[int]:
    raw = str(settings_store.get("admin_tg_ids") or "")
    return {int(x) for x in re.findall(r"-?\d+", raw)}


def _token() -> str:
    return settings_store.secret("admin_bot_token")


async def _call(method: str, payload: dict[str, Any], timeout: float = 20) -> dict[str, Any]:
    token = _token()
    if not token:
        return {"ok": False, "description": "нет токена админ-бота"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(API.format(token=token, method=method), json=payload)
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "description": f"{type(exc).__name__}: {exc}"}


def browser_of(ua: str) -> str:
    """«Chrome · Windows» из User-Agent — для заявки хватает."""
    ua = ua or ""
    os_name = next((name for pat, name in (
        (r"Android", "Android"), (r"iPhone|iPad", "iOS"), (r"Windows", "Windows"),
        (r"Mac OS X|Macintosh", "macOS"), (r"Linux", "Linux")) if re.search(pat, ua)), "")
    browser = next((name for pat, name in (
        (r"YaBrowser", "Яндекс Браузер"), (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"),
        (r"Chrome/", "Chrome"), (r"Safari/", "Safari")) if re.search(pat, ua)), "")
    return " · ".join(x for x in (browser, os_name) if x) or "неизвестно"


def request_text(user: Admin) -> str:
    when = user.created_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
    lines = [
        "🍬 <b>Заявка на доступ к Toffee Roller</b>",
        "",
        f"👤 Пользователь: <b>{esc(user.login)}</b>",
        f"🌐 IP: <code>{esc(user.reg_ip or '—')}</code>",
        f"🕒 Зарегистрирован: {when} МСК",
        f"🖥 Браузер: {esc(browser_of(user.reg_user_agent))}",
        "",
        "💬 <b>О себе</b>",
        f"<blockquote>{esc(user.description or '—')}</blockquote>",
    ]
    if user.status != "pending":
        decided = user.decided_at.astimezone(MSK).strftime("%d.%m %H:%M") if user.decided_at else ""
        by = f" · {esc(user.decided_by)}" if user.decided_by else ""
        lines += ["", f"{STATUS_LINE.get(user.status, user.status)} — {decided}{by}"]
    lines += ["", f"#заявка #u{user.id}"]
    return "\n".join(lines)


def keyboard(user: Admin) -> dict[str, Any]:
    if user.status == "pending":
        rows = [[{"text": "✅ Принять", "callback_data": f"reg:ok:{user.id}"},
                 {"text": "❌ Отклонить", "callback_data": f"reg:no:{user.id}"}]]
    elif user.status == "active":
        rows = [[{"text": "🚫 Заблокировать", "callback_data": f"reg:ban:{user.id}"}]]
    elif user.status in ("blocked", "rejected"):
        rows = [[{"text": "↩️ Вернуть доступ", "callback_data": f"reg:ok:{user.id}"}]]
    else:
        rows = []
    return {"inline_keyboard": rows}


async def send_request(user_id: int) -> None:
    """Отправить заявку всем, кто может решать. id сообщения храним, чтобы потом его обновить."""
    async with session_scope() as db:
        user = await db.get(Admin, user_id)
        if user is None:
            return
        text, kb = request_text(user), keyboard(user)
    first_message: int | None = None
    for chat in admin_ids():
        res = await _call("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML",
                                          "reply_markup": kb, "disable_web_page_preview": True})
        if res.get("ok") and first_message is None:
            first_message = res["result"]["message_id"]
        elif not res.get("ok"):
            log.warning("админ-бот: заявка не отправлена в %s: %s", chat, res.get("description"))
    if first_message is not None:
        async with session_scope() as db:
            user = await db.get(Admin, user_id)
            if user:
                user.tg_message_id = first_message


async def refresh_message(user: Admin, chat_id: int | None = None, message_id: int | None = None) -> None:
    message_id = message_id or user.tg_message_id
    chats = [chat_id] if chat_id else sorted(admin_ids())
    if not message_id or not chats:
        return
    for chat in chats:
        await _call("editMessageText", {"chat_id": chat, "message_id": message_id, "text": request_text(user),
                                        "parse_mode": "HTML", "reply_markup": keyboard(user),
                                        "disable_web_page_preview": True})


async def decide(user_id: int, action: str, by: str) -> Admin | None:
    """approve / reject / block. Владельца панели не трогаем. Блокировка гасит сессии и ключи."""
    status = {"ok": "active", "approve": "active", "no": "rejected", "reject": "rejected",
              "ban": "blocked", "block": "blocked"}.get(action)
    if status is None:
        return None
    async with session_scope() as db:
        user = await db.get(Admin, user_id)
        if user is None or user.role == "owner":
            return None
        user.status, user.decided_at, user.decided_by = status, utcnow(), by[:64]
        if status != "active":
            await db.execute(update(Session).where(Session.admin_id == user.id).values(revoked=True))
            await db.execute(update(SelectelKey).where(SelectelKey.owner_id == user.id, SelectelKey.enabled.is_(True))
                             .values(enabled=False, status="paused",
                                     status_reason="Доступ к панели закрыт — поиск остановлен"))
        await db.flush()
        snapshot = user
    if status != "active":
        from app.engine.manager import manager  # noqa: PLC0415 — движок импортирует уведомления
        await manager.reconcile()
    bus.publish("users", {})
    return snapshot


async def _handle_callback(cb: dict[str, Any]) -> None:
    sender = cb.get("from") or {}
    data = str(cb.get("data") or "")
    if sender.get("id") not in admin_ids():
        await _call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Нет прав на это действие"})
        return
    m = re.fullmatch(r"reg:(ok|no|ban):(\d+)", data)
    if not m:
        await _call("answerCallbackQuery", {"callback_query_id": cb["id"]})
        return
    who = sender.get("username") and f"@{sender['username']}" or str(sender.get("id"))
    user = await decide(int(m.group(2)), m.group(1), who)
    if user is None:
        await _call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Заявка не найдена"})
        return
    done = {"active": "Доступ открыт", "rejected": "Заявка отклонена", "blocked": "Пользователь заблокирован"}
    await _call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": done.get(user.status, "Готово")})
    message = cb.get("message") or {}
    await refresh_message(user, (message.get("chat") or {}).get("id"), message.get("message_id"))
    # Если заявка ушла нескольким админам — обновим и основное сообщение.
    if message.get("message_id") != user.tg_message_id:
        await refresh_message(user)


async def _handle_message(msg: dict[str, Any]) -> None:
    sender = (msg.get("from") or {}).get("id")
    chat = (msg.get("chat") or {}).get("id")
    if sender not in admin_ids() or not str(msg.get("text") or "").startswith("/"):
        return
    async with session_scope() as db:
        pending = (await db.execute(select(Admin).where(Admin.status == "pending")
                                    .order_by(Admin.created_at))).scalars().all()
        users = (await db.execute(select(func.count(Admin.id)).where(Admin.role != "owner",
                                                                       Admin.status == "active"))).scalar_one()
    await _call("sendMessage", {"chat_id": chat, "parse_mode": "HTML", "text":
                                f"🍬 <b>Toffee Roller · заявки</b>\nАктивных пользователей: {users}\n"
                                f"Ждут решения: {len(pending)}" + ("\nПрисылаю их ниже." if pending else "")})
    for user in pending:
        await send_request(user.id)


async def poller() -> None:
    """Long polling админ-бота. Пока токена нет — ждёт, токен из настроек подхватывается на лету."""
    offset = 0
    while True:
        if not _token() or not admin_ids():
            await asyncio.sleep(15)
            continue
        res = await _call("getUpdates", {"offset": offset, "timeout": 25,
                                         "allowed_updates": ["callback_query", "message"]}, timeout=35)
        if not res.get("ok"):
            log.warning("админ-бот: getUpdates — %s", res.get("description"))
            await asyncio.sleep(10)
            continue
        for upd in res.get("result", []):
            offset = max(offset, upd["update_id"] + 1)
            try:
                if "callback_query" in upd:
                    await _handle_callback(upd["callback_query"])
                elif "message" in upd:
                    await _handle_message(upd["message"])
            except Exception:  # noqa: BLE001
                log.exception("админ-бот: обработка обновления")
