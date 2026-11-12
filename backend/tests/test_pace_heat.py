"""Регулятор темпа и рейтинг подсетей — без БД и Selectel."""
from __future__ import annotations

import pytest

from app import settings_store
from app.engine.governor import PaceGovernor
from app.engine.heat import scores_for, score
from app.engine.worker import KeyWorker, Target


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    for k, v in {"pace_governor": True, "pace_max": 4.0, "pace_relax_min": 30,
                 "hot_priority": True, "cold_share": 0.35}.items():
        monkeypatch.setitem(settings_store._cache, k, v)


def test_429_slows_down_and_caps() -> None:
    g = PaceGovernor(clock=Clock())
    g.on_rate_limited()
    assert g.pace == 1.5 and g.dirty and "429" in g.reason
    for _ in range(10):
        g.on_rate_limited()
    assert g.pace == 4.0


def test_error_share_slows_down_network_errors_do_not() -> None:
    g = PaceGovernor(clock=Clock())
    for _ in range(30):
        g.observe("timeouts")
        g.observe("proxy_errors")
    assert g.pace == 1.0
    for i in range(24):
        g.observe("errors" if i % 2 else "busy")
    assert g.pace == 1.25


def test_relaxes_after_quiet_period() -> None:
    clock = Clock()
    g = PaceGovernor(2.0, "x", clock=clock)
    g.tick()
    assert g.pace == 2.0  # ещё рано
    clock.t += 31 * 60
    g.tick()
    assert g.pace == 1.7
    for _ in range(10):
        clock.t += 31 * 60
        g.tick()
    assert g.pace == 1.0 and g.reason == ""


def test_ban_sets_floor_even_when_disabled(monkeypatch) -> None:
    monkeypatch.setitem(settings_store._cache, "pace_governor", False)
    g = PaceGovernor(clock=Clock())
    g.on_rate_limited()
    assert g.pace == 1.0
    g.on_ban()
    assert g.pace == 2.0 and g.dirty
    g.tick()  # выключенный регулятор не сбрасывает «после бана»
    assert g.pace == 2.0


def test_delay_scaled() -> None:
    assert PaceGovernor(2.5).delay(10) == 25


def test_heat_scores() -> None:
    rows = [(1, 10_000, 10), (2, 10_000, 0), (3, 100, 0), (4, 10_000, 3)]
    s = scores_for(rows)
    assert s[1].tier == "hot" and s[1].heat > 2
    assert s[2].tier == "cold" and s[2].weight == 0.35
    assert s[3].tier == "new" and s[3].weight == 1.0
    assert s[4].tier == "normal"
    assert score(5000, 0, 0).tier == "normal"  # нигде ничего не ловилось — сравнивать не с чем


def _t(sid: int, heat: float, weight: float) -> Target:
    return Target(region="ru-1", network_id="n", os_subnet_id=f"s{sid}", pool_cidr="1.1.1.0/24", subnet_id=sid,
                  subnet_cidr="1.1.1.0/24", kind="exact", heat=heat, weight=weight)


def test_round_plan_orders_and_skips_cold() -> None:
    w = KeyWorker(1, manager=None)  # type: ignore[arg-type]
    hot, normal, cold = _t(1, 3.0, 1.0), _t(2, 1.0, 1.0), _t(3, 0.2, 0.35)
    plan = w.round_plan([normal, cold, hot], rand=lambda: 0.9)
    assert plan == [hot, normal] and w.runtime.skipped_cold == 1
    assert w.round_plan([normal, cold, hot], rand=lambda: 0.1) == [hot, normal, cold]
    assert w.round_plan([cold], rand=lambda: 0.9) == [cold]  # хоть одна цель за круг


def test_round_plan_off(monkeypatch) -> None:
    monkeypatch.setitem(settings_store._cache, "hot_priority", False)
    w = KeyWorker(1, manager=None)  # type: ignore[arg-type]
    targets = [_t(1, 0.2, 0.35), _t(2, 3.0, 1.0)]
    assert w.round_plan(targets, rand=lambda: 0.9) == targets
