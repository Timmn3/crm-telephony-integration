"""Reply-клавиатуры."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove


def phone_request_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой «Поделиться номером телефона»."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поделиться номером", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажмите кнопку, чтобы поделиться номером",
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    """Убирает reply-клавиатуру."""
    return ReplyKeyboardRemove()
