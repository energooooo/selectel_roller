"""Ежедневная сводка в Telegram (09:00 МСК): попытки, поимки, ошибки, проблемные ключи."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import CaughtIP, MetricPoint, SelectelKey, utcnow
from app.db.session import session_scope
from app.notify.telegram import esc, notify

log = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))
SEND_HOUR = 9


async def build_summary() -> str:
    since = utcnow() - timedelta(hours=24)
    async with session_scope() as db:
        sums = (await db.execute(select(
            func.coalesce(func.sum(MetricPoint.attempts), 0),
            func.coalesce(func.sum(MetricPoint.busy), 0),
            func.coalesce(func.sum(MetricPoint.errors + MetricPoint.timeouts + MetricPoint.proxy_errors), 0),
        ).where(MetricPoint.bucket >= since))).one()
        caught = (await db.execute(select(CaughtIP).where(CaughtIP.caught_at >= since))).scalars().all()
        keys = (await db.execute(select(SelectelKey))).scalars().all()
    attempts, busy, errors = (int(v) for v in sums)
    lines = [
        "📊 <b>Сводка за сутки</b>",
        f"Попыток: {attempts:,}".replace(",", " ") + f" · «занято»: {busy:,}".replace(",", " ") + f" · ошибок: {errors}",
        f"Поймано: <b>{len(caught)}</b>" + (": " + ", ".join(f"<code>{esc(c.ip)}</code>" for c in caught[:10]) if caught else ""),
        f"Ключей ловит: {sum(k.enabled for k in keys)} из {len(keys)}",
    ]
    problems = [k for k in keys if k.status in {"error", "banned", "auth_failed"}]
    if problems:
        lines.append("⚠️ Проблемы: " + "; ".join(f"{esc(k.name)} — {esc(k.status_reason[:80])}" for k in problems))
    return "\n".join(lines)


async def daily_loop() -> None:
    while True:
        now = datetime.now(MSK)
        target = now.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await notify("daily", await build_summary())
        except Exception:  # noqa: BLE001
            log.exception("daily summary")
