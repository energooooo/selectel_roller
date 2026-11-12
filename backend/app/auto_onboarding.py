"""Автовход через API панели Selectel с почтовым вторым фактором.

Контракт проверен по JS панели my.selectel.ru 2026-11-12. Это внутренний
API панели; при изменении контракта завершаемся с ошибкой, не повторяем вход.
Задания живут в памяти одного backend-процесса, секреты не попадают в status.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

import httpx
from socksio.exceptions import ProtocolError

from app.db.models import AuditLog
from app.db.session import session_scope
from app.mailyget import MailError, check_mail
from app.onboarding import OnboardingError, OnboardingPaymentRequired, onboard_account, random_connection_proxy

API = "https://api.selectel.ru"
PANEL = "https://my.selectel.ru"
TERMINAL = {"done", "error", "payment", "cancelled"}


@dataclass
class Job:
    id: str
    owner: int
    account_id: str
    stage: str = "starting"
    message: str = "Подготовка входа…"
    key_id: int | None = None
    added: bool = False
    updated: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = field(default=None, repr=False)

    def set(self, stage: str, message: str):
        self.stage, self.message, self.updated = stage, message, time.monotonic()

    def view(self):
        return {"id": self.id, "stage": self.stage, "message": self.message,
                "key_id": self.key_id, "added": self.added,
                "finished": self.stage in TERMINAL,
                "can_cancel": self.stage not in TERMINAL and self.stage != "connecting"}


jobs: dict[str, Job] = {}


async def request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    response = await client.request(method, url, **kwargs)
    if response.is_error:
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        code = str(data.get("code", "")).upper()
        if "RECAPTCHA" in code or data.get("recaptcha_required_for_next"):
            raise OnboardingError("Selectel запросил CAPTCHA. Выполните вход в панели и используйте режим IAM-токена.")
        if data.get("ban_info") or response.status_code == 429:
            raise OnboardingError("Selectel ограничил попытки входа. Подождите и проверьте доступ в панели; автоматический повтор остановлен.")
        if response.status_code in (401, 403):
            raise OnboardingError("Selectel не подтвердил вход. Проверьте номер аккаунта, email, пароль и ограничения доступа в панели.")
        raise OnboardingError(f"Selectel вернул HTTP {response.status_code}. Автоматический вход остановлен.")
    if response.is_redirect:
        raise OnboardingError("Selectel перенаправил запрос входа. Используйте вход через панель.")
    return response


async def login(client: httpx.AsyncClient, job: Job, email: str, password: str) -> str:
    job.set("mail", "Проверяем почту перед отправкой нового кода…")
    snapshot = await check_mail(email)
    job.set("login", "Входим в Selectel через выбранный прокси…")
    response = await request(client, "POST", f"{API}/v2/login", json={
        "login": job.account_id, "email": email, "password": password, "remember_me": False,
    })
    password = ""
    result = response.json()
    if not isinstance(result, dict):
        raise OnboardingError("Неожиданный ответ Selectel при входе.")
    if not result.get("uid"):
        factors = (await request(client, "GET", f"{API}/v1/account/2fa/factors")).json()
        if not any(item.get("type") == "email" for item in factors.get("items", [])):
            raise OnboardingError("Для этого аккаунта требуется другой второй фактор. Войдите в панели и используйте IAM-токен.")
        job.set("sending", "Запрашиваем проверочный код на email…")
        await request(client, "POST", f"{API}/v1/otp/email")
        job.set("waiting", "Код отправлен. Читаем новое письмо MailyGet…")
        async with asyncio.timeout(120):
            while True:
                mail = await check_mail(email, snapshot["cursor"])
                if mail["code"]:
                    break
                await asyncio.sleep(3)
        job.set("verifying", "Подтверждаем код входа…")
        await request(client, "POST", f"{API}/v1/otp/email/verify", data={"code": mail["code"]})
        result = (await request(client, "POST", f"{API}/v2/login")).json()
        if not isinstance(result, dict) or not result.get("uid"):
            raise OnboardingError("Selectel не завершил вход после подтверждения кода.")
    job.set("token", "Получаем IAM-токен аккаунта…")
    response = await request(client, "POST", f"{PANEL}/api/iam/v1/tokens",
                             json={"token": {"domain_name": job.account_id}})
    token = response.headers.get("X-Subject-Token")
    if not token:
        raise OnboardingError("Selectel не выдал IAM-токен аккаунта. Используйте ручной режим.")
    return token


async def run(job: Job, email: str, password: str, proxy_id: int | None, project_name: str):
    try:
        async with asyncio.timeout(240):
            proxy_id, proxy_url = await random_connection_proxy(job.account_id)
            async with httpx.AsyncClient(proxy=proxy_url, trust_env=False, timeout=20,
                                         follow_redirects=False, headers={
                                             "Accept": "application/json", "Origin": PANEL,
                                             "Referer": PANEL + "/login",
                                         }) as client:
                token = await login(client, job, email, password)
                password = ""
                job.set("connecting", "Проверяем баланс и подключаем проект…")
                key, added = await onboard_account(job.account_id, proxy_id, project_name, token,
                                                    connection_proxy=(proxy_id, proxy_url))
                job.key_id, job.added = key.id, added
                if added:
                    async with session_scope() as db:
                        db.add(AuditLog(admin_id=job.owner, ip="", action="key.create",
                                        target=f"{key.name} ({job.account_id})",
                                        details={"project": key.project_name, "method": "auto-login"}))
                job.set("done", f"Ключ «{key.name}» " + ("добавлен на паузе." if added else "уже подключён."))
    except asyncio.CancelledError:
        job.set("cancelled", "Подключение отменено.")
    except OnboardingPaymentRequired as exc:
        job.set("payment", str(exc))
    except (OnboardingError, MailError) as exc:
        job.set("error", str(exc))
    except TimeoutError:
        job.set("error", "Время ожидания истекло. Проверьте аккаунт и почту перед повтором.")
    except (httpx.HTTPError, ProtocolError):
        job.set("error", "Ошибка соединения с Selectel через выбранный прокси.")
    except Exception:
        job.set("error", "Не удалось обработать ответ сервиса. Проверьте аккаунт в панели перед повтором.")
    finally:
        password = ""


def start(owner: int, account_id: str, email: str, password: str, proxy_id: int | None, project_name: str) -> Job:
    for key, job in list(jobs.items()):
        if job.stage in TERMINAL and time.monotonic() - job.updated > 900:
            del jobs[key]
    active = [job for job in jobs.values() if job.stage not in TERMINAL]
    if any(job.account_id == account_id for job in active):
        raise OnboardingError("Этот аккаунт уже подключается. Дождитесь завершения текущей попытки.")
    if len(active) >= 4:
        raise OnboardingError("Уже выполняются четыре подключения. Дождитесь завершения одного из них.")
    job = Job(secrets.token_urlsafe(24), owner, account_id)
    jobs[job.id] = job
    job.task = asyncio.create_task(run(job, email, password, proxy_id, project_name))
    return job
