from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import onboarding
from app.core.crypto import decrypt, encrypt
from app.db.models import Base, Proxy


@pytest.mark.parametrize("balances,token_account,expected_error", [
    ({"vpc": 20_000}, "123456", None),
    ({"primary": 20_000}, "123456", None),
    ({"vpc": 0}, "123456", "Пополните баланс"),
    ({"primary": 50_000, "vpc": 19_999}, "123456", "Доступно 199,99 ₽"),
    ({"primary": 50_000, "storage": 50_000}, "123456", "Selectel не вернул доступный баланс"),
    ({"vpc": 20_000}, "654321", "Номер аккаунта не совпадает"),
])
def test_onboard_account(monkeypatch, balances, token_account, expected_error):
    requests = []
    created_user = {}
    proxy_url = "http://user:pass@proxy.example:8080"
    project_id = "a" * 32

    def handle_request(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append((request.method, path))
        if path == "/identity/v3/auth/tokens":
            assert "X-Auth-Token" not in request.headers
            auth = json.loads(request.content)["auth"]
            assert auth["identity"]["password"]["user"]["password"] == created_user["password"]
            assert auth["scope"]["project"]["id"] == project_id
            return httpx.Response(201, headers={"X-Subject-Token": "service-token"},
                                  json={"token": {"project": {"id": project_id}}})
        assert request.headers["X-Auth-Token"] == "owner-token"
        if path.endswith("/accounts"):
            return httpx.Response(200, json={"account": {"name": token_account, "locked": False, "enabled": True}})
        if path == "/v3/balances":
            return httpx.Response(200, json={"data": {"billings": [
                {"billing_type": billing_type, "final_sum": balance} for billing_type, balance in balances.items()
            ]}})
        if path.endswith("/projects") and request.method == "GET":
            return httpx.Response(200, json={"projects": []})
        if path.endswith("/projects") and request.method == "POST":
            project = json.loads(request.content)["project"]
            assert project["name"] == "toffee-roller-123456"
            return httpx.Response(200, json={"project": {**project, "id": project_id, "enabled": True}})
        if path == "/iam/v1/service_users":
            created_user.update(json.loads(request.content))
            assert created_user["roles"] == [{"scope": "project", "project_id": project_id, "role_name": "member"}]
            return httpx.Response(200)
        pytest.fail(f"Unexpected request: {request.method} {path}")

    async_client = httpx.AsyncClient

    def mock_client(*, proxy, trust_env, **kwargs):
        assert proxy == proxy_url
        assert trust_env is False
        return async_client(transport=httpx.MockTransport(handle_request), **kwargs)

    async def run():
        db_engine = create_async_engine("sqlite+aiosqlite://")
        make_session = async_sessionmaker(db_engine, expire_on_commit=False)

        @asynccontextmanager
        async def test_session():
            async with make_session.begin() as db:
                yield db

        monkeypatch.setattr(onboarding, "session_scope", test_session)
        try:
            async with db_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            with pytest.raises(onboarding.OnboardingError, match="Нет доступных прокси"):
                await onboarding.onboard_account("123456", 1, "toffee-roller-123456", "owner-token")
            assert not requests
            async with test_session() as db:
                db.add(Proxy(id=1, url_enc=encrypt(proxy_url), display="http://proxy.example:8080",
                             fingerprint="test-proxy", status="ok"))
            if expected_error:
                with pytest.raises(onboarding.OnboardingError, match=expected_error):
                    await onboarding.onboard_account("123456", 1, "toffee-roller-123456", "owner-token")
                assert requests == [("GET", "/vpc/resell/v2/accounts")] + (
                    [("GET", "/v3/balances")] if token_account == "123456" else []
                )
                return

            key, added = await onboarding.onboard_account("123456", 1, "toffee-roller-123456", "owner-token")
            assert added
            assert key.project_id == project_id
            assert (key.status, key.enabled, key.proxy_mode, key.proxy_id) == ("paused", False, "auto", 1)
            assert key.password_enc != created_user["password"]
            assert decrypt(key.password_enc) == created_user["password"]
            request_count = len(requests)
            same_key, added_again = await onboarding.onboard_account("123456", 1, "toffee-roller-123456", "owner-token")
            assert same_key.id == key.id and not added_again
            assert len(requests) == request_count
        finally:
            await db_engine.dispose()

    test_fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(onboarding.httpx, "AsyncClient", mock_client)
    monkeypatch.setattr("app.core.crypto._fernet", lambda: test_fernet)
    asyncio.run(run())


def test_onboard_route_requires_admin_and_accepts_iam_token_in_header(monkeypatch):
    from app.api import keys

    app = FastAPI()
    app.include_router(keys.router, prefix="/api")
    app.dependency_overrides[keys.get_db] = lambda: None
    client = TestClient(app)
    path = "/api/keys/onboard"
    payload = {"account_id": "123456", "proxy_id": 1}
    token = "test-iam-token"
    headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "toffee-roller"}

    assert client.post(path, json=payload).status_code == 403
    assert client.post(path, json=payload, headers=headers).status_code == 401

    app.dependency_overrides[keys.current_admin] = lambda: SimpleNamespace(admin=SimpleNamespace(id=1, role="owner"))

    async def mine(db, model, obj_id, current, missing=""):  # прокси из кабинета пользователя — проверка владельца
        return SimpleNamespace(id=obj_id, owner_id=1)

    monkeypatch.setattr(keys.scope, "get_owned", mine)
    missing_token = client.post(path, json=payload)
    assert missing_token.status_code == 422
    assert "IAM-токен" in missing_token.json()["detail"]
    invalid_proxy = client.post(path, json={**payload, "proxy_id": 0}, headers=headers)
    assert invalid_proxy.status_code == 422
    assert token not in invalid_proxy.text

    async def mock_empty_balance(*_, **__):
        raise onboarding.OnboardingPaymentRequired("Пополните баланс")

    monkeypatch.setattr(keys, "onboard_account", mock_empty_balance)
    empty_balance = client.post(path, json=payload, headers=headers)
    assert empty_balance.status_code == 402
    assert empty_balance.json() == {"detail": "Пополните баланс"}

    async def mock_onboard(account_id, proxy_id, project_name, iam_token, owner_id=None):
        assert (account_id, proxy_id, project_name, iam_token, owner_id) == ("123456", 1, "", token, 1)
        return SimpleNamespace(name="toffee-roller-123456", account_id=account_id, project_name="demo"), True

    async def mock_view(db, key):
        return {"name": key.name, "status": "paused", "enabled": False}

    async def mock_audit(current, action, target, details):
        assert action == "key.create" and token not in str(details)

    monkeypatch.setattr(keys, "onboard_account", mock_onboard)
    monkeypatch.setattr(keys, "_view", mock_view)
    monkeypatch.setattr(keys, "audit", mock_audit)
    monkeypatch.setattr(keys.bus, "publish", lambda *_: None)
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"key": {"name": "toffee-roller-123456", "status": "paused", "enabled": False}, "added": True}
    assert token not in response.text


def test_onboard_retries_dead_proxy(monkeypatch):
    """Мёртвый прокси рвёт соединение на проверке токена → берём другой и доходим до конца."""
    project_id = "b" * 32
    attempts = {"accounts": 0}
    used_proxies = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/accounts"):
            attempts["accounts"] += 1
            if attempts["accounts"] == 1:
                raise httpx.ConnectError("proxy refused", request=request)  # «мёртвый» прокси
            return httpx.Response(200, json={"account": {"name": "123456", "locked": False, "enabled": True}})
        if path == "/v3/balances":
            return httpx.Response(200, json={"data": {"billings": [{"billing_type": "vpc", "final_sum": 20_000}]}})
        if path.endswith("/projects") and request.method == "GET":
            return httpx.Response(200, json={"projects": []})
        if path.endswith("/projects") and request.method == "POST":
            project = json.loads(request.content)["project"]
            return httpx.Response(200, json={"project": {**project, "id": project_id, "enabled": True}})
        if path == "/iam/v1/service_users":
            return httpx.Response(200)
        if path == "/identity/v3/auth/tokens":
            return httpx.Response(201, headers={"X-Subject-Token": "service-token"},
                                  json={"token": {"project": {"id": project_id}}})
        pytest.fail(f"Unexpected request: {request.method} {path}")

    real_client = httpx.AsyncClient

    def mock_client(*, proxy, trust_env, **kwargs):
        used_proxies.append(proxy)
        return real_client(transport=httpx.MockTransport(handle_request), **kwargs)

    async def fake_proxy(account_id):
        return (len(used_proxies) + 1, f"http://proxy{len(used_proxies) + 1}.example:8080")

    async def run():
        db_engine = create_async_engine("sqlite+aiosqlite://")
        make_session = async_sessionmaker(db_engine, expire_on_commit=False)

        @asynccontextmanager
        async def test_session():
            async with make_session.begin() as db:
                yield db

        monkeypatch.setattr(onboarding, "session_scope", test_session)
        monkeypatch.setattr(onboarding, "random_connection_proxy", fake_proxy)
        try:
            async with db_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            key, added = await onboarding.onboard_account("123456", None, "toffee-roller-123456", "owner-token")
            assert added and key.project_id == project_id
            assert attempts["accounts"] == 2          # первый прокси упал, второй прошёл
            assert len(used_proxies) == 2             # сменили прокси
            assert key.proxy_id == 2                  # ключ привязан к рабочему прокси
        finally:
            await db_engine.dispose()

    test_fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(onboarding.httpx, "AsyncClient", mock_client)
    monkeypatch.setattr("app.core.crypto._fernet", lambda: test_fernet)
    asyncio.run(run())


def test_onboard_gives_up_after_all_proxies_dead(monkeypatch):
    """Если все прокси мертвы — внятная ошибка с номером последнего прокси, без бесконечных повторов."""
    calls = {"n": 0}

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            calls["n"] += 1
            raise httpx.ConnectError("dead", request=request)
        pytest.fail("Должны падать на проверке токена")

    real_client = httpx.AsyncClient

    def mock_client(*, proxy, trust_env, **kwargs):
        return real_client(transport=httpx.MockTransport(handle_request), **kwargs)

    async def fake_proxy(account_id):
        return (7, "http://dead.example:8080")

    async def run():
        monkeypatch.setattr(onboarding, "random_connection_proxy", fake_proxy)
        monkeypatch.setattr(onboarding.httpx, "AsyncClient", mock_client)
        with pytest.raises(onboarding.OnboardingError, match="ошибка соединения через прокси"):
            await onboarding.onboard_account("123456", None, "toffee-roller-123456", "owner-token")
        assert calls["n"] == 3  # ровно три попытки

    test_fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr("app.core.crypto._fernet", lambda: test_fernet)
    asyncio.run(run())
