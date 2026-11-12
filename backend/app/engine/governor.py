"""Регулятор темпа ключа: сам замедляет охоту, пока Selectel не забанил аккаунт, и осторожно разгоняет обратно.

Темп — множитель пауз из настроек (×1 = как в настройках, ×2 = паузы вдвое длиннее).
Сигналы на замедление:
- 429 «слишком много запросов» — прямой намёк Selectel, ×1.5;
- доля ошибок Selectel (не сети и не прокси) за последние попытки выше порога — ×1.25;
- бан аккаунта (403 rule:create_floatingip) — после снятия бана ключ стартует не быстрее ×2,
  а соседние ключи на всякий случай притормаживают ×1.25.
Разгон: если долго (pace_relax_min) не было ни одного сигнала — ×0.85, но не ниже ×1.

Модуль без БД и сети: состояние сохраняет воркер, когда `dirty`.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from app import settings_store

WINDOW = 40  # сколько последних попыток смотреть
MIN_SAMPLES = 12  # меньше — выводов о доле ошибок не делаем
ERROR_SHARE = 0.25
# Ответ Selectel по существу (адрес выдан / занято) — всё хорошо; errors — Selectel ответил ошибкой.
# timeouts / proxy_errors — сеть: их лечит пауза региона и смена прокси, а не темп.
GOOD = {"busy", "caught", "misses"}


def _max_pace() -> float:
    try:
        return max(1.0, float(settings_store.get("pace_max") or 4.0))
    except (TypeError, ValueError):
        return 4.0


def _relax_seconds() -> float:
    try:
        return max(60.0, float(settings_store.get("pace_relax_min") or 30) * 60)
    except (TypeError, ValueError):
        return 1800.0


class PaceGovernor:
    def __init__(self, pace: float = 1.0, reason: str = "", clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.pace = max(1.0, float(pace or 1.0))
        self.reason = reason or ""
        self.window: deque[str] = deque(maxlen=WINDOW)
        self.last_signal = clock()
        self.dirty = False
        self.changes: list[str] = []  # человеческие строки для журнала, воркер забирает их сам

    @staticmethod
    def enabled() -> bool:
        return bool(settings_store.get("pace_governor"))

    # ── сигналы ───────────────────────────────────────────────────────────

    def _slow(self, factor: float, reason: str) -> None:
        self.last_signal = self.clock()
        new = min(_max_pace(), round(self.pace * factor, 2))
        if new <= self.pace:
            self.reason = reason  # уже на потолке — обновим только причину
            return
        self.pace, self.reason, self.dirty = new, reason, True
        self.changes.append(f"темп замедлен до ×{new:g}: {reason}")

    def on_rate_limited(self) -> None:
        if self.enabled():
            self._slow(1.5, "Selectel просил снизить частоту (429)")

    def on_ban(self) -> None:
        """Аккаунт забанен — после разбана стартовать осторожно (сохраняется в БД даже при выключенном регуляторе)."""
        self.last_signal = self.clock()
        new = min(_max_pace(), max(2.0, round(self.pace * 1.5, 2)))
        self.pace, self.reason, self.dirty = new, "был бан аккаунта за частые запросы", True
        self.changes.append(f"после бана ключ будет искать медленнее: ×{new:g}")

    def on_neighbour_ban(self) -> None:
        if self.enabled():
            self._slow(1.25, "соседний аккаунт получил бан за частоту")

    def observe(self, outcome: str) -> None:
        if not self.enabled():
            return
        self.window.append(outcome)
        if len(self.window) < MIN_SAMPLES:
            return
        errors = sum(1 for o in self.window if o == "errors")
        if errors / len(self.window) > ERROR_SHARE:
            self.window.clear()
            self._slow(1.25, f"много ошибок Selectel ({errors} из последних попыток)")

    def tick(self) -> None:
        """Раз в круг: долго тихо — разгоняемся обратно к ×1."""
        if self.pace <= 1.0 or not self.enabled():
            if not self.enabled() and self.pace > 1.0 and self.reason != "был бан аккаунта за частые запросы":
                # Регулятор выключили — возвращаем темп из настроек.
                self.pace, self.reason, self.dirty = 1.0, "", True
                self.changes.append("регулятор выключен, темп как в настройках")
            return
        if self.clock() - self.last_signal < _relax_seconds():
            return
        self.last_signal = self.clock()
        new = max(1.0, round(self.pace * 0.85, 2))
        if new < 1.05:
            new = 1.0
        self.pace, self.dirty = new, True
        self.reason = "" if new == 1.0 else "разгоняюсь: давно без сигналов от Selectel"
        self.changes.append(f"темп ускорен до ×{new:g}: давно без ошибок")

    def delay(self, seconds: float) -> float:
        return seconds * self.pace

    def take_changes(self) -> list[str]:
        changes, self.changes = self.changes, []
        return changes
