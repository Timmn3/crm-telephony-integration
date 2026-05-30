# Техническое задание: Telegram-бот «Маскированные звонки»

## Суть проекта

Telegram-бот для компании с выездными менеджерами. Бот позволяет оператору отдела продаж (ОП) отправить заявку из amoCRM выездному менеджеру в Telegram. Менеджер видит карточку заявки (имя клиента, адрес, комментарий), но **не видит номер телефона клиента**. Когда менеджеру нужно позвонить клиенту, он запрашивает разрешение через бот, ОП одобряет, и менеджер звонит через Mango Office API. Клиент видит номер 8-800, менеджер видит 8-800. Реальные номера скрыты.

---

## Стек технологий

- **Python 3.11+**
- **aiogram 3.x** — Telegram Bot API
- **FastAPI** — HTTP-сервер для webhook-ов и внутреннего API
- **SQLAlchemy 2.x + asyncpg** — ORM и async драйвер PostgreSQL
- **Alembic** — миграции БД
- **PostgreSQL 15+** — основная БД
- **aiohttp** — HTTP-клиент для запросов к amoCRM API и Mango API
- **pydantic-settings** — конфигурация через .env
- **Docker + docker-compose** — деплой

---

## Структура проекта

```
call_masking_bot/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── alembic.ini
├── alembic/
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── main.py                  # Точка входа: запуск бота и FastAPI
│   ├── config.py                # Pydantic Settings, чтение .env
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── bot.py               # Создание Bot и Dispatcher
│   │   ├── handlers/
│   │   │   ├── __init__.py
│   │   │   ├── start.py         # /start, регистрация менеджера
│   │   │   ├── operator.py      # Команды оператора: создание заявки
│   │   │   ├── manager.py       # Действия менеджера: взять заявку, запросить звонок
│   │   │   └── admin.py         # Админ: управление менеджерами и регионами
│   │   ├── keyboards/
│   │   │   ├── __init__.py
│   │   │   └── inline.py        # Инлайн-клавиатуры
│   │   ├── middlewares/
│   │   │   ├── __init__.py
│   │   │   └── auth.py          # Проверка ролей (оператор/менеджер/админ)
│   │   └── filters/
│   │       ├── __init__.py
│   │       └── role.py          # Фильтры по ролям
│   ├── services/
│   │   ├── __init__.py
│   │   ├── amocrm.py            # Клиент amoCRM API
│   │   ├── mango.py             # Клиент Mango Office API
│   │   └── order_service.py     # Бизнес-логика заявок
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py          # Async engine, session factory
│   │   ├── models.py            # SQLAlchemy модели
│   │   └── repositories/
│   │       ├── __init__.py
│   │       ├── user_repo.py     # CRUD для пользователей
│   │       └── order_repo.py    # CRUD для заявок
│   └── api/
│       ├── __init__.py
│       └── webhooks.py          # FastAPI эндпоинты (Mango webhooks, healthcheck)
└── tests/
    └── ...
```

---

## Конфигурация (.env)

```env
# Telegram
BOT_TOKEN=<токен бота от @BotFather>
ADMIN_TG_IDS=123456789,987654321  # Telegram ID администраторов (через запятую)

# PostgreSQL
DB_HOST=postgres
DB_PORT=5432
DB_NAME=call_masking_bot
DB_USER=bot
DB_PASSWORD=<пароль>

# amoCRM
AMOCRM_SUBDOMAIN=<поддомен>.amocrm.ru
AMOCRM_ACCESS_TOKEN=<long-lived токен или OAuth токен>
AMOCRM_REDIRECT_URI=<redirect uri для OAuth>
AMOCRM_CLIENT_ID=<client_id интеграции>
AMOCRM_CLIENT_SECRET=<client_secret интеграции>

# Mango Office
MANGO_API_KEY=<vpbx_api_key>
MANGO_API_SALT=<vpbx_api_salt>
MANGO_API_URL=https://app.mango-office.ru/vpbx
MANGO_LINE_NUMBER=<номер 8-800 без плюса, формат 78001234567>

# Сервер
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
WEBHOOK_BASE_URL=https://<домен сервера>
```

---

## Модели базы данных (SQLAlchemy)

### Таблица `users`

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer, PK | Автоинкремент |
| tg_id | BigInteger, unique, index | Telegram user ID |
| tg_username | String, nullable | @username |
| full_name | String | Имя из Telegram |
| phone | String, nullable | Номер телефона (для менеджеров, из «Поделиться контактом») |
| role | Enum('admin', 'operator', 'manager') | Роль пользователя |
| region_id | Integer, FK → regions.id, nullable | Регион (для менеджеров) |
| is_active | Boolean, default True | Активен ли пользователь |
| created_at | DateTime | Дата создания |

### Таблица `regions`

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer, PK | Автоинкремент |
| name | String, unique | Название региона ("Москва") |
| tg_group_id | BigInteger, nullable | ID Telegram-группы региона (если используется групповая рассылка) |
| is_active | Boolean, default True | Активен ли регион |

### Таблица `orders`

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer, PK | Автоинкремент |
| amo_lead_id | BigInteger, index | ID сделки в amoCRM |
| client_name | String | Имя клиента (из amoCRM) |
| client_phone | String | Телефон клиента (из amoCRM, **никогда не отдаётся в Telegram**) |
| client_address | String, nullable | Адрес выезда |
| comment | Text, nullable | Комментарий к заявке |
| region_id | Integer, FK → regions.id | Регион заявки |
| operator_tg_id | BigInteger | Telegram ID оператора, создавшего заявку |
| manager_tg_id | BigInteger, nullable | Telegram ID назначенного менеджера |
| status | Enum (см. ниже) | Статус заявки |
| tg_message_id | BigInteger, nullable | ID сообщения с карточкой в Telegram (для редактирования кнопок) |
| tg_chat_id | BigInteger, nullable | ID чата, куда отправлена карточка |
| call_requested_at | DateTime, nullable | Когда менеджер запросил звонок |
| call_approved_at | DateTime, nullable | Когда ОП одобрил звонок |
| created_at | DateTime | Дата создания |
| updated_at | DateTime | Дата обновления |

### Статусы заявки (Enum `OrderStatus`)

```python
class OrderStatus(str, Enum):
    NEW = "new"                      # Создана оператором, ещё не отправлена
    SENT = "sent"                    # Отправлена менеджеру/в группу
    TAKEN = "taken"                  # Менеджер нажал «Беру»
    CALL_REQUESTED = "call_requested"  # Менеджер запросил звонок
    CALL_APPROVED = "call_approved"    # ОП одобрил звонок
    CALL_IN_PROGRESS = "call_in_progress"  # Звонок инициирован через Mango
    COMPLETED = "completed"          # Заявка закрыта
    CANCELLED = "cancelled"          # Заявка отменена
```

### Таблица `call_log`

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer, PK | Автоинкремент |
| order_id | Integer, FK → orders.id | Заявка |
| manager_tg_id | BigInteger | Менеджер |
| mango_command_id | String | command_id из запроса к Mango |
| status | String | Статус звонка (initiated, answered, missed, error) |
| duration | Integer, nullable | Длительность в секундах |
| created_at | DateTime | Когда инициирован |

---

## Роли и доступы

- **admin** — добавляет/удаляет операторов, менеджеров, регионы. Назначается через ADMIN_TG_IDS в .env при первом /start, или командой существующего админа.
- **operator** — создаёт заявки (вводит номер сделки amoCRM), одобряет/отклоняет запросы на звонок. Добавляется админом.
- **manager** — получает заявки, берёт их, запрашивает звонок, звонит. При регистрации обязан поделиться контактом (кнопка «Поделиться номером телефона»). Добавляется админом, привязывается к региону.
- Неизвестный пользователь — при /start видит сообщение «Обратитесь к администратору для получения доступа».

---

## Сценарии работы (пошагово)

### Сценарий 1: Регистрация менеджера

1. Менеджер пишет боту `/start`.
2. Бот: «Вы не зарегистрированы. Обратитесь к администратору.»
3. Админ в боте: `/add_manager @username` или нажимает кнопку «Добавить менеджера».
4. Бот запрашивает у админа регион для менеджера (инлайн-кнопки с регионами).
5. Админ выбирает регион.
6. Бот отправляет менеджеру сообщение: «Вас добавили. Пожалуйста, поделитесь номером телефона для связи.» + ReplyKeyboard с кнопкой KeyboardButton(text="Поделиться номером", request_contact=True).
7. Менеджер жмёт кнопку, бот получает contact.phone_number, сохраняет в БД.
8. Бот: «Готово! Вы зарегистрированы как выездной менеджер, регион: Москва.»

### Сценарий 2: Оператор создаёт заявку

1. Оператор пишет боту `/order` или нажимает кнопку «Новая заявка».
2. Бот: «Введите номер сделки из amoCRM:»
3. Оператор вводит число, например `12345678`.
4. Бот делает запрос к amoCRM API: `GET /api/v4/leads/{id}?with=contacts` — получает данные сделки и связанного контакта (имя, телефон, адрес из кастомных полей).
5. Если сделка не найдена: «Сделка не найдена, проверьте номер.»
6. Если найдена, бот показывает превью:
   ```
   Сделка #12345678
   Клиент: Иванов Иван Иванович
   Адрес: г. Москва, ул. Ленина, 10
   Комментарий: Замер окон, 2 этаж

   Телефон клиента найден ✓ (скрыт)

   Выберите регион:
   [Москва] [Москва] [Казань] ...
   ```
7. Оператор выбирает регион.
8. Бот: «Отправить всем менеджерам региона или выбрать конкретного?»
   - [Всем в регион] [Выбрать менеджера]
9. Если «Выбрать менеджера» — бот показывает список активных менеджеров региона.
10. Бот отправляет заявку (менеджеру в ЛС или в группу региона):
    ```
    📋 Заявка #12345678
    Клиент: Иванов Иван Иванович
    Адрес: г. Москва, ул. Ленина, 10
    Комментарий: Замер окон, 2 этаж

    [Беру заявку]
    ```
11. Заявка сохраняется в БД со статусом `SENT`.

### Сценарий 3: Менеджер берёт заявку

1. Менеджер нажимает «Беру заявку» (InlineKeyboardButton, callback_data=`take_order:{order_id}`).
2. Бот проверяет: заявка в статусе SENT, менеджер активен и принадлежит нужному региону.
3. Статус → `TAKEN`, `manager_tg_id` записывается.
4. Сообщение обновляется (edit_message_text + edit_message_reply_markup):
   ```
   📋 Заявка #12345678
   Клиент: Иванов Иван Иванович
   Адрес: г. Москва, ул. Ленина, 10
   Комментарий: Замер окон, 2 этаж
   Менеджер: Сергей К.

   [Запросить звонок клиенту]
   ```
5. Оператору приходит уведомление: «Менеджер Сергей К. взял заявку #12345678».

### Сценарий 4: Менеджер запрашивает звонок

1. Менеджер нажимает «Запросить звонок клиенту» (callback_data=`request_call:{order_id}`).
2. Бот проверяет: заявка в статусе TAKEN, менеджер совпадает.
3. Статус → `CALL_REQUESTED`, `call_requested_at` = now().
4. Сообщение у менеджера обновляется:
   ```
   📋 Заявка #12345678
   ...
   ⏳ Звонок запрошен, ожидайте одобрения
   ```
5. Оператору (тому, кто создал заявку) приходит сообщение:
   ```
   🔔 Менеджер Сергей К. запрашивает звонок клиенту
   Заявка #12345678
   Клиент: Иванов Иван Иванович

   [Одобрить] [Отклонить]
   ```

### Сценарий 5: Оператор одобряет звонок

1. Оператор нажимает «Одобрить» (callback_data=`approve_call:{order_id}`).
2. Статус → `CALL_APPROVED`, `call_approved_at` = now().
3. Сообщение у оператора обновляется: «✅ Звонок одобрен».
4. Сообщение у менеджера обновляется:
   ```
   📋 Заявка #12345678
   ...
   ✅ Звонок одобрен

   [📞 Позвонить клиенту]
   ```

### Сценарий 6: Менеджер звонит

1. Менеджер нажимает «Позвонить клиенту» (callback_data=`make_call:{order_id}`).
2. Бот проверяет: статус CALL_APPROVED, менеджер совпадает.
3. Бот берёт из БД: `manager.phone` (номер менеджера) и `order.client_phone` (номер клиента).
4. Бот отправляет POST-запрос к Mango API:
   ```
   POST https://app.mango-office.ru/vpbx/commands/callback
   ```
   Тело запроса (JSON внутри form-data):
   ```json
   {
     "command_id": "cb_<order_id>_<timestamp>",
     "from": {
       "extension": "",
       "number": "<phone менеджера, формат 7XXXXXXXXXX>"
     },
     "to_number": "<phone клиента, формат 7XXXXXXXXXX>",
     "line_number": "<номер 8-800, формат 7XXXXXXXXXX>"
   }
   ```
   Подпись запроса:
   ```python
   sign = sha256(api_key + json_string + api_salt).hexdigest()
   ```
   POST form-data:
   ```
   vpbx_api_key=<api_key>&sign=<sign>&json=<json_string>
   ```
5. Если Mango вернул успех — статус → `CALL_IN_PROGRESS`, запись в `call_log`.
6. Сообщение менеджеру: «📞 Звонок инициирован. Ожидайте, Mango позвонит вам на ваш номер.»
7. Если Mango вернул ошибку — сообщение менеджеру: «Не удалось инициировать звонок, попробуйте позже.» Лог ошибки в `call_log`.

### Сценарий 7: Оператор отклоняет звонок

1. Оператор нажимает «Отклонить» (callback_data=`reject_call:{order_id}`).
2. Бот запрашивает причину (можно пропустить).
3. Статус → `TAKEN` (возврат).
4. Менеджеру: «Запрос на звонок отклонён. Причина: ...»
5. Кнопка «Запросить звонок клиенту» снова доступна.

---

## Интеграция с amoCRM API

### Авторизация

amoCRM использует OAuth 2.0. Реализовать:
- Хранение access_token и refresh_token в БД (таблица `settings` или файл).
- Автообновление access_token по refresh_token, когда текущий истёк.

### Получение данных сделки

```
GET https://{subdomain}.amocrm.ru/api/v4/leads/{lead_id}?with=contacts
Headers: Authorization: Bearer {access_token}
```

Из ответа берём:
- `name` — название сделки (может пригодиться как комментарий).
- `custom_fields_values` — ищем поле с адресом (по имени или ID поля, настраивается в конфиге).
- `_embedded.contacts[0].id` — ID контакта.

Далее получаем контакт:
```
GET https://{subdomain}.amocrm.ru/api/v4/contacts/{contact_id}
```

Из контакта:
- `name` — имя клиента.
- `custom_fields_values` — ищем поле типа «phone» (field_code = "PHONE"), берём первый номер.

### Нормализация номера телефона

Все номера приводим к формату `7XXXXXXXXXX` (10 цифр после 7). Удаляем `+`, пробелы, скобки, дефисы. Если начинается на `8` — заменяем на `7`.

```python
import re

def normalize_phone(raw: str) -> str:
    """Приводит номер к формату 7XXXXXXXXXX."""
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    return digits
```

---

## Интеграция с Mango Office API

### Инициация звонка (callback)

```
POST https://app.mango-office.ru/vpbx/commands/callback
Content-Type: application/x-www-form-urlencoded
```

Формирование запроса:

```python
import hashlib
import json
import time

def make_mango_callback(
    api_key: str,
    api_salt: str,
    manager_phone: str,
    client_phone: str,
    line_number: str,
    order_id: int,
) -> dict:
    """Формирует подписанный запрос callback к Mango API."""
    data = {
        "command_id": f"cb_{order_id}_{int(time.time())}",
        "from": {
            "extension": "",
            "number": manager_phone,  # формат 7XXXXXXXXXX
        },
        "to_number": client_phone,    # формат 7XXXXXXXXXX
        "line_number": line_number,   # номер 8-800, формат 7XXXXXXXXXX
    }
    json_str = json.dumps(data)
    sign = hashlib.sha256(
        (api_key + json_str + api_salt).encode()
    ).hexdigest()
    return {
        "vpbx_api_key": api_key,
        "sign": sign,
        "json": json_str,
    }
```

Отправка:
```python
async with aiohttp.ClientSession() as session:
    async with session.post(
        "https://app.mango-office.ru/vpbx/commands/callback",
        data=make_mango_callback(...),
    ) as resp:
        result = await resp.json()
        # result содержит ключ "result" при ошибке
```

### Webhook от Mango (опционально, для логирования)

Mango может слать уведомления о событиях звонков на наш URL. Настраивается в ЛК Mango.

```python
# app/api/webhooks.py
@router.post("/webhooks/mango/call")
async def mango_call_event(request: Request):
    """Принимает уведомления от Mango о событиях звонков."""
    form = await request.form()
    json_data = json.loads(form.get("json", "{}"))
    # Обработка: обновление call_log, уведомление менеджера
```

IP Mango для whitelist: `81.88.80.132`, `81.88.80.133`, `81.88.82.36`.

---

## Команды бота

### Общие
- `/start` — регистрация / приветствие по роли
- `/help` — справка по доступным командам (зависит от роли)
- `/me` — информация о себе (роль, регион)

### Оператор
- `/order` — создать новую заявку (начинает диалог с вводом номера сделки)
- `/my_orders` — список активных заявок, созданных этим оператором

### Менеджер
- `/my_tasks` — список заявок, назначенных на этого менеджера

### Админ
- `/add_operator` — добавить оператора (бот запрашивает TG ID или username)
- `/add_manager` — добавить менеджера (бот запрашивает TG ID или username, затем регион)
- `/add_region <название>` — создать регион
- `/regions` — список регионов
- `/users` — список пользователей с ролями
- `/remove_user` — деактивировать пользователя

---

## Инлайн-клавиатуры (callback_data)

Формат callback_data: `action:order_id` или `action:order_id:extra`.

| callback_data | Кто нажимает | Что делает |
|---|---|---|
| `take_order:{order_id}` | Менеджер | Взять заявку |
| `request_call:{order_id}` | Менеджер | Запросить звонок |
| `make_call:{order_id}` | Менеджер | Инициировать звонок через Mango |
| `approve_call:{order_id}` | Оператор | Одобрить звонок |
| `reject_call:{order_id}` | Оператор | Отклонить звонок |
| `complete_order:{order_id}` | Оператор / Менеджер | Закрыть заявку |
| `select_region:{region_id}` | Оператор | Выбрать регион при создании заявки |
| `select_manager:{user_id}:{order_id}` | Оператор | Выбрать конкретного менеджера |
| `send_all:{order_id}` | Оператор | Отправить всем менеджерам региона |

---

## Безопасность

1. **Номер телефона клиента НИКОГДА не отправляется в Telegram.** Ни в тексте сообщения, ни в callback_data, ни в логах бота. Хранится только в БД, извлекается только в момент звонка для передачи в Mango API.

2. **Проверка ролей на каждый callback.** Менеджер не может одобрить звонок. Оператор не может взять заявку. Используем middleware или проверку в каждом handler.

3. **Проверка принадлежности.** Менеджер может нажать «Позвонить» только по своей заявке. Оператор может одобрить только по своей заявке (которую он создавал).

4. **Одноразовость кнопки «Позвонить».** После нажатия кнопка исчезает. Повторный звонок — через повторный запрос и одобрение.

5. **Логирование всех звонков** в таблицу `call_log` с привязкой к заявке.

---

## Docker Compose

```yaml
version: "3.8"

services:
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    ports:
      - "${SERVER_PORT:-8080}:8080"
    depends_on:
      postgres:
        condition: service_healthy

  postgres:
    image: postgres:15-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pgdata:
```

---

## Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "app.main"]
```

---

## Точка входа (app/main.py)

Запускаем одновременно:
- aiogram polling (для простоты, без Telegram webhooks)
- FastAPI на uvicorn (для Mango webhooks и healthcheck)

Используем `asyncio.gather` или запуск uvicorn в отдельном таске.

```python
import asyncio
import uvicorn
from app.bot.bot import dp, bot
from app.api.webhooks import app as fastapi_app
from app.db.database import init_db

async def main():
    await init_db()
    
    server = uvicorn.Server(
        uvicorn.Config(fastapi_app, host="0.0.0.0", port=8080)
    )
    
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve(),
    )

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Важные детали реализации

1. **FSM (Finite State Machine)** — использовать aiogram FSM для пошаговых диалогов:
   - Создание заявки оператором (ввод номера сделки → выбор региона → выбор менеджера).
   - Добавление менеджера админом.

2. **Обработка ошибок amoCRM** — токен может истечь, сделка может не существовать, контакт может не иметь телефона. Все ошибки обрабатывать gracefully с понятным сообщением пользователю.

3. **Обработка ошибок Mango** — сервис может быть недоступен, номер невалиден. Логировать, сообщать менеджеру.

4. **Конкурентный доступ к заявкам** — когда 3 менеджера видят заявку в группе и жмут «Беру» одновременно, только первый должен получить заявку. Использовать `SELECT ... FOR UPDATE` или проверку статуса с атомарным UPDATE.

5. **Формат карточки заявки** — использовать HTML-форматирование (parse_mode=HTML):
   ```html
   📋 <b>Заявка #12345678</b>
   
   <b>Клиент:</b> Иванов Иван Иванович
   <b>Адрес:</b> г. Москва, ул. Ленина, 10
   <b>Комментарий:</b> Замер окон, 2 этаж
   ```

6. **ID полей в amoCRM** — номер телефона и адрес хранятся в кастомных полях. Их ID разные в каждом аккаунте amoCRM. Реализовать:
   - Либо конфигурируемые ID полей через .env (`AMO_PHONE_FIELD_ID`, `AMO_ADDRESS_FIELD_ID`).
   - Либо поиск по `field_code`: телефон обычно имеет `field_code = "PHONE"`, адрес — нужно узнать у заказчика.
   - Рекомендуется: при первом запуске или по команде `/amo_fields` вывести список полей сделки и контакта с их ID, чтобы админ мог найти нужные.

7. **Повторные звонки** — после звонка статус заявки остаётся CALL_APPROVED или переходит в TAKEN. Если менеджеру нужно позвонить ещё раз, он снова нажимает «Запросить звонок» → ОП одобряет → звонит. Цикл может повторяться.

---

## Порядок реализации (рекомендуемый)

1. Каркас проекта: структура папок, config.py, docker-compose, БД, модели, миграции.
2. Бот: /start, роли, middleware авторизации, команды админа (add_operator, add_manager, add_region).
3. Сервис amoCRM: OAuth, получение сделки и контакта.
4. Сценарий оператора: /order, FSM, отправка заявки менеджеру.
5. Сценарий менеджера: «Беру», «Запросить звонок».
6. Сценарий оператора: одобрение/отклонение.
7. Сервис Mango: callback, кнопка «Позвонить».
8. Логирование звонков, обработка ошибок, тесты.

---

## Что НЕ входит в MVP

- Автоматические webhooks из amoCRM (заявки создаются вручную оператором через бот).
- Web-интерфейс или админ-панель (всё через Telegram).
- Аналитика и отчёты.
- SMS-уведомления.
- Многоразовая кнопка «Позвонить» без повторного одобрения.
- Интеграция обратно в amoCRM (записи о звонках из бота обратно в сделку). Записи разговоров и так попадают в amoCRM через штатную интеграцию Mango.
