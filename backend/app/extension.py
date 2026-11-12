"""Сопряжение браузерного расширения с панелью.

Расширение работает в реальном Chrome пользователя: подставляет логин/пароль в
форму Selectel, после решённой капчи само дочитывает код из MailyGet и забирает
IAM-токен, затем завершает онбординг ключа в Roller. Авторизуется оно
отдельным bearer-токеном (ExtensionToken), а не cookie-сессией панели.

Сопряжение (pairing): в панели администратор создаёт короткий код, расширение
меняет его на долгоживущий токен. Коды живут в памяти одного backend-процесса —
как и задания онбординга; переживать рестарт им незачем.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import new_token, token_hash
from app.db.models import Admin, ExtensionToken, utcnow

TOKEN_TTL = timedelta(days=90)
PAIR_TTL = 300  # секунд на то, чтобы ввести код в расширение
# Код сопряжения: 10 символов из цифр и заглавных латинских букв. Без похожих
# друг на друга 0/O, 1/I/L — код вводят руками с экрана панели.
PAIR_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PAIR_LENGTH = 10
PAIR_TOUCH = timedelta(minutes=5)
MAX_PAIRINGS = 50


@dataclass
class Pairing:
    admin_id: int
    expires: float


_pairings: dict[str, Pairing] = {}


def _sweep() -> None:
    now = time.monotonic()
    for code in [c for c, p in _pairings.items() if p.expires <= now]:
        _pairings.pop(code, None)


def normalize_code(raw: str) -> str:
    """«k7m2q-x9p4r» → «K7M2QX9P4R»: регистр, пробелы и дефисы не важны."""
    return re.sub(r"[^0-9A-Z]", "", raw.upper())


def _new_code() -> str:
    while True:
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_LENGTH))
        # И цифры, и буквы — чтобы код читался как код, а не как слово или число.
        if any(c.isdigit() for c in code) and any(c.isalpha() for c in code) and code not in _pairings:
            return code


def create_pairing(admin_id: int) -> tuple[str, int]:
    """Возвращает (код, срок_в_секундах). Код — для одноразового обмена на токен."""
    _sweep()
    if len(_pairings) >= MAX_PAIRINGS:
        # Не даём забить память: держим только самые свежие коды.
        for code in sorted(_pairings, key=lambda c: _pairings[c].expires)[: len(_pairings) - MAX_PAIRINGS + 1]:
            _pairings.pop(code, None)
    code = _new_code()
    _pairings[code] = Pairing(admin_id=admin_id, expires=time.monotonic() + PAIR_TTL)
    return code, PAIR_TTL


async def claim_pairing(db: AsyncSession, code: str, label: str) -> str:
    """Обменивает код на новый bearer-токен. Код одноразовый."""
    _sweep()
    pairing = _pairings.pop(normalize_code(code), None)
    if pairing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Код сопряжения неверен или истёк. Создайте новый в панели.")
    token = new_token()
    now = utcnow()
    db.add(ExtensionToken(
        token_hash=token_hash(token), admin_id=pairing.admin_id,
        label=label[:100], created_at=now, last_seen_at=now, expires_at=now + TOKEN_TTL,
    ))
    await db.commit()
    return token


async def current_extension(db: AsyncSession, authorization: str) -> ExtensionToken:
    """Аутентификация расширения по Authorization: Bearer <token>. Без cookie/CSRF:
    расширение ходит из привилегированного background-воркера, не из страницы."""
    scheme, _, raw = authorization.partition(" ")
    raw = raw.strip()
    if scheme.lower() != "bearer" or not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Подключите расширение в панели.")
    row = (await db.execute(select(ExtensionToken).where(
        ExtensionToken.token_hash == token_hash(raw)))).scalar_one_or_none()
    now = utcnow()
    if row is None or row.revoked or row.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Расширение отключено. Подключите его заново в панели.")
    owner = await db.get(Admin, row.admin_id)
    if owner is None or owner.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь панели заблокирован или ещё не одобрен.")
    if now - row.last_seen_at >= PAIR_TOUCH:
        row.last_seen_at = now
        await db.commit()
    return row
