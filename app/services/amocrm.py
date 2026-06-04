"""Клиент amoCRM API (OAuth 2.0).

Авторизация:
- одноразовый код (AMOCRM_AUTH_CODE) обменивается на access/refresh при первом
  запуске (см. exchange_code / ensure_token в main.py);
- access_token обновляется по refresh_token, когда до истечения < 5 минут;
- токены хранятся в БД (таблица amo_tokens), а не в .env.

Режим-заглушка: если реквизиты интеграции не заданы (settings.amocrm_configured
== False), get_order_data возвращает синтетического клиента с тестовым телефоном.
Это позволяет прогонять весь флоу (вплоть до звонка Mango) до получения ключей.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repositories import amo_token_repo
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)
PHONE_FIELD_CODE = "PHONE"
# За сколько до истечения access_token обновляем его заранее.
_REFRESH_MARGIN = timedelta(minutes=5)


# --------------------------------------------------------------------------- #
# Исключения
# --------------------------------------------------------------------------- #

class AmoCRMError(Exception):
    """Базовая ошибка amoCRM."""


class AmoAuthError(AmoCRMError):
    """Проблема авторизации (нет токена / refresh не удался)."""


class LeadNotFound(AmoCRMError):
    """Сделка не найдена."""


class PhoneNotFound(AmoCRMError):
    """У контакта сделки не найден телефон."""


# --------------------------------------------------------------------------- #
# Данные заявки из amoCRM
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class OrderData:
    """Извлечённые из amoCRM данные для заявки."""

    amo_lead_id: int
    client_name: str
    client_phone: str          # нормализован к 7XXXXXXXXXX
    client_address: str | None
    comment: str | None


@dataclass(slots=True)
class CustomFieldInfo:
    """Описание кастомного поля (для команды /amo_fields)."""

    id: int
    name: str
    field_type: str
    code: str | None


# --------------------------------------------------------------------------- #
# Клиент
# --------------------------------------------------------------------------- #

class AmoCRMClient:
    """Асинхронный клиент amoCRM. Создаётся на время обработки апдейта."""

    def __init__(self, session: AsyncSession) -> None:
        self.db = session
        self.settings = get_settings()
        self.base_url = self.settings.amocrm_base_url.rstrip("/")

    @property
    def is_stub(self) -> bool:
        """True — работаем в режиме-заглушке (реквизиты amoCRM не заданы)."""
        return not self.settings.amocrm_configured

    # ---------------------------------------------------------------- токены
    async def exchange_code(self, code: str, http: aiohttp.ClientSession | None = None) -> None:
        """Обменивает одноразовый код авторизации на access/refresh токены."""
        payload = {
            "client_id": self.settings.amocrm_client_id,
            "client_secret": self.settings.amocrm_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.amocrm_redirect_uri,
        }
        await self._request_token(payload, http)
        logger.info("Код авторизации amoCRM обменян на токены")

    async def _refresh(self, http: aiohttp.ClientSession) -> str:
        """Обновляет access_token по refresh_token, сохраняет в БД."""
        token = await amo_token_repo.get(self.db)
        if token is None or not token.refresh_token:
            raise AmoAuthError(
                "Нет refresh_token amoCRM. Выполните первичную авторизацию "
                "(AMOCRM_AUTH_CODE) или обновите код командой /set_amo_code."
            )
        payload = {
            "client_id": self.settings.amocrm_client_id,
            "client_secret": self.settings.amocrm_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "redirect_uri": self.settings.amocrm_redirect_uri,
        }
        return await self._request_token(payload, http)

    async def _request_token(
        self, payload: dict, http: aiohttp.ClientSession | None
    ) -> str:
        """Запрашивает токены у amoCRM и сохраняет их в БД. Возвращает access."""
        if not self.base_url:
            raise AmoAuthError("Не задан поддомен amoCRM (AMOCRM_SUBDOMAIN).")
        url = f"{self.base_url}/oauth2/access_token"
        own_http = http is None
        http = http or aiohttp.ClientSession(timeout=_TIMEOUT)
        try:
            async with http.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("amoCRM oauth2 -> %s %s", resp.status, text)
                    raise AmoAuthError(
                        "Не удалось получить токен amoCRM "
                        f"(HTTP {resp.status}). Код/refresh мог истечь."
                    )
                data = await resp.json()
        finally:
            if own_http:
                await http.close()

        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(data.get("expires_in", 0))
        )
        await amo_token_repo.save(
            self.db,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=expires_at,
        )
        return data["access_token"]

    async def _get_access_token(self, http: aiohttp.ClientSession) -> str:
        """Возвращает валидный access_token, при необходимости обновив его."""
        token = await amo_token_repo.get(self.db)
        if token is None:
            raise AmoAuthError(
                "amoCRM не авторизован. Задайте AMOCRM_AUTH_CODE и перезапустите "
                "бота либо примените код командой /set_amo_code."
            )
        if token.expires_at <= datetime.now(timezone.utc) + _REFRESH_MARGIN:
            logger.info("access_token amoCRM скоро истечёт — обновляем заранее")
            return await self._refresh(http)
        return token.access_token

    # ---------------------------------------------------------------- запросы
    async def _get(
        self, http: aiohttp.ClientSession, path: str, *, allow_404: bool = False
    ) -> dict | None:
        """GET-запрос с авто-обновлением токена при 401."""
        if not self.base_url:
            raise AmoCRMError("Не задан поддомен amoCRM (AMOCRM_SUBDOMAIN).")

        url = f"{self.base_url}{path}"
        token = await self._get_access_token(http)

        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {token}"}
            async with http.get(url, headers=headers) as resp:
                if resp.status == 401 and attempt == 1:
                    logger.info("amoCRM вернул 401, пробуем обновить токен")
                    token = await self._refresh(http)
                    continue
                if resp.status == 204:
                    return None
                if resp.status == 404:
                    if allow_404:
                        return None
                    raise LeadNotFound("Объект не найден в amoCRM.")
                if resp.status == 401:
                    raise AmoAuthError("amoCRM: доступ запрещён даже после обновления токена.")
                if resp.status >= 400:
                    text = await resp.text()
                    logger.error("amoCRM %s -> %s %s", path, resp.status, text)
                    raise AmoCRMError(f"amoCRM вернул ошибку {resp.status}.")
                return await resp.json()
        raise AmoCRMError("Не удалось выполнить запрос к amoCRM.")

    # ---------------------------------------------------------------- API
    async def get_order_data(self, lead_id: int) -> OrderData:
        """Загружает сделку и связанный контакт, собирает данные заявки.

        В режиме-заглушке возвращает синтетического клиента.
        """
        if self.is_stub:
            logger.info("[amoCRM STUB] возвращаю тестовые данные по сделке #%s", lead_id)
            return OrderData(
                amo_lead_id=lead_id,
                client_name=f"Тестовый Клиент #{lead_id}",
                client_phone=self.settings.amocrm_stub_phone,
                client_address="г. Москва, ул. Тестовая, 1",
                comment="Тестовая заявка (amoCRM не настроен)",
            )

        async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
            lead = await self._get(http, f"/api/v4/leads/{lead_id}?with=contacts")
            if lead is None:
                raise LeadNotFound(f"Сделка #{lead_id} не найдена.")

            lead_name = lead.get("name") or ""
            address = self._extract_address(lead)

            contact = None
            contact_id = self._first_contact_id(lead)
            if contact_id is not None:
                contact = await self._get(
                    http, f"/api/v4/contacts/{contact_id}", allow_404=True
                )

            client_name = ""
            raw_phone: str | None = None
            if contact is not None:
                client_name = contact.get("name") or ""
                raw_phone = self._extract_phone(contact)
                if address is None:
                    address = self._extract_address(contact)

            if not client_name:
                client_name = lead_name or "Клиент"

            phone = normalize_phone(raw_phone)
            if phone is None:
                raise PhoneNotFound(
                    "У контакта сделки не найден корректный номер телефона."
                )

            return OrderData(
                amo_lead_id=lead_id,
                client_name=client_name,
                client_phone=phone,
                client_address=address,
                comment=lead_name or None,
            )

    async def list_custom_fields(self) -> dict[str, list[CustomFieldInfo]]:
        """Возвращает кастомные поля сделок и контактов (для /amo_fields)."""
        if self.is_stub:
            raise AmoCRMError(
                "amoCRM не настроен (режим-заглушка). Поля недоступны, пока не "
                "заданы реквизиты интеграции и не выполнена авторизация."
            )
        result: dict[str, list[CustomFieldInfo]] = {"leads": [], "contacts": []}
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
            for entity in ("leads", "contacts"):
                data = await self._get(
                    http, f"/api/v4/{entity}/custom_fields", allow_404=True
                )
                fields = (data or {}).get("_embedded", {}).get("custom_fields", [])
                result[entity] = [
                    CustomFieldInfo(
                        id=f.get("id"),
                        name=f.get("name", ""),
                        field_type=f.get("type", ""),
                        code=f.get("code"),
                    )
                    for f in fields
                ]
        return result

    # ---------------------------------------------------------------- парсинг
    @staticmethod
    def _first_contact_id(lead: dict) -> int | None:
        contacts = lead.get("_embedded", {}).get("contacts", [])
        if not contacts:
            return None
        # Предпочитаем основной контакт (is_main), иначе первый.
        for c in contacts:
            if c.get("is_main"):
                return c.get("id")
        return contacts[0].get("id")

    def _extract_phone(self, contact: dict) -> str | None:
        """Достаёт телефон из custom_fields_values контакта."""
        fields = contact.get("custom_fields_values") or []
        configured_id = self.settings.amo_phone_field_id

        for field in fields:
            field_id = field.get("field_id")
            code = field.get("field_code")
            is_phone = (
                (configured_id is not None and field_id == configured_id)
                or code == PHONE_FIELD_CODE
            )
            if is_phone:
                values = field.get("values") or []
                if values and values[0].get("value"):
                    return str(values[0]["value"])
        return None

    def _extract_address(self, entity: dict) -> str | None:
        """Достаёт адрес из кастомного поля (по настроенному ID)."""
        field_id = self.settings.amo_address_field_id
        if field_id is None:
            return None
        fields = entity.get("custom_fields_values") or []
        for field in fields:
            if field.get("field_id") == field_id:
                values = field.get("values") or []
                if values and values[0].get("value"):
                    return str(values[0]["value"])
        return None
