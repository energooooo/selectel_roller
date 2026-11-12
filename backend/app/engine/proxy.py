"""Прокси: разбор строк, липкие сессии, проверка.

Форматы строки (после адреса через пробел — метка):
    host:port
    login:password@host:port
    host:port:login:password
    socks5://login:password@host:port   (http://, https://, socks5://, socks5h://)
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlparse

import httpx

SCHEMES = {"http", "https", "socks5", "socks5h"}
# Эндпоинты регионов Selectel: доступность через прокси = прокси годится для охоты в регионе.
SELECTEL_REGION_HOSTS = {
    "ru-1": "ru-1.cloud.api.selcloud.ru",
    "ru-2": "ru-2.cloud.api.selcloud.ru",
    "ru-3": "ru-3.cloud.api.selcloud.ru",
    "ru-9": "ru-9.cloud.api.selcloud.ru",
}


class ProxyParseError(ValueError):
    pass


@dataclass
class ParsedProxy:
    url: str
    kind: str
    display: str
    label: str = ""

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()


def _hostport_ok(host: str, port: str) -> bool:
    return bool(host) and port.isdigit() and 0 < int(port) < 65536


def parse_proxy_line(line: str, default_kind: str = "http") -> ParsedProxy:
    raw = line.strip()
    if not raw:
        raise ProxyParseError("пустая строка")
    parts = raw.split(maxsplit=1)
    address, label = parts[0], (parts[1].strip() if len(parts) > 1 else "")
    default_kind = default_kind if default_kind in SCHEMES else "http"

    if "://" in address:
        parsed = urlparse(address)
        scheme = parsed.scheme.lower()
        if scheme not in SCHEMES:
            raise ProxyParseError(f"неизвестная схема {scheme}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ProxyParseError("кривой порт") from exc
        if not parsed.hostname or not port:
            raise ProxyParseError("нет хоста или порта")
        user = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        host = parsed.hostname
    elif "@" in address:
        creds, hostport = address.rsplit("@", 1)
        user, _, password = creds.partition(":")
        host, _, port_s = hostport.rpartition(":")
        if not _hostport_ok(host, port_s):
            raise ProxyParseError("ожидается логин:пароль@хост:порт")
        scheme, port = default_kind, int(port_s)
    else:
        chunks = address.split(":")
        if len(chunks) == 2 and _hostport_ok(*chunks):
            host, port, user, password = chunks[0], int(chunks[1]), "", ""
        elif len(chunks) >= 4 and _hostport_ok(chunks[0], chunks[1]):
            host, port, user, password = chunks[0], int(chunks[1]), chunks[2], ":".join(chunks[3:])
        else:
            raise ProxyParseError("не похоже на хост:порт или хост:порт:логин:пароль")
        scheme = default_kind

    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user else ""
    url = f"{scheme}://{auth}{host}:{port}"
    kind = "socks5" if scheme.startswith("socks") else "http"
    return ParsedProxy(url=url, kind=kind, display=f"{scheme}://{host}:{port}", label=label)


def choose_random_proxy(proxies, previous_id: int | None = None):
    """Равновероятный выбор; предыдущий исключается, если есть другой доступный."""
    alive = [proxy for proxy in proxies if proxy.status in {"ok", "partial", "unchecked"}]
    alternatives = [proxy for proxy in alive if proxy.id != previous_id]
    candidates = alternatives or alive
    return secrets.choice(candidates) if candidates else None


def with_session(url: str, mode: str, session_id: str, generation: int = 0) -> str:
    """Липкая сессия прокси. mode=password_suffix: «_session-<id>» в конец пароля
    (proxxxymiron: id ≥ 8 символов, иначе ProxyError ruleset). generation > 0 —
    запасная сессия (другой выходной IP), если модем текущей сломан."""
    if mode != "password_suffix":
        return url
    parsed = urlparse(url)
    if parsed.password is None:
        return url
    session = session_id.ljust(8, "0") + (f"g{generation}" if generation else "")
    base_password = re.sub(r"_session-[^_]*", "", unquote(parsed.password))
    password = quote(base_password + f"_session-{session}", safe="")
    netloc = f"{parsed.username}:{password}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


@dataclass
class ProxyCheck:
    ok: bool
    exit_ip: str = ""
    country: str = ""
    latency_ms: int | None = None
    regions_ok: list[str] = field(default_factory=list)
    regions_failed: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def status(self) -> str:
        if not self.ok:
            return "dead"
        return "partial" if self.regions_failed else "ok"


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}" + (f": {text[:160]}" if text else "")


async def check_proxy(url: str, regions: list[str] | None = None, timeout: float = 12.0) -> ProxyCheck:
    """Выходной IP и страна, задержка, и какие регионы Selectel доступны через прокси."""
    regions = regions or list(SELECTEL_REGION_HOSTS)
    result = ProxyCheck(ok=False)
    async with httpx.AsyncClient(proxy=url, timeout=timeout, verify=True) as client:
        # Несколько сервисов «какой у меня IP»: мобильные прокси режут часть хостов своими правилами.
        echo = [
            ("https://ipwho.is/?fields=ip,country_code", "ip", "country_code"),
            ("https://api.ipify.org?format=json", "ip", None),
            ("http://ip-api.com/json/?fields=query,countryCode", "query", "countryCode"),
        ]
        errors = []
        for url_echo, ip_field, country_field in echo:
            try:
                started = time.perf_counter()
                resp = await client.get(url_echo)
                result.latency_ms = int((time.perf_counter() - started) * 1000)
                data = resp.json()
                result.exit_ip = str(data.get(ip_field, ""))
                result.country = str(data.get(country_field, "")) if country_field else ""
                if result.exit_ip:
                    result.ok = True
                    break
            except Exception as exc:  # noqa: BLE001 — любой сбой прокси = попробовать следующий
                errors.append(_short_error(exc))
        if not result.ok:
            result.error = errors[-1] if errors else "прокси не ответил"
            return result

        async def probe(region: str) -> tuple[str, bool]:
            host = SELECTEL_REGION_HOSTS.get(region, f"{region}.cloud.api.selcloud.ru")
            try:
                # Любой HTTP-ответ (хоть 401/404) = эндпоинт доступен. verify=False: у Selectel свой CA.
                async with httpx.AsyncClient(proxy=url, timeout=timeout, verify=False) as c:
                    await c.get(f"https://{host}/")
                return region, True
            except Exception:  # noqa: BLE001
                return region, False

        for region, ok in await asyncio.gather(*(probe(r) for r in regions)):
            (result.regions_ok if ok else result.regions_failed).append(region)
    return result
