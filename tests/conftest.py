"""Общие фикстуры для тестов.

Интеграционные тесты используют отдельную тестовую БД (call_masking_test)
в том же Postgres. Схема создаётся один раз за сессию, каждый тест выполняется
в транзакции и откатывается для изоляции.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Base

_settings = get_settings()
TEST_DB_URL = _settings.database_url.replace(
    f"/{_settings.db_name}", "/call_masking_test"
)


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Создаёт чистую схему в тестовой БД один раз за сессию.

    Выполняется в собственном event loop (asyncio.run), чтобы не держать
    соединения между loop-ами отдельных тестов.
    """
    async def setup() -> None:
        eng = create_async_engine(TEST_DB_URL, poolclass=NullPool)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.exec_driver_sql("DROP TYPE IF EXISTS order_status CASCADE")
            await conn.exec_driver_sql("DROP TYPE IF EXISTS user_role CASCADE")
            await conn.run_sync(Base.metadata.create_all)
        await eng.dispose()

    asyncio.run(setup())
    yield


@pytest_asyncio.fixture
async def session():
    """Изолированная сессия: свой engine + внешняя транзакция с откатом.

    Каждый тест работает в собственном event loop, поэтому engine создаётся
    на тест (NullPool), а данные откатываются по завершении.
    """
    eng = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    conn = await eng.connect()
    trans = await conn.begin()
    factory = async_sessionmaker(bind=conn, expire_on_commit=False, autoflush=False)
    s = factory()
    try:
        yield s
    finally:
        await s.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Поддельный Bot
# --------------------------------------------------------------------------- #

class FakeMessage:
    def __init__(self, chat_id: int, message_id: int) -> None:
        self.message_id = message_id
        self.chat = SimpleNamespace(id=chat_id)


class FakeBot:
    """Минимальный фейк aiogram.Bot: запоминает отправленные/изменённые сообщения."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self._mid = 1000
        # chat_id -> исключение, которое send_message должен выбросить вместо отправки.
        self.fail_for: dict[int, Exception] = {}

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if chat_id in self.fail_for:
            raise self.fail_for[chat_id]
        self._mid += 1
        self.sent.append({
            "chat_id": chat_id, "text": text,
            "reply_markup": reply_markup, "message_id": self._mid,
        })
        return FakeMessage(chat_id, self._mid)

    async def edit_message_text(self, text, chat_id=None, message_id=None,
                                reply_markup=None, **kwargs):
        self.edited.append({
            "chat_id": chat_id, "message_id": message_id,
            "text": text, "reply_markup": reply_markup,
        })


@pytest.fixture
def fake_bot() -> FakeBot:
    return FakeBot()
