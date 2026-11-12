"""Деньги и VDS: прайс, прогноз «на сколько хватит», разбор Billing API, выбор flavor'а — без сети."""
from __future__ import annotations

import asyncio

import httpx

from app.engine import billing, vds
from app.engine.resell import SelectelResellClient

TABLE = {
    ("network_floatingips", "ru-2"): 25.968384,
    ("compute_cores_percent_10", "ru-2c"): 20.10624,
    ("compute_cores_percent_20", "ru-2c"): 40.21248,
    ("compute_ram", "ru-2c"): 0.03576375,
    ("volume_gigabytes_basic", "ru-2c"): 1.1184096,
}


def test_prices_to_month() -> None:
    assert billing.fip_month(TABLE, "ru-2") == 189.57
    assert billing.fip_month({}, "ru-9") == 189.57  # прайс недоступен — запасная цена
    assert billing.flavor_month(TABLE, "PRC10.1-512", "ru-2c") == 280.45
    assert billing.flavor_month(TABLE, "PRC10.1-512", "ru-1a") is None
    assert billing.flavor_month(TABLE, "SL1.1-1024", "ru-2c") is None
    assert billing.disk_month(TABLE, 5, "ru-2c") == 40.82


def test_days_left() -> None:
    assert billing.days_left(100.0, 48, 10.0) == 2.0  # прогноз Selectel важнее
    assert billing.days_left(100.0, None, 10.0) == 10.0
    assert billing.days_left(None, None, 10.0) is None
    assert billing.days_left(100.0, None, 0.0) is None
    assert billing.days_left(95.0, 0, 0.0) is None  # трат нет — Selectel пишет 0 ч, это не «кончились»
    assert billing.days_left(95.0, 0, 6.24) == 15.2
    assert billing.days_left(-5.0, 0, 6.24) == 0.0


def test_candidates_cheapest_first() -> None:
    flavors = [{"id": "b", "name": "PRC20.1-1024", "ram": 1024}, {"id": "a", "name": "PRC10.1-512", "ram": 512},
               {"id": "c", "name": "SL1.1-1024", "ram": 1024}]
    out = vds.candidates(flavors, ["ru-2a", "ru-2c"], TABLE)
    assert [(o[1], o[3]) for o in out] == [("a", "ru-2c"), ("b", "ru-2c")]


def test_balance_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/balances":
            return httpx.Response(200, json={"status": "success", "data": {"billings": [
                {"billing_type": "primary", "final_sum": 123456, "balances_values_sum": 123456}]}})
        if request.url.path == "/v2/billing/prediction":
            return httpx.Response(200, json={"status": "success", "data": {"primary": 72, "vpc": None}})
        return httpx.Response(404)

    async def run() -> tuple:
        client = SelectelResellClient(username="u", password="p", account_id="1")
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client._token, client._token_expires = "t", None

        async def noop() -> None:
            pass

        client.ensure_authenticated = noop  # type: ignore[method-assign]
        try:
            return await client.get_balance()
        finally:
            await client.close()

    assert asyncio.run(run()) == (1234.56, 72)


def test_cloud_config_has_password() -> None:
    import base64
    text = base64.b64decode(vds.cloud_config("s3cret")).decode()
    assert text.startswith("#cloud-config") and "password: s3cret" in text and "ssh_pwauth: true" in text
