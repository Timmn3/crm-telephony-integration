"""Обработчики действий менеджера.

Блок 5: регистрация через «Поделиться контактом».
Блоки 8/10: взятие заявки, запрос звонка, инициация звонка.
"""
from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.reply import phone_request_keyboard, remove_keyboard
from app.db.models import User, UserRole
from app.db.repositories import region_repo, user_repo
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

router = Router(name="manager")


@router.message(F.contact)
async def on_contact(message: Message, user: User | None, session: AsyncSession) -> None:
    """Сохраняет номер менеджера из пересланного контакта."""
    if user is None or not user.is_active or user.role != UserRole.MANAGER:
        # Контакт от не-менеджера нам не нужен.
        await message.answer("Спасибо, но номер телефона сейчас не требуется.",
                             reply_markup=remove_keyboard())
        return

    contact = message.contact
    # Безопасность: принимаем только собственный контакт пользователя.
    if contact.user_id is not None and message.from_user is not None \
            and contact.user_id != message.from_user.id:
        await message.answer(
            "Пожалуйста, поделитесь именно своим номером — нажмите кнопку ниже.",
            reply_markup=phone_request_keyboard(),
        )
        return

    phone = normalize_phone(contact.phone_number)
    if phone is None:
        await message.answer(
            "Не удалось распознать номер телефона. Попробуйте ещё раз.",
            reply_markup=phone_request_keyboard(),
        )
        return

    await user_repo.set_phone(session, user, phone)

    region_name = "—"
    if user.region_id:
        region = await region_repo.get_by_id(session, user.region_id)
        if region:
            region_name = html.escape(region.name)

    await message.answer(
        "✅ Готово! Вы зарегистрированы как выездной менеджер.\n"
        f"Регион: <b>{region_name}</b>.\n\n"
        "Когда поступит заявка — вы получите карточку с кнопкой «Беру заявку».",
        reply_markup=remove_keyboard(),
    )
    logger.info("Менеджер tg_id=%s завершил регистрацию (телефон сохранён)", user.tg_id)
