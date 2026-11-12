"""Применение миграций Alembic при старте сервера и из CLI."""
from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config

from app.core.config import BACKEND_DIR


def _upgrade() -> None:
    (BACKEND_DIR / "data").mkdir(exist_ok=True)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    # Логгер настраивает приложение — fileConfig из alembic.ini его бы перетёр.
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


async def upgrade_db() -> None:
    # env.py сам крутит asyncio.run — поэтому в отдельном потоке, вне текущего цикла.
    await asyncio.to_thread(_upgrade)
