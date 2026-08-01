"""Репозиторий пользователей."""
from __future__ import annotations

import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserRole

logger = logging.getLogger(__name__)


async def get_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_admin_by_phone(session: AsyncSession, phone: str) -> User | None:
    """Активный выездной администратор с таким номером телефона.

    Нужен сценарию «звонок-сигнал»: по АОН входящего на служебную линию находим,
    кто из бригад звонил. Сравниваем по последним 10 цифрам — так совпадение не
    зависит от того, записан номер как 7XXXXXXXXXX или 8XXXXXXXXXX (Mango отдаёт
    в разных форматах: в статистике с 7, в правилах переадресации с 8).

    Роль и активность проверяем здесь же: звонок с номера уволенного сотрудника
    или менеджера не должен ничего запускать.
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        return None
    tail = digits[-10:]

    result = await session.execute(
        select(User).where(
            User.role == UserRole.ADMIN,
            User.is_active.is_(True),
            User.phone.isnot(None),
            func.right(User.phone, 10) == tail,
        )
    )
    return result.scalars().first()


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    """Ищет пользователя по @username (без учёта регистра, '@' игнорируется)."""
    clean = username.lstrip("@").strip().lower()
    if not clean:
        return None
    result = await session.execute(
        select(User).where(func.lower(User.tg_username) == clean)
    )
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    *,
    tg_id: int,
    role: UserRole,
    full_name: str = "",
    tg_username: str | None = None,
    group_id: int | None = None,
    phone: str | None = None,
    is_active: bool = True,
) -> User:
    """Создаёт пользователя."""
    user = User(
        tg_id=tg_id,
        role=role,
        full_name=full_name,
        tg_username=tg_username,
        group_id=group_id,
        phone=phone,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    logger.info("Создан пользователь tg_id=%s role=%s", tg_id, role.value)
    return user


async def update_tg_profile(
    session: AsyncSession,
    user: User,
    *,
    full_name: str | None = None,
    tg_username: str | None = None,
) -> None:
    """Обновляет имя/username из данных Telegram (вызывается при /start)."""
    changed = False
    if full_name is not None and full_name != user.full_name:
        user.full_name = full_name
        changed = True
    if tg_username != user.tg_username:
        user.tg_username = tg_username
        changed = True
    if changed:
        await session.flush()


async def set_role(session: AsyncSession, user: User, role: UserRole) -> None:
    user.role = role
    await session.flush()
    logger.info("Пользователь tg_id=%s -> role=%s", user.tg_id, role.value)


async def set_group(session: AsyncSession, user: User, group_id: int | None) -> None:
    user.group_id = group_id
    await session.flush()
    logger.info("Пользователь tg_id=%s -> group_id=%s", user.tg_id, group_id)


async def set_phone(session: AsyncSession, user: User, phone: str) -> None:
    user.phone = phone
    await session.flush()
    logger.info("Пользователь tg_id=%s сохранил телефон", user.tg_id)


async def set_active(session: AsyncSession, user: User, active: bool) -> None:
    user.is_active = active
    await session.flush()
    logger.info("Пользователь tg_id=%s is_active=%s", user.tg_id, active)


async def set_mango_extension(session: AsyncSession, user: User, extension: str | None) -> None:
    """Устанавливает (или очищает, если None) внутренний номер Mango пользователя."""
    user.mango_extension = extension
    await session.flush()
    logger.info("Пользователь tg_id=%s -> mango_extension=%s", user.tg_id, extension)


async def get_group_mango_extension(
    session: AsyncSession, group_id: int, *, exclude_user_id: int | None = None
) -> str | None:
    """Известный extension группы — берётся у активных ADMIN этой группы.

    exclude_user_id (User.id) исключает конкретного пользователя из поиска —
    нужно при смене его группы, чтобы не засчитать его собственное (уже
    устаревшее, от старой группы) значение как консенсус новой группы.

    При разошедшихся значениях у разных админов группы — не резолвим конфликт,
    логируем warning и возвращаем детерминированно наименьшее (sorted()[0]).
    """
    admins = await list_active_admins_by_group(session, group_id)
    extensions = {
        a.mango_extension for a in admins
        if a.mango_extension and (exclude_user_id is None or a.id != exclude_user_id)
    }
    if not extensions:
        return None
    if len(extensions) > 1:
        logger.warning(
            "Группа id=%s: у активных админов разошедшиеся mango_extension: %s",
            group_id, sorted(extensions),
        )
    return sorted(extensions)[0]


async def list_by_role(
    session: AsyncSession, role: UserRole, *, active_only: bool = False
) -> list[User]:
    stmt = select(User).where(User.role == role)
    if active_only:
        stmt = stmt.where(User.is_active.is_(True))
    stmt = stmt.order_by(User.full_name)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_all(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.role, User.full_name))
    return list(result.scalars().all())


async def list_active_admins_by_group(
    session: AsyncSession, group_id: int
) -> list[User]:
    """Активные выездные администраторы (UserRole.ADMIN) группы.

    В группе их может быть несколько — заявка адресуется одному из них (выбор
    делает менеджер при создании).
    """
    result = await session.execute(
        select(User).where(
            User.role == UserRole.ADMIN,
            User.group_id == group_id,
            User.is_active.is_(True),
        ).order_by(User.full_name)
    )
    return list(result.scalars().all())


async def list_admins_without_extension(session: AsyncSession) -> list[User]:
    """Активные ADMIN, привязанные к группе, но без mango_extension.

    Такие пользователи не смогут инициировать звонок (MangoExtensionMissingError).
    """
    result = await session.execute(
        select(User).where(
            User.role == UserRole.ADMIN,
            User.group_id.isnot(None),
            User.is_active.is_(True),
            (User.mango_extension.is_(None)) | (User.mango_extension == ""),
        ).order_by(User.full_name)
    )
    return list(result.scalars().all())


async def list_by_group(session: AsyncSession, group_id: int) -> list[User]:
    """Все пользователи, привязанные к группе."""
    result = await session.execute(
        select(User).where(User.group_id == group_id).order_by(User.role, User.full_name)
    )
    return list(result.scalars().all())


async def unassign_all_from_group(session: AsyncSession, group_id: int) -> list[User]:
    """Отвязывает всех пользователей группы. Возвращает список отвязанных."""
    users = await list_by_group(session, group_id)
    for u in users:
        u.group_id = None
    if users:
        await session.flush()
        logger.info(
            "Отвязано %d пользователей от группы id=%s: %s",
            len(users), group_id, [u.tg_id for u in users],
        )
    return users
