import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auto_onboarding as auto
from app.onboarding import OnboardingError


@pytest.mark.parametrize("mfa", [True, False])
def test_login_sequence(monkeypatch, mfa):
    events = []
    job = auto.Job("job", 1, "123456")
    polls = 0
    logins = 0

    async def mail(email, cursor=None):
        nonlocal polls
        assert email == "person@example.com"
        events.append("snapshot" if cursor is None else "mail")
        if cursor is None:
            return {"cursor": "snapshot", "code": None}
        polls += 1
        return {"cursor": cursor, "code": "001234"}

    def handle(request):
        nonlocal logins
        path = request.url.path
        events.append(path)
        if path == "/v2/login":
            logins += 1
            if logins == 1:
                assert json.loads(request.content) == {"login": "123456", "email": "person@example.com",
                                                       "password": "test-password", "remember_me": False}
                return httpx.Response(200, json={} if mfa else {"uid": "123456"},
                                      headers={"Set-Cookie": "sid=test; Domain=.selectel.ru; Path=/; Secure"})
            assert request.content == b""
            assert "sid=test" in request.headers["cookie"]
            return httpx.Response(200, json={"uid": "123456"})
        assert "sid=test" in request.headers["cookie"]
        if path == "/v1/account/2fa/factors":
            return httpx.Response(200, json={"items": [{"type": "email"}]})
        if path == "/v1/otp/email":
            assert request.method == "POST"
            return httpx.Response(200, json={})
        if path == "/v1/otp/email/verify":
            assert parse_qs(request.content.decode()) == {"code": ["001234"]}
            return httpx.Response(200, json={})
        if path == "/api/iam/v1/tokens":
            assert request.url.host == "my.selectel.ru"
            assert json.loads(request.content) == {"token": {"domain_name": "123456"}}
            return httpx.Response(201, headers={"X-Subject-Token": "owner-token"}, json={"token": {}})
        pytest.fail(path)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assert await auto.login(client, job, "person@example.com", "test-password") == "owner-token"
        assert events[0] == "snapshot"
        assert logins == (2 if mfa else 1)
        assert polls == (1 if mfa else 0)
        if mfa:
            assert events.index("/v1/otp/email") < events.index("mail") < events.index("/v1/otp/email/verify")
        assert "test-password" not in str(job.view()) and "owner-token" not in str(job.view())

    monkeypatch.setattr(auto, "check_mail", mail)
    asyncio.run(run())


@pytest.mark.parametrize("status,payload,message", [
    (403, {"code": "RECAPTCHA_REQUIRED"}, "CAPTCHA"),
    (429, {}, "ограничил попытки"),
    (401, {"secret": "must-not-leak"}, "не подтвердил вход"),
    (502, {}, "HTTP 502"),
])
def test_errors_are_safe_and_not_retried(status, payload, message):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=payload)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(OnboardingError, match=message) as error:
                await auto.request(client, "POST", auto.API + "/v2/login", json={"password": "secret"})
            assert "must-not-leak" not in str(error.value)
        assert len(calls) == 1
    asyncio.run(run())


def test_other_factor_stops_without_sending_code(monkeypatch):
    async def mail(*args):
        return {"cursor": "snapshot"}
    calls = []
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"items": [{"type": "otp"}]} if request.method == "GET" else {})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(OnboardingError, match="другой второй фактор"):
                await auto.login(client, auto.Job("job", 1, "123456"), "person@example.com", "password")
        assert calls == ["/v2/login", "/v1/account/2fa/factors"]
    monkeypatch.setattr(auto, "check_mail", mail)
    asyncio.run(run())


def test_job_conflict_and_cancel(monkeypatch):
    async def hold(*args):
        await asyncio.sleep(100)
    monkeypatch.setattr(auto, "jobs", {})
    monkeypatch.setattr(auto, "run", hold)
    async def run():
        job = auto.start(1, "123456", "person@example.com", "password", 1, "")
        with pytest.raises(OnboardingError, match="уже подключается"):
            auto.start(1, "123456", "person@example.com", "password", 1, "")
        job.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job.task
    asyncio.run(run())


def test_legacy_routes_require_auth_and_direct_new_connections_to_manual_login(monkeypatch):
    from app.api import keys
    app = FastAPI()
    app.include_router(keys.router, prefix="/api")
    app.dependency_overrides[keys.get_db] = lambda: None
    client = TestClient(app)
    path = "/api/keys/onboard/auto"
    body = {"account_id": "123456", "proxy_id": 1, "email": "person@example.com"}
    assert client.post(path, json=body).status_code == 403
    assert client.post(path, json=body, headers={"X-Requested-With": "toffee-roller"}).status_code == 401
    app.dependency_overrides[keys.current_admin] = lambda: SimpleNamespace(admin=SimpleNamespace(id=1))
    assert client.post(path, json=body).status_code == 409
    job = auto.Job("test-job", 1, "123456")
    def start(*args):
        pytest.fail("The legacy route must not start a login job")
    monkeypatch.setattr(auto, "start", start)
    monkeypatch.setattr(auto, "jobs", {job.id: job})
    response = client.post(path, json=body, headers={"X-Selectel-Password": "secret"})
    assert response.status_code == 409 and "secret" not in response.text
    assert "IAM-токен" in response.json()["detail"]
    assert client.get(path + "/test-job").status_code == 200
    job.owner = 2
    assert client.get(path + "/test-job").status_code == 404
    assert client.delete(path + "/test-job").status_code == 404
    job.owner = 1
    job.stage = "connecting"
    assert client.delete(path + "/test-job").status_code == 409
