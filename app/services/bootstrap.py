"""Инициализация при старте: seed групп, первичная авторизация amoCRM,
диагностика администраторов без mango_extension.

Вызывается из main.py после применения миграций.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone

from aiogram import Bot

from app.config import get_settings
from app.db.database import get_session
from app.db.repositories import amo_token_repo, group_repo, user_repo
from app.services.amocrm import AmoCRMClient

logger = logging.getLogger(__name__)

# Предзаполненный список рабочих групп (из ТЗ).
# Примечание: текст ТЗ упоминает «11 групп», но предметный список содержит 10 —
# создаём ровно столько, сколько перечислено. Недостающую можно добавить /add_group.
INITIAL_GROUPS = [
    {"name": "Группа-1", "city": "Москва"},
    {"name": "Группа-2", "city": "Москва"},
    {"name": "Группа-3", "city": "Москва"},
    {"name": "Казань", "city": "Казань"},
    {"name": "Тверь", "city": "Тверь"},
    {"name": "Группа-5", "city": "Самара"},
    {"name": "Группа-6", "city": "Самара"},
    {"name": "Пермь", "city": "Пермь"},
    {"name": "Тула", "city": "Тула"},
    {"name": "Рязань", "city": "Рязань"},
]


async def seed_groups() -> None:
    """Создаёт предзаполненные группы, если таблица groups пустая."""
    async with get_session() as session:
        if await group_repo.count(session) > 0:
            logger.info("Группы уже существуют — seed пропущен")
            return
        for item in INITIAL_GROUPS:
            await group_repo.create(session, item["name"], item["city"])
        logger.info("Создано %s групп (seed)", len(INITIAL_GROUPS))


def _jwt_expires_at(token: str) -> datetime:
    """Извлекает exp из JWT без внешних библиотек."""
    payload_b64 = token.split(".")[1]
    padding = (4 - len(payload_b64) % 4) % 4
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * padding))
    return datetime.fromtimestamp(payload["exp"], tz=timezone.utc)


async def ensure_amo_token() -> None:
    """Записывает токены amoCRM в БД при первом запуске если таблица пустая.

    Приоритет источников:
    1. Токен уже есть в БД — ничего не делаем.
    2. AMOCRM_AUTH_CODE — обмениваем на access/refresh (стандартный OAuth).
    3. AMOCRM_LONG_TOKEN — долгосрочный JWT, пишем напрямую в БД.
    4. Ничего нет — предупреждение, бот не сможет обращаться к amoCRM.

    Ничего не делает в режиме-заглушке (реквизиты интеграции не заданы).
    """
    settings = get_settings()
    if not settings.amocrm_configured:
        logger.info("amoCRM не настроен — работаем в режиме-заглушке")
        return
    async with get_session() as session:
        existing = await amo_token_repo.get(session)
        if existing is not None:
            logger.info("Токены amoCRM уже есть — обмен кода не требуется")
            return
        if settings.amocrm_auth_code:
            client = AmoCRMClient(session)
            try:
                await client.exchange_code(settings.amocrm_auth_code)
                logger.info("Первичная авторизация amoCRM выполнена (auth_code)")
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Не удалось обменять AMOCRM_AUTH_CODE. Код мог истечь — "
                    "получите новый и примените командой /set_amo_code."
                )
            return
        if settings.amocrm_long_token:
            try:
                expires_at = _jwt_expires_at(settings.amocrm_long_token)
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось разобрать AMOCRM_LONG_TOKEN как JWT.")
                return
            await amo_token_repo.save(
                session,
                access_token=settings.amocrm_long_token,
                refresh_token=settings.amocrm_long_token,
                expires_at=expires_at,
            )
            await session.commit()
            logger.info("Долгосрочный токен amoCRM записан в БД (до %s)", expires_at)
            return
        logger.warning(
            "amoCRM настроен, но нет токенов, AMOCRM_AUTH_CODE и AMOCRM_LONG_TOKEN. "
            "Авторизуйтесь командой /set_amo_code <code>."
        )


async def report_admins_without_extension(bot: Bot) -> None:
    """Уведомляет CODER о активных админах без mango_extension при старте.

    Не блокирует запуск бота ни при отсутствии CODER, ни при ошибке отправки.
    """
    settings = get_settings()
    async with get_session() as session:
        admins = await user_repo.list_admins_without_extension(session)
        groups = {g.id: g for g in await group_repo.list_all(session)}

    if not admins:
        logger.info("Диагностика extension: у всех активных админов extension задан")
        return

    lines = [f"⚠️ Админы без mango_extension ({len(admins)}) — звонки у них не заработают:"]
    for a in admins:
        group = groups.get(a.group_id)
        group_label = group.name if group else f"#{a.group_id}"
        lines.append(f"— {a.full_name or a.tg_id} ({group_label}, tg_id={a.tg_id})")
    text = "\n".join(lines)
    logger.warning("Найдено %d активных админов без extension", len(admins))

    if not settings.coder_tg_id:
        logger.warning("CODER не задан — уведомление об отсутствующих extension не отправлено")
        return
    try:
        await bot.send_message(settings.coder_tg_id, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось уведомить CODER об админах без extension: %s", exc)
