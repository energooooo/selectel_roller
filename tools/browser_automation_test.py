#!/usr/bin/env python3
"""Observe Cap.js verification on a page you are authorized to test."""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, ConsoleMessage, Error, Frame, Page, TimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.browser_watch import CAP_WIDGET, open_browser_page  # noqa: E402

log = logging.getLogger("cap-watch")

PREFIX = "[cap-watch] "
EVENTS = {
    "watching": "Наблюдение за виджетами Cap.js начато.",
    "ready": "Виджет Cap.js готов к проверке.",
    "pending": "Виджет Cap.js загружается или временно недоступен.",
    "verifying": "Cap.js выполняет проверку.",
    "done": "Проверка Cap.js завершилась. Токен присутствует.",
    "done-no-token": "Проверка Cap.js завершилась. Токен отсутствует.",
    "error": "Виджет Cap.js сообщил об ошибке.",
}

WATCH_SCRIPT = r"""
(() => {
  const installed = Symbol.for('cap-watch.installed');
  if (window[installed]) return;
  window[installed] = true;

  const states = new WeakMap();
  const report = (event) => console.info('[cap-watch] ' + event);

  function scan() {
    for (const widget of document.querySelectorAll('__CAP_WIDGET__')) {
      if (!states.has(widget)) {
        widget.addEventListener('error', () => report('error'));
      }

      const button = widget.shadowRoot?.querySelector('.captcha');
      const state = button?.getAttribute('data-state');
      let event = 'ready';
      if (state === 'done') event = widget.token ? 'done' : 'done-no-token';
      else if (state === 'verifying') event = 'verifying';
      else if (!button || button.disabled) event = 'pending';

      if (states.get(widget) !== event) report(event);
      states.set(widget, event);
    }
  }

  // The document observer detects new widgets. The timer also covers changes
  // made inside their shadow roots, including attachment of a new shadow root.
  new MutationObserver(scan).observe(document, {childList: true, subtree: true});
  setInterval(scan, __INTERVAL_MS__);
  report('watching');
  scan();
})();
"""


async def install_cap_watch(context: BrowserContext, interval_ms: int = 100) -> None:
    """Install the observer before page creation, including for future navigations."""
    interval_ms = min(max(25, interval_ms), 2_147_483_647)
    script = WATCH_SCRIPT.replace("__CAP_WIDGET__", CAP_WIDGET).replace("__INTERVAL_MS__", str(interval_ms))
    await context.add_init_script(script)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="HTTP(S) address of the page under test")
    parser.add_argument("--interval-ms", type=int, default=100, help="Fallback scan interval (minimum 25 ms)")
    parser.add_argument("--headless", action="store_true", help="Run Chromium without a visible window")
    parser.add_argument("--verify-timeout", type=float, metavar="SECONDS",
                        help="Exit successfully after a completed verification with a token")
    parser.add_argument("--verbose", action="store_true", help="Enable browser diagnostic logging")
    args = parser.parse_args(argv)

    try:
        url = urlsplit(args.url)
        valid_url = url.scheme in {"http", "https"} and bool(url.hostname)
    except ValueError:
        valid_url = False
    if not valid_url:
        parser.error("URL must be an HTTP(S) address with a hostname")
    timeout = args.verify_timeout
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        parser.error("--verify-timeout must be a positive finite number")
    return args


async def run(args: argparse.Namespace) -> int:
    async def prepare(context: BrowserContext) -> None:
        await install_cap_watch(context, args.interval_ms)

    async with open_browser_page(headless=args.headless, prepare=prepare) as page:
        return await watch_page(page, args)


async def watch_page(page: Page, args: argparse.Namespace) -> int:
    stopped = asyncio.Event()
    verified = False
    crashed = False

    def on_console(message: ConsoleMessage) -> None:
        nonlocal verified
        if not message.text.startswith(PREFIX):
            return
        event = message.text[len(PREFIX):]
        if event not in EVENTS:
            return
        log.log(logging.WARNING if event == "error" else logging.INFO, EVENTS[event])
        if event == "done":
            verified = True
            if args.verify_timeout is not None:
                stopped.set()

    def on_crash(*_) -> None:
        nonlocal crashed
        crashed = True
        stopped.set()

    def on_navigation(frame: Frame) -> None:
        if frame == page.main_frame:
            log.info("Переход основного фрейма.")

    page.on("console", on_console)
    page.on("framenavigated", on_navigation)
    page.on("pageerror", lambda _: log.debug("Ошибка JavaScript на странице."))
    page.on("close", lambda *_: stopped.set())
    page.on("crash", on_crash)
    page.context.on("close", lambda *_: stopped.set())

    try:
        response = await page.goto(args.url, wait_until="domcontentloaded")
    except TimeoutError:
        log.error("Истекло время ожидания загрузки страницы.")
        return 1
    except Error:
        log.error("Не удалось открыть страницу. Проверьте адрес и доступность сайта.")
        return 1
    if response and response.status >= 400:
        log.warning("Страница вернула HTTP %s.", response.status)

    log.info("Для остановки закройте страницу или нажмите Ctrl+C.")
    try:
        await asyncio.wait_for(stopped.wait(), timeout=args.verify_timeout)
    except asyncio.TimeoutError:
        log.error("За отведённое время Cap.js не сообщил об успешной проверке с токеном.")
        return 1
    if crashed:
        log.error("Страница Chromium аварийно завершила работу.")
        return 1
    if args.verify_timeout is not None and not verified:
        log.error("Браузер закрыт до завершения проверки.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("Наблюдение остановлено.")
        return 130
    except Error:
        log.error("Не удалось начать или завершить сеанс Playwright. Проверьте установку Chromium.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
