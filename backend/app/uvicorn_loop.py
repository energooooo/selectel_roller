"""Use a Windows event loop that supports Playwright's driver subprocess."""

import asyncio
import sys


def loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.SelectorEventLoop()


def serve_dev() -> None:
    """Start each dev worker with its own listening socket on Windows."""
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8200, timeout_graceful_shutdown=2)
