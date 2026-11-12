"""Фильтр живого потока (SSE) по кабинету.

Шина одна на всю панель, а смотреть её могут разные пользователи. Каждый — и владелец
панели тоже — получает только события своих ключей и аккаунтов; у прочих сообщений
вырезаются данные (остаётся тип — браузер просто перезапросит список, а API уже
отфильтрует его по кабинету). Системные события без ключа видит только владелец панели.
"""
from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select

from app.db.models import Account, SelectelKey
from app.db.session import session_scope

# Тип сообщения → поле с id ключа.
KEY_FIELD = {"key": "id", "key_deleted": "id", "runtime": "key_id", "event": "key_id", "caught": "key_id"}
# Сообщения без личных данных — проходят как есть.
PUBLIC = {"settings"}
REFRESH_EVERY = 5.0


class StreamFilter:
    def __init__(self, admin_id: int, is_owner: bool) -> None:
        self.admin_id = admin_id
        self.is_owner = is_owner
        self.keys: set[int] = set()
        self.accounts: set[str] = set()
        self._loaded_at = 0.0

    async def refresh(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._loaded_at < REFRESH_EVERY:
            return
        async with session_scope() as db:
            self.keys = set((await db.execute(select(SelectelKey.id).where(
                SelectelKey.owner_id == self.admin_id))).scalars())
            self.accounts = set((await db.execute(select(Account.account_id).where(
                Account.owner_id == self.admin_id))).scalars())
        self._loaded_at = time.monotonic()

    async def apply(self, raw: str) -> str | None:
        """Текст сообщения для этого пользователя или None — не отправлять."""
        try:
            msg: dict[str, Any] = json.loads(raw)
        except ValueError:
            return None
        kind, data = msg.get("type", ""), msg.get("data") or {}
        if kind in PUBLIC:
            return raw
        if isinstance(data, dict) and "owner" in data:
            return raw if data.get("owner") == self.admin_id else None
        if kind in KEY_FIELD:
            key_id = data.get(KEY_FIELD[kind]) if isinstance(data, dict) else None
            if key_id is None:
                return raw if self.is_owner and kind == "event" else None
            if key_id not in self.keys:
                await self.refresh(force=kind in ("key", "caught"))  # ключ мог только что появиться
            return raw if key_id in self.keys else None
        if kind == "account":
            account_id = data.get("account_id") if isinstance(data, dict) else None
            if account_id not in self.accounts:
                await self.refresh()
            return raw if account_id in self.accounts else None
        # Остальное (прокси, подсети, деньги, серверы…) — только сигнал «обнови», без данных.
        return json.dumps({"type": kind, "data": {}}, ensure_ascii=False)
