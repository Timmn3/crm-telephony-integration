"""SQLAlchemy-модели приложения.

Содержит таблицы users, regions, orders, call_log, settings и перечисления
ролей и статусов заявок.

ВАЖНО (безопасность): поле Order.client_phone хранит реальный номер клиента и
НИКОГДА не должно попадать в Telegram-сообщения, callback_data или логи. Оно
извлекается только в момент инициации звонка для передачи в Mango API.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""


class UserRole(str, PyEnum):
    """Роли пользователей."""

    ADMIN = "admin"
    OPERATOR = "operator"
    MANAGER = "manager"


class OrderStatus(str, PyEnum):
    """Статусы заявки (жизненный цикл)."""

    NEW = "new"                          # Создана оператором, ещё не отправлена
    SENT = "sent"                        # Отправлена менеджеру/в группу
    TAKEN = "taken"                      # Менеджер нажал «Беру»
    CALL_REQUESTED = "call_requested"    # Менеджер запросил звонок
    CALL_APPROVED = "call_approved"      # ОП одобрил звонок
    CALL_IN_PROGRESS = "call_in_progress"  # Звонок инициирован через Mango
    COMPLETED = "completed"              # Заявка закрыта
    CANCELLED = "cancelled"              # Заявка отменена


# Имена native enum-типов в PostgreSQL.
_USER_ROLE_ENUM = SAEnum(
    UserRole,
    name="user_role",
    values_callable=lambda enum: [member.value for member in enum],
)
_ORDER_STATUS_ENUM = SAEnum(
    OrderStatus,
    name="order_status",
    values_callable=lambda enum: [member.value for member in enum],
)


class Region(Base):
    """Регион выезда."""

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    tg_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="region")
    orders: Mapped[list["Order"]] = relationship(back_populates="region")

    def __repr__(self) -> str:
        return f"<Region id={self.id} name={self.name!r}>"


class User(Base):
    """Пользователь бота (админ, оператор или менеджер)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[UserRole] = mapped_column(_USER_ROLE_ENUM, nullable=False)
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("regions.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    region: Mapped[Region | None] = relationship(back_populates="users")

    def __repr__(self) -> str:
        return f"<User id={self.id} tg_id={self.tg_id} role={self.role}>"


class Order(Base):
    """Заявка из amoCRM, отправленная менеджеру."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amo_lead_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # НИКОГДА не отдаётся в Telegram. Используется только для звонка через Mango.
    client_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    client_address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"), nullable=False
    )
    operator_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manager_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        _ORDER_STATUS_ENUM, default=OrderStatus.NEW, nullable=False, index=True
    )
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    call_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    call_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    region: Mapped[Region] = relationship(back_populates="orders")
    call_logs: Mapped[list["CallLog"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} amo={self.amo_lead_id} status={self.status}>"


class CallLog(Base):
    """Журнал звонков, инициированных через Mango."""

    __tablename__ = "call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    manager_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mango_command_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship(back_populates="call_logs")

    def __repr__(self) -> str:
        return f"<CallLog id={self.id} order={self.order_id} status={self.status}>"


class Setting(Base):
    """Key-value хранилище настроек (токены amoCRM и т.п.)."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Setting key={self.key!r}>"
