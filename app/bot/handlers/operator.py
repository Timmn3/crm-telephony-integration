"""Обработчики команд оператора (наполняется в блоках 7 и 9)."""
from __future__ import annotations

import logging

from aiogram import Router

logger = logging.getLogger(__name__)

router = Router(name="operator")
