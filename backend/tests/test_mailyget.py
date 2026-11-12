import asyncio
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import mailyget


@pytest.mark.parametrize("payload,expected", [
    ({"email": {"text": "Используйте проверочный код: 001234"}}, "001234"),
    ({"email": {"html": "<style>123456</style><script>654321</script><p>Проверочный код <b>012345</b></p>"}}, "012345"),
    ({"email": {"html": "<style>Проверочный код: 123456</style><p>Заказ 987654</p>"}}, None),
    ({"email": {"text": "Проверочный код: 123456789"}}, None),
])
def test_extract_code(payload, expected):
    assert mailyget.extract_code(payload) == expected


def test_snapshot_poll_and_expiration(monkeypatch):
    settings = SimpleNamespace(
        mailyget_list_url="https://mail.example/api/{email}?headers=true",
        mailyget_detail_url_template="https://mail.example/api/{email}/{id}",
        mailyget_authorization="", mailyget_cookie="",
    )
    monkeypatch.setattr(mailyget, "get_settings", lambda: settings)
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr("app.core.crypto._fernet", lambda: fernet)
    now = [1000.0]
    monkeypatch.setattr(mailyget.time, "time", lambda: now[0])
    messages = [{"id": "old", "from": mailyget.DEFAULT_SENDER, "subject": mailyget.DEFAULT_SUBJECT}]
    detail_calls = []
    ready = [False]

    def handle(request):
        assert "person%40example.com" in str(request.url)
        if request.url.query:
            return httpx.Response(200, json={"success": True, "emails": list(messages)})
        detail_calls.append(request.url.path)
        return httpx.Response(200, json={"success": True, "email": {
            "text": "Проверочный код: 001234" if ready[0] else "Письмо загружается",
        }})

    client = httpx.AsyncClient
    monkeypatch.setattr(mailyget.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))

    async def run():
        snapshot = await mailyget.check_mail("person@example.com")
        cursor = snapshot["cursor"]
        assert snapshot["code"] is None
        assert (await mailyget.check_mail("person@example.com", cursor))["code"] is None
        assert not detail_calls
        messages.extend([
            {"id": "spam", "from": "other@example.com", "subject": mailyget.DEFAULT_SUBJECT},
            {"id": "wrong-subject", "from": mailyget.DEFAULT_SENDER, "subject": "Other"},
            {"id": "new/id", "from": "Selectel <no-reply@selectel.ru>", "subject": mailyget.DEFAULT_SUBJECT},
        ])
        assert (await mailyget.check_mail("person@example.com", cursor))["code"] is None
        ready[0] = True
        assert (await mailyget.check_mail("person@example.com", cursor))["code"] == "001234"
        assert len(detail_calls) == 2
        with pytest.raises(mailyget.MailError, match="недействительно"):
            await mailyget.check_mail("other@example.com", cursor)
        with pytest.raises(mailyget.MailError, match="недействительно"):
            await mailyget.check_mail("person@example.com", cursor[:-5] + "xxxxx")
        now[0] += 121
        with pytest.raises(mailyget.MailError, match="120 секунд"):
            await mailyget.check_mail("person@example.com", cursor)

    asyncio.run(run())


def test_mail_route_auth_and_no_store(monkeypatch):
    from app.api import keys

    app = FastAPI()
    app.include_router(keys.router, prefix="/api")
    app.dependency_overrides[keys.get_db] = lambda: None
    client = TestClient(app)
    path = "/api/keys/onboard/mail-code"
    body = {"email": "person@example.com"}
    assert client.post(path, json=body).status_code == 403
    assert client.post(path, json=body, headers={"X-Requested-With": "toffee-roller"}).status_code == 401
    app.dependency_overrides[keys.current_admin] = lambda: object()

    async def mock_check(email, cursor):
        assert email == body["email"] and cursor is None
        return {"cursor": "encrypted", "code": None}

    monkeypatch.setattr(keys, "check_mail", mock_check)
    response = client.post(path, json=body)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert client.post(path, json={"email": "bad"}).status_code == 422

    async def unavailable(*args):
        raise mailyget.MailError("Почта недоступна")

    monkeypatch.setattr(keys, "check_mail", unavailable)
    response = client.post(path, json=body)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
