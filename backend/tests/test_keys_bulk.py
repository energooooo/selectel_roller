from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import keys
from app.auth import deps
from app.auth.security import token_hash
from app.db.models import Admin, AuditLog, Base, CaughtIP, Proxy, SelectelKey, Session, utcnow
from app.engine import manager as manager_module


def test_pause_keys_scopes_and_preserves_held_ips(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://")
        make_session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def get_db():
            async with make_session() as db:
                yield db

        @asynccontextmanager
        async def session_scope():
            async with make_session.begin() as db:
                yield db

        manager = manager_module.WorkerManager()
        monkeypatch.setattr(keys, "manager", manager)
        monkeypatch.setattr(manager_module, "session_scope", session_scope)
        monkeypatch.setattr(deps, "session_scope", session_scope)
        app = FastAPI()
        app.include_router(keys.router)
        app.dependency_overrides[keys.get_db] = get_db
        token = "test-session"
        async with make_session.begin() as db:
            db.add_all([
                Admin(id=1, login="tester", password_hash="unused"),
                Session(admin_id=1, token_hash=token_hash(token), expires_at=utcnow() + timedelta(hours=1)),
                Proxy(id=1, display="proxy:1080", label="Test proxy", url_enc="unused", fingerprint="test-proxy"),
                SelectelKey(id=1, name="One", account_id="111", status="hunting", proxy_id=1),
                SelectelKey(id=2, name="Two", account_id="222", status="starting"),
                SelectelKey(id=3, name="Three", account_id="333", status="auth_failed"),
                SelectelKey(id=4, name="Four", account_id="444", status="error"),
                CaughtIP(ip="192.0.2.10", region="ru-1", key_id=1),
                CaughtIP(ip="192.0.2.11", region="ru-1", key_id=1, on_account=False),
            ])

        first, second, retrying = (asyncio.create_task(asyncio.sleep(60)) for _ in range(3))
        manager.tasks.update({1: first, 2: second, 4: retrying})
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers = {deps.CSRF_HEADER: deps.CSRF_VALUE}
                assert (await client.post("/keys/pause", json={"key_ids": None}, headers=headers)).status_code == 401
                client.cookies.set(deps.COOKIE_NAME, token)
                assert (await client.post("/keys/pause", json={"key_ids": None})).status_code == 403
                for body in ({}, {"key_ids": []}):
                    assert (await client.post("/keys/pause", json=body, headers=headers)).status_code == 422

                async def pause(ids):
                    response = await client.post("/keys/pause", json={"key_ids": ids}, headers=headers)
                    assert response.status_code == 200, response.text
                    return response.json()["paused"]

                assert await pause([1, 1, 3, 99]) == 1
                assert first.cancelled() and not second.done() and not retrying.done()
                assert await pause(None) == 2
                assert second.cancelled() and retrying.cancelled() and not manager.tasks
                assert await pause(None) == 0

                response = await client.get("/keys")
                assert response.status_code == 200
                rows = response.json()
                assert [(row["enabled"], row["status"], row["held_ips"]) for row in rows] == [
                    (False, "paused", 1), (False, "paused", 0), (True, "auth_failed", 0), (False, "paused", 0),
                ]
                assert rows[0]["proxy"]["label"] == "Test proxy"
                assert rows[1]["proxy"] is None

            async with make_session() as db:
                entries = (await db.scalars(select(AuditLog).order_by(AuditLog.id))).all()
                assert [(entry.action, entry.target, entry.details["key_ids"]) for entry in entries] == [
                    ("key.pause_bulk", "selected", [1]), ("key.pause_bulk", "all", [2, 4]),
                ]
        finally:
            tasks = (first, second, retrying)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    asyncio.run(run())
