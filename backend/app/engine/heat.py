"""Рейтинг подсетей: где адреса освобождаются чаще.

Частота поимок на попытку сглаживается к средней по всем подсетям (байесовская оценка):
новая подсеть без истории считается средней, а не холодной. heat = 1 — как в среднем,
2 — вдвое чаще. «Горячие» охота обходит первыми, «холодные» — не каждый круг
(доля кругов = cold_share): общий поток запросов от этого только меньше, риск бана не растёт.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.db.models import Subnet

PRIOR_ATTEMPTS = 2000  # вес «средней» оценки в попытках
HOT_HEAT, HOT_MIN_CAUGHT = 1.5, 2
COLD_HEAT, COLD_MIN_ATTEMPTS = 0.5, 3000


@dataclass
class Heat:
    heat: float
    tier: str  # hot / normal / cold / new
    weight: float  # доля кругов, в которых подсеть обходится


def score(attempts: int, caught: int, avg_rate: float) -> Heat:
    attempts, caught = max(0, attempts or 0), max(0, caught or 0)
    if avg_rate <= 0:
        # Ещё ни одной поимки нигде — сравнивать не с чем.
        return Heat(1.0, "new" if attempts < COLD_MIN_ATTEMPTS else "normal", 1.0)
    rate = (caught + avg_rate * PRIOR_ATTEMPTS) / (attempts + PRIOR_ATTEMPTS)
    heat = round(rate / avg_rate, 2)
    cold_share = _cold_share()
    if heat >= HOT_HEAT and caught >= HOT_MIN_CAUGHT:
        return Heat(heat, "hot", 1.0)
    if heat <= COLD_HEAT and attempts >= COLD_MIN_ATTEMPTS:
        return Heat(heat, "cold", cold_share)
    if attempts < 500 and caught == 0:
        return Heat(heat, "new", 1.0)
    return Heat(heat, "normal", 1.0)


def _cold_share() -> float:
    try:
        return min(1.0, max(0.05, float(settings_store.get("cold_share") or 0.35)))
    except (TypeError, ValueError):
        return 0.35


def scores_for(rows: list[tuple[int, int, int]]) -> dict[int, Heat]:
    """rows: (subnet_id, attempts, caught)."""
    total_attempts = sum(a for _, a, _ in rows)
    total_caught = sum(c for _, _, c in rows)
    avg = total_caught / total_attempts if total_attempts and total_caught else 0.0
    return {sid: score(a, c, avg) for sid, a, c in rows}


async def subnet_scores(db: AsyncSession) -> dict[int, Heat]:
    rows = (await db.execute(select(Subnet.id, Subnet.attempts_total, Subnet.caught_total))).all()
    return scores_for([(r[0], r[1] or 0, r[2] or 0) for r in rows])
