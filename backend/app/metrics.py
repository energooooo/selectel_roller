"""Метрики охоты: счётчики по минутам на ключ и регион.

Движок дёргает record() на каждую попытку — это просто инкремент в памяти.
Раз в 30 с накопленное сбрасывается в metric_points (суммированием, так что
повторный сброс в ту же минуту не теряет данные).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.models import MetricPoint
from app.db.session import session_scope

log = logging.getLogger(__name__)
FIELDS = ("attempts", "busy", "misses", "errors", "timeouts", "proxy_errors", "caught")
RETENTION = timedelta(days=30)

_pending: dict[tuple[datetime, int, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
# Скользящее окно попыток для «скорости сейчас»: (monotonic, key_id).
_recent: deque[tuple[float, int]] = deque(maxlen=50_000)


def _bucket(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(second=0, microsecond=0)


def record(key_id: int, region: str, outcome: str, latency_ms: float | None = None) -> None:
    """Одна попытка create_floatingip и её исход (busy / misses / errors / timeouts / proxy_errors / caught)."""
    point = _pending[(_bucket(), key_id, region)]
    point["attempts"] += 1
    if outcome in FIELDS and outcome != "attempts":
        point[outcome] += 1
    _recent.append((time.monotonic(), key_id))
    if latency_ms is not None:
        point["latency_sum_ms"] += latency_ms
        point["latency_count"] += 1


def rate_per_minute(key_id: int | None = None, window_s: float = 300.0) -> float:
    """Попыток в минуту за последние window_s секунд (по ключу или всего)."""
    cutoff = time.monotonic() - window_s
    count = sum(1 for ts, k in _recent if ts >= cutoff and (key_id is None or k == key_id))
    return round(count * 60.0 / window_s, 2)


async def flush() -> None:
    if not _pending:
        return
    batch = dict(_pending)
    _pending.clear()
    async with session_scope() as db:
        for (bucket, key_id, region), values in batch.items():
            row = (await db.execute(select(MetricPoint).where(
                MetricPoint.bucket == bucket, MetricPoint.key_id == key_id, MetricPoint.region == region,
            ))).scalar_one_or_none()
            if row is None:
                row = MetricPoint(bucket=bucket, key_id=key_id, region=region)
                for name in (*FIELDS, "latency_sum_ms", "latency_count"):
                    setattr(row, name, 0)
                db.add(row)
            for name, value in values.items():
                setattr(row, name, (getattr(row, name) or 0) + (int(value) if name != "latency_sum_ms" else value))


async def prune() -> None:
    async with session_scope() as db:
        await db.execute(delete(MetricPoint).where(MetricPoint.bucket < _bucket() - RETENTION))


async def flusher() -> None:
    tick = 0
    while True:
        await asyncio.sleep(30)
        try:
            await flush()
            tick += 1
            if tick % 120 == 0:  # раз в час
                await prune()
        except Exception:  # noqa: BLE001
            log.exception("metrics flush failed")
