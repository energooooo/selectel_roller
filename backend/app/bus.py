"""Шина событий: всё, что происходит в движке, уходит подписчикам (SSE в браузер).

Подписчик — очередь с ограничением: медленный браузер не тормозит движок,
лишнее для него просто отбрасывается.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

_subscribers: set[asyncio.Queue[str]] = set()


def publish(kind: str, payload: dict[str, Any]) -> None:
    message = json.dumps({"type": kind, "data": payload}, ensure_ascii=False, default=str)
    for queue in list(_subscribers):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


def open_queue() -> asyncio.Queue[str]:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
    _subscribers.add(queue)
    return queue


def close_queue(queue: asyncio.Queue[str]) -> None:
    _subscribers.discard(queue)


def subscriber_count() -> int:
    return len(_subscribers)
