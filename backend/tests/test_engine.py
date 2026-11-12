"""Тесты движка без обращения к Selectel: разбор прокси, сессии, классификация ответов Neutron."""
from __future__ import annotations

import asyncio
from urllib.parse import unquote, urlparse

import httpx
import pytest

from app.engine import worker as worker_mod
from app.engine.proxy import ProxyParseError, parse_proxy_line, with_session
from app.engine.worker import KeyWorker, QuotaExceeded, RegionUnreachable, StopHunt, Target, neutron_error


# ── прокси ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,url,label", [
    ("1.2.3.4:8000", "http://1.2.3.4:8000", ""),
    ("user:pa:ss@host.ru:3128 моб-1", "http://user:pa%3Ass@host.ru:3128", "моб-1"),
    ("host.ru:1080:user:pass", "http://user:pass@host.ru:1080", ""),
    ("socks5://u:p@h:1080 метка с пробелами", "socks5://u:p@h:1080", "метка с пробелами"),
])
def test_parse_proxy(line: str, url: str, label: str) -> None:
    parsed = parse_proxy_line(line)
    assert parsed.url == url
    assert parsed.label == label
    assert "@" not in parsed.display


def test_parse_proxy_errors() -> None:
    for bad in ("", "nonsense", "ftp://h:1", "host:99999"):
        with pytest.raises(ProxyParseError):
            parse_proxy_line(bad)


def test_session_suffix() -> None:
    url = "socks5://u48545:secret_country-RU@mp.eu.example.cc:3002"
    sticky = with_session(url, "password_suffix", "tr710039")
    assert unquote(urlparse(sticky).password) == "secret_country-RU_session-tr710039"
    assert unquote(urlparse(with_session(url, "password_suffix", "tr1", 2)).password).endswith("_session-tr100000g2")
    assert with_session(url, "none", "x") == url


# ── классификация ответов ───────────────────────────────────────────────────

def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://ru-1/x"))


def test_neutron_error() -> None:
    status, kind, msg = neutron_error(_resp(409, {"NeutronError": {"type": "IpAddressGenerationFailure", "message": "No more IP"}}))
    assert (status, kind, msg) == (409, "IpAddressGenerationFailure", "No more IP")


class FakeClient:
    def __init__(self, outcome) -> None:  # noqa: ANN001
        self.outcome = outcome

    async def create_floating_ip_in_subnet(self, *a, **k):  # noqa: ANN002, ANN003
        raise self.outcome


def _worker(monkeypatch, outcome) -> KeyWorker:  # noqa: ANN001
    w = KeyWorker(1, manager=None)  # type: ignore[arg-type]
    w.client = FakeClient(outcome)  # type: ignore[assignment]
    recorded: list[str] = []

    async def record(outcome: str, region: str, latency) -> None:  # noqa: ANN001
        recorded.append(outcome)
        w.consecutive_errors = 0 if outcome in ("busy", "caught", "misses") else w.consecutive_errors + 1

    async def log(*a, **k) -> None:  # noqa: ANN002, ANN003
        pass

    monkeypatch.setattr(w, "_record_attempt", record)
    monkeypatch.setattr(w, "log", log)
    w.recorded = recorded  # type: ignore[attr-defined]
    return w


def _target() -> Target:
    return Target(region="ru-1", network_id="n", os_subnet_id="s", pool_cidr="46.182.24.0/24",
                  subnet_id=1, subnet_cidr="46.182.24.0/24", kind="exact")


def _status_error(status: int, body: dict) -> httpx.HTTPStatusError:
    r = _resp(status, body)
    return httpx.HTTPStatusError("x", request=r.request, response=r)


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def test_busy_is_not_error(monkeypatch) -> None:  # noqa: ANN001
    w = _worker(monkeypatch, _status_error(409, {"NeutronError": {"type": "IpAddressGenerationFailure", "message": ""}}))
    t = _target()
    assert run(w._attempt(t)) is None
    assert w.recorded == ["busy"] and t.busy == 1 and not t.disabled and w.consecutive_errors == 0


def test_exhausted_400_is_busy(monkeypatch) -> None:  # noqa: ANN001
    w = _worker(monkeypatch, _status_error(400, {"NeutronError": {"type": "ExternalIpAddressExhausted", "message": ""}}))
    run(w._attempt(_target()))
    assert w.recorded == ["busy"]


def test_account_ban_stops_and_disables(monkeypatch) -> None:  # noqa: ANN001
    w = _worker(monkeypatch, _status_error(403, {"NeutronError": {
        "type": "PolicyNotAuthorized", "message": "rule:create_floatingip is disallowed by policy"}}))
    with pytest.raises(StopHunt) as stop:
        run(w._attempt(_target()))
    assert stop.value.status == "banned" and stop.value.disable


def test_quota(monkeypatch) -> None:  # noqa: ANN001
    w = _worker(monkeypatch, _status_error(409, {"NeutronError": {"type": "OverQuota", "message": "Quota exceeded"}}))
    with pytest.raises(QuotaExceeded):
        run(w._attempt(_target()))


def test_other_4xx_disables_target(monkeypatch) -> None:  # noqa: ANN001
    w = _worker(monkeypatch, _status_error(404, {"NeutronError": {"type": "SubnetNotFound", "message": "gone"}}))
    t = _target()
    run(w._attempt(t))
    assert t.disabled.startswith("404")


def test_connect_timeout_is_region_unreachable(monkeypatch) -> None:  # noqa: ANN001
    w = _worker(monkeypatch, httpx.ConnectTimeout("timed out"))
    with pytest.raises(RegionUnreachable):
        run(w._attempt(_target()))
    assert w.recorded == ["timeouts"]


def test_proxy_error_counted(monkeypatch) -> None:  # noqa: ANN001
    w = _worker(monkeypatch, httpx.ProxyError("ruleset"))
    with pytest.raises(RegionUnreachable):
        run(w._attempt(_target()))
    assert w.recorded == ["proxy_errors"]


def test_jitter_bounds() -> None:
    for _ in range(100):
        assert 10 <= worker_mod._jitter(10, 12) <= 12
    assert worker_mod._jitter(5, 3) <= 5
