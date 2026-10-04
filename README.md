# PawSpot

PawSpot — коллекция встреч с городскими котами и собаками. Реализован первый сценарий через Telegram Bot: фото → вид → место или пропуск → существующее или новое животное → необязательные имя и заметка → сохранение. Mini App и открытый доступ пока отсутствуют. Пилот закрыт для Telegram-пользователей из allowlist.

Документы: [продукт](docs/product.md), [архитектура](docs/architecture.md), [проект API](docs/api.md), [roadmap](docs/roadmap.md).

## Требования

- Docker Desktop с запущенным Docker Engine либо Docker Engine + Compose plugin.
- uv 0.12.23, установленный по [официальной инструкции](https://docs.astral.sh/uv/getting-started/installation/). `uv python install 3.14` установит нужный Python; системный Python не обязателен.
- Свободный локальный порт 5432 для PostgreSQL и 8000 для API.

Версии Python и библиотек закреплены `.python-version`, `pyproject.toml`, `backend/pyproject.toml` и `uv.lock`. Рабочая БД — PostgreSQL 18 с PostGIS 3.6. При первом запуске uv и Docker скачивают пакеты/образ через интернет. Приложение читает настройки из environment variables или локального `.env`; пароль обязателен. `.env` исключён из Git.

Официальный образ `postgis/postgis:18-3.6` публикуется для amd64. Compose фиксирует эту платформу; на ARM/macOS он запускается через эмуляцию Docker и может работать медленнее.

## Windows PowerShell

Команды выполняются из корня репозитория. Установка uv 0.12.23 через официальный installer при отсутствии команды `uv`:

```powershell
powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/0.12.23/install.ps1 | iex"
```

После установки откройте новую PowerShell-сессию:

```powershell
uv --version
uv python install 3.14
Copy-Item .env.example .env
notepad .env
```

Замените `replace-with-local-password` на свой локальный пароль. Затем:

```powershell
docker compose --env-file .env -f infra/compose.yaml up -d --wait
docker compose --env-file .env -f infra/compose.yaml exec postgres psql -U pawspot -d pawspot -c "SELECT PostGIS_Version();"
uv sync --locked --all-packages --all-extras
uv run --locked --all-packages --all-extras alembic -c backend/alembic.ini upgrade head
uv run --locked --all-packages --all-extras uvicorn pawspot.main:app --host 127.0.0.1 --port 8000
```

В другой PowerShell-сессии:

```powershell
curl.exe -i http://127.0.0.1:8000/health
curl.exe -i http://127.0.0.1:8000/ready
uv run --locked --all-packages --all-extras pytest
uv run --locked --all-packages --all-extras ruff check backend bot
uv run --locked --all-packages --all-extras ruff format --check backend bot
uv run --locked --all-packages --all-extras mypy
uv run --locked --all-packages --all-extras pytest -m integration
uv lock --check
```

## POSIX / Linux / macOS

Для Linux нужен Docker Engine + Compose plugin; для macOS — Docker Desktop. Команды из корня репозитория:

```sh
curl -LsSf https://astral.sh/uv/0.12.23/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
uv python install 3.14
cp .env.example .env
${EDITOR:-vi} .env
docker compose --env-file .env -f infra/compose.yaml up -d --wait
docker compose --env-file .env -f infra/compose.yaml exec postgres psql -U pawspot -d pawspot -c 'SELECT PostGIS_Version();'
uv sync --locked --all-packages --all-extras
uv run --locked --all-packages --all-extras alembic -c backend/alembic.ini upgrade head
uv run --locked --all-packages --all-extras uvicorn pawspot.main:app --host 127.0.0.1 --port 8000
```

В другом терминале:

```sh
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
uv run --locked --all-packages --all-extras pytest
uv run --locked --all-packages --all-extras ruff check backend bot
uv run --locked --all-packages --all-extras ruff format --check backend bot
uv run --locked --all-packages --all-extras mypy
uv run --locked --all-packages --all-extras pytest -m integration
uv lock --check
```

Если меняли `PAWSPOT_DB_USER` или `PAWSPOT_DB_NAME` в `.env`, замените их и в команде `psql -U ... -d ...`. Переменные инициализации БД действуют только при создании пустого volume; изменение `.env` после первого запуска не меняет существующую роль или пароль в PostgreSQL.

## Проверка недоступной БД

Остановите только БД, оставив API запущенным:

```powershell
docker compose --env-file .env -f infra/compose.yaml stop postgres
curl.exe -i http://127.0.0.1:8000/health
curl.exe -i http://127.0.0.1:8000/ready
docker compose --env-file .env -f infra/compose.yaml start postgres
```

В POSIX замените `curl.exe` на `curl`. `/health` должен вернуть 200, `/ready` — 503 при недоступной БД. Для завершения локальной работы: `docker compose --env-file .env -f infra/compose.yaml down` (volume сохраняется).

## Сейчас реализовано

`GET /health` проверяет живость FastAPI без обращения к БД. `GET /ready` выполняет `SELECT PostGIS_Version()` и возвращает версию PostGIS либо 503 при SQLAlchemy/DB ошибке. Тайм-аут подключения задаётся `PAWSPOT_DB_CONNECT_TIMEOUT_SECONDS` (по умолчанию 3). Alembic создаёт доменные таблицы, PostGIS extension и seed Тбилиси; повторный `upgrade head` безопасен. Для проверки отката на одноразовой БД: `uv run --locked --all-packages --all-extras alembic -c backend/alembic.ini downgrade base`, затем снова `upgrade head`. **Downgrade удаляет доменные таблицы и данные.**

Геоприватность использует фиксированную сетку; радиус задаётся `PAWSPOT_PUBLIC_LOCATION_RADIUS_M` (default 200 м), смена параметра после появления опубликованных встреч требует отдельной миграции и анализа приватности. Точные точки не входят в публичную схему `EncounterPublic`, в том числе для автора. Обычный `pytest` не запускает интеграционные тесты; после миграции выполните `pytest -m integration`. CI проверяет upgrade/downgrade, constraints и PostGIS на реальной БД.

Закрытый пилот требует `PAWSPOT_ALLOWED_TELEGRAM_IDS`, `PAWSPOT_TELEGRAM_BOT_TOKEN` и отдельный `PAWSPOT_INTERNAL_SERVICE_TOKEN` в `.env`. Пустой allowlist не даёт доступ никому. Mini App передаёт raw `initData` в `POST /api/v1/auth/telegram`, backend проверяет HMAC и время, выдаёт случайную сессию (в БД только SHA-256 токена). Bot использует внутренний service token и Telegram `from.id`; клиентский заголовок actor принимается только после проверки service token. `PAWSPOT_CORS_ORIGINS` — список конкретных origins через запятую, по умолчанию пустой. Токены и raw initData не логируются приложением. In-process лимит на auth endpoint рассчитан на один небольшой процесс пилота, не на несколько независимых воркеров.

## Telegram Bot — первый пользовательский сценарий

Создайте бота через BotFather и запишите его token только в локальный `.env` как `PAWSPOT_TELEGRAM_BOT_TOKEN`. Укажите собственный числовой Telegram ID в `PAWSPOT_ALLOWED_TELEGRAM_IDS` и отдельную длинную случайную строку в `PAWSPOT_INTERNAL_SERVICE_TOKEN`. Эти же `.env` читает backend; запускайте его и примените миграции до запуска бота. `PAWSPOT_BOT_BACKEND_URL` указывает доступный боту адрес API; для локального запуска подходит `http://127.0.0.1:8000`. При размещении процессов на разных хостах используйте защищённый внутренний канал и TLS. Не публикуйте `/internal/v1` наружу через reverse proxy.

В отдельной PowerShell-сессии из корня репозитория:

```powershell
uv run --locked --all-packages --all-extras python -m pawspot_bot.main
```

В отдельном POSIX-терминале:

```sh
uv run --locked --all-packages --all-extras python -m pawspot_bot.main
```

Откройте личный чат с ботом, отправьте `/start`, нажмите «📸 Добавить встречу» и отправьте фото. Бот предлагает вид, геолокацию или пропуск, кандидатов либо новое животное, затем необязательные имя и заметку. `/cancel` отменяет черновик; повтор `/start` продолжает его. Один polling-процесс должен работать с данным token. Реальный Telegram smoke test требует действительный token и участника в allowlist; в CI проверяются синтетические Telegram Update и backend/PostGIS без внешнего Telegram.

## Mini App: локальная разработка

Frontend использует Node.js 24, React, TypeScript и Vite. В Phase 6 каталог `frontend/` создан заново: до этого в репозитории его не было. Из корня репозитория в PowerShell:

```powershell
Copy-Item frontend/.env.example frontend/.env.local
cd frontend
npm ci
npm run dev
```

POSIX:

```sh
cp frontend/.env.example frontend/.env.local
cd frontend
npm ci
npm run dev
```

`VITE_DEV_PREVIEW=true` включает только демонстрационный просмотр интерфейса в dev-сборке и не выдаёт backend-сессию. Для проверки реальных данных задайте `VITE_DEV_PREVIEW=false`, откройте Mini App из Telegram через HTTPS-адрес, настроенный в BotFather, и укажите тот же URL в `PAWSPOT_MINI_APP_URL` локального `.env` бота. Telegram требует HTTPS для URL Mini App; `localhost` в обычной вкладке не содержит подписанного `initData`. Vite перенаправляет `/api` на локальный backend. Если frontend и backend размещены на разных origin, задайте `VITE_API_BASE_URL` для frontend и конкретный origin в `PAWSPOT_CORS_ORIGINS` backend. В production frontend и `/api` удобно обслуживать с одного origin.

Проверки frontend из каталога `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Backend workflow добавления встречи доступен под `/api/v1/encounter-drafts` для Bearer-сессии и `/internal/v1/encounter-drafts` для бота с отдельным service token. `POST` создаёт или возвращает один активный draft, `PUT /{id}/photo` принимает multipart JPEG/PNG/WebP, `PATCH /{id}` сохраняет вид, локацию/skip и выбор животного, `POST /{id}/matches` ищет кандидатов, `POST /{id}/commit` атомарно создаёт Encounter. Первый commit возвращает 201, повтор — 200 с тем же UUID. `GET /encounters/{id}` и `GET /photos/{id}/{variant}` доступны только авторизованным участникам пилота. Точная локация отсутствует в этих ответах. Фото перекодируются в JPEG и хранятся вне публичной static-директории; исходники не сохраняются. Отменённые/просроченные черновики и старые осиротевшие фото очищаются командой `uv run --locked --all-packages --all-extras python -m pawspot.cli cleanup` (запускать по расписанию ОС). Радиус matching, период и число кандидатов заданы `PAWSPOT_MATCHING_*` в `.env.example`.
