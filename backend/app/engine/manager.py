"""Менеджер воркеров: держит запущенными ровно те ключи, что включены в БД.

API после любого изменения зовёт reconcile() / subnets_changed() / settings_changed() —
так всё применяется на лету, без перезапуска сервера.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from sqlalchemy import select

from app import bus, metrics, settings_store
from app.core.crypto import decrypt
from app.db.models import Proxy, SelectelKey, utcnow
from app.db.session import session_scope
from app.engine.proxy import check_proxy, choose_random_proxy, with_session
from app.engine.selectel import SelectelClient
from app.engine.worker import KeyWorker, resolve_project
from app.events import emit
from app.notify.telegram import notify, panel_owner_ids

log = logging.getLogger(__name__)
# Статусы, после которых ключ сам не перезапускается — нужен человек.
TERMINAL = {"done", "banned", "auth_failed"}


class WorkerManager:
    def __init__(self) -> None:
        self.workers: dict[int, KeyWorker] = {}
        self.tasks: dict[int, asyncio.Task[None]] = {}
        self._restart_backoff: dict[int, float] = {}
        self._restart_at: dict[int, float] = {}
        self._lock = asyncio.Lock()
        self._background: list[asyncio.Task[Any]] = []
        self._caught_hooks: list[Any] = []
        self._next_start_at = 0.0  # monotonic: раньше этого момента следующий ключ не входит в Selectel

    def reserve_start_slot(self) -> float:
        """Очередь запуска: ключи входят в Selectel по одному, с интервалом start_stagger (±25 %).
        Возвращает, сколько секунд этому ключу ждать своей очереди."""
        interval = max(0.0, float(settings_store.get("start_stagger") or 0))
        now = time.monotonic()
        slot = max(now, self._next_start_at)
        self._next_start_at = slot + interval * random.uniform(0.75, 1.25)
        return slot - now

    # ── жизненный цикл ─────────────────────────────────────────────────────

    async def start(self) -> None:
        # Ключи, которые в прошлой жизни сервера охотились, снова в «старт».
        async with session_scope() as db:
            for key in (await db.execute(select(SelectelKey).where(SelectelKey.enabled.is_(True)))).scalars():
                if key.status not in TERMINAL:
                    key.status = "starting"
        self._background = [
            asyncio.create_task(metrics.flusher(), name="metrics-flusher"),
            asyncio.create_task(self._supervisor(), name="supervisor"),
            asyncio.create_task(self._proxy_checker(), name="proxy-checker"),
        ]
        await self.reconcile()

    async def stop(self) -> None:
        for task in self._background:
            task.cancel()
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*self.tasks.values(), *self._background, return_exceptions=True)
        await metrics.flush()

    async def reconcile(self) -> None:
        async with self._lock:
            async with session_scope() as db:
                keys = (await db.execute(select(SelectelKey))).scalars().all()
            wanted = {k.id for k in keys if k.enabled and k.status not in TERMINAL}
            await asyncio.gather(*(self._stop_worker(key_id) for key_id in self.tasks.keys() - wanted))
            now = time.monotonic()
            for key_id in wanted:
                task = self.tasks.get(key_id)
                if task and not task.done():
                    continue
                if self._restart_at.get(key_id, 0) > now:
                    continue
                self._start_worker(key_id)
            # Выключенные ключи в БД не должны висеть в «ловит».
            async with session_scope() as db:
                for key in (await db.execute(select(SelectelKey).where(SelectelKey.enabled.is_(False)))).scalars():
                    if key.status in {"starting", "hunting", "sleeping"}:
                        key.status = "paused"
                        bus.publish("key", {"id": key.id, "status": "paused"})

    def _start_worker(self, key_id: int) -> None:
        worker = KeyWorker(key_id, self)
        self.workers[key_id] = worker
        task = asyncio.create_task(worker.run(), name=f"key-{key_id}")
        task.add_done_callback(lambda t, k=key_id: self._on_done(k, t))
        self.tasks[key_id] = task

    async def _stop_worker(self, key_id: int) -> None:
        task = self.tasks.pop(key_id, None)
        self.workers.pop(key_id, None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _on_done(self, key_id: int, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            self._restart_backoff.pop(key_id, None)
            return
        # Непредвиденное падение: статус «ошибка», перезапуск с растущей паузой.
        backoff = min(max(30.0, self._restart_backoff.get(key_id, 0) * 2), 600.0)
        self._restart_backoff[key_id] = backoff
        self._restart_at[key_id] = time.monotonic() + backoff
        log.error("worker %s crashed", key_id, exc_info=exc)
        asyncio.get_event_loop().create_task(self._report_crash(key_id, exc, backoff))

    async def _report_crash(self, key_id: int, exc: BaseException, backoff: float) -> None:
        reason = f"Внутренняя ошибка поиска ({type(exc).__name__}: {str(exc)[:200]}). Перезапуск через {int(backoff)} с."
        async with session_scope() as db:
            key = await db.get(SelectelKey, key_id)
            if key:
                key.status, key.status_reason = "error", reason
        bus.publish("key", {"id": key_id, "status": "error", "status_reason": reason})
        await emit(reason, level="error", kind="crash", key_id=key_id)

    async def _supervisor(self) -> None:
        """Раз в 10 с: перезапуски после падений, состояние воркеров на сайт."""
        while True:
            await asyncio.sleep(10)
            try:
                await self.reconcile()
                for worker in list(self.workers.values()):
                    worker._publish()
            except Exception:  # noqa: BLE001
                log.exception("supervisor")

    # ── сигналы от API ─────────────────────────────────────────────────────

    async def restart_key(self, key_id: int) -> None:
        self._restart_at.pop(key_id, None)
        self._restart_backoff.pop(key_id, None)
        await self._stop_worker(key_id)
        await self.reconcile()

    def subnets_changed(self) -> None:
        for worker in self.workers.values():
            worker.targets_dirty = True

    def neighbour_banned(self, banned_key_id: int) -> None:
        """Один аккаунт получил бан за частоту — остальные на всякий случай притормаживают."""
        for key_id, worker in self.workers.items():
            if key_id != banned_key_id:
                worker.governor.on_neighbour_ban()

    async def reset_pace(self, key_id: int) -> None:
        worker = self.workers.get(key_id)
        if worker:
            worker.governor.pace, worker.governor.reason, worker.governor.dirty = 1.0, "", True
            worker.governor.changes.append("темп сброшен вручную до ×1")
            await worker._sync_pace()

    def settings_changed(self) -> None:
        # Паузы и таймауты воркеры читают сами на каждом шаге; цели зависят от регионов/exact_only.
        self.subnets_changed()

    def runtime(self, key_id: int) -> dict[str, Any] | None:
        worker = self.workers.get(key_id)
        task = self.tasks.get(key_id)
        if not worker or not task or task.done():
            return None
        return worker.runtime.to_dict()

    def on_caught(self, caught_id: int) -> None:
        for hook in self._caught_hooks:
            asyncio.get_event_loop().create_task(hook(caught_id))

    def add_caught_hook(self, hook: Any) -> None:
        self._caught_hooks.append(hook)

    # ── прокси ─────────────────────────────────────────────────────────────

    async def proxy_for_key(self, key_id: int) -> Proxy | None:
        """auto: новый случайный доступный прокси, без повторения предыдущего."""
        async with session_scope() as db:
            key = await db.get(SelectelKey, key_id)
            if key is None or key.proxy_mode == "none":
                return None
            if key.proxy_mode == "fixed":
                proxy = await db.get(Proxy, key.proxy_id) if key.proxy_id else None
                return proxy if proxy is not None and proxy.owner_id == key.owner_id else None
            proxies = (await db.execute(select(Proxy).where(Proxy.owner_id == key.owner_id))).scalars().all()
            chosen = choose_random_proxy(proxies, key.proxy_id)
            key.proxy_id = chosen.id if chosen else None
            return chosen

    async def check_proxy(self, proxy_id: int) -> dict[str, Any]:
        async with session_scope() as db:
            proxy = await db.get(Proxy, proxy_id)
            if proxy is None:
                return {}
            url = with_session(decrypt(proxy.url_enc), proxy.session_mode, "trcheck01")
        result = await check_proxy(url, list(settings_store.get("regions") or []) or None)
        async with session_scope() as db:
            proxy = await db.get(Proxy, proxy_id)
            if proxy is None:
                return {}
            was = proxy.status
            proxy.status = result.status
            proxy.exit_ip, proxy.exit_country = result.exit_ip, result.country
            proxy.latency_ms = result.latency_ms
            proxy.regions_ok, proxy.regions_failed = result.regions_ok, result.regions_failed
            proxy.last_error = result.error
            proxy.checked_at = utcnow()
            data = {"id": proxy.id, "status": proxy.status, "exit_ip": proxy.exit_ip,
                    "exit_country": proxy.exit_country, "latency_ms": proxy.latency_ms,
                    "regions_ok": proxy.regions_ok, "regions_failed": proxy.regions_failed,
                    "last_error": proxy.last_error, "checked_at": proxy.checked_at, "owner_id": proxy.owner_id}
        bus.publish("proxy", data)
        if was in ("ok", "partial") and result.status == "dead" and data.get("owner_id") in await panel_owner_ids():
            await emit(f"прокси {proxy.label or proxy.display} перестал работать: {result.error}", level="warn", kind="proxy")
        return data

    async def _proxy_checker(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                async with session_scope() as db:
                    ids = list((await db.execute(select(Proxy.id))).scalars())
                for proxy_id in ids:
                    await self.check_proxy(proxy_id)
                async with session_scope() as db:
                    proxies = (await db.execute(select(Proxy).where(
                        Proxy.owner_id.in_(await panel_owner_ids())))).scalars().all()
                if proxies and all(p.status == "dead" for p in proxies):
                    await notify("alert", "🧯 Ни один прокси не работает — ключи с автовыбором прокси не смогут искать IP.")
            except Exception:  # noqa: BLE001
                log.exception("proxy checker")
            await asyncio.sleep(max(5, int(settings_store.get("proxy_check_interval_min") or 30)) * 60)

    # ── Selectel-клиент для разовых запросов из API (вкладка «IP») ─────────

    async def proxy_url_for_key(self, key_id: int) -> str | None:
        """Прокси-URL с липкой сессией ключа — для разовых запросов (баланс, VDS)."""
        proxy = await self.proxy_for_key(key_id)
        if not proxy:
            return None
        async with session_scope() as db:
            key = await db.get(SelectelKey, key_id)
            if key is None:
                return None
            return with_session(decrypt(proxy.url_enc), proxy.session_mode, f"tr{key.account_id}",
                                key.proxy_session_gen or 0)

    async def client_for_key(self, key_id: int) -> tuple[SelectelClient, bool]:
        """(клиент, нужно_закрыть). Берёт клиент работающего воркера, иначе создаёт новый."""
        worker = self.workers.get(key_id)
        if worker and worker.client:
            return worker.client, False
        proxy = await self.proxy_for_key(key_id)
        async with session_scope() as db:
            key = await db.get(SelectelKey, key_id)
            if key is None:
                raise LookupError("нет такого ключа")
            url = None
            if proxy:
                url = with_session(decrypt(proxy.url_enc), proxy.session_mode, f"tr{key.account_id}",
                                   key.proxy_session_gen or 0)
            if not key.project_id and not key.project_name:
                # Импортированный ключ без проекта: найти его так же, как это делает охота.
                key.project_id, key.project_name = await resolve_project(
                    key.username, decrypt(key.password_enc), key.account_id, url)
            client = SelectelClient(username=key.username, password=decrypt(key.password_enc),
                                    account_id=key.account_id, project_name=key.project_name,
                                    project_id=key.project_id, request_timeout=20, connect_timeout=8, proxy=url)
        return client, True


manager = WorkerManager()
