"""Подключение одного аккаунта через публичный Selectel API."""
from __future__ import annotations

import hashlib
import math
import secrets
from typing import Any

import httpx
from socksio.exceptions import ProtocolError
from sqlalchemy import select

from app.core.crypto import decrypt, encrypt
from app.db.models import Proxy, SelectelKey
from app.db.session import session_scope
from app.engine.proxy import choose_random_proxy, parse_proxy_line, with_session
from app.engine.resell import RESELL_BASE_URL, ResellProject
from app.engine.selectel import IDENTITY_URL

API_URL = "https://api.selectel.ru"
MIN_MEMBER_BALANCE_KOPECKS = 20_000


class OnboardingError(Exception):
    pass


class OnboardingPaymentRequired(OnboardingError):
    pass


async def _request(client: httpx.AsyncClient, method: str, url: str, *, step: str, **kwargs: Any) -> httpx.Response:
    try:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
    except (httpx.RequestError, ProtocolError) as exc:
        raise OnboardingError(f"{step}: ошибка соединения через выбранный прокси.") from exc
    except httpx.HTTPStatusError as exc:
        raise OnboardingError(f"{step}: Selectel вернул HTTP {exc.response.status_code}.") from exc
    return response


_last_onboarding_proxy: dict[str, int] = {}


async def random_connection_proxy(account_id: str) -> tuple[int, str]:
    async with session_scope() as db:
        proxies = (await db.execute(select(Proxy))).scalars().all()
        proxy = choose_random_proxy(proxies, _last_onboarding_proxy.get(account_id))
        if proxy is None:
            raise OnboardingError("Нет доступных прокси. Добавьте или проверьте прокси.")
        if len(_last_onboarding_proxy) >= 10_000:
            _last_onboarding_proxy.pop(next(iter(_last_onboarding_proxy)))
        _last_onboarding_proxy[account_id] = proxy.id
        url = with_session(parse_proxy_line(decrypt(proxy.url_enc)).url,
                           proxy.session_mode, "tr" + secrets.token_hex(8))
        return proxy.id, url


async def onboard_account(account_id: str, proxy_id: int | None, project_name: str, iam_token: str,
                          *, connection_proxy: tuple[int, str] | None = None) -> tuple[SelectelKey, bool]:
    """Создаёт проект и сервисного пользователя, возвращает (ключ, добавлен)."""
    project_name = project_name.strip() or f"toffee-roller-{account_id}"
    if len(project_name) > 64 or not project_name.isprintable():
        raise OnboardingError("Имя проекта должно содержать до 64 печатных символов.")
    iam_token = iam_token.strip()
    if not iam_token or not iam_token.isascii() or not iam_token.isprintable() or " " in iam_token:
        raise OnboardingError("Введите IAM-токен с областью доступа «Аккаунт».")

    digest = hashlib.sha256(f"{account_id}/{project_name}".encode()).hexdigest()[:16]
    username = f"toffee-{digest}"
    async with session_scope() as db:
        existing = await db.scalar(select(SelectelKey).where(
            SelectelKey.account_id == account_id, SelectelKey.username == username,
        ))
        if existing:
            return existing, False
    password = "Aa1!" + secrets.token_hex(24)
    password_enc = encrypt(password)
    headers = {"X-Auth-Token": iam_token}

    async def _provision() -> tuple[SelectelKey, bool]:
        # One proxy session for the complete login/provisioning flow; new on every attempt.
        proxy_id, proxy_url = connection_proxy or await random_connection_proxy(account_id)
        async with httpx.AsyncClient(proxy=proxy_url, trust_env=False, timeout=httpx.Timeout(25, connect=8)) as client:
            try:
                account = (await _request(client, "GET", f"{RESELL_BASE_URL}/accounts", headers=headers,
                                          step="Проверка IAM-токена аккаунта")).json()["account"]
            except OnboardingError as exc:
                # Называем прокси, чтобы было видно, какой чинить (и чтобы retry его сменил).
                if str(exc).startswith("Проверка IAM-токена аккаунта: ошибка соединения"):
                    raise OnboardingError(f"Проверка IAM-токена аккаунта: ошибка соединения через прокси № {proxy_id}.") from exc
                raise
            if str(account["name"]) != account_id:
                raise OnboardingError("Номер аккаунта не совпадает с аккаунтом IAM-токена.")

            billing = (await _request(client, "GET", f"{API_URL}/v3/balances", headers=headers,
                                      step="Проверка баланса")).json()["data"]["billings"]
            balances = {item["billing_type"]: item.get("final_sum") for item in billing}
            # В аккаунтах с раздельными балансами облачные услуги оплачиваются с vpc.
            balance_type = "primary" if balances.keys() == {"primary"} else "vpc"
            cloud_balance = balances.get(balance_type)
            if not isinstance(cloud_balance, (int, float)) or not math.isfinite(cloud_balance):
                raise OnboardingError("Selectel не вернул доступный баланс облачной платформы. Повторите подключение.")
            if cloud_balance < MIN_MEMBER_BALANCE_KOPECKS:
                balance_rubles = f"{cloud_balance / 100:.2f}".replace(".", ",")
                raise OnboardingPaymentRequired(
                    f"Доступно {balance_rubles} ₽. Для создания сервисного пользователя с ролью member нужно минимум 200 ₽. "
                    "Пополните баланс в Selectel и повторите подключение."
                )
            if not account["enabled"] or account["locked"]:
                raise OnboardingError("Облачный аккаунт недоступен. Проверьте его статус в панели Selectel.")

            projects_url = f"{RESELL_BASE_URL}/projects"
            projects = (await _request(client, "GET", projects_url, headers=headers,
                                       step="Список проектов")).json()["projects"]
            matching = [ResellProject.from_payload(item) for item in projects if item["name"] == project_name]
            if len(matching) > 1:
                raise OnboardingError("Найдено несколько проектов с таким именем. Укажите уникальное имя.")
            if matching:
                project = matching[0]
            else:
                created = await _request(client, "POST", projects_url, headers=headers,
                                         json={"project": {"name": project_name, "description": "Toffee Roller"}},
                                         step="Создание проекта")
                project = ResellProject.from_payload(created.json()["project"])
            if not project.id or not project.enabled:
                raise OnboardingError("Проект недоступен. Проверьте его статус в панели Selectel.")

            await _request(client, "POST", f"{API_URL}/iam/v1/service_users", headers=headers,
                           json={"name": username, "password": password, "enabled": True, "roles": [
                               {"role_name": "member", "scope": "project", "project_id": project.id},
                           ]},
                           step=f"Создание сервисного пользователя {username}")

            identity = {"methods": ["password"], "password": {"user": {
                "name": username, "password": password, "domain": {"name": account_id},
            }}}
            try:
                login = await _request(client, "POST", IDENTITY_URL, json={"auth": {
                    "identity": identity, "scope": {"project": {"id": project.id, "domain": {"name": account_id}}},
                }}, step="Проверка доступа к проекту")
                if not login.headers.get("X-Subject-Token") or login.json()["token"]["project"]["id"] != project.id:
                    raise OnboardingError("Selectel не подтвердил доступ к выбранному проекту.")
                async with session_scope() as db:
                    key = SelectelKey(
                        name=project.name, account_id=account_id, username=username, password_enc=password_enc,
                        project_id=project.id, project_name=project.name, enabled=False, status="paused",
                        status_reason="Подключён через IAM. Нажмите «Запустить», когда будете готовы.",
                        proxy_mode="auto", proxy_id=proxy_id,
                    )
                    db.add(key)
            except Exception as exc:
                detail = f" {exc}" if isinstance(exc, OnboardingError) else ""
                raise OnboardingError(f"Пользователь {username} создан в Selectel, но ключ не добавлен.{detail} "
                                      "Проверьте сервисного пользователя в панели Selectel перед повтором.") from exc
        return key, True

    # Мёртвый прокси рвёт соединение на ПЕРВОМ шаге (проверка токена) — тогда ничего ещё
    # не создано, и можно безопасно взять другой прокси. С фиксированным прокси — без повторов.
    attempts = 1 if connection_proxy is not None else 3
    last_error: OnboardingError | None = None
    for attempt in range(attempts):
        try:
            return await _provision()
        except OnboardingError as exc:
            retriable = str(exc).startswith("Проверка IAM-токена аккаунта: ошибка соединения")
            if retriable and attempt + 1 < attempts:
                last_error = exc
                continue
            raise
    raise last_error or OnboardingError("Не удалось подключиться к Selectel через прокси.")
