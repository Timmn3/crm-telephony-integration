"""Обработчики команд администратора (наполняется в блоке 5)."""
from __future__ import annotations

import logging

from aiogram import Router

logger = logging.getLogger(__name__)

router = Router(name="admin")
