# Telegram-бот «Маскированные звонки»

Бот для компании с выездными менеджерами. Оператор отдела продаж отправляет
заявку из amoCRM выездному менеджеру в Telegram. Менеджер видит карточку заявки
(имя клиента, адрес, комментарий), но **не видит номер телефона клиента**. Когда
менеджеру нужно позвонить, он запрашивает разрешение, оператор одобряет, и звонок
инициируется через Mango Office (номер клиента нигде в Telegram не светится).

## Стек

Python 3.11+, aiogram 3.x, FastAPI + uvicorn, SQLAlchemy 2.x (async, asyncpg),
Alembic, PostgreSQL 15, aiohttp, pydantic-settings, Docker.

## Роли

- **admin** — управляет операторами, менеджерами, регионами. Назначается через
  `ADMIN_TG_IDS` в `.env` (при первом `/start`).
- **operator** — создаёт заявки (`/order`), одобряет/отклоняет звонки.
- **manager** — берёт заявки, запрашивает звонок, звонит. При регистрации делится
  контактом (номер телефона).

## Команды

| Роль | Команды |
|------|---------|
| Все | `/start`, `/help`, `/me`, `/cancel` |
| Оператор | `/order`, `/my_orders` |
| Менеджер | `/my_tasks` |
| Админ | `/add_operator`, `/add_manager`, `/add_region`, `/regions`, `/users`, `/remove_user`, `/amo_fields` |

## Быстрый старт (Docker)

```bash
cp .env.example .env       # заполнить реальными значениями (см. ниже)
docker compose up -d --build
docker compose logs -f bot
```

При старте контейнер сам применяет миграции (`alembic upgrade head`) и запускает
бота (polling) + HTTP-сервер (healthcheck `/health`, webhook Mango).

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
- `AMOCRM_SUBDOMAIN`, `AMOCRM_ACCESS_TOKEN` (и для автообновления —
  `AMOCRM_REFRESH_TOKEN`, `AMOCRM_CLIENT_ID`, `AMOCRM_CLIENT_SECRET`,
  `AMOCRM_REDIRECT_URI`).
- `MANGO_API_KEY`, `MANGO_API_SALT`, `MANGO_LINE_NUMBER` (номер 8-800 в формате
  `7XXXXXXXXXX`).

### Поля amoCRM

ID кастомных полей (адрес) различаются в каждом аккаунте amoCRM. Узнать их:
команда `/amo_fields` в боте (от админа) выведет список полей сделок и контактов с
их ID. Затем задайте `AMO_ADDRESS_FIELD_ID` в `.env`. Телефон ищется автоматически
по `field_code = PHONE` (можно переопределить через `AMO_PHONE_FIELD_ID`).

## Регистрация пользователей: важная особенность Telegram

Бот **не может** узнать Telegram ID по `@username` и не может написать
пользователю первым, пока тот сам не обратился к боту. Поэтому:

1. Новый сотрудник пишет боту `/start` — бот покажет его Telegram ID.
2. Сотрудник сообщает ID администратору.
3. Админ выполняет `/add_operator <id>` или `/add_manager <id>` (затем выбирает
   регион).

`@username` поддерживается только для пользователей, которых бот уже «видел».

## Сценарий звонка (маскирование)

1. Менеджер берёт заявку → «Запросить звонок».
2. Оператор одобряет.
3. Менеджер жмёт «Позвонить» → бот шлёт callback в Mango с номером менеджера и
   клиента и линией 8-800. Mango звонит менеджеру, затем соединяет с клиентом.
   Реальные номера в Telegram не показываются.

Webhook событий Mango (опционально): `POST /webhooks/mango/call`. Для whitelisting
на стороне reverse-proxy IP Mango: `81.88.80.132`, `81.88.80.133`, `81.88.82.36`.

## Тесты

```bash
# нужен доступный PostgreSQL и БД call_masking_test
pip install -r requirements-dev.txt
pytest -q
```

## Структура

```
app/
  main.py            # точка входа (polling + uvicorn)
  config.py          # настройки из .env
  logging_config.py  # логирование (консоль + файл)
  bot/               # aiogram: handlers, middlewares, filters, keyboards, states
  services/          # amocrm, mango, order_service (бизнес-логика заявок)
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
- Конкурентное взятие заявки атомарно (один первый менеджер).
```
