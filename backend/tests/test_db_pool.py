from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import data, keys
from app.auth.deps import COOKIE_NAME
from app.auth.security import token_hash
from app.db.models import Admin, Base, SelectelKey, Session, utcnow
from app.db.session import get_db


@pytest.mark.parametrize("path", ["/stream", "/keys/1/floating-ips"])
def test_live_requests_release_connection_before_waiting(path, tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'pool.db').as_posix()}",
                                     pool_size=1, max_overflow=0, pool_timeout=0.5)
        make_session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        token = "test-session"
        async with make_session.begin() as db:
            db.add_all([
                Admin(id=1, login="tester", password_hash="unused", role="owner", status="active"),
                Session(admin_id=1, token_hash=token_hash(token), expires_at=utcnow() + timedelta(hours=1)),
                SelectelKey(id=1, name="Test key", account_id="123456", enabled=False, owner_id=1),
            ])

        async def request_db():
            async with make_session() as db:
                yield db

        waiting = asyncio.Event()

        async def list_floating_ips():
            waiting.set()
            await asyncio.Future()

        async def client_for_key(_):
            return SimpleNamespace(list_floating_ips=list_floating_ips), False

        monkeypatch.setattr(keys.manager, "client_for_key", client_for_key)
        app = FastAPI()
        app.include_router(data.router)
        app.include_router(keys.router)
        app.dependency_overrides[get_db] = request_db

        async def receive():
            await asyncio.Future()

        async def send(message):
            if message["type"] == "http.response.start":
                assert message["status"] == 200
            if message.get("body") == b"retry: 3000\n\n":
                waiting.set()

        request = asyncio.create_task(app({
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "query_string": b"",
            "headers": [(b"cookie", f"{COOKIE_NAME}={token}".encode())],
        }, receive, send))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=5)
            assert not request.done()
            async with make_session() as db:
                assert await db.scalar(text("SELECT 1")) == 1
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            await engine.dispose()

    asyncio.run(run())
