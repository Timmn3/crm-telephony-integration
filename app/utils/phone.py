"""Нормализация телефонных номеров к формату 7XXXXXXXXXX."""
from __future__ import annotations

import re


def normalize_phone(raw: str | None) -> str | None:
    """Приводит номер к формату 7XXXXXXXXXX (11 цифр, начинается с 7).

    - удаляет всё, кроме цифр;
    - ведущую 8 заменяет на 7;
    - к 10-значному номеру добавляет 7 спереди.

    Возвращает None, если из строки не получился валидный 11-значный номер,
    начинающийся на 7.
    """
    if not raw:
        return None

    digits = re.sub(r"\D", "", raw)

    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits

    if len(digits) == 11 and digits.startswith("7"):
        return digits
    return None
