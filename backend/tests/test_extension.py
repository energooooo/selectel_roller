from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import extension
from app.db.models import Admin, Base, ExtensionToken, utcnow


@pytest.fixture
def sessionmaker():
    async def build():
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    return asyncio.run(build())


def _add_admin(sessionmaker) -> int:
    async def run():
        async with sessionmaker() as db:
            admin = Admin(login="root", password_hash="x")
            db.add(admin)
            await db.commit()
            return admin.id

    return asyncio.run(run())


def test_pairing_roundtrip(sessionmaker, monkeypatch):
    extension._pairings.clear()
    admin_id = _add_admin(sessionmaker)

    code, ttl = extension.create_pairing(admin_id)
    assert ttl == extension.PAIR_TTL
    assert len(code) == 10
    assert set(code) <= set(extension.PAIR_ALPHABET)
    assert any(c.isdigit() for c in code) and any(c.isalpha() for c in code)

    async def run():
        async with sessionmaker() as db:
            # Регистр, пробелы и дефис-разделитель не важны.
            typed = f" {code[:5].lower()}-{code[5:].lower()} "
            token = await extension.claim_pairing(db, typed, "test")
            # Код одноразовый: повторный обмен отклоняется.
            with pytest.raises(HTTPException) as exc:
                await extension.claim_pairing(db, code, "test")
            assert exc.value.status_code == 404
            # Валидный токен проходит.
            row = await extension.current_extension(db, f"Bearer {token}")
            assert row.admin_id == admin_id
            # Неверный токен — 401.
            with pytest.raises(HTTPException):
                await extension.current_extension(db, "Bearer nope")
            # Отозванный токен — 401.
            row.revoked = True
            await db.commit()
            with pytest.raises(HTTPException):
                await extension.current_extension(db, f"Bearer {token}")

    asyncio.run(run())


def test_expired_pairing_rejected(sessionmaker):
    extension._pairings.clear()
    admin_id = _add_admin(sessionmaker)
    code, _ = extension.create_pairing(admin_id)
    extension._pairings[code].expires -= extension.PAIR_TTL + 1  # искусственно просрочим

    async def run():
        async with sessionmaker() as db:
            with pytest.raises(HTTPException):
                await extension.claim_pairing(db, code, "test")

    asyncio.run(run())


def test_expired_token_rejected(sessionmaker):
    admin_id = _add_admin(sessionmaker)

    async def run():
        async with sessionmaker() as db:
            from app.auth.security import new_token, token_hash
            raw = new_token()
            now = utcnow()
            db.add(ExtensionToken(token_hash=token_hash(raw), admin_id=admin_id, label="old",
                                  created_at=now, last_seen_at=now, expires_at=now - timedelta(seconds=1)))
            await db.commit()
            with pytest.raises(HTTPException):
                await extension.current_extension(db, f"Bearer {raw}")

    asyncio.run(run())


def test_pairing_codes_unique_and_mixed():
    extension._pairings.clear()
    codes = {extension.create_pairing(1)[0] for _ in range(extension.MAX_PAIRINGS)}
    assert len(codes) == extension.MAX_PAIRINGS
    for code in codes:
        assert len(code) == 10
        assert not set(code) & set("01ILO")
    assert extension.normalize_code("ab-cd ef") == "ABCDEF"
