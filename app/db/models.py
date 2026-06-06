"""SQLAlchemy-модели приложения.

Содержит таблицы users, groups, orders, call_log, amo_tokens и перечисления
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

    DIRECTOR = "director"
    MANAGER = "manager"
    ADMIN = "admin"


class OrderStatus(str, PyEnum):
    """Статусы заявки (жизненный цикл).

    Заявка сразу создаётся в статусе SENT и отправляется выбранному администратору
    группы — кнопки «Беру» нет, конкуренции нет (статусы NEW/TAKEN убраны в v2).
    """

    SENT = "sent"                        # Отправлена администратору
    CALL_REQUESTED = "call_requested"    # Администратор запросил звонок
    CALL_APPROVED = "call_approved"      # Менеджер одобрил звонок
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


class Group(Base):
    """Рабочая группа выездных администраторов.

    К группе привязываются выездные администраторы (UserRole.ADMIN, см.
    User.group_id) — их в одной группе может быть несколько. Заявку менеджер
    адресует конкретному админу группы при создании.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="group")
    orders: Mapped[list["Order"]] = relationship(back_populates="group")

    def __repr__(self) -> str:
        return f"<Group id={self.id} name={self.name!r} city={self.city!r}>"


class User(Base):
    """Пользователь бота (директор, менеджер или администратор)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[UserRole] = mapped_column(_USER_ROLE_ENUM, nullable=False)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    group: Mapped[Group | None] = relationship(back_populates="users")

    def __repr__(self) -> str:
        return f"<User id={self.id} tg_id={self.tg_id} role={self.role}>"


class Order(Base):
    """Заявка из amoCRM, отправленная выбранному администратору группы."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amo_lead_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # НИКОГДА не отдаётся в Telegram. Используется только для звонка через Mango.
    client_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    client_address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    operator_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Получатель-администратор: менеджер выбирает его из группы при создании.
    manager_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        _ORDER_STATUS_ENUM, default=OrderStatus.SENT, nullable=False, index=True
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

    group: Mapped[Group] = relationship(back_populates="orders")
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


class PendingUser(Base):
    """Обращение незарегистрированного пользователя, ожидающее назначения роли.

    Создаётся при первом /start незнакомца и служит «флагом» того, что админы
    уже уведомлены: пока строка существует, повторные /start не дублируют
    уведомления. Удаляется, когда пользователю назначена роль (или его пропустили).
    """

    __tablename__ = "pending_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    tg_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<PendingUser id={self.id} tg_id={self.tg_id}>"


class AmoToken(Base):
    """OAuth-токены amoCRM (всегда одна запись).

    Хранятся в БД, а не в .env: access/refresh обновляются автоматически по
    refresh_token. В .env остаётся только одноразовый код авторизации.
    """

    __tablename__ = "amo_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AmoToken id={self.id} expires_at={self.expires_at}>"
