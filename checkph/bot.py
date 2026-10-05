"""Telegram-бот: по номеру отвечает оператором и регионом из реестра."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time

from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from dotenv import load_dotenv

from . import db, lookup

START_TEXT = (
    "Реестр российской системы и плана нумерации. Для получения информации о принадлежности "
    "номера оператору и региону введите номер телефона в формате 7<код><номер>."
)
BAD_FORMAT_TEXT = "Не похоже на номер. Нужны 11 цифр, начиная с 7, например 79001234567."
NOT_FOUND_TEXT = "Номер не найден."

access_log = logging.getLogger("checkph.access")


class Repository:
    """Сериализует обращения к одному соединению SQLite из рабочих потоков."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con
        self._lock = threading.Lock()

    def resolve(self, number: int) -> lookup.Attribution | None:
        with self._lock:
            return lookup.resolve(self._con, number)

    def log_lookup(self, number: int | None, raw_input: str, tg_user_id: int | None, found: bool, duration_ms: float) -> None:
        with self._lock, db.transaction(self._con):
            self._con.execute(
                "INSERT INTO lookup_event (number, raw_input, tg_user_id, found, duration_ms) VALUES (?, ?, ?, ?, ?)",
                (number, raw_input, tg_user_id, int(found), duration_ms),
            )


def format_answer(attr: lookup.Attribution) -> str:
    lines = [f"{attr.operator_name} ({attr.display_region})"]
    if attr.owner_name:
        lines.append(f"Владелец: {attr.owner_name} (источник: {attr.owner_source})")
    if attr.is_spam is True:
        lines.append("Отмечен как спам.")
    return "\n".join(lines)


def _access_line(message: types.Message, parts: list[str], duration_s: float) -> str:
    user = message.from_user
    return "|".join(
        [
            *parts,
            user.full_name if user and user.full_name else "N/A",
            f"@{user.username}" if user and user.username else "N/A",
            str(user.id) if user else "N/A",
            str(duration_s),
        ]
    )


def build_dispatcher(repo: Repository) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(message: types.Message) -> None:
        await message.answer(START_TEXT)

    @dp.message()
    async def cmd_lookup(message: types.Message) -> None:
        raw = message.text or ""
        tg_user_id = message.from_user.id if message.from_user else None
        number = lookup.normalize(raw)
        if number is None:
            await asyncio.to_thread(repo.log_lookup, None, raw, tg_user_id, False, 0.0)
            await message.reply(BAD_FORMAT_TEXT)
            return

        started = time.perf_counter()
        attr = await asyncio.to_thread(repo.resolve, number)
        duration_s = time.perf_counter() - started
        await asyncio.to_thread(repo.log_lookup, number, raw, tg_user_id, attr is not None, duration_s * 1000)

        if attr is None:
            access_log.info(_access_line(message, [raw, NOT_FOUND_TEXT, "", ""], duration_s))
            await message.reply(NOT_FOUND_TEXT)
            return

        access_log.info(_access_line(message, [raw, str(number), attr.operator_name, attr.display_region], duration_s))
        await message.reply(format_answer(attr))

    return dp


def _setup_access_log(path: str) -> None:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    access_log.setLevel(logging.INFO)
    access_log.addHandler(handler)


async def main() -> None:
    load_dotenv()
    token = os.getenv("API_TOKEN")
    if not token:
        raise SystemExit("API_TOKEN не задан в .env")
    db_path = os.getenv("DB_PATH", db.DEFAULT_DB_PATH)
    _setup_access_log(os.getenv("ACCESS_LOG", "access.log"))

    con = db.connect(db_path)
    db.init_schema(con)
    repo = Repository(con)
    bot = Bot(token=token)
    try:
        await build_dispatcher(repo).start_polling(bot)
    finally:
        con.close()
