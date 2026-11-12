"""Очередь запуска: ключи входят в Selectel не разом, а с интервалом start_stagger."""
from app import settings_store
from app.engine.manager import WorkerManager


def test_keys_get_increasing_start_delays(monkeypatch):
    monkeypatch.setitem(settings_store._cache, "start_stagger", 20.0)
    manager = WorkerManager()
    delays = [manager.reserve_start_slot() for _ in range(5)]
    assert delays[0] < 1
    for prev, cur in zip(delays, delays[1:]):
        assert 15 - 0.1 <= cur - prev <= 25 + 0.1  # 20 с ± 25 %


def test_zero_stagger_starts_everything_at_once(monkeypatch):
    monkeypatch.setitem(settings_store._cache, "start_stagger", 0)
    manager = WorkerManager()
    assert all(manager.reserve_start_slot() < 0.1 for _ in range(5))
