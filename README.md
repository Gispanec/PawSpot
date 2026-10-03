# PawSpot

PawSpot — коллекция встреч с городскими котами и собаками. Реализован каркас **Phase 1**; для окончательной локальной проверки PostgreSQL/PostGIS необходим Docker. Это ещё не пользовательский сервис: Animal, Encounter, Bot и Mini App появятся в следующих этапах. Пилот закрыт для Telegram-пользователей из allowlist, share-ссылки не дают анонимного доступа.

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
uv sync --locked --package pawspot-backend --extra dev
uv run --locked --package pawspot-backend --extra dev uvicorn pawspot.main:app --host 127.0.0.1 --port 8000
```

В другой PowerShell-сессии:

```powershell
curl.exe -i http://127.0.0.1:8000/health
curl.exe -i http://127.0.0.1:8000/ready
uv run --locked --package pawspot-backend --extra dev pytest
uv run --locked --package pawspot-backend --extra dev ruff check backend
uv run --locked --package pawspot-backend --extra dev ruff format --check backend
uv run --locked --package pawspot-backend --extra dev mypy
uv run --locked --package pawspot-backend --extra dev pytest -m integration
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
uv sync --locked --package pawspot-backend --extra dev
uv run --locked --package pawspot-backend --extra dev uvicorn pawspot.main:app --host 127.0.0.1 --port 8000
```

В другом терминале:

```sh
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
uv run --locked --package pawspot-backend --extra dev pytest
uv run --locked --package pawspot-backend --extra dev ruff check backend
uv run --locked --package pawspot-backend --extra dev ruff format --check backend
uv run --locked --package pawspot-backend --extra dev mypy
uv run --locked --package pawspot-backend --extra dev pytest -m integration
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

`GET /health` проверяет живость FastAPI без обращения к БД. `GET /ready` выполняет `SELECT PostGIS_Version()` и возвращает версию PostGIS либо 503 при SQLAlchemy/DB ошибке. Тайм-аут подключения задаётся `PAWSPOT_DB_CONNECT_TIMEOUT_SECONDS` (по умолчанию 3). `/docs` содержит только эти маршруты. Обычный `pytest` не запускает интеграционный тест, требующий БД; после запуска Compose выполните `pytest -m integration`. CI запускает обе группы с PostgreSQL/PostGIS service. Следующий этап — только после отдельного подтверждения владельца.
