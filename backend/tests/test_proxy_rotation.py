import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import unquote, urlparse
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import onboarding
from app.core.crypto import encrypt
from app.db.models import Base, Proxy, SelectelKey
from app.engine import proxy as proxy_mod, worker as worker_mod
from app.engine import manager as manager_mod
from app.engine.manager import WorkerManager
from app.engine.worker import KeyWorker, StopHunt
import pytest


def test_random_candidates_and_single_fallback(monkeypatch):
    proxies = [SimpleNamespace(id=1, status="ok"), SimpleNamespace(id=2, status="partial"),
               SimpleNamespace(id=3, status="unchecked"), SimpleNamespace(id=4, status="dead")]
    candidates = []
    def choose(pool):
        candidates.append([p.id for p in pool])
        return pool[-1]
    monkeypatch.setattr(proxy_mod.secrets, "choice", choose)
    assert proxy_mod.choose_random_proxy(proxies, 1).id == 3
    assert candidates == [[2, 3]]
    assert proxy_mod.choose_random_proxy(proxies[:1], 1).id == 1
    assert proxy_mod.choose_random_proxy(proxies[3:]) is None


def test_existing_sticky_suffix_is_replaced():
    url = "http://user:password_country-RU_session-old@proxy.example:8080"
    first = proxy_mod.with_session(url, "password_suffix", "abcdefgh", 1)
    second = proxy_mod.with_session(first, "password_suffix", "abcdefgh", 2)
    password = unquote(urlparse(second).password)
    assert password == "password_country-RU_session-abcdefghg2"
    assert proxy_mod.with_session(url, "none", "new") == url


def test_manager_worker_and_onboarding_rotation(monkeypatch):
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr("app.core.crypto._fernet", lambda: fernet)
    monkeypatch.setattr(onboarding, "_last_onboarding_proxy", {})

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        @asynccontextmanager
        async def scope():
            async with sessions.begin() as db:
                yield db
        monkeypatch.setattr(manager_mod, "session_scope", scope)
        monkeypatch.setattr(worker_mod, "session_scope", scope)
        monkeypatch.setattr(onboarding, "session_scope", scope)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with scope() as db:
                for i in range(1, 4):
                    db.add(Proxy(id=i, url_enc=encrypt(f"http://u:p@proxy{i}.example:8080"),
                                 display=f"proxy{i}", fingerprint=str(i), session_mode="password_suffix",
                                 status="dead" if i == 3 else "ok"))
                db.add(SelectelKey(id=1, name="test", account_id="123456", username="service",
                                  password_enc=encrypt("password"), proxy_mode="auto", proxy_id=1))
            manager = WorkerManager()
            assert (await manager.proxy_for_key(1)).id == 2
            assert (await manager.proxy_for_key(1)).id == 1
            worker = KeyWorker(1, manager)
            worker.account_id = "123456"
            worker._proxy_selection_mode = "auto"
            worker.client = SimpleNamespace(set_proxy=AsyncMock())
            await worker._rotate_proxy()
            first = worker.client.set_proxy.call_args.args[0]
            await worker._rotate_proxy()
            second = worker.client.set_proxy.call_args.args[0]
            assert urlparse(first).hostname != urlparse(second).hostname
            assert unquote(urlparse(first).password) != unquote(urlparse(second).password)
            async with scope() as db:
                assert (await db.get(SelectelKey, 1)).proxy_session_gen == 2
            first_id, first_url = await onboarding.random_connection_proxy("123456")
            second_id, second_url = await onboarding.random_connection_proxy("123456")
            assert first_id != second_id and first_url != second_url
            async with scope() as db:
                for i in (1, 2):
                    (await db.get(Proxy, i)).status = "dead"
            with pytest.raises(StopHunt, match="Нет доступных прокси"):
                await worker._rotate_proxy()
            assert worker.client.set_proxy.await_count == 2
            with pytest.raises(onboarding.OnboardingError, match="Нет доступных прокси"):
                await onboarding.random_connection_proxy("123456")
        finally:
            await engine.dispose()
    asyncio.run(run())
