from __future__ import annotations

"""Прицельная охота за floating IP: адрес запрашивается сразу из нужной подсети.

Вместо «взять случайный IP → не подошёл → удалить» сканер передаёт в Neutron
`subnet_id` подсети внешней сети, пересекающейся с whitelist.txt. Если свободных
адресов в подсети нет, Neutron отвечает 409 — переходим к следующей подсети и
ходим по кругу, пока кто-нибудь не освободит адрес.

Команды:
    discover — только чтение: какие подсети из whitelist.txt Selectel выдаёт
               как floating IP и в каких регионах. Ничего не создаёт.
    hunt     — охота по найденным подсетям.

Удаляются только адреса, которые охота сама только что создала и которые не
попали в whitelist (бывает, если подсеть Selectel шире записи whitelist).
"""

import argparse
import asyncio
import html
import ipaddress
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .bootstrap import ensure_project_resolved
from .client import SelectelScannerClient
from .models import FloatingIPRecord, MatchRecord, ScannerSettings
from .paths import DEFAULT_WHITELIST_PATH, DOTENV_PATH, TEMP_DIR
from .setup_wizard import needs_setup, run_setup_wizard
from .state import MatchStore
from .telegram import TelegramNotifier, run_tg_test
from .whitelist import WhitelistMatcher, load_selectel_default_ranges


TARGETS_PATH = TEMP_DIR / "hunt-targets.json"
POOLS_CACHE_PATH = TEMP_DIR / "floating-pools.json"
STATE_PATH = TEMP_DIR / "selectel-scanner-state.json"
STATE_SECTION = "hunt"


def _account_number(value: str) -> str:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("номер аккаунта — целое число от 1")
    return str(int(value))


def _env_prefix(account: str) -> str:
    """Префикс переменных аккаунта в .env: SEL_ для первого, SEL2_, SEL3_, … для остальных."""
    return "SEL_" if account == "1" else f"SEL{account}_"


def _state_section(account: str) -> str:
    """Секция найденных IP в state-файле: у каждого аккаунта своя (две охоты пишут в один файл)."""
    return STATE_SECTION if account == "1" else f"{STATE_SECTION}-{account}"

# Selectel отвечает на «в подсети нет свободных адресов» то 409 IpAddressGenerationFailure,
# то 400 ExternalIpAddressExhausted — и то и другое значит «ждать», а не «отключить цель».
FULL_ERROR_TYPES = {
    "IpAddressGenerationFailure", "IpAddressAlreadyAllocated", "IpAddressInUse", "ExternalIpAddressExhausted",
}


class QuotaExceeded(Exception):
    pass


class RegionUnreachable(Exception):
    """API региона не принимает соединения (сбой или блокировка Selectel по IP за частые запросы)."""


class AddressNotAllowed(Exception):
    """Политика Selectel запрещает выбирать конкретный floating_ip_address."""


@dataclass
class HuntTarget:
    region: str
    network_id: str
    subnet_id: str
    pool_cidr: str
    wanted: list[str]
    # exact   — вся подсеть Selectel внутри whitelist, любой выданный адрес подходит;
    # address — в whitelist конкретный IP из подсети, он запрашивается напрямую (floating_ip_address);
    # partial — whitelist покрывает часть подсети, неподходящий адрес сразу удаляется.
    kind: str
    attempts: int = 0
    full: int = 0
    errors: int = 0
    disabled: str = ""

    def label(self) -> str:
        return f"{self.region} {self.pool_cidr}"


@dataclass
class RoundStats:
    counts: Counter = field(default_factory=Counter)
    disabled: Counter = field(default_factory=Counter)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Прицельная охота за floating IP Selectel по подсетям из whitelist.txt",
    )
    parser.add_argument("command", choices=("discover", "hunt"))
    parser.add_argument("--account", type=_account_number, default="1",
                        help="номер аккаунта из .env: 1 — SEL_*, 2 — SEL2_*, 3 — SEL3_* и т. д.")
    env_regions = [r.strip() for r in os.getenv("SEL_HUNT_REGIONS", "").split(",") if r.strip()]
    parser.add_argument("--regions", nargs="+", default=env_regions or None,
                        help="регионы (по умолчанию SEL_HUNT_REGIONS из .env, иначе все из каталога)")
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST_PATH)
    parser.add_argument("--target-count", type=int, default=int(os.getenv("SEL_TARGET_COUNT", "1") or 1),
                        help="сколько новых подходящих IP найти (по умолчанию 1)")
    parser.add_argument("--delay", type=float, default=_float_env("SEL_HUNT_DELAY", 2.0),
                        help="пауза между запросами, с")
    parser.add_argument("--round-delay", type=float, default=_float_env("SEL_HUNT_ROUND_DELAY", 5.0),
                        help="пауза между кругами, с")
    parser.add_argument("--exact-only", action="store_true",
                        help="только подсети, целиком входящие в whitelist (без удалений)")
    parser.add_argument("--no-auto-create-project", dest="auto_create_project", action="store_false")
    return parser.parse_args(argv)


def _load_whitelist_networks(path: Path) -> list[ipaddress.IPv4Network]:
    networks = []
    for entry in load_selectel_default_ranges(path):
        network = ipaddress.ip_network(entry, strict=False)
        if network.version == 4:
            networks.append(network)
    return networks


def _build_settings(args: argparse.Namespace, prefix: str) -> ScannerSettings:
    return ScannerSettings(
        username=os.getenv(f"{prefix}USERNAME", "").strip(),
        password=os.getenv(f"{prefix}PASSWORD", ""),
        account_id=os.getenv(f"{prefix}ACCOUNT_ID", "").strip(),
        project_name=os.getenv(f"{prefix}PROJECT_NAME", "").strip(),
        project_id=os.getenv(f"{prefix}PROJECT_ID", "").strip(),
        whitelist_path=args.whitelist,
        state_path=STATE_PATH,
        regions=tuple(args.regions or ()),
        state_section=_state_section(args.account),
    )


def _neutron_error(response: httpx.Response) -> tuple[int, str, str]:
    error_type, message = "", ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("NeutronError") or body.get("error") or body
        if isinstance(error, dict):
            error_type = str(error.get("type") or error.get("title") or "")
            message = str(error.get("message") or error.get("detail") or "")
    if not message:
        message = response.text[:200]
    return response.status_code, error_type, " ".join(message.split())


def _load_pools_cache() -> dict[str, list[dict]]:
    try:
        cache = json.loads(POOLS_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) else {}


def _save_pools_cache(region: str, pools: list[dict]) -> None:
    cache = _load_pools_cache()
    cache[region] = [{k: p.get(k) for k in ("subnet_id", "cidr", "network_id")} for p in pools]
    POOLS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    POOLS_CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")


def _cached_pools(region: str) -> list[dict]:
    """Пулы региона из прошлого запуска — если API региона сейчас недоступен.
    Цели из них строятся заново по текущему whitelist."""
    pools = _load_pools_cache().get(region)
    if pools:
        return pools
    # Старый формат: только цели прошлого запуска (hunt-targets.json).
    try:
        saved = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    unique = {
        item["subnet_id"]: {"subnet_id": item["subnet_id"], "cidr": item["pool_cidr"], "network_id": item["network_id"]}
        for item in saved
        if isinstance(item, dict) and item.get("region") == region and item.get("subnet_id")
    }
    return list(unique.values())


async def discover_targets(
    client: SelectelScannerClient,
    regions: tuple[str, ...],
    whitelist: list[ipaddress.IPv4Network],
    console: Console,
) -> tuple[list[HuntTarget], dict[str, int]]:
    targets: list[HuntTarget] = []
    pools_per_region: dict[str, int] = {}
    for region in regions:
        try:
            pools = await client.list_floatingip_pools(region)
        except httpx.TransportError as exc:
            pools = _cached_pools(region)
            console.print(f"[red]{region}: API не отвечает ({type(exc).__name__}). "
                          f"Беру подсети региона из прошлого запуска: {len(pools)}.[/red]")
        except httpx.HTTPStatusError as exc:
            status, error_type, message = _neutron_error(exc.response)
            console.print(f"[yellow]{region}: floatingip_pools недоступен ({status} {error_type} {message}), "
                          f"смотрю подсети внешних сетей[/yellow]")
            try:
                pools = await client.list_external_subnets(region)
            except httpx.HTTPStatusError as sub_exc:
                status, error_type, message = _neutron_error(sub_exc.response)
                console.print(f"[red]{region}: подсети тоже недоступны ({status} {error_type} {message})[/red]")
                continue
        else:
            _save_pools_cache(region, pools)
        pools_per_region[region] = len(pools)
        for pool in pools:
            try:
                pool_cidr = ipaddress.ip_network(str(pool.get("cidr", "")), strict=False)
            except ValueError:
                continue
            if pool_cidr.version != 4:
                continue
            wanted = [n for n in whitelist if n.overlaps(pool_cidr)]
            if not wanted:
                continue
            base = {
                "region": region,
                "network_id": str(pool.get("network_id", "")),
                "subnet_id": str(pool.get("subnet_id", "")),
                "pool_cidr": str(pool_cidr),
            }
            if any(pool_cidr.subnet_of(n) for n in wanted):
                targets.append(HuntTarget(**base, wanted=[str(n) for n in wanted], kind="exact"))
                continue
            singles = [n for n in wanted if n.prefixlen == 32]
            for single in singles:
                targets.append(HuntTarget(**base, wanted=[str(single)], kind="address"))
            ranges = [n for n in wanted if n.prefixlen < 32]
            if ranges:
                targets.append(HuntTarget(**base, wanted=[str(n) for n in ranges], kind="partial"))
    # Сначала точные (любой адрес подходит), потом конкретные адреса, потом лотерея с удалениями.
    order = {"exact": 0, "address": 1, "partial": 2}
    targets.sort(key=lambda t: (order[t.kind], t.region, ipaddress.ip_network(t.wanted[0])))
    return targets, pools_per_region


def render_targets(
    console: Console,
    targets: list[HuntTarget],
    pools_per_region: dict[str, int],
    whitelist: list[ipaddress.IPv4Network],
) -> None:
    table = Table(title="Подсети Selectel, пересекающиеся с whitelist", show_lines=False)
    table.add_column("Регион")
    table.add_column("Подсеть Selectel")
    table.add_column("Тип")
    table.add_column("Записи whitelist")
    for target in targets:
        kind = {"exact": "[green]точно[/green]", "address": "[cyan]адрес[/cyan]",
                "partial": "[yellow]частично[/yellow]"}[target.kind]
        wanted = ", ".join(target.wanted[:3]) + (f" … +{len(target.wanted) - 3}" if len(target.wanted) > 3 else "")
        table.add_row(target.region, target.pool_cidr, kind, wanted)
    console.print(table)

    covered = {w for t in targets for w in t.wanted}
    regions_line = ", ".join(f"{r}: {n}" for r, n in pools_per_region.items()) or "—"
    console.print(f"Пулов floating IP по регионам: {regions_line}")
    console.print(
        f"Целей: [bold]{len(targets)}[/bold] "
        f"(точных {sum(t.kind == 'exact' for t in targets)}, адресов {sum(t.kind == 'address' for t in targets)}, "
        f"частичных {sum(t.kind == 'partial' for t in targets)}). "
        f"Записей whitelist покрыто: {len(covered)} из {len(whitelist)}."
    )


def save_targets(targets: list[HuntTarget]) -> None:
    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGETS_PATH.write_text(
        json.dumps([asdict(t) for t in targets], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def _recover_created(
    client: SelectelScannerClient, target: HuntTarget, matches: dict[str, MatchRecord]
) -> FloatingIPRecord | None:
    """После обрыва уже отправленного POST ищет IP, который Selectel мог успеть выдать:
    свободный (не привязанный) адрес из подсети цели, которого ещё нет среди найденных."""
    await asyncio.sleep(1.0)
    try:
        records = await client.list_floating_ips(regions={target.region})
    except httpx.HTTPError:
        return None
    pool = ipaddress.ip_network(target.pool_cidr)
    for record in records:
        if record.id in matches or record.port_id or record.fixed_ip_address:
            continue
        try:
            if ipaddress.ip_address(record.address) in pool:
                return record
        except ValueError:
            continue
    return None


async def try_target(
    client: SelectelScannerClient,
    target: HuntTarget,
    matcher: WhitelistMatcher,
    store: MatchStore,
    matches: dict[str, MatchRecord],
    console: Console,
    stats: RoundStats,
) -> MatchRecord | None:
    """Одна попытка по цели. Возвращает найденный подходящий IP или None."""
    target.attempts += 1
    try:
        address = target.wanted[0].split("/")[0] if target.kind == "address" else None
        record = await client.create_floating_ip_in_subnet(
            target.region, target.network_id, target.subnet_id, floating_ip_address=address
        )
    except httpx.HTTPStatusError as exc:
        status, error_type, message = _neutron_error(exc.response)
        if error_type == "OverQuota" or "quota" in message.lower():
            raise QuotaExceeded(f"{target.region}: {status} {error_type} {message}") from exc
        if status == 409 or error_type in FULL_ERROR_TYPES:
            target.full += 1
            stats.counts["full"] += 1
            return None
        if status == 429:
            retry_after = exc.response.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else 60.0
            stats.counts["rate_limit"] += 1
            console.print(f"[{_now()}] [yellow]429 rate limit, жду {delay:.0f} с[/yellow]")
            await asyncio.sleep(delay)
            return None
        if target.kind == "address" and status == 403 and "floating_ip_address" in message:
            raise AddressNotAllowed(f"{status} {error_type}: {message}") from exc
        if status in (400, 403, 404):
            target.disabled = f"{status} {error_type}: {message}"
            stats.disabled[target.disabled] += 1
            return None
        target.errors += 1
        stats.counts["error"] += 1
        console.print(f"[{_now()}] [red]{target.label()}: {status} {error_type} {message}[/red]")
        return None
    except httpx.TransportError as exc:
        recovered = None
        if isinstance(exc, SelectelScannerClient._POST_SEND_ERRORS):
            recovered = await _recover_created(client, target, matches)
        if recovered is None and isinstance(exc, SelectelScannerClient._PRE_SEND_ERRORS):
            raise RegionUnreachable(type(exc).__name__) from exc
        if recovered is None:
            target.errors += 1
            stats.counts["error"] += 1
            console.print(f"[{_now()}] [red]{target.label()}: сеть: {type(exc).__name__}[/red]")
            return None
        record = recovered
        console.print(f"[{_now()}] [yellow]{target.label()}: ответ потерян ({type(exc).__name__}), "
                      f"но IP создан: {record.address}[/yellow]")

    if matcher.contains(record.address):
        match = MatchRecord.from_floating_ip(record, source="hunt")
        matches[match.id] = match
        store.save(matches)
        stats.counts["match"] += 1
        console.print(f"[{_now()}] [bold green]✓ Найден: {record.address}[/bold green] "
                      f"({target.region}, подсеть {target.pool_cidr}, id={record.id})")
        return match

    # Подсеть Selectel шире записи whitelist: адрес не тот — сразу отдаём обратно.
    stats.counts["miss"] += 1
    try:
        deleted = await client.delete_floating_ip(record.id)
    except (httpx.HTTPError, KeyError):
        deleted = False
    mark = "удалён" if deleted else "[red]НЕ удалён — удалите вручную или запустите run.bat (он чистит лишние)[/red]"
    console.print(f"[{_now()}] ✗ {record.address} из {target.pool_cidr} не в whitelist → {mark}")
    return None


async def hunt(
    client: SelectelScannerClient,
    targets: list[HuntTarget],
    matcher: WhitelistMatcher,
    args: argparse.Namespace,
    console: Console,
    notifier: TelegramNotifier | None = None,
) -> int:
    section = _state_section(getattr(args, "account", "1"))
    store = MatchStore(STATE_PATH, section=section)
    matches = store.load()
    found = 0
    round_no = 0
    console.print(
        f"Охота: цель — {args.target_count} IP, пауза {args.delay} с между запросами, "
        f"{args.round_delay} с между кругами. Остановить: Ctrl+C."
    )
    started = time.monotonic()
    attempts = 0
    last_summary: tuple = ()
    last_print = 0.0
    # Регион, чей API перестал принимать соединения, ставится на паузу с растущим интервалом:
    # долбить его дальше — только продлевать блокировку.
    region_pause: dict[str, float] = {}
    region_backoff: dict[str, float] = {}
    while True:
        active = [t for t in targets if not t.disabled]
        if not active:
            console.print("[red]Все цели отключены — охотиться не по чему. Причины выше.[/red]")
            if notifier:
                reasons = sorted({t.disabled for t in targets})
                await notifier.send("⛔ Охота остановлена: все цели отключены.\n"
                                    + "\n".join(f"• {html.escape(r)}" for r in reasons))
            return 1
        round_no += 1
        stats = RoundStats()
        for target in active:
            if target.disabled:  # регион мог отключиться по квоте посреди круга
                continue
            if region_pause.get(target.region, 0.0) > time.monotonic():
                continue
            attempts += 1
            try:
                match = await try_target(client, target, matcher, store, matches, console, stats)
                if target.region in region_backoff:
                    region_backoff.pop(target.region)
                    console.print(f"[{_now()}] [green]{target.region}: API снова отвечает, охота продолжается.[/green]")
                    if notifier:
                        await notifier.send(f"🟢 API {html.escape(target.region)} снова отвечает, охота продолжается.")
                if match:
                    found += 1
                    if notifier:
                        await notifier.send(
                            f"✅ Найден IP <code>{html.escape(match.address)}</code>\n"
                            f"Регион: {html.escape(target.region)}, подсеть {html.escape(target.pool_cidr)}\n"
                            f"Попыток: {attempts}, времени: {int(time.monotonic() - started) // 60} мин"
                        )
                    if found >= args.target_count:
                        console.print(f"[bold green]Готово: найдено {found} IP. "
                                      f"Список — {STATE_PATH} (секция «{section}»).[/bold green]")
                        return 0
            except QuotaExceeded as exc:
                for other in targets:
                    if other.region == target.region:
                        other.disabled = f"квота floating IP в {target.region} исчерпана"
                console.print(f"[{_now()}] [red]Квота исчерпана: {exc}. Регион {target.region} пропускается.[/red]")
                continue
            except RegionUnreachable as exc:
                stats.counts["error"] += 1
                backoff = min(max(120.0, region_backoff.get(target.region, 0.0) * 2), 1800.0)
                first = target.region not in region_backoff
                region_backoff[target.region] = backoff
                region_pause[target.region] = time.monotonic() + backoff
                console.print(f"[{_now()}] [red]{target.region}: API не принимает соединения ({exc}). "
                              f"Пауза региона {int(backoff // 60)} мин.[/red]")
                if notifier and first:
                    await notifier.send(
                        f"⚠️ API {html.escape(target.region)} не принимает соединения ({html.escape(str(exc))}). "
                        f"Возможно, Selectel временно заблокировал IP за частые запросы. Регион на паузе, "
                        f"остальные продолжают."
                    )
                continue
            except AddressNotAllowed as exc:
                address_targets = [t for t in targets if t.kind == "address"]
                for other in address_targets:
                    other.disabled = "Selectel запрещает выбирать конкретный адрес"
                console.print(f"[{_now()}] [yellow]Selectel не разрешает запрашивать конкретный IP ({exc}). "
                              f"Цели «адрес» отключены: {len(address_targets)}.[/yellow]")
                continue
            # Разброс ±30 %: запросы не идут строго по таймеру.
            await asyncio.sleep(max(0.0, args.delay) * random.uniform(0.7, 1.3))

        for reason, count in stats.disabled.items():
            console.print(f"[{_now()}] [red]Отклонено Selectel ({count} цел.): {reason}[/red]")
        c = stats.counts
        paused = sorted(r for r, until in region_pause.items() if until > time.monotonic())
        summary = (len(active), c["full"], c["miss"], c["error"], tuple(paused))
        now = time.monotonic()
        # Пока ничего не меняется, строка состояния — раз в минуту, а не на каждый круг.
        if summary != last_summary or now - last_print >= 60:
            elapsed = int(now - started)
            console.print(
                f"[{_now()}] круг {round_no} ({elapsed // 60} мин, запросов {attempts}): целей {len(active)}, "
                f"нет свободных {c['full']}, мимо whitelist {c['miss']}, ошибок {c['error']}, найдено {found}"
                + (f", на паузе: {', '.join(paused)}" if paused else "")
            )
            last_summary, last_print = summary, now
        await asyncio.sleep(max(0.0, args.round_delay) * random.uniform(0.7, 1.3))


async def run_async(argv: list[str]) -> int:
    # .env до parse_args: из него берутся значения по умолчанию (SEL_HUNT_REGIONS и др.).
    load_dotenv(DOTENV_PATH, override=False)
    args = parse_args(argv)
    console = Console()
    if needs_setup():
        run_setup_wizard(console)

    prefix = _env_prefix(args.account)
    settings = _build_settings(args, prefix)
    if not (settings.username and settings.password and settings.account_id):
        console.print(f"[red]В .env не заполнены {prefix}USERNAME / {prefix}PASSWORD / {prefix}ACCOUNT_ID[/red]")
        return 2
    if not settings.account_id.isdigit():
        console.print(
            f"[red]{prefix}ACCOUNT_ID={settings.account_id} — это не номер аккаунта. Нужен номер из правого "
            f"верхнего угла панели Selectel (только цифры, например 326910).[/red]"
        )
        if settings.username.isdigit():
            console.print(f"[red]{prefix}USERNAME={settings.username} похож на номер аккаунта — "
                          f"в USERNAME нужно имя сервисного пользователя (IAM → Сервисные пользователи).[/red]")
        return 2

    whitelist = _load_whitelist_networks(args.whitelist)
    if not whitelist:
        console.print(f"[red]{args.whitelist} пуст или не найден[/red]")
        return 2

    await ensure_project_resolved(
        settings,
        console=console,
        account_label=f"Аккаунт {args.account}",
        dotenv_env_prefix=prefix,
        auto_create=args.auto_create_project,
    )

    client = SelectelScannerClient(
        username=settings.username,
        password=settings.password,
        account_id=settings.account_id,
        project_name=settings.project_name,
        project_id=settings.project_id,
        regions=settings.regions,
        request_timeout=15.0,
    )
    # Охота сама ставит недоступный регион на паузу — долгие повторы внутри клиента не нужны.
    client.transport_attempts = 2
    try:
        await client.ensure_authenticated()
        regions = tuple(sorted(client.available_regions()))
        console.print(f"Регионы: {', '.join(regions)}")

        targets, pools_per_region = await discover_targets(client, regions, whitelist, console)
        if args.exact_only:
            targets = [t for t in targets if t.kind == "exact"]
        save_targets(targets)
        render_targets(console, targets, pools_per_region, whitelist)
        console.print(f"Цели сохранены в {TARGETS_PATH}")

        matcher = WhitelistMatcher.from_path(args.whitelist)
        existing = []
        for region in regions:
            try:
                existing += [r for r in await client.list_floating_ips(regions={region}) if matcher.contains(r.address)]
            except httpx.TransportError:
                pass  # недоступный регион уже отмечен выше
        if existing:
            console.print("Уже есть в проекте: " + ", ".join(f"{r.address} ({r.region})" for r in existing))

        if args.command == "discover":
            return 0
        if not targets:
            console.print("[red]Ни одна подсеть whitelist не выдаётся как floating IP в доступных регионах.[/red]")
            return 1
        notifier = TelegramNotifier.from_env()
        if notifier:
            # Две охоты (на двух аккаунтах) шлют в один чат — подписываем аккаунт.
            notifier.label = " · ".join(filter(None, [notifier.label, f"акк {settings.account_id}"]))
        console.print("Telegram: " + ("уведомления включены" if notifier else
                                      "выключен (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID в .env, проверка: run.bat tg-test)"))
        if notifier is None:
            return await hunt(client, targets, matcher, args, console)
        active = [t for t in targets if not t.disabled]
        await notifier.send(
            f"🔎 Охота запущена: {len(active)} целей, регионы {html.escape(', '.join(sorted({t.region for t in active})))}"
        )
        try:
            return await hunt(client, targets, matcher, args, console, notifier)
        except asyncio.CancelledError:
            await notifier.send("⏹ Охота остановлена вручную.")
            raise
        except Exception as exc:
            await notifier.send(f"⛔ Охота упала: {html.escape(type(exc).__name__)}: {html.escape(str(exc)[:300])}")
            raise
        finally:
            await notifier.close()
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    # В pipe/старой консоли Windows кодировка cp1251/cp866: ✓ ✗ → не должны ронять охоту.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ["tg-test"]:
            return asyncio.run(run_tg_test())
        return asyncio.run(run_async(argv))
    except KeyboardInterrupt:
        print("\nОстановлено.")
        return 130
    except httpx.TransportError as exc:
        print(f"Ошибка сети при обращении к Selectel ({type(exc).__name__}: {exc or 'без подробностей'}). "
              f"Проверьте интернет/VPN и запустите ещё раз.", file=sys.stderr)
        return 3
    except (ValueError, RuntimeError) as exc:
        cause = exc.__cause__
        detail = ""
        if isinstance(cause, httpx.HTTPStatusError):
            detail = f" (HTTP {cause.response.status_code}: {cause.response.text[:200]})"
        print(f"Ошибка: {exc}{detail}", file=sys.stderr)
        return 2
