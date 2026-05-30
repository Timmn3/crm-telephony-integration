"""Клиент amoCRM API.

Поддерживает два режима авторизации одновременно:
- long-lived access_token из .env (AMOCRM_ACCESS_TOKEN);
- OAuth 2.0 с автообновлением по refresh_token (токены хранятся в таблице
  settings и при обновлении перезаписываются там же).

При ответе 401 клиент пытается обновить токен по refresh_token и повторяет запрос.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repositories import settings_repo
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)
PHONE_FIELD_CODE = "PHONE"


# --------------------------------------------------------------------------- #
# Исключения
# --------------------------------------------------------------------------- #

class AmoCRMError(Exception):
    """Базовая ошибка amoCRM."""


class AmoAuthError(AmoCRMError):
    """Проблема авторизации (токен истёк и не удалось обновить)."""


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

    # ---------------------------------------------------------------- токены
    async def _get_access_token(self) -> str:
        token = await settings_repo.get(self.db, settings_repo.AMOCRM_ACCESS_TOKEN)
        return token or self.settings.amocrm_access_token

    async def _get_refresh_token(self) -> str:
        token = await settings_repo.get(self.db, settings_repo.AMOCRM_REFRESH_TOKEN)
        return token or self.settings.amocrm_refresh_token

    async def _refresh_access_token(self, http: aiohttp.ClientSession) -> str:
        """Обновляет access_token по refresh_token и сохраняет оба в БД."""
        refresh_token = await self._get_refresh_token()
        if not (refresh_token and self.settings.amocrm_client_id
                and self.settings.amocrm_client_secret):
            raise AmoAuthError(
                "Токен amoCRM истёк, а данных для обновления (refresh_token / "
                "client_id / client_secret) недостаточно."
            )

        payload = {
            "client_id": self.settings.amocrm_client_id,
            "client_secret": self.settings.amocrm_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "redirect_uri": self.settings.amocrm_redirect_uri,
        }
        url = f"{self.base_url}/oauth2/access_token"
        async with http.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("Ошибка обновления токена amoCRM: %s %s", resp.status, text)
                raise AmoAuthError("Не удалось обновить токен amoCRM.")
            data = await resp.json()

        await settings_repo.set_value(self.db, settings_repo.AMOCRM_ACCESS_TOKEN, data["access_token"])
        await settings_repo.set_value(self.db, settings_repo.AMOCRM_REFRESH_TOKEN, data["refresh_token"])
        logger.info("access_token amoCRM успешно обновлён")
        return data["access_token"]

    # ---------------------------------------------------------------- запросы
    async def _get(
        self, http: aiohttp.ClientSession, path: str, *, allow_404: bool = False
    ) -> dict | None:
        """GET-запрос с авто-обновлением токена при 401."""
        if not self.base_url:
            raise AmoCRMError("Не задан поддомен amoCRM (AMOCRM_SUBDOMAIN).")

        url = f"{self.base_url}{path}"
        token = await self._get_access_token()
        if not token:
            raise AmoAuthError("Не задан access_token amoCRM.")

        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {token}"}
            async with http.get(url, headers=headers) as resp:
                if resp.status == 401 and attempt == 1:
                    logger.info("amoCRM вернул 401, пробуем обновить токен")
                    token = await self._refresh_access_token(http)
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
        """Загружает сделку и связанный контакт, собирает данные заявки."""
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
