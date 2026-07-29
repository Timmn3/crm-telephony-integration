"""Конфигурация приложения через переменные окружения (.env).

Все секреты и настройки читаются из .env через pydantic-settings.
Никогда не храните реальные секреты в коде — только в .env (см. .env.example).
"""
from __future__ import annotations

import re
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения.

    Значения берутся из переменных окружения или файла .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ Telegram
    bot_token: str = Field(..., alias="BOT_TOKEN")
    # Храним сырой строкой, чтобы pydantic-settings не пытался JSON-парсить список.
    admin_tg_ids_raw: str = Field("", alias="ADMIN_TG_IDS")

    # ------------------------------------------------------------------ PostgreSQL
    db_host: str = Field("localhost", alias="DB_HOST")
    db_port: int = Field(5432, alias="DB_PORT")
    db_name: str = Field("call_masking_bot", alias="DB_NAME")
    db_user: str = Field("bot", alias="DB_USER")
    db_password: str = Field("", alias="DB_PASSWORD")

    # ------------------------------------------------------------------ amoCRM
    # OAuth 2.0: токены хранятся в БД (таблица amo_tokens), не в .env.
    # В .env только реквизиты интеграции и одноразовый код авторизации.
    amocrm_subdomain: str = Field("", alias="AMOCRM_SUBDOMAIN")
    amocrm_client_id: str = Field("", alias="AMOCRM_CLIENT_ID")
    amocrm_client_secret: str = Field("", alias="AMOCRM_CLIENT_SECRET")
    amocrm_redirect_uri: str = Field("", alias="AMOCRM_REDIRECT_URI")
    # Одноразовый код авторизации — обменивается на токены при первом запуске.
    amocrm_auth_code: str = Field("", alias="AMOCRM_AUTH_CODE")
    # Долгосрочный токен (до 2030) — альтернатива auth_code для деплоя с нуля.
    # При старте записывается в БД если таблица пустая.
    amocrm_long_token: str = Field("", alias="AMOCRM_LONG_TOKEN")
    amo_address_field_id: int | None = Field(None, alias="AMO_ADDRESS_FIELD_ID")
    amo_phone_field_id: int | None = Field(None, alias="AMO_PHONE_FIELD_ID")
    # Телефон синтетического клиента в режиме-заглушке (для отладки звонка).
    amocrm_stub_phone: str = Field("79991234567", alias="AMOCRM_STUB_PHONE")

    # ------------------------------------------------------------------ Mango Office
    mango_api_key: str = Field("", alias="MANGO_API_KEY")
    mango_api_salt: str = Field("", alias="MANGO_API_SALT")
    mango_api_url: str = Field("https://app.mango-office.ru/vpbx", alias="MANGO_API_URL")
    # Корпоративный номер-маска (АОН), который видит клиент. ВАЖНО: НЕ 8-800 —
    # федеральные 8-800 Mango не выпускает как исходящие. Только городской/мобильный
    # номер, подключённый к ВАТС, формат 7XXXXXXXXXX.
    mango_line_number: str = Field("", alias="MANGO_LINE_NUMBER")

    # ------------------------------------------------------------------ Сервер
    server_host: str = Field("0.0.0.0", alias="SERVER_HOST")
    server_port: int = Field(8080, alias="SERVER_PORT")
    webhook_base_url: str = Field("", alias="WEBHOOK_BASE_URL")

    # ------------------------------------------------------------------ Прочее
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    coder_tg_id: int | None = Field(None, alias="CODER")

    @field_validator("amo_address_field_id", "amo_phone_field_id", mode="before")
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        """Пустая строка в .env означает «не задано»."""
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("mango_line_number", mode="after")
    @classmethod
    def _validate_line_number(cls, value: str) -> str:
        """Маскирующий номер — формат 7XXXXXXXXXX (11 цифр, начинается с 7).

        Пустая строка допустима на уровне конфига (обязательность проверяется при
        инициации звонка), но кривой формат (напр. 8-800 как 88005559802) ловим сразу,
        чтобы он не ушёл в Mango незаметно.
        """
        if value and not re.fullmatch(r"7\d{10}", value):
            raise ValueError(
                "MANGO_LINE_NUMBER должен быть в формате 7XXXXXXXXXX "
                "(11 цифр, начинается с 7); напр. 88005559802 недопустим."
            )
        return value

    @property
    def admin_tg_ids(self) -> list[int]:
        """Список Telegram ID администраторов (из строки "123,456")."""
        raw = self.admin_tg_ids_raw
        if not raw:
            return []
        return [int(part.strip()) for part in raw.split(",") if part.strip()]

    @property
    def database_url(self) -> str:
        """Async DSN для SQLAlchemy + asyncpg."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync DSN (для Alembic, использует psycopg/asyncpg драйвер через async)."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def amocrm_base_url(self) -> str:
        """Базовый URL amoCRM API."""
        subdomain = self.amocrm_subdomain
        if subdomain and not subdomain.startswith("http"):
            return f"https://{subdomain}"
        return subdomain

    @property
    def amocrm_configured(self) -> bool:
        """True, если заданы реквизиты OAuth-интеграции amoCRM.

        Если False — клиент работает в режиме-заглушке (фейковые данные сделки),
        что позволяет прогонять весь флоу до получения реальных ключей.
        """
        return bool(
            self.amocrm_subdomain
            and self.amocrm_client_id
            and self.amocrm_client_secret
        )


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек."""
    return Settings()  # type: ignore[call-arg]
