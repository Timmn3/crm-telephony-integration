"""FSM-состояния для пошаговых диалогов."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddOperator(StatesGroup):
    waiting_tg_id = State()
    waiting_group = State()


class AddManager(StatesGroup):
    waiting_target = State()   # ввод @username или пересылка сообщения менеджера
    waiting_group = State()    # выбор группы
    waiting_replace = State()  # подтверждение замены текущего менеджера


class AddGroup(StatesGroup):
    waiting_name = State()
    waiting_city = State()


class EditGroup(StatesGroup):
    waiting_group = State()   # выбор группы из инлайн-кнопок
    waiting_field = State()   # выбор поля: name / city
    waiting_value = State()   # ввод нового значения


class RemoveUser(StatesGroup):
    waiting_identifier = State()


class CreateOrder(StatesGroup):
    waiting_lead_id = State()
    waiting_group = State()
    waiting_comment = State()


class SetAmoCode(StatesGroup):
    waiting_code = State()
