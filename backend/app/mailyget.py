"""Получение новых кодов Selectel; адаптация mailyget_code.py под async API."""
from __future__ import annotations

import json
import re
import time
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import get_settings
from app.core.crypto import encrypt, decrypt


class MailError(Exception):
    pass


DEFAULT_SENDER = "no-reply@selectel.ru"
DEFAULT_SUBJECT = "Подтверждение входа в аккаунт"
CODE_REGEX = re.compile(
    r"(?:используйте\s+проверочный\s+код|проверочный\s+код)"
    r"\s*[:：-]?\s*(\d{4,8})\b",
    flags=re.IGNORECASE,
)


class PlainTextParser(HTMLParser):
    """Вытаскивает текст без CSS/JS, если поле text отсутствует."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"style", "script"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"style", "script"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def extract_code(detail_json: dict) -> str | None:
    """Извлечение 4–8 цифр после фразы Selectel, без поиска по случайным цифрам CSS."""
    email = detail_json.get("email", detail_json)
    if not isinstance(email, dict):
        raise ValueError("Ожидался объект email")

    text = email.get("text") or ""
    match = CODE_REGEX.search(text)
    if match:
        return match.group(1)

    html = email.get("html") or ""
    if html:
        parser = PlainTextParser()
        parser.feed(html)
        clean_text = unescape(" ".join(parser.parts))
        match = CODE_REGEX.search(clean_text)
        if match:
            return match.group(1)
    return None


def _urls(email: str) -> tuple[str, str]:
    settings = get_settings()
    listing = settings.mailyget_list_url
    detail = settings.mailyget_detail_url_template
    if "{email}" not in listing or "{id}" not in detail:
        raise MailError("Настройте MAILYGET_LIST_URL с {email} и MAILYGET_DETAIL_URL_TEMPLATE с {id} на сервере.")
    listing = listing.replace("{email}", quote(email, safe=""))
    detail = detail.replace("{email}", quote(email, safe=""))
    for url in (listing, detail):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise MailError("Адреса API MailyGet должны использовать HTTPS без логина и пароля в URL.")
    return listing, detail


async def _json(client: httpx.AsyncClient, url: str) -> dict:
    response = await client.get(url)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or data.get("success") is not True:
        raise MailError("Неожиданный ответ API MailyGet.")
    return data


async def check_mail(email: str, cursor: str | None = None) -> dict:
    # Snapshot is encrypted and authenticated; callers cannot remove old message IDs.
    old_ids = set()
    if cursor:
        try:
            state = json.loads(decrypt(cursor))
            if state["purpose"] != "selectel-mail" or state["email"] != email:
                raise ValueError()
            if not 0 <= time.time() - state["started"] < 120:
                raise MailError("Код не пришёл за 120 секунд. Начните ожидание заново и запросите новый код.")
            old_ids = set(state["ids"])
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            raise MailError("Ожидание недействительно. Начните его заново.") from exc
    listing, detail = _urls(email)
    settings = get_settings()
    headers = {"Accept": "application/json"}
    if settings.mailyget_authorization:
        headers["Authorization"] = settings.mailyget_authorization
    if settings.mailyget_cookie:
        headers["Cookie"] = settings.mailyget_cookie
    try:
        async with httpx.AsyncClient(headers=headers, timeout=10, trust_env=False,
                                     follow_redirects=False) as client:
            messages = (await _json(client, listing)).get("emails")
            if not isinstance(messages, list) or any(not isinstance(m, dict) or "id" not in m for m in messages):
                raise MailError("В ответе MailyGet нет корректного списка писем.")
            if not cursor:
                state = {"purpose": "selectel-mail", "email": email, "started": time.time(),
                         "ids": [str(m["id"]) for m in messages]}
                cursor = encrypt(json.dumps(state))
                if len(cursor) > 100_000:
                    raise MailError("Слишком много писем в ящике. Очистите старые письма и повторите.")
                return {"cursor": cursor, "code": None}
            for item in sorted(messages, key=lambda m: str(m.get("date", "")), reverse=True):
                if str(item["id"]) in old_ids:
                    continue
                if parseaddr(str(item.get("from", "")))[1].casefold() != DEFAULT_SENDER:
                    continue
                if str(item.get("subject", "")).strip().casefold() != DEFAULT_SUBJECT.casefold():
                    continue
                payload = await _json(client, detail.replace("{id}", quote(str(item["id"]), safe="")))
                code = extract_code(payload)
                if code:
                    return {"cursor": cursor, "code": code}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        # Never expose URLs, cookies, message bodies or authorization values.
        raise MailError("Не удалось прочитать почту MailyGet. Проверьте адреса API и авторизацию на сервере.") from exc
    return {"cursor": cursor, "code": None}
