"""«Поднять сервер под IP»: самый дешёвый VDS в том же аккаунте и регионе + привязка пойманного IP.

Опыт с rebekah-vds: floating IP требует приватную сеть и роутер с выходом во внешнюю сеть;
загрузочный диск — сетевой том с volume_type basic.<зона> (нужен microversion compute 2.67);
дешёвые flavor'ы бывают «распроданы» (Nova: No valid host) — пробуем следующий по цене.

Сам пойманный IP панель не удаляет никогда: при ошибке и при удалении сервера он только
отвязывается и остаётся на аккаунте.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import secrets
from typing import Any

import httpx

from app import bus, settings_store
from app.core.crypto import encrypt
from app.db.models import CaughtIP, Vds
from app.db.session import session_scope
from app.engine import billing
from app.engine.selectel import SelectelClient
from app.events import emit
from app.notify.telegram import esc, notify_for

log = logging.getLogger(__name__)

NET_NAME, SUBNET_NAME, ROUTER_NAME = "toffee-net", "toffee-subnet", "toffee-router"
SUBNET_CIDR = "192.168.77.0/24"
DNS = ["188.93.16.19", "188.93.17.19"]
NOVA_HEADERS = {"OpenStack-API-Version": "compute 2.67", "X-OpenStack-Nova-API-Version": "2.67"}
MAX_CANDIDATES = 4
_tasks: set[asyncio.Task[Any]] = set()


class VdsError(Exception):
    pass


def short(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        text = exc.response.text[:300]
        return f"Selectel {exc.response.status_code}: {text}"
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def cloud_config(password: str) -> str:
    cfg = f"""#cloud-config
ssh_pwauth: true
disable_root: false
chpasswd:
  expire: false
  users:
    - name: root
      password: {password}
      type: text
runcmd:
  - [sh, -c, "printf 'PermitRootLogin yes\\nPasswordAuthentication yes\\n' > /etc/ssh/sshd_config.d/01-toffee.conf"]
  - [sh, -c, "systemctl restart ssh || systemctl restart sshd"]
"""
    return base64.b64encode(cfg.encode()).decode()


def candidates(flavors: list[dict[str, Any]], zones: list[str], table: dict[tuple[str, str], float]
               ) -> list[tuple[float, str, str, str]]:
    """(₽/мес, flavor_id, имя, зона) — дешёвые flavor'ы с долей ядра, у которых есть цена в зоне."""
    out = []
    for f in flavors:
        name = str(f.get("name") or "")
        if not billing.FLAVOR_RE.match(name) or int(f.get("ram") or 0) < 512:
            continue
        for zone in zones:
            cost = billing.flavor_month(table, name, zone)
            if cost is not None:
                out.append((cost, str(f.get("id")), name, zone))
    out.sort()
    return out


def pick_image(images: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    active = [i for i in images if i.get("status") == "active"]
    exact = next((i for i in active if i.get("name") == wanted), None)
    if exact:
        return exact
    ubuntu = [i for i in active if re.match(r"^Ubuntu \d+\.\d+ LTS 64-bit$", str(i.get("name") or ""))]
    ubuntu.sort(key=lambda i: [int(x) for x in re.findall(r"\d+", i["name"])[:2]], reverse=True)
    return ubuntu[0] if ubuntu else None


async def quote(region: str) -> dict[str, Any]:
    """Оценка без запросов к аккаунту: самый дешёвый PRC-flavor региона по прайсу + диск + роутер."""
    table = await billing.prices()
    zones = sorted({g for (r, g) in table if r == "compute_cores_percent_10" and g.startswith(region)
                    and re.fullmatch(rf"{re.escape(region)}[a-z]", g)})
    best = None
    for zone in zones:
        server = billing.flavor_month(table, "PRC10.1-512", zone)
        if server is not None and (best is None or server < best[0]):
            best = (server, zone)
    gb = int(settings_store.get("vds_disk_gb") or 5)
    disk = billing.disk_month(table, gb, best[1]) if best else None
    router = billing.router_month(table, region)
    server = best[0] if best else None
    total = round((server or 0) + (disk or 0), 2)
    return {"region": region, "zone": best[1] if best else None, "flavor": "PRC10.1-512", "server_month": server,
            "disk_gb": gb, "disk_month": disk, "router_month": router, "total_month": total,
            "total_with_router": round(total + router, 2), "available": best is not None}


# ── создание ────────────────────────────────────────────────────────────────

async def _set(vds_id: int, **fields: Any) -> None:
    async with session_scope() as db:
        v = await db.get(Vds, vds_id)
        if v is None:
            return
        for k, val in fields.items():
            setattr(v, k, val)
    bus.publish("vds", {"id": vds_id, **{k: v for k, v in fields.items() if k != "password_enc"}})


async def _find_or_create_network(client: SelectelClient, region: str, ext_net: str) -> str:
    net_url = client.endpoint("network", region)
    nets = (await client.call("GET", f"{net_url}/v2.0/networks", params={"name": NET_NAME})).get("networks") or []
    if nets:
        net_id = nets[0]["id"]
    else:
        net_id = (await client.call("POST", f"{net_url}/v2.0/networks",
                                    json={"network": {"name": NET_NAME}}))["network"]["id"]
    subs = (await client.call("GET", f"{net_url}/v2.0/subnets", params={"network_id": net_id})).get("subnets") or []
    if subs:
        subnet_id = subs[0]["id"]
    else:
        subnet_id = (await client.call("POST", f"{net_url}/v2.0/subnets", json={"subnet": {
            "network_id": net_id, "cidr": SUBNET_CIDR, "ip_version": 4, "name": SUBNET_NAME,
            "dns_nameservers": DNS}}))["subnet"]["id"]
    routers = (await client.call("GET", f"{net_url}/v2.0/routers", params={"name": ROUTER_NAME})).get("routers") or []
    if routers:
        router = routers[0]
    else:
        router = (await client.call("POST", f"{net_url}/v2.0/routers", json={"router": {
            "name": ROUTER_NAME, "external_gateway_info": {"network_id": ext_net}}}))["router"]
    ports = (await client.call("GET", f"{net_url}/v2.0/ports", params={
        "device_id": router["id"], "network_id": net_id})).get("ports") or []
    if not ports:
        await client.call("PUT", f"{net_url}/v2.0/routers/{router['id']}/add_router_interface",
                          json={"subnet_id": subnet_id})
    return net_id


async def _wait_server(client: SelectelClient, nova: str, server_id: str, timeout: float = 420) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        server = (await client.call("GET", f"{nova}/servers/{server_id}", headers=NOVA_HEADERS)).get("server") or {}
        status = server.get("status")
        if status == "ACTIVE":
            return server
        if status == "ERROR":
            fault = (server.get("fault") or {}).get("message") or "сервер в состоянии ERROR"
            raise VdsError(fault)
        await asyncio.sleep(6)
    raise VdsError("сервер не запустился за 7 минут")


async def _delete_server(client: SelectelClient, nova: str, server_id: str) -> None:
    try:
        await client.call("DELETE", f"{nova}/servers/{server_id}", headers=NOVA_HEADERS)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        return
    for _ in range(40):
        await asyncio.sleep(3)
        try:
            await client.call("GET", f"{nova}/servers/{server_id}", headers=NOVA_HEADERS)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return
            raise


async def create(vds_id: int) -> None:
    from app.engine.manager import manager  # noqa: PLC0415

    async with session_scope() as db:
        v = await db.get(Vds, vds_id)
        caught = await db.get(CaughtIP, v.caught_id) if v and v.caught_id else None
        if v is None or caught is None or v.key_id is None:
            return
        key_id, region, fip_id, ip, name = v.key_id, caught.region, caught.floating_ip_id, caught.ip, v.name
        owner_id = v.owner_id
    client: SelectelClient | None = None
    owned = False
    port_id = server_id = ""
    try:
        client, owned = await manager.client_for_key(key_id)
        await client.ensure_authenticated()
        net_url = client.endpoint("network", region)
        nova = client.endpoint("compute", region)
        glance = client.endpoint("image", region)

        await _set(vds_id, step="Проверяю IP на аккаунте")
        fip = (await client.call("GET", f"{net_url}/v2.0/floatingips/{fip_id}")).get("floatingip") or {}
        if not fip:
            raise VdsError("этого IP больше нет на аккаунте")
        if fip.get("port_id"):
            raise VdsError("IP уже привязан к другому серверу — сначала отвяжите его в панели Selectel")
        ext_net = fip.get("floating_network_id") or await client.external_network_id(region)

        await _set(vds_id, step="Подбираю самый дешёвый сервер")
        table = await billing.prices()
        zones_raw = (await client.call("GET", f"{nova}/os-availability-zone", headers=NOVA_HEADERS)
                     ).get("availabilityZoneInfo") or []
        zones = [z["zoneName"] for z in zones_raw if (z.get("zoneState") or {}).get("available")]
        flavors = (await client.call("GET", f"{nova}/flavors/detail", headers=NOVA_HEADERS)).get("flavors") or []
        options = candidates(flavors, zones, table)[:MAX_CANDIDATES]
        if not options:
            raise VdsError(f"в {region} нет дешёвых flavor'ов линейки с долей ядра (PRC)")
        images = (await client.call("GET", f"{glance}/v2/images", params={"visibility": "public", "limit": 500})
                  ).get("images") or []
        image = pick_image(images, str(settings_store.get("vds_image") or ""))
        if image is None:
            raise VdsError("не нашёл публичный образ Ubuntu")

        await _set(vds_id, step="Готовлю сеть и роутер")
        net_id = await _find_or_create_network(client, region, ext_net)
        port = (await client.call("POST", f"{net_url}/v2.0/ports", json={"port": {
            "network_id": net_id, "name": f"toffee-{ip}"}}))["port"]
        port_id = port["id"]
        await _set(vds_id, port_id=port_id)

        password = secrets.token_urlsafe(15)
        gb = int(settings_store.get("vds_disk_gb") or 5)
        last_error = ""
        server = None
        for cost, flavor_id, flavor_name, zone in options:
            await _set(vds_id, step=f"Создаю {flavor_name} в {zone}", zone=zone, flavor=flavor_name)
            body = {"server": {
                "name": name, "flavorRef": flavor_id, "availability_zone": zone,
                "networks": [{"port": port_id}], "user_data": cloud_config(password),
                "block_device_mapping_v2": [{
                    "boot_index": 0, "uuid": image["id"], "source_type": "image", "destination_type": "volume",
                    "volume_size": gb, "volume_type": f"basic.{zone}", "delete_on_termination": True}],
            }}
            try:
                server_id = (await client.call("POST", f"{nova}/servers", json=body, headers=NOVA_HEADERS))["server"]["id"]
                await _set(vds_id, server_id=server_id, step=f"Жду запуска {flavor_name} в {zone}")
                server = await _wait_server(client, nova, server_id)
                disk = billing.disk_month(table, gb, zone) or 0
                await _set(vds_id, month_cost=round(cost + disk, 2))
                break
            except (VdsError, httpx.HTTPStatusError) as exc:
                last_error = short(exc)
                if server_id:
                    await _set(vds_id, step=f"{flavor_name} в {zone} не вышел, убираю")
                    await _delete_server(client, nova, server_id)
                    server_id = ""
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
                    break
        if server is None:
            raise VdsError(f"ни один вариант не запустился: {last_error}")

        await _set(vds_id, step="Привязываю IP к серверу")
        await client.call("PUT", f"{net_url}/v2.0/floatingips/{fip_id}", json={"floatingip": {"port_id": port_id}})
        await _set(vds_id, status="active", step="Готово", error="", password_enc=encrypt(password))
        await emit(f"сервер {name} поднят под IP {ip} ({region})", level="success", kind="vds",
                   data={"vds_id": vds_id, "ip": ip})
        await notify_for(owner_id, "money", f"🖥 Сервер под <code>{esc(ip)}</code> готов: root, пароль — в панели (раздел «IP на аккаунтах»).")
    except Exception as exc:  # noqa: BLE001
        log.exception("vds create %s", vds_id)
        msg = str(exc) if isinstance(exc, VdsError) else short(exc)
        if client is not None:
            try:
                nova = client.endpoint("compute", region)
                net_url = client.endpoint("network", region)
                if server_id:
                    await _delete_server(client, nova, server_id)
                if port_id:
                    await client.call("DELETE", f"{net_url}/v2.0/ports/{port_id}")
            except Exception:  # noqa: BLE001
                log.exception("vds cleanup %s", vds_id)
        await _set(vds_id, status="error", step="Не получилось", error=msg[:2000], server_id="", port_id="")
        await emit(f"сервер под IP {ip} не поднялся: {msg[:300]}", level="error", kind="vds", data={"vds_id": vds_id})
    finally:
        if client is not None and owned:
            await client.close()


async def destroy(vds_id: int) -> None:
    """Удаляет сервер и его порт. IP отвязывается и остаётся на аккаунте."""
    from app.engine.manager import manager  # noqa: PLC0415

    async with session_scope() as db:
        v = await db.get(Vds, vds_id)
        caught = await db.get(CaughtIP, v.caught_id) if v and v.caught_id else None
        if v is None:
            return
        key_id, region, server_id, port_id = v.key_id, v.region, v.server_id, v.port_id
        fip_id = caught.floating_ip_id if caught else ""
    client: SelectelClient | None = None
    owned = False
    try:
        if key_id is None:
            raise VdsError("ключ аккаунта удалён из панели — удалите сервер в панели Selectel")
        client, owned = await manager.client_for_key(key_id)
        await client.ensure_authenticated()
        net_url = client.endpoint("network", region)
        nova = client.endpoint("compute", region)
        if fip_id:
            await _set(vds_id, step="Отвязываю IP (он остаётся на аккаунте)")
            try:
                await client.call("PUT", f"{net_url}/v2.0/floatingips/{fip_id}", json={"floatingip": {"port_id": None}})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
        if server_id:
            await _set(vds_id, step="Удаляю сервер")
            await _delete_server(client, nova, server_id)
        if port_id:
            try:
                await client.call("DELETE", f"{net_url}/v2.0/ports/{port_id}")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
        await _set(vds_id, status="deleted", step="Удалён", password_enc=None)
        await emit(f"сервер {vds_id} удалён, IP остался на аккаунте", level="info", kind="vds")
    except Exception as exc:  # noqa: BLE001
        log.exception("vds destroy %s", vds_id)
        msg = str(exc) if isinstance(exc, VdsError) else short(exc)
        await _set(vds_id, status="error", step="Удалить не получилось", error=msg[:2000])
    finally:
        if client is not None and owned:
            await client.close()


def spawn(coro: Any) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
