"""FSM-состояния для пошаговых диалогов."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddOperator(StatesGroup):
    waiting_tg_id = State()


class AddManager(StatesGroup):
    waiting_tg_id = State()
    waiting_region = State()


class AddRegion(StatesGroup):
    waiting_name = State()


class RemoveUser(StatesGroup):
    waiting_identifier = State()


class CreateOrder(StatesGroup):
    waiting_lead_id = State()
    waiting_region = State()
    waiting_target = State()  # всем в регион или выбрать менеджера


class RejectCall(StatesGroup):
    waiting_reason = State()
