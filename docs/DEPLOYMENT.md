# Развёртывание на боевом сервере

Пошаговая инструкция по установке и сопровождению бота «Маскированные звонки».

---

## 1. Требования

- Сервер Linux (Ubuntu 22.04+ или аналог), 1 CPU / 1 ГБ RAM минимум.
- **Docker** и **Docker Compose** (`docker compose version`).
- Домен с HTTPS — нужен **только** если будете принимать webhook-уведомления от
  Mango о статусах звонков. Для базовой работы (бот на polling) домен не обязателен.
- Доступы: токен Telegram-бота, доступ к amoCRM (API), доступ к Mango Office (VPBX API).

---

## 2. Получение секретов

### 2.1. Telegram

1. В Telegram напишите [@BotFather](https://t.me/BotFather) → `/newbot` → получите
   `BOT_TOKEN`.
2. Узнайте свой Telegram ID: напишите [@userinfobot](https://t.me/userinfobot) или
   просто запустите бота `/start` после первого деплоя — он покажет ID.
   Это значение пойдёт в `ADMIN_TG_IDS`.

### 2.2. amoCRM

Возможны два режима (бот поддерживает оба):

**A. Долгоживущий токен (проще).**
В amoCRM: «Настройки» → «Интеграции» → создайте приватную интеграцию → во вкладке
«Ключи и доступы» получите долгоживущий токен. Положите его в `AMOCRM_ACCESS_TOKEN`.

**B. OAuth 2.0 с автообновлением (надёжнее на длинной дистанции).**
При создании интеграции получите `client_id`, `client_secret`, `redirect_uri`.
По коду авторизации обменяйте на `access_token` + `refresh_token` (см.
официальную документацию amoCRM по OAuth 2.0). Заполните `AMOCRM_CLIENT_ID`,
`AMOCRM_CLIENT_SECRET`, `AMOCRM_REDIRECT_URI`, `AMOCRM_ACCESS_TOKEN`,
`AMOCRM_REFRESH_TOKEN`. Бот сам обновит access-токен по refresh при истечении и
сохранит новые значения в БД.

> Точные шаги в интерфейсе amoCRM меняются — сверяйтесь с актуальной
> [документацией amoCRM](https://www.amocrm.ru/developers/content/oauth/step-by-step).

`AMOCRM_SUBDOMAIN` — поддомен вашего аккаунта, например `mycompany.amocrm.ru`.

### 2.3. Mango Office

1. В личном кабинете Mango включите **VPBX API** (API для интеграций).
2. Получите `vpbx_api_key` → `MANGO_API_KEY` и `vpbx_api_salt` → `MANGO_API_SALT`.
3. `MANGO_LINE_NUMBER` — ваш номер 8-800 в формате `7XXXXXXXXXX` (без `+`).
4. (Опционально) Настройте отправку уведомлений о звонках на адрес
   `https://<домен>/webhooks/mango/call`.

---

## 3. Установка

```bash
git clone <репозиторий> call_masking_bot
cd call_masking_bot
git checkout develop          # или master после мержа

cp .env.example .env
nano .env                     # заполнить все значения (см. раздел 4)

docker compose up -d --build
docker compose logs -f bot    # проверить, что бот стартовал без ошибок
```

При старте контейнер автоматически:
1. применяет миграции БД (`alembic upgrade head`);
2. запускает бота (long polling) и HTTP-сервер на порту 8080.

Проверка живости: `curl http://localhost:8080/health` → `{"status":"ok"}`.

---

## 4. Заполнение .env

Шаблон — [`.env.example`](../.env.example). Обязательные поля:

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | токен бота от @BotFather |
| `ADMIN_TG_IDS` | Telegram ID администраторов через запятую |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | креды PostgreSQL (БД поднимается в compose) |
| `AMOCRM_SUBDOMAIN`, `AMOCRM_ACCESS_TOKEN` | доступ к amoCRM |
| `MANGO_API_KEY`, `MANGO_API_SALT`, `MANGO_LINE_NUMBER` | доступ к Mango |

> В docker-compose `DB_HOST`/`DB_PORT` переопределяются на `postgres`/`5432`
> (внутренняя сеть), поэтому значения в `.env` для них не важны.

После первого запуска войдите в бота как админ (`/start`) и выполните
`/amo_fields` — бот покажет ID кастомных полей amoCRM. Найдите поле адреса и
впишите его ID в `AMO_ADDRESS_FIELD_ID`, затем перезапустите: `docker compose up -d`.

---

## 5. Webhook Mango (опционально)

Если хотите, чтобы статусы звонков прилетали в журнал:

1. Поднимите reverse-proxy (nginx/Caddy) с HTTPS на домен, проксируйте на `:8080`.
2. В ЛК Mango укажите URL уведомлений: `https://<домен>/webhooks/mango/call`.
3. Ограничьте доступ к эндпоинту по IP Mango (firewall / nginx allow):
   `81.88.80.132`, `81.88.80.133`, `81.88.82.36`.

---

## 6. Сопровождение

**Логи:**
```bash
docker compose logs -f bot          # живой лог
# внутри контейнера также пишется logs/bot.log с ротацией
```

**Обновление кода:**
```bash
git pull
docker compose up -d --build        # пересоберёт и применит новые миграции
```

**Бэкап БД:**
```bash
docker compose exec postgres pg_dump -U <DB_USER> <DB_NAME> > backup_$(date +%F).sql
```

**Восстановление:**
```bash
cat backup.sql | docker compose exec -T postgres psql -U <DB_USER> -d <DB_NAME>
```

**Новая миграция при изменении моделей:**
```bash
docker compose exec bot alembic revision --autogenerate -m "описание"
docker compose exec bot alembic upgrade head
```

**Перезапуск / остановка:**
```bash
docker compose restart bot
docker compose down            # остановить всё (данные БД в volume сохраняются)
```

---

## 7. Траблшутинг

| Симптом | Причина / решение |
|---|---|
| Бот не отвечает | Проверьте `BOT_TOKEN`, `docker compose logs bot` |
| «Сделка не найдена» на валидном номере | Проверьте `AMOCRM_SUBDOMAIN` и токен; токен мог истечь — нужен refresh |
| Адрес не подтягивается из amoCRM | Не задан `AMO_ADDRESS_FIELD_ID` — узнайте через `/amo_fields` |
| «Не удалось инициировать звонок» | Проверьте `MANGO_API_KEY/SALT/LINE_NUMBER`; у менеджера должен быть сохранён телефон |
| Бот не пишет новому сотруднику | Сотрудник должен сам сначала написать боту `/start` (ограничение Telegram) |
| БД недоступна при старте | Дождитесь healthcheck postgres; проверьте креды в `.env` |
