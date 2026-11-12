from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .models import FloatingIPRecord

try:  # socksio ставится вместе с httpx[socks]; без него SOCKS-прокси не работает вовсе
    from socksio.exceptions import ProtocolError as _SocksProtocolError
except ImportError:  # pragma: no cover
    class _SocksProtocolError(Exception):
        pass


IDENTITY_URL = "https://cloud.api.selcloud.ru/identity/v3/auth/tokens"


class SelectelClient:
    """Клиент Selectel: Keystone-авторизация и Neutron floating IP, опционально через прокси."""

    def __init__(
        self,
        username: str,
        password: str,
        account_id: str,
        project_name: str = "",
        project_id: str = "",
        request_timeout: float = 30.0,
        regions: tuple[str, ...] = (),
        connect_timeout: float | None = None,
        proxy: str | None = None,
    ) -> None:
        self.username = username.strip()
        self.password = password
        self.account_id = account_id.strip()
        self.project_name = project_name.strip()
        self.project_id = project_id.strip()
        # Прокси (напр. socks5://user:pass@host:port) — весь трафик к Selectel идёт через него.
        # Нужно, когда прямой IP хоста забанен Selectel'ом на эндпоинтах региона: мобильный/
        # другой egress-IP снова видит ru-1/ru-3/ru-9. Для socks5:// требуется пакет socksio.
        self.proxy = (proxy or "").strip() or None
        self.request_timeout = max(float(request_timeout), 5.0)
        # Отдельный таймаут на установку соединения. Живой эндпоинт Selectel принимает
        # коннект за ~45 мс, поэтому короткий connect-таймаут не мешает рабочим регионам,
        # но мёртвый/заблокированный эндпоинт отваливается за секунды, а не за request_timeout.
        if connect_timeout is None:
            self.connect_timeout = self.request_timeout
        else:
            self.connect_timeout = max(float(connect_timeout), 1.0)
        # Сколько раз пробовать запрос при обрыве соединения (см. _send_with_retries).
        self.transport_attempts = 4
        self.regions = tuple(region.strip() for region in regions if region.strip())

        self._token: str = ""
        self._token_expires: datetime | None = None
        self._auth_lock = asyncio.Lock()
        self._neutron_urls: dict[str, str] = {}
        self._nova_urls: dict[str, str] = {}
        self._cinder_urls: dict[str, str] = {}
        self._glance_urls: dict[str, str] = {}
        self._external_network_ids: dict[str, str] = {}
        self._resource_regions: dict[str, str] = {}

        self._client = self._build_http_client()

    def _build_http_client(self) -> httpx.AsyncClient:
        client_kwargs: dict[str, Any] = dict(
            timeout=httpx.Timeout(self.request_timeout, connect=self.connect_timeout),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            verify=False,
            follow_redirects=True,
        )
        if self.proxy:
            client_kwargs["proxy"] = self.proxy
            # Запросы идут раз в ~10 с, а keepalive httpx по умолчанию 5 с: без этого каждый
            # запрос заново открывает SOCKS-туннель + TLS, и любой флап прокси на установке
            # туннеля бьёт по каждому запросу. Держим туннель к эндпоинту региона живым.
            client_kwargs["limits"] = httpx.Limits(keepalive_expiry=120.0)
        return httpx.AsyncClient(**client_kwargs)

    async def set_proxy(self, proxy: str | None) -> None:
        """Пересоздаёт HTTP-клиент с другим прокси (смена липкой сессии); токен сохраняется."""
        old = self._client
        self.proxy = (proxy or "").strip() or None
        self._client = self._build_http_client()
        with suppress(Exception):
            await old.aclose()

    async def _raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Запрос через httpx; мусорный ответ SOCKS-прокси приводим к httpx.ProxyError.

        httpcore не оборачивает socksio.ProtocolError («Malformed reply», когда модем прокси
        отвечает на SOCKS-рукопожатие мусором) — без этого исключение пролетало мимо ретраев
        и ротации сессии и роняло охоту целиком. Это отказ до отправки запроса, как ProxyError.
        """
        try:
            return await self._client.request(method, url, **kwargs)
        except _SocksProtocolError as exc:
            raise httpx.ProxyError(f"SOCKS: {exc}") from exc

    def _token_valid(self) -> bool:
        if not self._token or not self._token_expires:
            return False
        return datetime.now(timezone.utc) < (self._token_expires - timedelta(minutes=5))

    async def ensure_authenticated(self) -> None:
        if self._token_valid():
            return

        async with self._auth_lock:
            if self._token_valid():
                return
            await self.authenticate()

    # Авторизация делается редко, но критична: один флап сети/прокси не должен ронять
    # весь процесс (в hunt это = выход с кодом 2 и ожидание 5 минут). Поэтому POST на
    # identity переживает transient-ошибки транспорта (вкл. ProxyError флапающего прокси).
    _AUTH_ATTEMPTS = 6

    async def _post_identity(self, payload: dict[str, Any]) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self._AUTH_ATTEMPTS):
            try:
                return await self._raw("POST", IDENTITY_URL, json=payload)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < self._AUTH_ATTEMPTS - 1:
                    await asyncio.sleep(min(2 ** attempt, 8))
        assert last_exc is not None
        raise last_exc

    async def authenticate(self) -> None:
        last_error: Exception | None = None
        payloads = self._project_scope_payloads()
        if not payloads:
            raise ValueError("Selectel Scanner requires project_name or project_id for project-scoped auth")

        for payload in payloads:
            try:
                response = await self._post_identity(payload)
            except Exception as exc:  # pragma: no cover - network passthrough
                last_error = exc
                continue
            if response.status_code == 401:
                last_error = httpx.HTTPStatusError(
                    "Selectel authentication failed",
                    request=response.request,
                    response=response,
                )
                continue
            response.raise_for_status()
            self._token = response.headers.get("X-Subject-Token", "").strip()
            if not self._token:
                raise ValueError("Selectel authentication returned no X-Subject-Token header")

            body = response.json()
            self._token_expires = self._parse_token_expiration(body)
            token_project = body.get("token", {}).get("project", {})
            resolved_project_id = str(token_project.get("id", "")).strip()
            if resolved_project_id:
                self.project_id = resolved_project_id
            self._apply_service_catalog(body)
            return

        raise ValueError(
            "Selectel authentication failed. Check username, password, account ID, "
            "and project scope."
        ) from last_error

    def _project_scope_payloads(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for domain_key in ("name", "id"):
            project_scope: dict[str, Any]
            if self.project_id:
                project_scope = {
                    "project": {
                        "id": self.project_id,
                        "domain": {domain_key: self.account_id},
                    }
                }
            elif self.project_name:
                project_scope = {
                    "project": {
                        "name": self.project_name,
                        "domain": {domain_key: self.account_id},
                    }
                }
            else:
                continue

            payloads.append(
                {
                    "auth": {
                        "identity": {
                            "methods": ["password"],
                            "password": {
                                "user": {
                                    "name": self.username,
                                    "password": self.password,
                                    "domain": {domain_key: self.account_id},
                                }
                            },
                        },
                        "scope": project_scope,
                    }
                }
            )
        return payloads

    def _parse_token_expiration(self, payload: dict[str, Any]) -> datetime | None:
        raw_value = str(payload.get("token", {}).get("expires_at", "")).strip()
        if not raw_value:
            return None
        normalized = raw_value[:-1] + "+00:00" if raw_value.endswith("Z") else raw_value
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _apply_service_catalog(self, payload: dict[str, Any]) -> None:
        self._neutron_urls = {}
        self._nova_urls = {}
        self._cinder_urls = {}
        self._glance_urls = {}
        services = payload.get("token", {}).get("catalog", [])
        for service in services:
            stype = str(service.get("type", "")).strip().lower()
            if stype not in ("network", "compute", "volumev3", "block-storage", "image"):
                continue
            for endpoint in service.get("endpoints", []):
                region = str(endpoint.get("region_id") or endpoint.get("region") or "").strip()
                if not region:
                    continue
                if stype == "network" and self.regions and region not in self.regions:
                    continue
                if endpoint.get("interface") != "public":
                    continue
                url = str(endpoint.get("url", "")).rstrip("/")
                if not url:
                    continue
                if stype == "network":
                    self._neutron_urls[region] = url
                elif stype == "compute":
                    self._nova_urls[region] = url
                elif stype == "image":
                    self._glance_urls[region] = url
                else:
                    self._cinder_urls[region] = url

        missing = [region for region in self.regions if region not in self._neutron_urls]
        if missing:
            raise RuntimeError(
                "Selectel service catalog is missing public network endpoints for: "
                + ", ".join(missing)
            )

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._token:
            headers["X-Auth-Token"] = self._token
        return headers

    def available_regions(self) -> tuple[str, ...]:
        return tuple(self._neutron_urls.keys())

    def compute_regions(self) -> tuple[str, ...]:
        return tuple(self._nova_urls.keys())

    async def list_servers(self, *, regions: set[str] | None = None) -> list[dict[str, Any]]:
        """Возвращает краткий список VM по регионам (через Nova `/servers/detail`).

        Результат: каждый элемент — словарь с ключами id, name, status, region.
        Если у проекта нет compute-эндпойнтов в service catalog — возвращает пустой список.
        """
        await self.ensure_authenticated()
        if not self._nova_urls:
            return []
        target_regions = tuple(regions or set(self._nova_urls.keys()))
        servers: list[dict[str, Any]] = []
        for region in target_regions:
            base = self._nova_urls.get(region, "").rstrip("/")
            if not base:
                continue
            try:
                response = await self._request("GET", f"{base}/servers/detail")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 404):
                    continue
                raise
            items = response.json().get("servers", [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                servers.append(
                    {
                        "id": str(item.get("id") or "").strip(),
                        "name": str(item.get("name") or "").strip(),
                        "status": str(item.get("status") or "").strip(),
                        "region": region,
                    }
                )
        return servers

    def endpoint(self, service: str, region: str) -> str:
        """Публичный URL сервиса (network / compute / image / volume) в регионе."""
        table = {"network": self._neutron_urls, "compute": self._nova_urls, "image": self._glance_urls,
                 "volume": self._cinder_urls}[service]
        url = table.get(region, "").rstrip("/")
        if not url:
            raise RuntimeError(f"В регионе {region} нет сервиса {service} — проект не видит его в каталоге Selectel")
        return url

    async def call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Запрос к любому сервису облака; JSON-ответ или {}."""
        response = await self._request(method, url, **kwargs)
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def external_network_id(self, region: str) -> str:
        return await self._get_external_network_id(region)

    def volume_regions(self) -> tuple[str, ...]:
        return tuple(self._cinder_urls.keys())

    async def delete_server(self, region: str, server_id: str) -> bool:
        """Удаляет VM в регионе. True если удалена (или уже не существует)."""
        await self.ensure_authenticated()
        base = self._nova_urls.get(region, "").rstrip("/")
        if not base or not server_id.strip():
            return False
        try:
            response = await self._request(
                "DELETE", f"{base}/servers/{server_id.strip()}"
            )
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code in (404, 409):
                return exc.response.status_code == 404
            raise
        return response.status_code in (200, 202, 204)

    async def list_volumes(
        self, *, regions: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Возвращает краткий список volume'ов по регионам (через Cinder `/volumes/detail`)."""
        await self.ensure_authenticated()
        if not self._cinder_urls:
            return []
        target_regions = tuple(regions or set(self._cinder_urls.keys()))
        volumes: list[dict[str, Any]] = []
        for region in target_regions:
            base = self._cinder_urls.get(region, "").rstrip("/")
            if not base:
                continue
            try:
                response = await self._request("GET", f"{base}/volumes/detail")
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code in (403, 404):
                    continue
                raise
            for item in response.json().get("volumes", []):
                if not isinstance(item, dict):
                    continue
                volumes.append(
                    {
                        "id": str(item.get("id") or "").strip(),
                        "name": str(item.get("name") or "").strip(),
                        "status": str(item.get("status") or "").strip(),
                        "region": region,
                        "size": int(item.get("size") or 0),
                    }
                )
        return volumes

    async def delete_volume(self, region: str, volume_id: str) -> bool:
        """Удаляет volume (cascade=true — вместе со снапшотами)."""
        await self.ensure_authenticated()
        base = self._cinder_urls.get(region, "").rstrip("/")
        if not base or not volume_id.strip():
            return False
        url = f"{base}/volumes/{volume_id.strip()}?cascade=true"
        try:
            response = await self._request("DELETE", url)
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return True
            raise
        return response.status_code in (200, 202, 204)

    async def _request(
        self,
        method: str,
        url: str,
        retry_on_unauthorized: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        extra_headers = kwargs.pop("headers", {})
        await self.ensure_authenticated()
        response = await self._send_with_retries(method, url, extra_headers, **kwargs)

        if response.status_code == 401 and retry_on_unauthorized:
            self._token = ""
            self._token_expires = None
            await self.ensure_authenticated()
            response = await self._send_with_retries(method, url, extra_headers, **kwargs)

        response.raise_for_status()
        return response

    # Обрыв до отправки запроса (connect/TLS) безопасно повторить для любого метода;
    # обрыв после отправки — только для идемпотентных, иначе POST мог уже создать IP.
    # ProxyError — отказ прокси на установке туннеля (SOCKS-хендшейк/HTTP CONNECT), т.е.
    # запрос до Selectel не дошёл: повтор безопасен и для POST.
    _PRE_SEND_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError)
    _POST_SEND_ERRORS = (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.WriteError)
    _IDEMPOTENT = {"GET", "HEAD", "DELETE", "PUT"}

    async def _send_with_retries(
        self, method: str, url: str, extra_headers: dict[str, str], **kwargs: Any
    ) -> httpx.Response:
        attempts = max(1, self.transport_attempts)
        for attempt in range(attempts):
            try:
                return await self._raw(
                    method,
                    url,
                    headers={**self._auth_headers(), **extra_headers},
                    **kwargs,
                )
            except self._PRE_SEND_ERRORS + self._POST_SEND_ERRORS as exc:
                retryable = isinstance(exc, self._PRE_SEND_ERRORS) or method.upper() in self._IDEMPOTENT
                if not retryable or attempt == attempts - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    async def list_floating_ips(
        self,
        *,
        regions: set[str] | None = None,
    ) -> list[FloatingIPRecord]:
        await self.ensure_authenticated()
        target_regions = tuple(regions or set(self.regions or self._neutron_urls.keys()))
        records: list[FloatingIPRecord] = []
        for region in target_regions:
            records.extend(await self._list_region_floating_ips(region))
        return records

    async def _list_region_floating_ips(self, region: str) -> list[FloatingIPRecord]:
        response = await self._request("GET", f"{self._neutron_url(region)}/v2.0/floatingips")
        items = response.json().get("floatingips", [])
        records = [
            FloatingIPRecord.from_payload({**item, "region": item.get("region") or region})
            for item in items
            if isinstance(item, dict)
        ]
        for record in records:
            if record.id:
                self._resource_regions[record.id] = region
        return records

    async def get_floating_ip(self, floating_ip_id: str) -> FloatingIPRecord:
        region = await self._resolve_region(floating_ip_id)
        response = await self._request("GET", f"{self._neutron_url(region)}/v2.0/floatingips/{floating_ip_id}")
        payload = response.json().get("floatingip", {})
        if not payload:
            raise RuntimeError(f"Selectel floating IP {floating_ip_id} returned an empty payload")
        record = FloatingIPRecord.from_payload({**payload, "region": payload.get("region") or region})
        if record.id:
            self._resource_regions[record.id] = region
        return record

    async def allocate_floating_ips(
        self,
        region: str,
        quantity: int,
        *,
        poll_attempts: int = 5,
        poll_delay: float = 0.45,
    ) -> list[FloatingIPRecord]:
        await self.ensure_authenticated()
        requested_count = max(1, int(quantity))
        before_task = asyncio.create_task(self._list_region_floating_ips(region))
        tasks = [asyncio.create_task(self._create_floating_ip(region)) for _ in range(requested_count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        discovered: dict[str, FloatingIPRecord] = {}
        first_error: Exception | None = None
        for result in results:
            if isinstance(result, Exception):
                if first_error is None:
                    first_error = result
                continue
            discovered[result.id] = result

        should_reconcile = first_error is not None or len(discovered) < requested_count
        if discovered and not should_reconcile:
            if before_task.done():
                with suppress(asyncio.CancelledError, Exception):
                    before_task.result()
            else:
                before_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await before_task
            return list(discovered.values())

        before_ids = {record.id for record in await before_task}
        attempts = max(1, int(poll_attempts))
        if should_reconcile and first_error is not None:
            attempts = max(attempts, 12)

        for _ in range(attempts):
            await asyncio.sleep(max(0.1, float(poll_delay)))
            current_records = await self._list_region_floating_ips(region)
            for record in current_records:
                if record.id not in before_ids:
                    discovered.setdefault(record.id, record)
            if len(discovered) >= requested_count:
                break
        if discovered:
            return list(discovered.values())
        if first_error is not None:
            raise first_error
        return list(discovered.values())

    async def _create_floating_ip(self, region: str) -> FloatingIPRecord:
        network_id = await self._get_external_network_id(region)
        response = await self._request(
            "POST",
            f"{self._neutron_url(region)}/v2.0/floatingips",
            json={"floatingip": {"floating_network_id": network_id}},
        )
        payload = response.json().get("floatingip", {})
        if not payload:
            raise RuntimeError(f"Selectel create floating IP returned an empty payload for {region}")
        record = FloatingIPRecord.from_payload({**payload, "region": payload.get("region") or region})
        if record.id:
            self._resource_regions[record.id] = region
        return record

    async def _get_external_network_id(self, region: str) -> str:
        cached = self._external_network_ids.get(region, "").strip()
        if cached:
            return cached
        response = await self._request(
            "GET",
            f"{self._neutron_url(region)}/v2.0/networks",
            params={"router:external": True, "status": "ACTIVE"},
        )
        networks = response.json().get("networks", [])
        if not networks:
            raise RuntimeError(f"Selectel region {region} has no active external networks")
        network_id = str(networks[0].get("id", "")).strip()
        if not network_id:
            raise RuntimeError(f"Selectel region {region} returned an external network without ID")
        self._external_network_ids[region] = network_id
        return network_id

    async def list_floatingip_pools(self, region: str) -> list[dict[str, Any]]:
        """Neutron extension floatingip-pools: подсети внешних сетей, из которых выдаются floating IP."""
        response = await self._request("GET", f"{self._neutron_url(region)}/v2.0/floatingip_pools")
        return [item for item in response.json().get("floatingip_pools", []) if isinstance(item, dict)]

    async def list_external_subnets(self, region: str) -> list[dict[str, Any]]:
        """Фоллбэк для floatingip_pools: подсети всех внешних сетей региона."""
        response = await self._request(
            "GET",
            f"{self._neutron_url(region)}/v2.0/networks",
            params={"router:external": True},
        )
        subnets: list[dict[str, Any]] = []
        for network in response.json().get("networks", []):
            network_id = str(network.get("id", "")).strip()
            if not network_id:
                continue
            sub_response = await self._request(
                "GET",
                f"{self._neutron_url(region)}/v2.0/subnets",
                params={"network_id": network_id},
            )
            for subnet in sub_response.json().get("subnets", []):
                if isinstance(subnet, dict):
                    subnets.append(
                        {
                            "subnet_id": subnet.get("id"),
                            "subnet_name": subnet.get("name"),
                            "cidr": subnet.get("cidr"),
                            "network_id": network_id,
                        }
                    )
        return subnets

    async def create_floating_ip_in_subnet(
        self, region: str, network_id: str, subnet_id: str, floating_ip_address: str | None = None
    ) -> FloatingIPRecord:
        """Запрашивает floating IP из конкретной подсети внешней сети (Neutron `subnet_id`),
        при `floating_ip_address` — конкретный адрес из неё."""
        body = {"floating_network_id": network_id, "subnet_id": subnet_id}
        if floating_ip_address:
            body["floating_ip_address"] = floating_ip_address
        response = await self._request(
            "POST",
            f"{self._neutron_url(region)}/v2.0/floatingips",
            json={"floatingip": body},
        )
        payload = response.json().get("floatingip", {})
        if not payload:
            raise RuntimeError(f"Selectel create floating IP returned an empty payload for {region}")
        record = FloatingIPRecord.from_payload({**payload, "region": payload.get("region") or region})
        if record.id:
            self._resource_regions[record.id] = region
        return record

    async def delete_floating_ip(self, floating_ip_id: str) -> bool:
        """Neutron DELETE floatingips/{id}. Политику «не трогать whitelist» обеспечивает SelectelScannerApp._delete_records."""
        try:
            region = await self._resolve_region(floating_ip_id)
        except KeyError:
            return False
        try:
            await self._request("DELETE", f"{self._neutron_url(region)}/v2.0/floatingips/{floating_ip_id}")
            self._resource_regions.pop(floating_ip_id, None)
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self._resource_regions.pop(floating_ip_id, None)
                return False
            raise

    async def _resolve_region(self, floating_ip_id: str) -> str:
        known = self._resource_regions.get(floating_ip_id)
        candidates = list(self.regions or self._neutron_urls.keys())
        if known and known in candidates:
            candidates = [known] + [region for region in candidates if region != known]
        for region in candidates:
            try:
                await self._request(
                    "GET",
                    f"{self._neutron_url(region)}/v2.0/floatingips/{floating_ip_id}",
                )
                self._resource_regions[floating_ip_id] = region
                return region
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise
        raise KeyError(floating_ip_id)

    def _neutron_url(self, region: str) -> str:
        url = self._neutron_urls.get(region, "").strip()
        if not url:
            raise RuntimeError(f"Selectel region {region} has no Neutron endpoint in the service catalog")
        return url

    @staticmethod
    def _extract_floating_ips(payload: dict[str, Any]) -> list[FloatingIPRecord]:
        records: dict[str, FloatingIPRecord] = {}
        for key in ("floatingips", "floatingip"):
            raw_value = payload.get(key)
            if isinstance(raw_value, list):
                for item in raw_value:
                    if isinstance(item, dict):
                        record = FloatingIPRecord.from_payload(item)
                        SelectelClient._remember_record(records, record)
            elif isinstance(raw_value, dict):
                record = FloatingIPRecord.from_payload(raw_value)
                SelectelClient._remember_record(records, record)
        return list(records.values())

    @staticmethod
    def _remember_record(records: dict[str, FloatingIPRecord], record: FloatingIPRecord) -> None:
        if not record.id and not record.address:
            return
        key = record.id or record.address
        records[key] = record

    async def close(self) -> None:
        await self._client.aclose()
