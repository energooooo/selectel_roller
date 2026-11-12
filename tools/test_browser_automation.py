from __future__ import annotations

import unittest
from urllib.parse import urlsplit

from playwright.async_api import Route, async_playwright, expect

from browser_automation_test import PREFIX, install_cap_watch

PANEL_URL = "http://127.0.0.1:3200"
SELECTEL_LOGIN = "https://my.selectel.ru/login"


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        playwright = await self.enterAsyncContext(async_playwright())
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.context = await browser.new_context(service_workers="block")
        self.addAsyncCleanup(self.context.close)

    async def test_cap_watch_detects_dynamic_widget_and_restarts_after_reload(self) -> None:
        url = "http://127.0.0.1/cap-fixture"
        await self.context.route(url, lambda route: route.fulfill(content_type="text/html", body="<!doctype html>"))
        await install_cap_watch(self.context, interval_ms=25)
        page = await self.context.new_page()

        def watch(event: str):
            return page.expect_console_message(lambda message: message.text == PREFIX + event, timeout=5000)

        async with watch("watching"):
            await page.goto(url)
        async with watch("pending"):
            await page.evaluate("document.body.append(document.createElement('cap-widget'))")

        widget = page.locator("cap-widget")
        async with watch("ready"):
            await widget.evaluate("""(widget) => {
                const button = document.createElement('button');
                button.className = 'captcha';
                button.addEventListener('click', () => { widget.dataset.clicked = 'yes'; });
                widget.attachShadow({mode: 'open'}).append(button);
            }""")
        async with watch("verifying"):
            await widget.locator("button").evaluate("button => button.dataset.state = 'verifying'")
        async with watch("error"):
            await widget.dispatch_event("error")
        async with watch("done-no-token"):
            await widget.locator("button").evaluate("button => button.dataset.state = 'done'")
        async with watch("done"):
            await widget.evaluate("widget => widget.token = 'fixture-token'")
        self.assertIsNone(await widget.get_attribute("data-clicked"))

        async with watch("watching"):
            await page.reload()
        async with watch("pending"):
            await page.evaluate("document.body.append(document.createElement('cap-widget'))")

    async def mock_panel(self) -> list[dict]:
        accounts: dict[str, dict] = {}
        submissions: list[dict] = []

        async def route_panel(route: Route) -> None:
            request = route.request
            url = urlsplit(request.url)
            if f"{url.scheme}://{url.netloc}" != PANEL_URL:
                await route.abort()
                return
            if not url.path.startswith("/api/"):
                await route.continue_()
                return

            match (request.method, url.path):
                case ("GET", "/api/auth/state"):
                    await route.fulfill(json={"authenticated": True, "needs_setup": False})
                case ("GET", "/api/overview"):
                    await route.fulfill(json={"keys": {"hunting": 0}})
                case ("GET", "/api/stream"):
                    await route.fulfill(content_type="text/event-stream", body=": test stream\n\n")
                case ("GET", "/api/keys"):
                    await route.fulfill(json=[])
                case ("GET", "/api/proxies"):
                    await route.fulfill(json=[{"id": 1, "status": "ok"}])
                case ("GET", "/api/accounts"):
                    await route.fulfill(json=list(accounts.values()))
                case ("GET", "/api/extension/tokens"):
                    await route.fulfill(json=[])
                case ("POST", "/api/accounts/import"):
                    added = updated = 0
                    for row in request.post_data_json["accounts"]:
                        submissions.append(row)
                        account_id = row["account_id"]
                        if account_id in accounts:
                            updated += 1
                        else:
                            accounts[account_id] = {
                                "id": len(accounts) + 1,
                                "account_id": account_id,
                                "email": row["email"],
                                "has_password": bool(row["password"]),
                                "onboarded": False,
                                "login_stage": "",
                            }
                            added += 1
                    await route.fulfill(json={"added": added, "updated": updated})
                case _:
                    await route.fulfill(status=404, json={"detail": "Unknown test endpoint"})

        await self.context.route("**/*", route_panel)
        return submissions

    async def test_keys_imports_two_accounts_for_the_extension(self) -> None:
        submissions = await self.mock_panel()
        page = await self.context.new_page()
        await page.goto(f"{PANEL_URL}/keys", wait_until="domcontentloaded")
        await page.get_by_role("button", name="Подключить аккаунты").click(timeout=15000)
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("Строки аккаунтов").fill("\n".join([
            r'4\. first@example.test | Test"Password<$ | 123456' + "\\",
            r'5\. second@example.test | Test"Password> | 234567' + "\\",
            "123456", "invalid",
        ]))
        await dialog.get_by_role("button", name="Импортировать 3").click()
        await expect(dialog.get_by_role("status")).to_have_text("Добавлено 2 · обновлено 1 · не распознано 1")
        self.assertEqual(submissions, [
            {"account_id": "123456", "email": "first@example.test", "password": 'Test"Password<$'},
            {"account_id": "234567", "email": "second@example.test", "password": 'Test"Password>'},
            {"account_id": "123456", "email": "", "password": ""},
        ])
        await page.reload(wait_until="domcontentloaded")
        await page.get_by_role("button", name="Подключить аккаунты").click()
        await expect(dialog.get_by_role("button", name="Войти: first@example.test")).to_be_enabled()
        await expect(dialog.get_by_role("button", name="Войти: second@example.test")).to_be_enabled()

    async def test_keys_opens_selectel_in_the_same_browser_before_connecting(self) -> None:
        submissions = await self.mock_panel()
        await self.context.route(SELECTEL_LOGIN + "**", lambda route: route.fulfill(
            content_type="text/html", body='<!doctype html><input id="username"><input type="password">',
        ))
        page = await self.context.new_page()
        await page.goto(f"{PANEL_URL}/keys", wait_until="domcontentloaded")
        await page.get_by_role("button", name="Подключить аккаунты").click(timeout=15000)
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("Строки аккаунтов").fill("first@example.test | fixture-password | 123456")
        await dialog.get_by_role("button", name="Импортировать 1").click()

        async with self.context.expect_page() as popup:
            await dialog.get_by_role("button", name="Войти: first@example.test").click()
        sign_in = await popup.value
        await expect(sign_in).to_have_url(f"{SELECTEL_LOGIN}?toffee-account=123456#blank")
        self.assertIs(sign_in.context, page.context)
        self.assertIsNone(await sign_in.evaluate("window.opener"))
        await expect(sign_in.locator('input[type="password"]')).to_be_empty()
        self.assertEqual(len(submissions), 1)
