"""Обработчики действий менеджера (наполняется в блоках 5, 8, 10)."""
from __future__ import annotations

import logging

from aiogram import Router

logger = logging.getLogger(__name__)

router = Router(name="manager")
