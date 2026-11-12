"""Observe sign-in and Cap.js state in an isolated Playwright Chromium window.

Optional automatic Cap.js smoke check is enabled by default via CAP_LOCAL_TEST_AUTORUN.
This is not a production CAPTCHA bypass or an authentication substitute.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from importlib.util import find_spec
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.browser_watch import CAP_WIDGET, open_browser_page
from app.core.config import get_settings

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)
PANEL_URL = os.getenv("BROWSER_ONBOARD_PANEL_URL", "https://my.selectel.ru/login")

CAP_LOCAL_TEST_AUTORUN = os.getenv("CAP_LOCAL_TEST_AUTORUN", "1") == "1"
CAP_TEST_ALLOWED_HOSTS = {
    h.strip().lower()
    for h in os.getenv("CAP_TEST_ALLOWED_HOSTS", "").split(",")
    if h.strip()
}

SESSION_TIMEOUT = 300
TERMINAL = {"closed", "expired", "cancelled", "error"}
MESSAGES = {
    "starting": "Открываем окно Chromium…",
    "loading": "Окно Chromium открыто. Ожидаем форму входа…",
    "navigation_error": "Страница пока недоступна. Попробуйте обновить её в Chromium.",
    "login": "Войдите в панель управления в открытом окне Chromium.",
    "account_mismatch": "В Chromium указан другой номер аккаунта. Проверьте выбранный аккаунт.",
    "captcha": "Ожидаем прохождения Cap.js в открытом окне Chromium.",
    "captcha_solved": "Cap.js пройдена, ожидаем продолжения авторизации.",
    "authenticated": "Вход обнаружен. Продолжите работу в панели управления.",
    "closed": "Окно Chromium закрыто.",
    "expired": "Время ожидания истекло. Можно открыть новое окно.",
    "cancelled": "Окно входа закрыто.",
    "error": "Не удалось открыть или продолжить вход. Проверьте, что Chromium для Playwright установлен.",
}
OTHER_CAPTCHA = (
    'iframe[src*="/recaptcha/api2/bframe"], '
    'iframe[src*="/recaptcha/enterprise/bframe"], iframe[title*="reCAPTCHA challenge" i], '
    'iframe[src*="/recaptcha/"][src*="/anchor"][src*="size=normal"], '
    'iframe[src*="/recaptcha/"][src*="/anchor"][src*="size=compact"]'
)
CAP_TOKEN = 'input[name="cap-token"]'
LOGIN_FIELDS = 'input[autocomplete="username"], input[type="password"], input[autocomplete="one-time-code"]'
ACCOUNT_FIELD = 'input#username[name="username"][autocomplete="username"][type="text"]'


@dataclass
class BrowserJob:
    id: str
    owner: int
    account_id: str
    stage: str = "starting"
    authenticated: bool = False
    updated: float = field(default_factory=time.monotonic)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.updated = time.monotonic()
        self.authenticated |= stage == "authenticated"

    def view(self) -> dict:
        return {"id": self.id, "account_id": self.account_id, "stage": self.stage, "message": MESSAGES[self.stage],
                "authenticated": self.authenticated, "finished": self.stage in TERMINAL}


class BrowserBusy(Exception):
    pass


jobs: dict[str, BrowserJob] = {}


def available() -> bool:
    desktop = sys.platform in {"win32", "darwin"} or bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))
    return not get_settings().production and desktop and find_spec("playwright") is not None


async def try_cap_smoke_check(page: Page) -> bool:
    if not CAP_LOCAL_TEST_AUTORUN:
        return False

    if CAP_TEST_ALLOWED_HOSTS and (urlsplit(page.url).hostname or "").lower() not in CAP_TEST_ALLOWED_HOSTS:
        return False

    try:
        widget = page.locator('capjs-widget cap-widget').first
        if not await widget.is_visible():
            return False

        already_clicked = await widget.evaluate("el => el.dataset.capLocalTestClicked === '1'")
        if already_clicked:
            return False

        control = widget.locator('.captcha').first
        if not await control.is_visible():
            return False

        state = await control.get_attribute('data-state')
        if state in ('done', 'verifying'):
            return False

        box = await control.bounding_box()
        if not box:
            return False

        start_x = box["x"] - 200
        start_y = box["y"] - 200
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(0.2)
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=15)
        await asyncio.sleep(0.3)

        await widget.evaluate("el => el.dataset.capLocalTestClicked = '1'")
        await control.click(force=True, delay=100)

        await asyncio.sleep(1)
        new_state = await control.get_attribute('data-state')
        log.info(f"[DEBUG CAP] Состояние ПОСЛЕ клика: {new_state}")

        return True
    except Exception as e:
        log.error(f"[DEBUG CAP] Ошибка в процессе клика: {type(e).__name__} - {e}")
        return False


async def page_stage(page: Page, account_id: str) -> str | None:
    url = urlsplit(page.url)
    panel = urlsplit(PANEL_URL)
    if (url.scheme, url.netloc) != (panel.scheme, panel.netloc):
        return None
    if url.path.rstrip("/") == panel.path.rstrip("/"):
        field = page.locator(ACCOUNT_FIELD).filter(visible=True).first
        if await field.is_visible():
            if await field.is_editable(timeout=750):
                should_fill = await field.evaluate("""(input) => {
                    const checked = Symbol.for('toffee-roller.account-prefill');
                    if (input[checked]) return false;
                    input[checked] = true;
                    return input.value === '';
                }""", timeout=750)
                if should_fill:
                    await field.fill(account_id, timeout=750)
            entered = (await field.input_value(timeout=750)).strip()
            if entered and entered != account_id:
                return "account_mismatch"

    account_name = re.compile(rf"(?<!\d){re.escape(account_id)}(?!\d)")
    account_menu = page.get_by_role("button", name=account_name)
    if await account_menu.filter(visible=True).first.is_visible():
        return "authenticated"

    cap = page.locator(CAP_WIDGET).filter(visible=True).first
    if await cap.count() and await cap.is_visible():
        token = cap.locator(CAP_TOKEN).first
        if await token.count():
            if (await token.input_value(timeout=750)).strip():
                return "captcha_solved"
        return "captcha"

    if await page.locator(OTHER_CAPTCHA).filter(visible=True).first.is_visible():
        return "captcha"
    if await page.locator(LOGIN_FIELDS).filter(visible=True).first.is_visible():
        return "login"
    return None


async def run(job: BrowserJob, headless: bool = False, proxy: dict | None = None) -> None:
    from playwright.async_api import Error, TimeoutError as NavigationTimeout

    try:
        async with asyncio.timeout(SESSION_TIMEOUT):
            async with open_browser_page(headless=headless, proxy=proxy) as page:
                job.set_stage("loading")
                try:
                    await page.goto(PANEL_URL, wait_until="domcontentloaded", timeout=20_000)
                except NavigationTimeout:
                    job.set_stage("navigation_error")
                except Error as exc:
                    log.warning("Initial browser navigation failed: %s", type(exc).__name__)
                    job.set_stage("navigation_error")

                while pages := page.context.pages:
                    if not job.authenticated:
                        for candidate in reversed(pages):
                            try:
                                # 1. Пытаемся автоматически нажать капчу
                                if await try_cap_smoke_check(candidate):
                                    log.info("Cap.js test verification started")

                                stage = await page_stage(candidate, job.account_id)

                                # 2. Если капча решена, но мы всё ещё на странице логина, жмем Войти
                                if stage in ("captcha_solved", "login"):
                                    try:
                                        btn = candidate.locator('button.ant-btn-primary')
                                        if await btn.is_visible():
                                            await btn.click(force=True, timeout=2000)
                                            log.info("Нажата кнопка Войти")
                                            await asyncio.sleep(2) # Даем время на отправку
                                            stage = await page_stage(candidate, job.account_id)
                                    except Exception:
                                        pass

                            except Error:
                                continue

                            if stage:
                                if stage != job.stage:
                                    log.info("Browser sign-in state: %s -> %s", job.stage, stage)
                                    job.set_stage(stage)
                                break
                    await asyncio.sleep(0.1)
                job.set_stage("closed")
    except asyncio.CancelledError:
        job.set_stage("cancelled")
        raise
    except TimeoutError:
        job.set_stage("expired")
    except (Error, OSError) as exc:
        log.warning("Browser sign-in stopped: %s", type(exc).__name__)
        job.set_stage("error")
    except Exception as exc:
        log.error("Browser sign-in failed: %s", type(exc).__name__)
        job.set_stage("error")


def start(owner: int, account_id: str, headless: bool = False, proxy: dict | None = None) -> BrowserJob:
    for job_id, job in list(jobs.items()):
        if job.stage in TERMINAL and time.monotonic() - job.updated > 900:
            del jobs[job_id]

    active = [job for job in jobs.values() if job.task is not None and not job.task.done()]
    for job in active:
        if (job.owner == owner and job.account_id == account_id and job.stage not in TERMINAL
                and job.task is not None and not job.task.cancelling()):
            return job
    if len(active) >= 4:
        raise BrowserBusy("Уже открыто четыре окна входа. Закройте одно из них и повторите попытку.")

    job = BrowserJob(secrets.token_urlsafe(24), owner, account_id)
    jobs[job.id] = job
    job.task = asyncio.create_task(run(job, headless=headless, proxy=proxy), name="browser-onboarding")
    return job


async def cancel(job: BrowserJob) -> None:
    if job.task is not None and not job.task.done() and job.stage not in TERMINAL:
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        if job.stage not in TERMINAL:
            job.set_stage("cancelled")


async def shutdown() -> None:
    await asyncio.gather(*(cancel(job) for job in jobs.values()))
    jobs.clear()