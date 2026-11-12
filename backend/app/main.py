from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders

from app import __version__, browser_onboarding, settings_store
from app.api import data, extension, finance, keys, proxies, subnets
from app.api.health import router as health_router
from app.auth.routes import ensure_setup_token, router as auth_router
from app.checks import bsboard
from app.core.config import get_settings
from app.core.crypto import encrypt
from app.db.migrate import upgrade_db
from app.db.session import SessionLocal
from app.engine.billing import money_loop
from app.engine.manager import manager
from app.notify.daily import daily_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await upgrade_db()
    encrypt("self-check")  # ключ шифрования есть и рабочий — иначе падаем сразу, а не на первом ключе
    await settings_store.load()
    async with SessionLocal() as db:
        await ensure_setup_token(db)
    manager.add_caught_hook(bsboard.on_caught)
    await manager.start()
    background = [asyncio.create_task(bsboard.rechecker()), asyncio.create_task(daily_loop()),
                  asyncio.create_task(money_loop())]
    try:
        yield
    finally:
        await browser_onboarding.shutdown()
        for task in background:
            task.cancel()
        await manager.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Swagger только локально: на проде API наружу не описываем.
    app = FastAPI(
        title="Toffee Roller",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.production else "/api/openapi.json",
    )
    app.include_router(health_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    for module in (keys, proxies, subnets, data, finance):
        app.include_router(module.router, prefix="/api")
    app.include_router(extension.router, prefix="/api")
    app.include_router(extension.accounts_router, prefix="/api")

    app.add_middleware(SecurityHeaders, hsts=settings.production)
    return app


class SecurityHeaders:
    """Заголовки безопасности. Чистый ASGI — не мешает потоку SSE, в отличие от BaseHTTPMiddleware."""

    def __init__(self, app, hsts: bool = False) -> None:  # noqa: ANN001
        self.app, self.hsts = app, hsts

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        is_stream = scope.get("path") == "/api/stream"

        async def send_with_headers(message):  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                if not is_stream:
                    headers.setdefault("Cache-Control", "no-store")
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, send_with_headers)


app = create_app()
