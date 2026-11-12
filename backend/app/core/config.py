"""Настройки процесса. Здесь только то, без чего сервер не стартует.

Всё остальное (ключи Selectel, прокси, подсети, паузы, Telegram) хранится в БД
и меняется из панели на лету.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR.parent / ".env", extra="ignore")

    # Ключ Fernet для шифрования секретов в БД. Потерять = потерять доступ к паролям ключей.
    app_secret_key: str = Field(default="", alias="APP_SECRET_KEY")
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{(BACKEND_DIR / 'data' / 'roller.db').as_posix()}",
        alias="DATABASE_URL",
    )
    # Прод за HTTPS: cookie только Secure, HSTS включён.
    production: bool = Field(default=False, alias="PRODUCTION")
    mailyget_list_url: str = Field(default="https://www.mailyget.com/api/emails/{email}?headers=true", alias="MAILYGET_LIST_URL")
    mailyget_detail_url_template: str = Field(default="https://www.mailyget.com/api/emails/{email}/{id}", alias="MAILYGET_DETAIL_URL_TEMPLATE")
    mailyget_authorization: str = Field(default="", alias="MAILYGET_AUTHORIZATION")
    mailyget_cookie: str = Field(default="", alias="MAILYGET_COOKIE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
