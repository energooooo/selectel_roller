"""Настройки панели: одна строка в БД + копия в памяти.

Движок читает значения из памяти на каждом шаге, поэтому сохранение в панели
действует сразу, без перезапуска воркеров.
"""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select

from app.core.crypto import decrypt, encrypt
from app.db.models import AppSettings
from app.db.session import session_scope

# Темп по умолчанию соответствует настройкам LatencyLab.
DEFAULTS: dict[str, Any] = {
    "busy_delay_min": 5.0,  # пауза после попытки, с
    "busy_delay_max": 5.0,
    "round_delay_min": 120.0,  # пауза между кругами, с
    "round_delay_max": 180.0,
    "long_pause_every": 0,  # длинная пауза каждые N кругов (0 = выкл)
    "long_pause_min": 720.0,
    "long_pause_max": 1080.0,
    "ip_goal": 1,  # сколько IP поймать на ключ, потом ключ останавливается
    "request_timeout": 15.0,
    "connect_timeout": 8.0,
    "start_stagger": 20.0,  # интервал между запусками ключей, с (0 = все сразу)
    "max_consecutive_errors": 30,  # выключать ключ после N ошибок подряд
    "region_fail_threshold": 3,  # таймаутов подряд до паузы региона
    "region_pause_base": 20.0,
    "region_pause_max": 180.0,
    "proxy_rotate_after": 4,  # транспортных сбоев подряд в ≥2 регионах → новая прокси-сессия
    "proxy_check_interval_min": 30,  # фоновая проверка прокси
    "regions": ["ru-1", "ru-2", "ru-3", "ru-9"],
    "exact_only": True,  # только подсети, целиком лежащие в белом списке (без удалений)
    # Регулятор темпа (app.engine.governor)
    "pace_governor": True,
    "pace_max": 4.0,  # во сколько раз максимум можно растянуть паузы
    "pace_relax_min": 30,  # минут без сигналов — и темп ускоряется на ступень
    # Рейтинг подсетей (app.engine.heat)
    "hot_priority": True,  # горячие — первыми, холодные — не каждый круг
    "cold_share": 0.35,  # в какой доле кругов обходить холодные подсети
    # Деньги
    "balance_check_min": 60,  # как часто спрашивать баланс аккаунтов
    "balance_warn_days": 3,  # предупреждать, когда денег осталось меньше чем на N дней
    "ip_expiry_warn_days": 3,  # напоминать о сроке IP за N дней
    "vds_disk_gb": 5,
    "vds_image": "Ubuntu 24.04 LTS 64-bit",
    # Telegram
    "tg_bot_token_enc": None,
    "tg_chat_id": "",
    "tg_admin_chat_id": "",
    "notify_caught": True,
    "notify_alerts": True,
    "notify_logins": True,
    "notify_rotations": False,
    "notify_daily": True,
    "notify_money": True,  # мало денег на аккаунте, срок IP подходит
    # Проверка пойманных IP
    "bsboard_url": "",
    "bsboard_key_enc": None,
    "bsboard_recheck_hours": 6,
}

SECRET_FIELDS = {"tg_bot_token_enc": "tg_bot_token", "bsboard_key_enc": "bsboard_key"}

_cache: dict[str, Any] = copy.deepcopy(DEFAULTS)


def get(name: str) -> Any:
    return _cache.get(name, DEFAULTS.get(name))


def secret(name: str) -> str:
    """Расшифрованное значение секретной настройки по публичному имени (tg_bot_token, bsboard_key)."""
    for enc_name, public in SECRET_FIELDS.items():
        if public == name:
            return decrypt(_cache.get(enc_name))
    raise KeyError(name)


def snapshot() -> dict[str, Any]:
    return copy.deepcopy(_cache)


def public_view() -> dict[str, Any]:
    """Настройки для фронта: секреты заменены флагом «задан»."""
    data = {k: v for k, v in _cache.items() if k not in SECRET_FIELDS}
    for enc_name, public in SECRET_FIELDS.items():
        data[f"{public}_set"] = bool(_cache.get(enc_name))
    return data


async def load() -> None:
    async with session_scope() as db:
        row = await db.get(AppSettings, 1)
        if row is None:
            row = AppSettings(id=1, data={})
            db.add(row)
        merged = {**copy.deepcopy(DEFAULTS), **(row.data or {})}
        _cache.clear()
        _cache.update(merged)


async def update(changes: dict[str, Any]) -> dict[str, Any]:
    """Применяет изменения (секреты — открытым текстом по публичным именам; "" = стереть)."""
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        enc_name = next((e for e, p in SECRET_FIELDS.items() if p == key), None)
        if enc_name:
            clean[enc_name] = encrypt(value) if value else None
        elif key in DEFAULTS and key not in SECRET_FIELDS:
            clean[key] = value
    async with session_scope() as db:
        row = (await db.execute(select(AppSettings).where(AppSettings.id == 1))).scalar_one_or_none()
        if row is None:
            row = AppSettings(id=1, data={})
            db.add(row)
        row.data = {**(row.data or {}), **clean}
    _cache.update(clean)
    return public_view()
