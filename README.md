# Telegram-бот «Маскированные звонки»

Бот для компании с выездными менеджерами. Оператор отдела продаж отправляет
заявку из amoCRM выездному менеджеру в Telegram. Менеджер видит карточку заявки
(имя клиента, адрес, комментарий), но **не видит номер телефона клиента**. Когда
менеджеру нужно позвонить, он запрашивает разрешение, оператор одобряет, и звонок
инициируется через Mango Office (номер клиента нигде в Telegram не светится).

Актуальное ТЗ — [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md).

## Стек

Python 3.11+, aiogram 3.x, FastAPI + uvicorn, SQLAlchemy 2.x (async, asyncpg),
Alembic, PostgreSQL 15, aiohttp, pydantic-settings, Docker.

## Модель работы (v2)

- **Группы вместо регионов.** В компании рабочие группы (Группа-1, Казань, Группа-5…).
  В каждой группе в любой момент работает **один** менеджер. Админ переназначает:
  старый менеджер при замене отвязывается от группы.
- **Без кнопки «Беру».** Заявка сразу уходит единственному менеджеру группы в ЛС
  (статус `SENT`). Конкуренции нет.
- **Статусы:** SENT → CALL_REQUESTED → CALL_APPROVED → CALL_IN_PROGRESS →
  COMPLETED (или CANCELLED).

## Роли

- **admin** — управляет операторами, менеджерами, группами. Назначается через
  `ADMIN_TG_IDS` в `.env` (при первом `/start`).
- **operator** — создаёт заявки (`/order`), одобряет/отклоняет звонки.
- **manager** — получает заявки, запрашивает звонок, звонит. При регистрации
  делится контактом (номер телефона).

## Команды

| Роль | Команды |
|------|---------|
| Все | `/start`, `/help`, `/me`, `/cancel` |
| Оператор | `/order`, `/my_orders` |
| Менеджер | `/my_tasks` |
| Админ | `/add_operator`, `/add_manager`, `/add_group`, `/groups`, `/users`, `/remove_user`, `/amo_fields`, `/set_amo_code` |

## Быстрый старт (Docker)

```bash
cp .env.example .env       # заполнить реальными значениями (см. ниже)
docker compose up -d --build
docker compose logs -f bot
```

При старте контейнер сам применяет миграции (`alembic upgrade head`), создаёт
предзаполненные группы (если их нет) и запускает бота (polling) + HTTP-сервер
(healthcheck `/health`, webhook Mango).

## Локальный запуск (без Docker)

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements-dev.txt
# поднять PostgreSQL и указать его в .env (DB_HOST/DB_PORT/...)
alembic upgrade head
python -m app.main
```

## Конфигурация (.env)

Все ключи описаны в [.env.example](.env.example). Минимально необходимое:

- `BOT_TOKEN` — токен бота от @BotFather.
- `ADMIN_TG_IDS` — Telegram ID администраторов через запятую.
- `DB_*` — параметры PostgreSQL.
- `MANGO_API_KEY`, `MANGO_API_SALT`, `MANGO_LINE_NUMBER` (номер 8-800 в формате
  `7XXXXXXXXXX`).
- amoCRM (OAuth): `AMOCRM_SUBDOMAIN`, `AMOCRM_CLIENT_ID`, `AMOCRM_CLIENT_SECRET`,
  `AMOCRM_REDIRECT_URI`, `AMOCRM_AUTH_CODE`.

### Режим-заглушка amoCRM

Пока реквизиты amoCRM (`AMOCRM_SUBDOMAIN`/`CLIENT_ID`/`CLIENT_SECRET`) не заданы,
бот работает в **режиме-заглушке**: `/order` по любому номеру сделки возвращает
тестового клиента (с тестовым телефоном). Это позволяет проверить весь флоу
(вплоть до звонка Mango) до получения ключей. После заполнения реквизитов и
`AMOCRM_AUTH_CODE` бот при старте обменяет код на токены (хранятся в БД,
обновляются автоматически). Если refresh-токен истёк — `/set_amo_code <code>`.

### Поля amoCRM

ID кастомных полей (адрес) различаются в каждом аккаунте. Узнать: команда
`/amo_fields` (от админа) выведет поля сделок и контактов с их ID. Затем задайте
`AMO_ADDRESS_FIELD_ID` в `.env`. Телефон ищется по `field_code = PHONE`
(можно переопределить `AMO_PHONE_FIELD_ID`).

## Регистрация пользователей: особенность Telegram

Бот **не может** узнать Telegram ID по `@username` и не может написать
пользователю первым, пока тот сам не обратился к боту. Поэтому менеджера/оператора
добавляют одним из способов:

1. Сотрудник пишет боту `/start` — бот покажет его Telegram ID; он сообщает ID админу.
2. Админ **пересылает боту сообщение** от сотрудника (бот узнаёт ID из пересылки).
3. По `@username` — только если бот уже «видел» этого пользователя.

## Сценарий звонка (маскирование)

1. Менеджер в карточке жмёт «Запросить звонок».
2. Оператор одобряет.
3. Менеджер жмёт «Позвонить» → бот шлёт callback в Mango с номером менеджера и
   клиента и линией 8-800. Mango звонит менеджеру, затем соединяет с клиентом.
   Реальные номера в Telegram не показываются.

Webhook событий Mango (опционально): `POST /webhooks/mango/call`. IP Mango для
whitelisting на reverse-proxy: `81.88.80.132`, `81.88.80.133`, `81.88.82.36`.

## Тесты

```bash
# нужен доступный PostgreSQL и БД call_masking_test
pip install -r requirements-dev.txt
pytest -q
```

## Структура

```text
app/
  main.py            # точка входа (polling + uvicorn)
  config.py          # настройки из .env
  logging_config.py  # логирование (консоль + файл)
  bot/               # aiogram: handlers, middlewares, filters, keyboards, states
  services/          # amocrm, mango, order_service, bootstrap (seed + auth)
  db/                # models, database, repositories
  api/               # FastAPI: healthcheck, webhook Mango
alembic/             # миграции
tests/               # unit + интеграционные тесты
```

## Безопасность

- Номер телефона клиента **никогда** не попадает в Telegram-сообщения,
  `callback_data` и логи — хранится только в БД, извлекается лишь в момент звонка.
- Проверка ролей и принадлежности на каждое действие.
- Кнопка «Позвонить» одноразовая (только в статусе «звонок одобрен»).
- Все звонки логируются в таблицу `call_log`.
- amoCRM-токены хранятся в БД; секреты Mango — только в `.env` на сервере.
