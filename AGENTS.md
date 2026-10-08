# PawSpot: правила проекта

- Закрытый Telegram-пилот. Allowlist и серверная проверка подписанного initData обязательны; dev preview не заменяет auth.
- Domain rules находятся в backend/services. Bot — adapter к backend, Mini App использует backend API. Не дублировать правила в клиентах.
- Обычные API, frontend, links и navigation state используют только public_location. Точные координаты не выдаются даже автору.
- Не переходить к следующей продуктовой фазе без разрешения. Не добавлять инфраструктуру и абстракции без конкретной необходимости.
- Integration требуют явных PAWSPOT_TEST_DB_* и выделенной pawspot_test[_suffix] БД. Обычный .env не является test config. Migration downgrade допустим только в выделенном тестовом окружении, никогда на пилоте.
- Не коммитить .env, токены, secrets, runtime logs, DB dumps и пользовательские фото. Перед Git проверять diff и ignore. Не менять пилотные данные при проверках.
- Backend/Bot: `uv run --locked --all-packages --all-extras pytest`, `ruff check backend bot`, `ruff format --check backend bot`, `mypy` (последние три также через тот же uv run).
- Integration: настройка и команды в README; `uv run --locked --all-packages --all-extras pytest -m integration`. Fixture проверяет upgrade/downgrade/upgrade только в своей схеме.
- Frontend при его изменениях: из frontend `npm test`, `npm run typecheck`, `npm run build`.
- Windows launcher: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-launcher.ps1`. Polling readiness не подтверждает реальный ответ на /start: это отдельный Telegram live-test.
- Основные документы: [README](README.md), [architecture](docs/architecture.md), [API](docs/api.md), [roadmap](docs/roadmap.md), [backup/restore](docs/backup-restore.md).
