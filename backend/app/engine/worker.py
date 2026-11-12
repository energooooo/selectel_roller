"""Охота одного ключа (аккаунта Selectel).

Floating IP запрашивается сразу из нужной подсети: в Neutron передаётся subnet_id
подсети внешней сети, совпадающей с белой подсетью из БД. Нет свободных — 409,
идём к следующей и ходим кругами, пока адрес не освободится.

Что важно сохранить из опыта прода:
- слишком частые запросы могут привести к 403 rule:create_floatingip —
  это бан аккаунта, ключ выключается;
- ConnectTimeout в одном регионе = бан IP на эндпоинте → короткая пауза региона;
- транспортные сбои сразу в нескольких регионах = умерла прокси-сессия → новая сессия;
- пойманный IP никогда не удаляется; удаляется только только что созданный адрес,
  который не попал в белую подсеть (для «частичных» подсетей).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import func, select

from app import bus, metrics, settings_store
from app.core.crypto import decrypt
from app.db.models import CaughtIP, Proxy, RegionPool, SelectelKey, Subnet, utcnow
from app.db.session import session_scope
from app.engine import heat as heat_mod
from app.engine.governor import GOOD, PaceGovernor
from app.engine.proxy import with_session
from app.engine.resell import SelectelResellClient
from app.engine.selectel import SelectelClient
from app.events import emit
from app.notify.telegram import esc, notify_for

if TYPE_CHECKING:
    from app.engine.manager import WorkerManager

log = logging.getLogger(__name__)

# «В подсети нет свободных адресов» приходит то 409, то 400 с этими типами — ждать, а не отключать цель.
FULL_ERROR_TYPES = {
    "IpAddressGenerationFailure", "IpAddressAlreadyAllocated", "IpAddressInUse", "ExternalIpAddressExhausted",
}
DEFAULT_PROJECT_NAME = "toffee-roller"


class StopHunt(Exception):
    """Остановить охоту с итоговым статусом ключа."""

    def __init__(self, status: str, reason: str, *, disable: bool = False, alert: bool = True) -> None:
        super().__init__(reason)
        self.status, self.reason, self.disable, self.alert = status, reason, disable, alert


class RegionUnreachable(Exception):
    pass


class QuotaExceeded(Exception):
    pass


@dataclass
class Target:
    region: str
    network_id: str
    os_subnet_id: str
    pool_cidr: str
    subnet_id: int  # наша запись в subnets
    subnet_cidr: str
    kind: str  # exact — вся подсеть Selectel белая; partial — белая только часть
    heat: float = 1.0  # рейтинг подсети (app.engine.heat)
    tier: str = "new"
    weight: float = 1.0  # доля кругов, в которых цель обходится
    attempts: int = 0
    busy: int = 0
    errors: int = 0
    disabled: str = ""

    @property
    def label(self) -> str:
        return f"{self.region} {self.pool_cidr}"


@dataclass
class Runtime:
    """То, что видно на сайте про живую охоту ключа (не хранится в БД)."""

    state: str = "starting"
    round: int = 0
    current: str = ""
    attempts_run: int = 0
    caught_run: int = 0
    started_at: float = field(default_factory=time.time)
    targets_total: int = 0
    targets_active: int = 0
    paused_regions: dict[str, float] = field(default_factory=dict)
    proxy: str = ""
    next_action_at: float | None = None
    last_error: str = ""
    pace: float = 1.0
    pace_reason: str = ""
    skipped_cold: int = 0

    def to_dict(self) -> dict[str, Any]:
        now = time.time()
        return {
            "state": self.state,
            "round": self.round,
            "current": self.current,
            "attempts_run": self.attempts_run,
            "caught_run": self.caught_run,
            "started_at": self.started_at,
            "uptime_s": int(now - self.started_at),
            "targets_total": self.targets_total,
            "targets_active": self.targets_active,
            "paused_regions": {r: int(t - now) for r, t in self.paused_regions.items() if t > now},
            "proxy": self.proxy,
            "next_action_in": max(0, int(self.next_action_at - now)) if self.next_action_at else None,
            "last_error": self.last_error,
            "pace": self.pace,
            "pace_reason": self.pace_reason,
            "skipped_cold": self.skipped_cold,
        }


def neutron_error(response: httpx.Response) -> tuple[int, str, str]:
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


def short_exc(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return type(exc).__name__ + (f": {text[:140]}" if text else "")


def _jitter(lo: float, hi: float) -> float:
    lo, hi = max(0.0, float(lo)), max(0.0, float(hi))
    if hi < lo:
        lo, hi = hi, lo
    return random.uniform(lo, hi)


async def resolve_project(username: str, password: str, account_id: str, proxy: str | None,
                          wanted_name: str = "") -> tuple[str, str]:
    """Проект облака для охоты: указанный по имени → иначе первый существующий →
    иначе создаётся «toffee-roller». Возвращает (id, name)."""
    client = SelectelResellClient(username=username, password=password, account_id=account_id, proxy=proxy)
    try:
        projects = [p for p in await client.list_projects() if p.enabled]
        if wanted_name:
            match = next((p for p in projects if p.name == wanted_name), None)
            if match:
                return match.id, match.name
            created = await client.create_project(wanted_name, description="Toffee Roller")
            return created.id, created.name
        preferred = next((p for p in projects if p.name == DEFAULT_PROJECT_NAME), None) or (
            projects[0] if projects else None)
        if preferred:
            return preferred.id, preferred.name
        created = await client.create_project(DEFAULT_PROJECT_NAME, description="Toffee Roller")
        return created.id, created.name
    finally:
        await client.close()


class KeyWorker:
    def __init__(self, key_id: int, manager: WorkerManager) -> None:
        self.key_id = key_id
        self.manager = manager
        self.runtime = Runtime()
        self.targets: list[Target] = []
        self.targets_dirty = False
        self.client: SelectelClient | None = None
        self.key_name = f"#{key_id}"
        self.owner_id: int | None = None  # кабинет ключа: подсети и прокси берутся только из него
        self.account_id = ""
        self._proxy_base: str | None = None
        self._proxy_mode = "none"
        self._proxy_selection_mode = "none"
        self._session_gen = 0
        self.consecutive_errors = 0
        self.governor = PaceGovernor()
        self._current: Target | None = None

    # ── публикация состояния ───────────────────────────────────────────────

    def _publish(self) -> None:
        bus.publish("runtime", {"key_id": self.key_id, **self.runtime.to_dict()})

    async def _set_status(self, status: str, reason: str = "", *, enabled: bool | None = None) -> None:
        async with session_scope() as db:
            key = await db.get(SelectelKey, self.key_id)
            if key is None:
                return
            key.status = status
            key.status_reason = reason
            if enabled is not None:
                key.enabled = enabled
        self.runtime.state = status
        bus.publish("key", {"id": self.key_id, "status": status, "status_reason": reason,
                            **({"enabled": enabled} if enabled is not None else {})})
        self._publish()

    async def log(self, message: str, level: str = "info", region: str = "", kind: str = "log",
                  **data: Any) -> None:
        await emit(f"[{self.key_name}] {message}", level=level, kind=kind, key_id=self.key_id,
                   region=region, data=data)

    # ── запуск ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            await self._run()
        except StopHunt as stop:
            if stop.status == "banned":
                await self._sync_pace()
                if self.manager:
                    self.manager.neighbour_banned(self.key_id)
            await self._set_status(stop.status, stop.reason, enabled=False if stop.disable else None)
            level = "success" if stop.status == "done" else "error"
            await self.log(stop.reason, level=level, kind="stop")
            if stop.alert and stop.status != "done":
                await notify_for(self.owner_id, "alert", f"⛔ <b>{esc(self.key_name)}</b> ({esc(self.account_id)}): {esc(stop.reason)}")
        except asyncio.CancelledError:
            self.runtime.state = "paused"
            self._publish()
            raise
        finally:
            if self.client:
                await self.client.close()
                self.client = None

    async def _load_key(self) -> SelectelKey:
        async with session_scope() as db:
            key = await db.get(SelectelKey, self.key_id)
            if key is None:
                raise StopHunt("error", "ключ удалён", alert=False)
            self.key_name = key.name
            self.account_id = key.account_id
            self.owner_id = key.owner_id
            self._session_gen = key.proxy_session_gen or 0
            self.governor = PaceGovernor(key.pace or 1.0, key.pace_reason or "")
            self.runtime.pace, self.runtime.pace_reason = self.governor.pace, self.governor.reason
            return key

    def _proxy_url(self) -> str | None:
        if not self._proxy_base:
            return None
        return with_session(self._proxy_base, self._proxy_mode, f"tr{self.account_id}", self._session_gen)

    async def _make_client(self, key: SelectelKey) -> SelectelClient:
        client = SelectelClient(
            username=key.username,
            password=decrypt(key.password_enc),
            account_id=key.account_id,
            project_name=key.project_name,
            project_id=key.project_id,
            request_timeout=float(settings_store.get("request_timeout")),
            connect_timeout=float(settings_store.get("connect_timeout")),
            proxy=self._proxy_url(),
        )
        # Охота сама паузит недоступный регион — долгие повторы внутри клиента не нужны.
        client.transport_attempts = 4 if client.proxy else 2
        return client

    async def _run(self) -> None:
        key = await self._load_key()
        self._proxy_selection_mode = key.proxy_mode
        # Не все ключи разом: пачка входов с одной панели — это рейт-лимит и риск бана аккаунтов.
        wait = self.manager.reserve_start_slot()
        if wait >= 1:
            await self._set_status("starting", f"В очереди на запуск, ~{int(wait)} с")
            await self._sleep(wait, "starting")
        await self._set_status("starting", "Вход в Selectel…")

        proxy = await self.manager.proxy_for_key(self.key_id)
        if proxy is None and key.proxy_mode != "none":
            raise StopHunt("error", "Нет рабочего прокси. Без прокси Selectel быстро блокирует сервер, поэтому поиск не начат. "
                                    "Добавьте прокси или выберите в настройках ключа «Без прокси».")
        if proxy:
            self._proxy_base, self._proxy_mode = decrypt(proxy.url_enc), proxy.session_mode
            self.runtime.proxy = proxy.display + (f" · {proxy.label}" if proxy.label else "")

        if not key.project_id and not key.project_name:
            try:
                pid, pname = await resolve_project(key.username, decrypt(key.password_enc), key.account_id,
                                                   self._proxy_url())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    raise StopHunt("auth_failed", "Selectel не принял данные для входа. Проверьте номер аккаунта, "
                                                  "имя пользователя и пароль.", disable=True) from exc
                raise
            async with session_scope() as db:
                db_key = await db.get(SelectelKey, self.key_id)
                db_key.project_id, db_key.project_name = pid, pname
            key.project_id, key.project_name = pid, pname
            await self.log(f"проект в облаке: {pname}")

        if proxy:
            await self._rotate_session(persist_only=True)
        await self._authenticate(key)
        await self.discover()
        await self._hunt()

    async def _authenticate(self, key: SelectelKey) -> None:
        """Вход с перебором прокси-сессий: сломанный модем сессии (TLS-мусор вместо ответа)
        лечится следующей сессией — другим выходным IP."""
        tries = 5 if self._proxy_mode == "password_suffix" or self._proxy_selection_mode == "auto" else 1
        last: BaseException | None = None
        rechecks = 2  # 401 бывает разовым (всплеск входов, сбой identity) — перепроверяем перед выключением ключа
        attempt = 0
        while attempt < tries:
            attempt += 1
            self.client = await self._make_client(key)
            try:
                await self.client.ensure_authenticated()
                await self._mark_ok()
                return
            except ValueError as exc:
                cause = exc.__cause__
                await self.client.close()
                self.client = None
                if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 401 and rechecks > 0:
                    rechecks -= 1
                    attempt -= 1
                    pause = random.uniform(45, 90)
                    await self.log(f"Selectel ответил 401 на вход, перепроверка через {int(pause)} с", "warn")
                    await self._sleep(pause, "starting")
                    continue
                if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 401:
                    raise StopHunt("auth_failed", "Selectel не принял данные для входа. Проверьте имя пользователя, пароль "
                                                  "и доступ пользователя к проекту.", disable=True) from exc
                if isinstance(cause, httpx.TransportError) and tries > 1:
                    last = cause
                    await self.log(f"прокси не отвечает ({short_exc(cause)}), пробую другой внешний IP", "warn")
                    await self._rotate_proxy(persist_only=True)
                    continue
                raise StopHunt("error", f"Не удалось войти в Selectel: {short_exc(cause or exc)}") from exc
        raise StopHunt("error", f"Прокси не отвечает ни с одного внешнего IP: {short_exc(last) if last else '—'}")

    async def _rotate_proxy(self, *, persist_only: bool = False) -> str:
        if self._proxy_selection_mode == "auto":
            proxy = await self.manager.proxy_for_key(self.key_id)
            if proxy is None:
                raise StopHunt("error", "Нет доступных прокси для ротации. Поиск остановлен.")
            self._proxy_base, self._proxy_mode = decrypt(proxy.url_enc), proxy.session_mode
            self.runtime.proxy = proxy.display + (f" · {proxy.label}" if proxy.label else "")
        note = await self._rotate_session(persist_only=persist_only)
        self._publish()
        return f"{self.runtime.proxy}, {note}"

    async def _rotate_session(self, *, persist_only: bool = False) -> str:
        self._session_gen += 1
        async with session_scope() as db:
            db_key = await db.get(SelectelKey, self.key_id)
            if db_key:
                db_key.proxy_session_gen = self._session_gen
        if not persist_only and self.client:
            await self.client.set_proxy(self._proxy_url())
        return f"сессия g{self._session_gen}"

    async def _mark_ok(self) -> None:
        async with session_scope() as db:
            db_key = await db.get(SelectelKey, self.key_id)
            if db_key:
                db_key.last_ok_at = utcnow()

    # ── цели ───────────────────────────────────────────────────────────────

    async def _pools(self, region: str) -> list[dict[str, str]]:
        """Пулы floating IP региона из API (и в кэш), а если API лежит — из кэша."""
        assert self.client
        try:
            try:
                pools = await self.client.list_floatingip_pools(region)
            except httpx.HTTPStatusError:
                pools = await self.client.list_external_subnets(region)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            async with session_scope() as db:
                rows = (await db.execute(select(RegionPool).where(RegionPool.region == region))).scalars().all()
            await self.log(f"{region}: Selectel не отдал список подсетей ({short_exc(exc)}), беру сохранённый: {len(rows)} шт.", "warn", region)
            return [{"subnet_id": r.os_subnet_id, "network_id": r.network_id, "cidr": r.cidr} for r in rows]
        clean = [{"subnet_id": str(p.get("subnet_id", "")), "network_id": str(p.get("network_id", "")),
                  "cidr": str(p.get("cidr", ""))} for p in pools if p.get("subnet_id")]
        async with session_scope() as db:
            existing = {r.os_subnet_id: r for r in (await db.execute(
                select(RegionPool).where(RegionPool.region == region))).scalars()}
            for p in clean:
                row = existing.get(p["subnet_id"])
                if row is None:
                    db.add(RegionPool(region=region, os_subnet_id=p["subnet_id"], network_id=p["network_id"],
                                      cidr=p["cidr"]))
                else:
                    row.network_id, row.cidr, row.updated_at = p["network_id"], p["cidr"], utcnow()
            # Подсети, которых Selectel больше не отдаёт (или мусор из кэша), — вон, иначе на них
            # строятся цели и каждый проход ловит 400 «not a valid UUID».
            if clean:
                fresh = {p["subnet_id"] for p in clean}
                for os_id, row in existing.items():
                    if os_id not in fresh:
                        await db.delete(row)
        return clean

    async def discover(self) -> None:
        """Пулы регионов из API → кэш; цели строятся по кэшу и подсетям из БД."""
        assert self.client
        wanted_regions = set(settings_store.get("regions") or [])
        regions = sorted(r for r in self.client.available_regions() if not wanted_regions or r in wanted_regions)
        for region in regions:
            await self._pools(region)
        await self.rebuild_targets()

    async def rebuild_targets(self) -> None:
        """Цели из кэша пулов и включённых подсетей. Дёшево — без запросов к Selectel,
        поэтому вызывается на лету при любом изменении подсетей/настроек."""
        self.targets_dirty = False
        wanted_regions = set(settings_store.get("regions") or [])
        exact_only = bool(settings_store.get("exact_only"))
        available = set(self.client.available_regions()) if self.client else set()
        async with session_scope() as db:
            subnets = (await db.execute(select(Subnet).where(
                Subnet.enabled.is_(True), Subnet.owner_id == self.owner_id))).scalars().all()
            pools = (await db.execute(select(RegionPool))).scalars().all()
            old = {(t.region, t.os_subnet_id, t.subnet_id): t for t in self.targets}
            targets: list[Target] = []
            for pool in pools:
                if wanted_regions and pool.region not in wanted_regions:
                    continue
                if available and pool.region not in available:
                    continue
                try:
                    pool_net = ipaddress.ip_network(pool.cidr, strict=False)
                except ValueError:
                    continue
                for subnet in subnets:
                    try:
                        net = ipaddress.ip_network(subnet.cidr, strict=False)
                    except ValueError:
                        continue
                    if not net.overlaps(pool_net):
                        continue
                    kind = "exact" if pool_net.subnet_of(net) else "partial"
                    if kind == "partial" and exact_only:
                        continue
                    if not subnet.region:
                        subnet.region = pool.region
                    prev = old.get((pool.region, pool.os_subnet_id, subnet.id))
                    targets.append(prev or Target(
                        region=pool.region, network_id=pool.network_id, os_subnet_id=pool.os_subnet_id,
                        pool_cidr=pool.cidr, subnet_id=subnet.id, subnet_cidr=subnet.cidr, kind=kind))
        targets.sort(key=lambda t: (t.kind != "exact", t.region, t.pool_cidr))
        self.targets = targets
        await self.refresh_heat()
        self.runtime.targets_total = len(targets)
        self.runtime.targets_active = sum(1 for t in targets if not t.disabled)
        self._publish()

    async def refresh_heat(self) -> None:
        """Рейтинг подсетей из БД — раз в круг, чтобы горячие поднимались сами по мере поимок."""
        if not self.targets:
            return
        async with session_scope() as db:
            scores = await heat_mod.subnet_scores(db)
        for t in self.targets:
            h = scores.get(t.subnet_id)
            if h:
                t.heat, t.tier, t.weight = h.heat, h.tier, h.weight

    def round_plan(self, active: list[Target], rand: Any = random.random) -> list[Target]:
        """Порядок обхода на круг: горячие первыми; холодные — только в доле кругов."""
        if not settings_store.get("hot_priority"):
            self.runtime.skipped_cold = 0
            return active
        ordered = sorted(active, key=lambda t: -t.heat)  # sorted стабилен: внутри равных — прежний порядок
        plan = [t for t in ordered if t.weight >= 1.0 or rand() < t.weight]
        self.runtime.skipped_cold = len(ordered) - len(plan)
        return plan or ordered[:1]

    async def _sync_pace(self) -> None:
        """Изменения регулятора темпа — в БД, журнал и на сайт."""
        self.runtime.pace, self.runtime.pace_reason = self.governor.pace, self.governor.reason
        for line in self.governor.take_changes():
            try:
                await self.log(line, "warn" if "замедл" in line or "бан" in line else "info", kind="pace",
                               pace=self.governor.pace)
            except Exception:  # noqa: BLE001
                log.exception("pace log")
        if not self.governor.dirty:
            return
        self.governor.dirty = False
        try:
            async with session_scope() as db:
                key = await db.get(SelectelKey, self.key_id)
                if key:
                    key.pace, key.pace_reason = self.governor.pace, self.governor.reason
        except Exception:  # noqa: BLE001
            log.exception("pace save")
        bus.publish("key", {"id": self.key_id, "pace": self.governor.pace, "pace_reason": self.governor.reason})
        self._publish()

    def _paced(self, lo: Any, hi: Any) -> float:
        return self.governor.delay(_jitter(lo, hi))

    # ── охота ──────────────────────────────────────────────────────────────

    async def _goal_reached(self) -> tuple[bool, int, int]:
        async with session_scope() as db:
            key = await db.get(SelectelKey, self.key_id)
            goal = key.ip_goal if key and key.ip_goal else int(settings_store.get("ip_goal") or 1)
            held = (await db.execute(select(func.count(CaughtIP.id)).where(
                CaughtIP.key_id == self.key_id, CaughtIP.on_account.is_(True)))).scalar_one()
        return held >= goal, held, goal

    async def _sleep(self, seconds: float, state: str) -> None:
        self.runtime.state = state
        self.runtime.next_action_at = time.time() + seconds
        self._publish()
        # Сон кусками: если подсети поменялись — перестроим цели, не дожидаясь конца паузы.
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            await asyncio.sleep(min(left, 2.0))
            if self.targets_dirty:
                await self.rebuild_targets()
        self.runtime.next_action_at = None

    async def _hunt(self) -> None:
        done, held, goal = await self._goal_reached()
        if done:
            raise StopHunt("done", f"Готово: на аккаунте уже {held} из {goal} нужных IP.", alert=False)
        if not self.targets:
            raise StopHunt("error", "Искать негде: ни одна включённая подсеть не выдаётся Selectel "
                                    "в выбранных регионах. Включите подсети или регионы.", alert=False)
        await self._set_status("hunting", "")
        regions = sorted({t.region for t in self.targets})
        await self.log(f"поиск запущен: подсетей {len(self.targets)}, регионы {', '.join(regions)}", "info",
                       kind="start", proxy=self.runtime.proxy)

        region_pause: dict[str, float] = {}
        region_backoff: dict[str, float] = {}
        region_fails: dict[str, int] = {}
        transport_streak, streak_regions = 0, set()
        max_errors = lambda: int(settings_store.get("max_consecutive_errors") or 0)  # noqa: E731

        while True:
            if self.targets_dirty:
                await self.rebuild_targets()
            active = [t for t in self.targets if not t.disabled]
            self.runtime.targets_active = len(active)
            if not active:
                reasons = sorted({t.disabled for t in self.targets}) or ["нет целей"]
                raise StopHunt("error", "Selectel отказал во всех подсетях: " + "; ".join(reasons[:3]))
            self.runtime.round += 1
            self.runtime.paused_regions = region_pause
            self.governor.tick()
            await self._sync_pace()
            if self.runtime.round > 1:
                await self.refresh_heat()

            for target in self.round_plan(active):
                if target.disabled or self.targets_dirty:
                    continue
                if region_pause.get(target.region, 0.0) > time.monotonic():
                    continue
                if self._proxy_selection_mode == "auto":
                    await self._rotate_proxy()
                self.runtime.current = target.label
                self.runtime.state = "hunting"
                self._publish()
                try:
                    caught = await self._attempt(target)
                    if self.consecutive_errors and self.consecutive_errors >= max_errors() > 0:
                        raise StopHunt("error", f"{self.consecutive_errors} ошибок подряд — ключ остановлен. Подробности в журнале.",
                                       disable=True)
                    region_fails.pop(target.region, None)
                    transport_streak, streak_regions = 0, set()
                    if target.region in region_backoff:
                        region_backoff.pop(target.region)
                        region_pause.pop(target.region, None)
                        await self.log(f"{target.region}: Selectel снова отвечает", "success", target.region)
                    if caught:
                        done, held, goal = await self._goal_reached()
                        if done:
                            raise StopHunt("done", f"Готово: найдено {held} из {goal} нужных IP, поиск остановлен.", alert=False)
                except QuotaExceeded as exc:
                    for other in self.targets:
                        if other.region == target.region:
                            other.disabled = f"квота floating IP в {target.region} исчерпана"
                    await self.log(f"{target.region}: закончился лимит IP на аккаунте ({exc}), регион пропускается", "warn", target.region)
                    await notify_for(self.owner_id, "alert", f"⚠️ <b>{esc(self.key_name)}</b>: квота floating IP в "
                                          f"{esc(target.region)} исчерпана, регион пропускается")
                    continue
                except RegionUnreachable as exc:
                    transport_streak += 1
                    streak_regions.add(target.region)
                    if ((self._proxy_mode == "password_suffix" or self._proxy_selection_mode == "auto")
                            and transport_streak >= int(settings_store.get("proxy_rotate_after"))
                            and len(streak_regions) >= 2):
                        note = await self._rotate_proxy()
                        await self.log(f"прокси перестал отвечать во всех регионах ({exc}), переключаюсь на другой внешний IP ({note})", "warn",
                                       kind="rotation")
                        await notify_for(self.owner_id, "rotation", f"🔁 <b>{esc(self.key_name)}</b>: прокси-сессия умерла, "
                                                 f"сменил на {esc(note)}")
                        transport_streak, streak_regions = 0, set()
                        region_fails.clear(), region_backoff.clear(), region_pause.clear()
                        continue
                    fails = region_fails.get(target.region, 0) + 1
                    region_fails[target.region] = fails
                    if fails >= int(settings_store.get("region_fail_threshold")):
                        base = float(settings_store.get("region_pause_base"))
                        backoff = min(max(base, region_backoff.get(target.region, 0.0) * 2),
                                      float(settings_store.get("region_pause_max")))
                        first = target.region not in region_backoff
                        region_backoff[target.region] = backoff
                        region_pause[target.region] = time.monotonic() + backoff
                        await self.log(f"{target.region}: Selectel не отвечает ({exc}), "
                                       f"регион на паузе {int(backoff)} с", "warn", target.region)
                        if first:
                            await notify_for(self.owner_id, "alert", f"⚠️ <b>{esc(self.key_name)}</b>: API {esc(target.region)} не "
                                                  f"отвечает ({esc(exc)}). Регион на паузе, остальные ловят.")
                    if self.consecutive_errors >= max_errors() > 0:
                        raise StopHunt("error", f"{self.consecutive_errors} ошибок подряд — ключ остановлен. Подробности в журнале.",
                                       disable=True)
                    continue

                if self.governor.dirty or self.governor.changes:
                    await self._sync_pace()
                await self._sleep(self._paced(settings_store.get("busy_delay_min"),
                                              settings_store.get("busy_delay_max")), "hunting")

            self.runtime.current = ""
            every = int(settings_store.get("long_pause_every") or 0)
            if every and self.runtime.round % every == 0:
                await self._sleep(self._paced(settings_store.get("long_pause_min"),
                                              settings_store.get("long_pause_max")), "sleeping")
            else:
                await self._sleep(self._paced(settings_store.get("round_delay_min"),
                                              settings_store.get("round_delay_max")), "sleeping")

    async def _record_attempt(self, outcome: str, region: str, latency_ms: float | None) -> None:
        metrics.record(self.key_id, region, outcome, latency_ms)
        self.governor.observe(outcome)
        self.runtime.attempts_run += 1
        # Ответ Selectel по существу (занято / выдал адрес) = связь в порядке, счётчик ошибок с нуля.
        self.consecutive_errors = 0 if outcome in ("busy", "caught", "misses") else self.consecutive_errors + 1
        async with session_scope() as db:
            key = await db.get(SelectelKey, self.key_id)
            if key:
                key.attempts_total = (key.attempts_total or 0) + 1
                if outcome in ("busy", "caught", "misses"):
                    key.last_ok_at = utcnow()
            if outcome in GOOD and self._current is not None:
                subnet = await db.get(Subnet, self._current.subnet_id)
                if subnet:
                    subnet.attempts_total = (subnet.attempts_total or 0) + 1

    async def _attempt(self, target: Target) -> CaughtIP | None:
        assert self.client
        target.attempts += 1
        self._current = target
        started = time.perf_counter()
        try:
            record = await self.client.create_floating_ip_in_subnet(
                target.region, target.network_id, target.os_subnet_id)
        except httpx.HTTPStatusError as exc:
            latency = (time.perf_counter() - started) * 1000
            status, error_type, message = neutron_error(exc.response)
            if error_type == "OverQuota" or "quota" in message.lower():
                await self._record_attempt("errors", target.region, latency)
                raise QuotaExceeded(f"{status} {error_type} {message}") from exc
            if status == 409 or error_type in FULL_ERROR_TYPES:
                target.busy += 1
                await self._record_attempt("busy", target.region, latency)
                return None
            if status == 429:
                self.governor.on_rate_limited()
                await self._record_attempt("errors", target.region, latency)
                retry_after = exc.response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 60.0
                await self.log(f"Selectel просит снизить частоту запросов, жду {int(delay)} с", "warn", target.region)
                await self._sleep(delay, "sleeping")
                return None
            if status == 403 and "create_floatingip" in message:
                self.governor.on_ban()
                await self._record_attempt("errors", target.region, latency)
                raise StopHunt("banned", "Selectel временно запретил аккаунту получать IP за слишком частые запросы. "
                                         "Ключ остановлен. Обычно запрет снимается за сутки; после включения "
                                         f"ключ сам будет искать медленнее (темп ×{self.governor.pace:g}).",
                               disable=True)
            if status in (400, 403, 404):
                await self._record_attempt("errors", target.region, latency)
                target.disabled = f"{status} {error_type}: {message}"[:200]
                await self.log(f"{target.label}: Selectel отказал, подсеть пропускается — {target.disabled}", "warn", target.region)
                return None
            target.errors += 1
            await self._record_attempt("errors", target.region, latency)
            await self.log(f"{target.label}: {status} {error_type} {message}", "error", target.region)
            return None
        except httpx.TransportError as exc:
            latency = (time.perf_counter() - started) * 1000
            recovered = None
            if isinstance(exc, SelectelClient._POST_SEND_ERRORS):
                recovered = await self._recover_created(target)
            if recovered is None:
                is_proxy = isinstance(exc, httpx.ProxyError)
                outcome = "proxy_errors" if is_proxy else "timeouts"
                await self._record_attempt(outcome, target.region, latency)
                self.runtime.last_error = short_exc(exc)
                if isinstance(exc, SelectelClient._PRE_SEND_ERRORS):
                    raise RegionUnreachable(short_exc(exc)) from exc
                target.errors += 1
                await self.log(f"{target.label}: ошибка сети — {short_exc(exc)}", "error", target.region)
                return None
            record = recovered
            await self.log(f"{target.label}: ответ потерялся, но IP выдан: {record.address}", "warn", target.region)

        latency = (time.perf_counter() - started) * 1000
        try:
            in_subnet = ipaddress.ip_address(record.address) in ipaddress.ip_network(target.subnet_cidr)
        except ValueError:
            in_subnet = False
        if in_subnet:
            await self._record_attempt("caught", target.region, latency)
            return await self._on_caught(target, record.address, record.id)

        # «Частичная» подсеть: адрес не из белой части — сразу отдаём обратно.
        await self._record_attempt("misses", target.region, latency)
        try:
            deleted = await self.client.delete_floating_ip(record.id)
        except (httpx.HTTPError, KeyError):
            deleted = False
        await self.log(f"выдан неподходящий адрес {record.address}, "
                       + ("он сразу возвращён Selectel" if deleted else "вернуть не удалось — удалите его во вкладке «IP»"),
                       "info" if deleted else "error", target.region)
        return None

    async def _recover_created(self, target: Target):  # noqa: ANN202
        """После обрыва уже отправленного POST ищем адрес, который Selectel мог успеть выдать."""
        assert self.client
        await asyncio.sleep(1.0)
        try:
            records = await self.client.list_floating_ips(regions={target.region})
        except httpx.HTTPError:
            return None
        pool = ipaddress.ip_network(target.pool_cidr)
        async with session_scope() as db:
            known = set((await db.execute(select(CaughtIP.floating_ip_id))).scalars())
        for record in records:
            if record.id in known or record.port_id or record.fixed_ip_address:
                continue
            try:
                if ipaddress.ip_address(record.address) in pool:
                    return record
            except ValueError:
                continue
        return None

    async def _on_caught(self, target: Target, address: str, floating_ip_id: str) -> CaughtIP:
        self.runtime.caught_run += 1
        async with session_scope() as db:
            key = await db.get(SelectelKey, self.key_id)
            caught = CaughtIP(ip=address, region=target.region, key_id=self.key_id, account_id=self.account_id,
                              owner_id=key.owner_id if key else self.owner_id,
                              subnet_id=target.subnet_id, pool_cidr=target.pool_cidr,
                              floating_ip_id=floating_ip_id, attempts_spent=key.attempts_total if key else None,
                              protected=True, on_account=True)
            db.add(caught)
            if key:
                key.caught_total = (key.caught_total or 0) + 1
                key.last_caught_at = utcnow()
            subnet = await db.get(Subnet, target.subnet_id)
            if subnet:
                subnet.caught_total = (subnet.caught_total or 0) + 1
            await db.flush()
            caught_id = caught.id
        await self.log(f"найден IP {address} ({target.region}, подсеть {target.pool_cidr})", "success", target.region,
                       kind="caught", ip=address, caught_id=caught_id)
        bus.publish("caught", {"id": caught_id, "ip": address, "region": target.region, "key_id": self.key_id,
                               "key_name": self.key_name})
        await notify_for(self.owner_id, "caught", f"🎯 Найден IP <code>{esc(address)}</code>\n"
                               f"Ключ: <b>{esc(self.key_name)}</b> ({esc(self.account_id)})\n"
                               f"Регион: {esc(target.region)}, подсеть {esc(target.pool_cidr)}\n"
                               f"Запросов с запуска: {self.runtime.attempts_run}", admin_copy=True)
        self.manager.on_caught(caught_id)
        return caught
