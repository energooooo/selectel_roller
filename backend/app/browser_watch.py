"""Shared Playwright lifecycle and widget selector for browser observation."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

CAP_WIDGET = "cap-widget"

@asynccontextmanager
async def open_browser_page(
    *,
    headless: bool,
    proxy: dict | None = None,
    prepare: Callable[[BrowserContext], Awaitable[None]] | None = None,
) -> AsyncIterator[Page]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            timeout=15_000,
            proxy=proxy,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        try:
            context = await browser.new_context(
                accept_downloads=False,
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)

            if prepare is not None:
                await prepare(context)
            yield await context.new_page()
        finally:
            if browser.is_connected():
                await browser.close()