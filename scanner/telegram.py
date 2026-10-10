from __future__ import annotations

"""Уведомления в Telegram через Bot API.

Настройка в .env:
    TELEGRAM_BOT_TOKEN=123456:ABC...   # токен от @BotFather
    TELEGRAM_CHAT_ID=123456789         # куда слать; узнать: run.bat tg-test

Отправка никогда не роняет охоту: ошибка Telegram только печатается в консоль.
"""

import html
import os
import sys

import httpx
from dotenv import load_dotenv

from .paths import DOTENV_PATH

API_URL = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, *, label: str = "") -> None:
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        # Подпись в начале сообщения — чтобы отличать несколько запущенных охот.
        self.label = label.strip()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))

    @classmethod
    def from_env(cls) -> "TelegramNotifier | None":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            return None
        return cls(token, chat_id, label=os.getenv("TELEGRAM_LABEL", ""))

    async def send(self, text: str) -> bool:
        """text — HTML (parse_mode=HTML); значения подставлять через html.escape."""
        if self.label:
            text = f"<b>[{html.escape(self.label)}]</b> {text}"
        try:
            response = await self._client.post(
                API_URL.format(token=self.token, method="sendMessage"),
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
            )
            if response.status_code != 200:
                print(f"Telegram: HTTP {response.status_code} {response.text[:200]}", file=sys.stderr)
                return False
            return True
        except httpx.HTTPError as exc:
            print(f"Telegram недоступен: {type(exc).__name__}", file=sys.stderr)
            return False

    async def close(self) -> None:
        await self._client.aclose()


async def _get_updates(token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        response = await client.get(API_URL.format(token=token, method="getUpdates"))
        payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram отклонил токен: {payload.get('description', response.status_code)}")
    return payload.get("result", [])


async def run_tg_test() -> int:
    """`run.bat tg-test`: подсказывает chat_id и шлёт тестовое сообщение."""
    load_dotenv(DOTENV_PATH, override=False)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        print("В .env нет TELEGRAM_BOT_TOKEN.\n"
              "1) Напишите @BotFather в Telegram → /newbot → скопируйте токен.\n"
              "2) Добавьте в .env строку: TELEGRAM_BOT_TOKEN=<токен>\n"
              "3) Напишите своему боту любое сообщение и снова запустите run.bat tg-test")
        return 2

    if not chat_id:
        chats: dict[str, str] = {}
        for update in await _get_updates(token):
            message = update.get("message") or update.get("channel_post") or update.get("my_chat_member") or {}
            chat = message.get("chat") or {}
            if "id" in chat:
                name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
                chats[str(chat["id"])] = f"{name} @{chat.get('username', '')}".strip()
        if not chats:
            print("Бот пока не получил ни одного сообщения. Напишите ему что-нибудь (например /start) "
                  "и запустите run.bat tg-test ещё раз.")
            return 2
        print("Чаты, которые писали боту:")
        for cid, name in chats.items():
            print(f"  TELEGRAM_CHAT_ID={cid}    ({name})")
        print("Добавьте нужную строку в .env и запустите run.bat tg-test ещё раз — придёт тестовое сообщение.")
        return 0

    notifier = TelegramNotifier(token, chat_id, label=os.getenv("TELEGRAM_LABEL", ""))
    try:
        ok = await notifier.send("✅ Уведомления Selectel Roller работают.")
    finally:
        await notifier.close()
    print("Тестовое сообщение отправлено." if ok else "Не удалось отправить — причина выше.")
    return 0 if ok else 1
