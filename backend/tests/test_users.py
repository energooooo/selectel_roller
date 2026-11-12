"""Регистрация с одобрением и кабинеты: чужое не видно ни списком, ни по id, ни в живом потоке."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import settings_store, stream_filter
from app.api import keys, users
from app.auth import deps, routes
from app.auth.security import hash_password
from app.db.models import Admin, Base, SelectelKey
from app.engine import manager as manager_module
from app.notify import admin_bot, telegram

PASSWORD = "Toffee-Roller-2026!x"
HEADERS = {deps.CSRF_HEADER: deps.CSRF_VALUE}


def _setup(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    make_session = async_sessionmaker(engine, expire_on_commit=False)

    async def get_db():
        async with make_session() as db:
            yield db

    @asynccontextmanager
    async def session_scope():
        async with make_session.begin() as db:
            yield db

    for module in (deps, admin_bot, stream_filter):
        monkeypatch.setattr(module, "session_scope", session_scope)
    sent: list[tuple[str, dict]] = []

    async def fake_call(method, payload, timeout=20):
        sent.append((method, payload))
        return {"ok": True, "result": {"message_id": 77}}

    monkeypatch.setattr(admin_bot, "_call", fake_call)
    monkeypatch.setattr(routes, "notify", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setitem(settings_store._cache, "admin_tg_ids", "1152862206")
    manager = manager_module.WorkerManager()
    monkeypatch.setattr(keys, "manager", manager)

    async def reconcile():
        return None

    monkeypatch.setattr(manager, "reconcile", reconcile)
    import app.engine.manager as mm
    monkeypatch.setattr(mm, "manager", manager)

    app = FastAPI()
    for r in (routes.router, keys.router, users.router):
        app.include_router(r, prefix="/api")
    app.dependency_overrides[routes.get_db] = get_db
    return engine, make_session, app, sent


def test_register_approve_and_isolation(monkeypatch):
    async def run():
        engine, make_session, app, sent = _setup(monkeypatch)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with make_session.begin() as db:
            db.add(Admin(id=1, login="owner", password_hash=hash_password(PASSWORD), role="owner", status="active"))
            db.add(SelectelKey(id=10, name="Чужой ключ", account_id="111", owner_id=1))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=HEADERS) as client:
            r = await client.post("/api/auth/register", json={"login": "alice", "password": PASSWORD,
                                                               "description": "Хочу ловить IP на своих аккаунтах"})
            assert r.status_code == 200, r.text
            await asyncio.sleep(0.05)  # заявка уходит в бот фоном
            req = [p for m, p in sent if m == "sendMessage"]
            assert req and req[0]["chat_id"] == 1152862206
            assert "alice" in req[0]["text"] and "Хочу ловить IP" in req[0]["text"]
            buttons = req[0]["reply_markup"]["inline_keyboard"][0]
            assert [b["text"] for b in buttons] == ["✅ Принять", "❌ Отклонить"]

            # Повтор логина — занят; неверный пароль — как обычно 401, верный — честное «на рассмотрении».
            dup = await client.post("/api/auth/register", json={"login": "ALICE", "password": PASSWORD,
                                                                 "description": "Ещё одна заявка, дубль"})
            assert dup.status_code == 409
            assert (await client.post("/api/auth/login", json={"login": "alice", "password": "wrong-pass"})).status_code == 401
            pending = await client.post("/api/auth/login", json={"login": "alice", "password": PASSWORD})
            assert pending.status_code == 403 and "рассмотрении" in pending.json()["detail"]

            # Кнопку нажал чужой — ничего не меняется.
            user_id = int(buttons[0]["callback_data"].split(":")[2])
            await admin_bot._handle_callback({"id": "c1", "from": {"id": 5}, "data": f"reg:ok:{user_id}", "message": {}})
            async with make_session() as db:
                assert (await db.get(Admin, user_id)).status == "pending"
            # Нажал админ — пользователь одобрен, сообщение заявки переписано.
            await admin_bot._handle_callback({"id": "c2", "from": {"id": 1152862206, "username": "boss"},
                                              "data": f"reg:ok:{user_id}",
                                              "message": {"message_id": 77, "chat": {"id": 1152862206}}})
            async with make_session() as db:
                user = await db.get(Admin, user_id)
                assert user.status == "active" and user.decided_by == "@boss"
            edits = [p for m, p in sent if m == "editMessageText"]
            assert edits and "Принят" in edits[-1]["text"]

            ok = await client.post("/api/auth/login", json={"login": "alice", "password": PASSWORD})
            assert ok.status_code == 200, ok.text
            # Кабинет: чужого ключа нет в списке, по id — 404, управлять им нельзя.
            assert (await client.get("/api/keys")).json() == []
            assert (await client.post("/api/keys/10/start")).status_code == 404
            assert (await client.delete("/api/keys/10")).status_code == 404
            # Заявки и пользователи — только владельцу.
            assert (await client.get("/api/users")).status_code == 403

            # Блокировка из бота закрывает текущую сессию.
            await admin_bot._handle_callback({"id": "c3", "from": {"id": 1152862206}, "data": f"reg:ban:{user_id}",
                                              "message": {"message_id": 77, "chat": {"id": 1152862206}}})
            assert (await client.get("/api/keys")).status_code == 401
            blocked = await client.post("/api/auth/login", json={"login": "alice", "password": PASSWORD})
            assert blocked.status_code == 403 and "заблокирован" in blocked.json()["detail"]

    asyncio.run(run())


def test_stream_filter_hides_other_workspaces(monkeypatch):
    async def run():
        engine, make_session, _app, _ = _setup(monkeypatch)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with make_session.begin() as db:
            db.add_all([Admin(id=1, login="owner", password_hash="x", role="owner", status="active"),
                        Admin(id=2, login="bob", password_hash="x", role="user", status="active"),
                        SelectelKey(id=10, name="owner-key", account_id="111", owner_id=1),
                        SelectelKey(id=20, name="bob-key", account_id="222", owner_id=2)])
        bob = stream_filter.StreamFilter(2, is_owner=False)
        owner = stream_filter.StreamFilter(1, is_owner=True)
        await bob.refresh(force=True)
        await owner.refresh(force=True)
        msg = lambda t, d: json.dumps({"type": t, "data": d}, ensure_ascii=False)  # noqa: E731
        caught_owner = msg("caught", {"key_id": 10, "ip": "203.0.113.5"})
        caught_bob = msg("caught", {"key_id": 20, "ip": "203.0.113.9"})
        assert await bob.apply(caught_owner) is None
        assert await bob.apply(caught_bob) == caught_bob
        assert await owner.apply(caught_bob) is None  # владелец панели чужие поимки тоже не видит
        assert await owner.apply(msg("event", {"key_id": None, "message": "сбой"})) is not None
        assert await bob.apply(msg("event", {"key_id": None, "message": "сбой"})) is None
        assert json.loads(await bob.apply(msg("proxy", {"id": 1, "exit_ip": "1.2.3.4"})))["data"] == {}
        assert await bob.apply(msg("extension", {"label": "x", "owner": 1})) is None

    asyncio.run(run())


def test_notify_for_only_panel_owner(monkeypatch):
    async def run():
        sent = []

        async def fake_notify(kind, text, **_):
            sent.append(text)
            return True

        async def owners():
            return {1}

        monkeypatch.setattr(telegram, "notify", fake_notify)
        monkeypatch.setattr(telegram, "panel_owner_ids", owners)
        await telegram.notify_for(2, "caught", "чужой IP")
        await telegram.notify_for(None, "caught", "без кабинета")
        await telegram.notify_for(1, "caught", "мой IP")
        assert sent == ["мой IP"]

    asyncio.run(run())


def test_browser_of():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    assert admin_bot.browser_of(ua) == "Chrome · Windows"
    assert admin_bot.browser_of("") == "неизвестно"
