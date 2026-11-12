"""Деньги: цены облака Selectel, балансы аккаунтов, во что обходятся пойманные IP.

Цены — публичный JSON без авторизации: value = копейки в час, ×7.3 = ₽ в месяц (730 ч).
Баланс — Billing API под IAM-токеном аккаунта (сервисному пользователю нужна роль
с доступом к биллингу; нет доступа — в панели так и пишем, охоту это не трогает).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app import bus, settings_store
from app.core.crypto import decrypt
from app.db.models import AccountBalance, CaughtIP, SelectelKey, Vds, utcnow
from app.db.session import session_scope
from app.engine.resell import SelectelResellClient
from app.notify.telegram import esc, notify, panel_owner_ids

log = logging.getLogger(__name__)

PRICES_URL = "https://api.selectel.ru/prices/vpc?currency=rub"
HOURS_IN_MONTH_FACTOR = 7.3  # коп/ч → ₽/мес
FALLBACK_FIP_KOPH = 25.968384  # ru-*, если прайс недоступен
_cache: dict[tuple[str, str], float] = {}
_cache_at = 0.0
_lock = asyncio.Lock()


def month_rub(kop_per_hour: float) -> float:
    return round(kop_per_hour * HOURS_IN_MONTH_FACTOR, 2)


async def prices(force: bool = False) -> dict[tuple[str, str], float]:
    global _cache_at
    async with _lock:
        if _cache and not force and time.monotonic() - _cache_at < 12 * 3600:
            return _cache
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(PRICES_URL)
                resp.raise_for_status()
                items = resp.json()
            fresh = {(str(i["resource"]), str(i["group"])): float(i["value"])
                     for i in items if isinstance(i, dict) and "resource" in i and i.get("currency") == "rub"}
            if fresh:
                _cache.clear()
                _cache.update(fresh)
                _cache_at = time.monotonic()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.warning("prices: %s", exc)
        return _cache


def price(table: dict[tuple[str, str], float], resource: str, group: str) -> float | None:
    """Цена ресурса в группе (зона ru-2c → регион ru-2 как запасной вариант)."""
    if (resource, group) in table:
        return table[(resource, group)]
    region = re.sub(r"[a-z]$", "", group)
    return table.get((resource, region))


def fip_month(table: dict[tuple[str, str], float], region: str) -> float:
    return month_rub(price(table, "network_floatingips", region) or FALLBACK_FIP_KOPH)


FLAVOR_RE = re.compile(r"^PRC(10|20|50)\.(\d+)-(\d+)$")


def flavor_month(table: dict[tuple[str, str], float], name: str, zone: str) -> float | None:
    """₽/мес за flavor линейки «с долей ядра» (PRC10.1-512 и т. п.); None — нет цены в этой зоне."""
    m = FLAVOR_RE.match(name)
    if not m:
        return None
    share, vcpus, ram = m.group(1), int(m.group(2)), int(m.group(3))
    core = price(table, f"compute_cores_percent_{share}", zone)
    ram_mb = price(table, "compute_ram", zone)
    if core is None or ram_mb is None:
        return None
    return month_rub(core * vcpus + ram_mb * ram)


def disk_month(table: dict[tuple[str, str], float], gb: int, zone: str, kind: str = "basic") -> float | None:
    per_gb = price(table, f"volume_gigabytes_{kind}", zone)
    return month_rub(per_gb * gb) if per_gb is not None else None


def router_month(table: dict[tuple[str, str], float], region: str) -> float:
    return month_rub(price(table, "cloud_router", region) or 27.39726027)


# ── сводка по аккаунтам ──────────────────────────────────────────────────────

async def account_costs() -> dict[str, dict[str, Any]]:
    """Сколько в месяц стоит то, что панель держит на каждом аккаунте: IP + серверы (по всем кабинетам)."""
    table = await prices()
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"ips": 0, "ip_month": 0.0, "vds": 0, "vds_month": 0.0})
    async with session_scope() as db:
        for c in (await db.execute(select(CaughtIP).where(CaughtIP.on_account.is_(True)))).scalars():
            row = out[c.account_id]
            row["ips"] += 1
            row["ip_month"] += fip_month(table, c.region)
        for v in (await db.execute(select(Vds).where(Vds.status.in_(("creating", "active", "error"))))).scalars():
            row = out[v.account_id]
            row["vds"] += 1
            row["vds_month"] += v.month_cost or 0.0
    for row in out.values():
        row["ip_month"] = round(row["ip_month"], 2)
        row["month"] = round(row["ip_month"] + row["vds_month"], 2)
        row["day"] = round(row["month"] / 30.4, 2)
    return dict(out)


def days_left(balance: float | None, hours_left: int | None, day_cost: float) -> float | None:
    # Selectel отдаёт 0 часов, когда трат нет вовсе, — при положительном балансе это не «кончились».
    if hours_left is not None and (hours_left > 0 or (balance is not None and balance <= 0)):
        return round(hours_left / 24, 1)
    if balance is not None and day_cost > 0:
        return round(balance / day_cost, 1)
    return None


# ── балансы ──────────────────────────────────────────────────────────────────

async def refresh_balances(only: set[str] | None = None) -> list[dict[str, Any]]:
    from app.engine.manager import manager  # noqa: PLC0415 — менеджер импортирует движок, цикл импорта

    async with session_scope() as db:
        keys = (await db.execute(select(SelectelKey).order_by(SelectelKey.enabled.desc(), SelectelKey.id))).scalars().all()
        by_account: dict[str, SelectelKey] = {}
        for k in keys:
            if only is not None and k.account_id not in only:
                continue
            if k.account_id and k.password_enc and k.account_id not in by_account:
                by_account[k.account_id] = k
        creds = {acc: (k.id, k.username, decrypt(k.password_enc)) for acc, k in by_account.items()}

    async def one(account_id: str, key_id: int, username: str, password: str) -> None:
        balance, hours, error = None, None, ""
        try:
            proxy = await manager.proxy_url_for_key(key_id)
        except Exception:  # noqa: BLE001
            proxy = None
        client = SelectelResellClient(username=username, password=password, account_id=account_id, proxy=proxy)
        try:
            balance, hours = await client.get_balance()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            error = ("У пользователя ключа нет доступа к биллингу — дайте ему роль с просмотром баланса"
                     if code in (401, 403) else f"Selectel ответил {code}")
        except ValueError:
            error = "Не удалось войти в аккаунт: Selectel не принял пользователя ключа"
        except httpx.TransportError as exc:
            error = f"Selectel не ответил ({type(exc).__name__})"
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {str(exc)[:150]}"
        finally:
            await client.close()
        async with session_scope() as db:
            row = await db.get(AccountBalance, account_id)
            if row is None:
                row = AccountBalance(account_id=account_id, error="")
                db.add(row)
            if not error:
                row.balance, row.hours_left = balance, hours
            row.error, row.checked_at = error[:300], utcnow()

    await asyncio.gather(*(one(acc, *c) for acc, c in creds.items()), return_exceptions=True)
    await warn_low_balances()
    bus.publish("money", {})
    return await balances_view(only)


async def balances_view(only: set[str] | None = None) -> list[dict[str, Any]]:
    """only — номера аккаунтов кабинета (None — все, для владельца панели и фоновых проверок)."""
    costs = await account_costs()
    async with session_scope() as db:
        rows = {b.account_id: b for b in (await db.execute(select(AccountBalance))).scalars()}
        keys = (await db.execute(select(SelectelKey))).scalars().all()
    names: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        names[k.account_id].append(k.name)
    accounts = sorted(set(names) | set(rows) | set(costs))
    if only is not None:
        accounts = [a for a in accounts if a in only]
    out = []
    for acc in accounts:
        b = rows.get(acc)
        cost = costs.get(acc, {"ips": 0, "ip_month": 0.0, "vds": 0, "vds_month": 0.0, "month": 0.0, "day": 0.0})
        balance = b.balance if b else None
        hours = b.hours_left if b else None
        out.append({
            "account_id": acc, "keys": names.get(acc, []), "balance": balance, "hours_left": hours,
            "days_left": days_left(balance, hours, cost["day"]), "error": b.error if b else "",
            "checked_at": b.checked_at if b else None, **cost,
        })
    # Сверху — где есть деньги и адреса; аккаунты без доступа к биллингу и без IP — вниз.
    out.sort(key=lambda r: (r["balance"] is None, -r["ips"], -(r["balance"] or 0), r["account_id"]))
    return out


async def warn_low_balances() -> None:
    try:
        warn_days = float(settings_store.get("balance_warn_days") or 0)
    except (TypeError, ValueError):
        warn_days = 0
    if warn_days <= 0:
        return
    now = utcnow()
    # В бот панели — только аккаунты кабинета владельца; чужие балансы ему не шлём.
    async with session_scope() as db:
        mine = set((await db.execute(select(SelectelKey.account_id).where(
            SelectelKey.owner_id.in_(await panel_owner_ids())))).scalars())
    for row in await balances_view(mine):
        left = row["days_left"]
        if left is None or left >= warn_days:
            continue
        async with session_scope() as db:
            b = await db.get(AccountBalance, row["account_id"])
            if b is None or (b.warned_at and now - b.warned_at < timedelta(hours=20)):
                continue
            b.warned_at = now
        who = ", ".join(row["keys"]) or row["account_id"]
        await notify("money", f"💸 На аккаунте <b>{esc(who)}</b> ({esc(row['account_id'])}) денег на ~{left:g} дн. "
                              f"Баланс {row['balance'] if row['balance'] is not None else '—'} ₽, "
                              f"держим IP: {row['ips']}, ≈{row['month']:g} ₽/мес. "
                              f"Кончатся деньги — Selectel заберёт адреса.")


# ── сроки IP ─────────────────────────────────────────────────────────────────

async def warn_expiring_ips() -> None:
    try:
        warn_days = float(settings_store.get("ip_expiry_warn_days") or 0)
    except (TypeError, ValueError):
        warn_days = 0
    if warn_days <= 0:
        return
    now = utcnow()
    horizon = now + timedelta(days=warn_days)
    lines = []
    async with session_scope() as db:
        rows = (await db.execute(select(CaughtIP).where(
            CaughtIP.expires_at.is_not(None), CaughtIP.expires_at <= horizon,
            CaughtIP.expiry_notified_at.is_(None), CaughtIP.owner_id.in_(await panel_owner_ids())))).scalars().all()
        for c in rows:
            c.expiry_notified_at = now
            left = (c.expires_at - now).total_seconds() / 86400
            when = "срок уже прошёл" if left < 0 else f"осталось {max(left, 0):.0f} дн."
            lines.append(f"<code>{esc(c.ip)}</code>" + (f" · {esc(c.client)}" if c.client else "") + f" — {when}")
    if lines:
        await notify("money", "⏳ <b>Подходит срок IP</b>\n" + "\n".join(lines[:20]))
        bus.publish("caught_update", {})


async def money_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await prices()
            await refresh_balances()
            await warn_expiring_ips()
        except Exception:  # noqa: BLE001
            log.exception("money loop")
        try:
            minutes = max(10, int(settings_store.get("balance_check_min") or 60))
        except (TypeError, ValueError):
            minutes = 60
        await asyncio.sleep(minutes * 60)
