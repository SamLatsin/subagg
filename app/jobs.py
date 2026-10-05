"""Долгие операции в фоне: кнопка в админке отвечает сразу, работа идет дальше.

Один и тот же джоб не запускается дважды: две проверки подрались бы за порт
API ядра mihomo, а два обновления подписок - за одни и те же строки в базе.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable

from .checker import run_check
from .db import SessionLocal
from .fetcher import sync_all
from .models import utcnow

log = logging.getLogger(__name__)

# Ядро проверки одно и слушает один порт API: полная проверка и пинг одной
# ноды из админки не должны идти одновременно
MIHOMO_LOCK = asyncio.Lock()


class Job:
    def __init__(self, name: str, title: str, fn: Callable[[], Awaitable[object]]) -> None:
        self.name = name
        self.title = title
        self._fn = fn
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.started_at: dt.datetime | None = None
        self.finished_at: dt.datetime | None = None
        self.result: object = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def run(self) -> None:
        """Выполнить и дождаться. Если джоб уже идет, тихо выйти."""
        if self.running:
            log.info("%s уже идет, пропускаю", self.name)
            return
        async with self._lock:
            self.started_at = utcnow()
            self.finished_at = None
            self.error = None
            try:
                self.result = await self._fn()
                # run_check не бросает исключения, а пишет причину в сводку
                if isinstance(self.result, dict) and self.result.get("error"):
                    self.error = str(self.result["error"])
            except Exception as exc:  # noqa: BLE001 - статус должен дожить до админки
                self.error = f"{type(exc).__name__}: {exc}"
                log.exception("%s упал", self.name)
            finally:
                self.finished_at = utcnow()

    def start(self) -> bool:
        """Запустить в фоне. False, если уже идет."""
        if self.running:
            return False
        self._task = asyncio.create_task(self.run())
        return True

    def status(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }


async def _fetch() -> object:
    async with SessionLocal() as session:
        return await sync_all(session)


async def _check() -> object:
    async with MIHOMO_LOCK, SessionLocal() as session:
        return await run_check(session)


FETCH = Job("fetch", "Обновление подписок", _fetch)
CHECK = Job("check", "Проверка нод", _check)
JOBS = [FETCH, CHECK]
