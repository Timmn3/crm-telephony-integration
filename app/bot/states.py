"""FSM-состояния для пошаговых диалогов."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddManager(StatesGroup):
    waiting_tg_id = State()    # менеджер не привязан к группе — роль выдаётся сразу


class AddAdmin(StatesGroup):
    waiting_target = State()   # ввод @username или пересылка сообщения администратора
    waiting_group = State()    # выбор группы (в группе может быть несколько админов)


class AddGroup(StatesGroup):
    waiting_name = State()
    waiting_city = State()


class EditGroup(StatesGroup):
    waiting_group = State()   # выбор группы из инлайн-кнопок
    waiting_field = State()   # выбор поля: name / city
    waiting_value = State()   # ввод нового значения


class SetExtension(StatesGroup):
    waiting_group = State()   # выбор группы из инлайн-кнопок
    waiting_value = State()   # ввод нового значения extension


class RemoveUser(StatesGroup):
    waiting_identifier = State()


class CreateOrder(StatesGroup):
    waiting_lead_id = State()
    waiting_group = State()
    waiting_admin = State()    # выбор конкретного админа, если в группе их несколько
    waiting_comment = State()


class DeleteGroup(StatesGroup):
    waiting_group = State()   # выбор группы из инлайн-кнопок


class SetAmoCode(StatesGroup):
    waiting_code = State()
