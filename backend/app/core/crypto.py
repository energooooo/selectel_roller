"""Шифрование секретов в БД (пароли ключей, токены, прокси, 2FA-секреты).

Ключ — APP_SECRET_KEY (Fernet). Если он не задан, при локальной разработке ключ
генерируется один раз и кладётся в data/secret.key рядом с базой. На проде ключ
обязан прийти из env: без него сервер не стартует.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import BACKEND_DIR, get_settings

log = logging.getLogger(__name__)
_LOCAL_KEY_PATH = BACKEND_DIR / "data" / "secret.key"


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.app_secret_key.strip()
    if not key:
        if settings.production:
            raise RuntimeError("APP_SECRET_KEY не задан — на проде без него старт запрещён")
        if _LOCAL_KEY_PATH.exists():
            key = _LOCAL_KEY_PATH.read_text(encoding="ascii").strip()
        else:
            _LOCAL_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key().decode()
            _LOCAL_KEY_PATH.write_text(key, encoding="ascii")
            log.warning("APP_SECRET_KEY не задан: создан локальный ключ %s", _LOCAL_KEY_PATH)
    return Fernet(key.encode())


def encrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Не удалось расшифровать секрет: APP_SECRET_KEY не тот, что при сохранении") from exc
