"""Смена телефона выездного администратора: /change_phone (сам) и /set_phone (директор).

БД не нужна: репозиторий подменяется заглушками, проверяется логика хендлеров.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import admin as director_h
from app.bot.handlers import manager as admin_h
from app.bot.states import ChangePhone, SetPhone
from app.db.models import UserRole
from app.db.repositories import user_repo


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def make_user(tg_id, role, phone=None, name="Кобляков Дмитрий"):
    return SimpleNamespace(
        tg_id=tg_id, role=role, phone=phone, full_name=name,
        is_active=True, group_id=None, tg_username=None,
    )


def make_message(text=None, contact=None, from_id=1):
    return SimpleNamespace(
        text=text, contact=contact, from_user=SimpleNamespace(id=from_id),
        answer=AsyncMock(), edit_text=AsyncMock(),
    )


@pytest.fixture
def state():
    storage = MemoryStorage()
    return FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=1))


@pytest.fixture
def repo(monkeypatch):
    """Заглушки репозитория: set_phone пишет в объект, директора возвращаются списком."""
    directors = [make_user(900, UserRole.DIRECTOR, name="Директор")]
    users = {}

    async def set_phone(session, user, phone):
        user.phone = phone

    async def list_by_role(session, role, active_only=False):
        return directors if role == UserRole.DIRECTOR else [
            u for u in users.values() if u.role == role
        ]

    async def get_by_tg_id(session, tg_id):
        return users.get(tg_id)

    monkeypatch.setattr(user_repo, "set_phone", set_phone)
    monkeypatch.setattr(user_repo, "list_by_role", list_by_role)
    monkeypatch.setattr(user_repo, "get_by_tg_id", get_by_tg_id)
    return SimpleNamespace(users=users, directors=directors)


async def test_admin_changes_phone_by_text(repo, state):
    user = make_user(10, UserRole.ADMIN, phone="79990001111")
    bot = FakeBot()

    start_msg = make_message("/change_phone")
    await admin_h.change_phone_start(start_msg, user, state)
    assert await state.get_state() == ChangePhone.waiting_phone.state

    msg = make_message("8 (999) 222-33-44")
    await admin_h.change_phone_text(msg, user, state, session=None, bot=bot)

    assert user.phone == "79992223344"
    assert await state.get_state() is None
    # Директору ушло уведомление с замаскированными номерами, без полного номера.
    assert [c for c, _ in bot.sent] == [900]
    assert "79992223344" not in bot.sent[0][1]
    assert "7***1111" in bot.sent[0][1] and "7***3344" in bot.sent[0][1]


async def test_bad_text_keeps_state_and_phone(repo, state):
    user = make_user(10, UserRole.ADMIN, phone="79990001111")
    await state.set_state(ChangePhone.waiting_phone)
    msg = make_message("позвоните мне")
    await admin_h.change_phone_text(msg, user, state, session=None, bot=FakeBot())

    assert user.phone == "79990001111"
    assert await state.get_state() == ChangePhone.waiting_phone.state


async def test_same_phone_is_noop(repo, state):
    user = make_user(10, UserRole.ADMIN, phone="79990001111")
    bot = FakeBot()
    await state.set_state(ChangePhone.waiting_phone)
    await admin_h.change_phone_text(make_message("89990001111"), user, state, None, bot)

    assert user.phone == "79990001111"
    assert bot.sent == []
    assert await state.get_state() is None


async def test_contact_with_existing_phone_is_change(repo, state):
    user = make_user(10, UserRole.ADMIN, phone="79990001111")
    bot = FakeBot()
    contact = SimpleNamespace(user_id=10, phone_number="+7 999 555-66-77")
    msg = make_message(contact=contact, from_id=10)
    await admin_h.on_contact(msg, user, None, state, bot)

    assert user.phone == "79995556677"
    assert "обновлён" in msg.answer.await_args.args[0]
    assert len(bot.sent) == 1


async def test_contact_first_time_is_registration(repo, state):
    user = make_user(10, UserRole.ADMIN, phone=None)
    contact = SimpleNamespace(user_id=10, phone_number="89995556677")
    msg = make_message(contact=contact, from_id=10)
    await admin_h.on_contact(msg, user, SimpleNamespace(), state, FakeBot())

    assert user.phone == "79995556677"
    assert "зарегистрированы" in msg.answer.await_args.args[0]


async def test_foreign_contact_rejected(repo, state):
    user = make_user(10, UserRole.ADMIN, phone="79990001111")
    contact = SimpleNamespace(user_id=777, phone_number="89995556677")
    msg = make_message(contact=contact, from_id=10)
    await admin_h.on_contact(msg, user, None, state, FakeBot())

    assert user.phone == "79990001111"


async def test_director_set_phone_by_args(repo, state):
    target = make_user(10, UserRole.ADMIN, phone="79990001111")
    repo.users[10] = target
    bot = FakeBot()
    msg = make_message("/set_phone 10 89991112233")
    command = SimpleNamespace(args="10 89991112233")
    await director_h.set_phone_start(msg, command, state, None, bot)

    assert target.phone == "79991112233"
    # Уведомлён сам администратор.
    assert [c for c, _ in bot.sent] == [10]


async def test_director_set_phone_rejects_non_admin(repo, state):
    manager = make_user(20, UserRole.MANAGER, phone=None, name="Менеджер")
    repo.users[20] = manager
    msg = make_message()
    await director_h.set_phone_start(msg, SimpleNamespace(args="20 89991112233"), state, None, FakeBot())

    assert manager.phone is None


async def test_director_set_phone_interactive(repo, state):
    target = make_user(10, UserRole.ADMIN, phone="79990001111")
    repo.users[10] = target
    bot = FakeBot()

    await director_h.set_phone_start(make_message(), SimpleNamespace(args=None), state, None, bot)
    assert await state.get_state() == SetPhone.waiting_admin.state

    cb = SimpleNamespace(data="admin_set_phone:10", message=make_message(), answer=AsyncMock())
    await director_h.set_phone_pick_admin(cb, state, None)
    assert await state.get_state() == SetPhone.waiting_phone.state

    # Мусор вместо номера - остаёмся в состоянии, телефон не тронут.
    await director_h.set_phone_value(make_message("abc"), state, None, bot)
    assert target.phone == "79990001111"
    assert await state.get_state() == SetPhone.waiting_phone.state

    await director_h.set_phone_value(make_message("+7 999 111 22 33"), state, None, bot)
    assert target.phone == "79991112233"
    assert await state.get_state() is None
