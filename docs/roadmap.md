# PawSpot: roadmap реализации

Phase 0 и Phase 1 утверждены владельцем; Phase 2 выполняется отдельно. **Phase 3 автоматически не начинается.** Каждый этап — самостоятельное небольшое изменение. Примерные номера задают зависимости, не обещание календарных сроков.

## Правила каждого этапа

Изучить актуальные README/AGENTS/код и git diff → назвать цель и затрагиваемые области → реализовать минимум → выполнить доступные tests/lint/format/type checks → исправить → обновить документы → сообщить фактический результат и предложить commit message. Не смешивать посторонние исправления. Не объявлять готовым то, что не запускалось.

Backend/bot после bootstrap: pytest, Ruff check, Ruff format --check, mypy. Frontend после появления: lint, typecheck, build, имеющиеся tests. Интеграционные тесты используют PostgreSQL/PostGIS с отдельной тестовой БД. Секреты и реальные фотографии пользователей в fixtures не попадают.

## Phase 0 — Architecture & Project Plan

- **Цель:** согласовать границы и проверяемый порядок разработки.
- **Реализуем:** обследование репозитория, product, architecture, API sketch, roadmap, актуальные стабильные линии зависимостей.
- **Не реализуем:** application code, установку зависимостей, миграции, инфраструктуру, deployment.
- **Dependencies:** исходные требования и доступ к репозиторию/документации.
- **DoD:** описаны модели, границы клиентов, privacy, matching, merge, auth, photo lifecycle и этапы; файлы прочитаны и проверены на согласованность. Подтверждение владельца требуется для Phase 1.
- **Commit:** `docs: define PawSpot MVP architecture and roadmap`.

## Phase 1 — Repository, bootstrap, infrastructure

- **Цель:** воспроизводимая среда без продуктовых функций.
- **Области:** корневые manifests/lockfile, backend skeleton, infra/compose.yaml, CI, README, .env.example, .gitignore. Подключить локальную папку к указанному GitHub-репозиторию, сохранив документы Phase 0.
- **Реализуем:** Python/uv, FastAPI health/readiness, PostgreSQL + PostGIS, конфигурация secrets, минимальные проверки и CI; точные команды PowerShell и POSIX.
- **Не реализуем:** реальные users/animals, bot dialogue, frontend UI, новые production-сервисы.
- **Dependencies:** подтверждённая Phase 0; доступный Docker/runtime. Проверяем binary wheels Python 3.14 и выбранный PostGIS image; при несовместимости фиксируем конкретное отклонение, не замалчиваем.
- **DoD:** чистая установка из lockfile, БД health, `SELECT PostGIS_Version()`, API health и readiness, tests/lint/format/type checks проходят; новый разработчик запускает по README; секреты исключены из Git.
- **Commit:** `chore: bootstrap backend and local database`.

## Phase 2 — Domain, migrations, geo privacy

- **Цель:** корректные Animal/Encounter и безопасное геопредставление до появления реальных данных.
- **Области:** backend models/schemas/geo, migrations, tests, architecture.
- **Реализуем:** users/cities/animals/encounters/photos/reactions foundation, nullable geo, UUID, FK/CHECK/unique, city seed, public grid policy и безопасные DTO. Сущности drafts/auth_sessions вводятся в этапах их использования, не пустыми service abstractions.
- **Не реализуем:** пользовательский encounter endpoint, bot, карта, AI, merge UI.
- **Dependencies:** Phase 1.
- **DoD:** миграции применяются с нуля и повторный seed не дублирует город; тесты FK/constraints/Animal→Encounter; geo boundary tests, стабильная сетка, public DTO без private fields; интеграционные тесты на реальной БД.
- **Commit:** `feat: add animal and encounter domain models`.

## Phase 3 — Telegram authentication and access

- **Цель:** доверенный actor для обоих клиентов.
- **Области:** auth services/routes, auth_sessions migration, internal principal, tests, env docs.
- **Реализуем:** initData HMAC + freshness, User upsert, opaque sessions/expiry/revoke, allowlist, internal service credential, CORS, отсутствие sensitive logging, базовые auth rate limits.
- **Не реализуем:** email/password, OAuth providers, JWT refresh infrastructure, публичный dev bypass.
- **Dependencies:** Phase 2; тестовые подписи локально, реальный bot token только для smoke test.
- **DoD:** valid/tampered/stale/future/duplicate-key/Unicode cases проходят; user_id spoofing не работает; public session не принимается как bot credential; отрицательные authorization tests.
- **Commit:** `feat: add Telegram authentication and access control`.

## Phase 4 — Shared encounter workflow, photos and nearby matching

- **Цель:** полный backend use case добавления встречи.
- **Области:** drafts migration, services/routes, local storage, matching, tests, API docs.
- **Реализуем:** uploads/thumbnail/EXIF removal, draft state/version, optional geo, species+public distance+recency candidates, атомарный commit, idempotency, canonical resolver, базовый GET результата и собственная коллекция для выбора животного без geo. Cleanup отменённых/просроченных drafts и orphan photos; минимальные стабильные share IDs/routes.
- **Не реализуем:** Bot UI, визуальную карту, embeddings, S3 implementation, фотоальбомы, reverse geocoding.
- **Dependencies:** Phases 2–3. Matching расположен здесь, потому что следующий этап Bot зависит от него.
- **DoD:** API создаёт новое животное и встречу существующему, в том числе без geo; retry/parallel commit дают один результат; ownership, invalid image, storage failure/rollback, matching cutoff/recency/dedup tests; private fields не выходят через ответы/ошибки.
- **Commit:** `feat: add shared encounter workflow and nearby matching`.

## Phase 5 — Telegram Bot encounter flow

- **Цель:** первый полностью рабочий пользовательский путь.
- **Области:** bot package, handlers/backend client, tests, README.
- **Реализуем:** polling, фото → вид → геолокация/skip → кандидаты/new → optional name/comment → результат; backend draft resume/cancel; timeout/retry; ограничения личным чатом. Карточка может существовать до готовности Mini App: бот показывает результат сам, web share landing честно сообщает доступность просмотра.
- **Не реализуем:** webhook infrastructure, собственную БД/FSM бизнес-правил бота, групповые сценарии, Mini App.
- **Dependencies:** Phase 4; созданный владельцем Telegram Bot и token в окружении для live smoke test.
- **DoD:** synthetic Update tests и реальное прохождение на тестовом боте; restart resume, double click, старые callback, чужой draft, отмена, плохая сеть; замер 15–30 секунд с учётом сети. Без токена live-check обозначается как невыполненный, этап нельзя объявлять полностью проверенным.
- **Commit:** `feat: add Telegram Bot encounter flow`.

## Phase 6 — Mini App foundation and Add

- **Цель:** второй клиент того же workflow.
- **Области:** frontend, auth integration, Telegram launch config/docs.
- **Реализуем:** React/TS/Vite, Map | Feed | Add | Profile shell, Telegram theme/safe area/back button, session bootstrap, Add/upload/location permission/skip, продолжение draft, deep-link routing после auth. Пока неготовые экраны подписаны явно.
- **Не реализуем:** отдельную регистрацию, native apps, дизайн-систему большой платформы, копию backend validation rules как источник истины.
- **Dependencies:** Phases 3–5, HTTPS development URL и настройка Main Mini App в BotFather.
- **DoD:** lint/typecheck/build/tests; auth и Add работают в Telegram Android/iOS; отказ геолокации не блокирует сохранение; просроченный initData обрабатывается; Bot и Mini App возвращают один draft/result.
- **Commit:** `feat: add Telegram Mini App foundation and encounter form`.

## Phase 7 — Animal profiles and feed

- **Цель:** Animal как персонаж с историей.
- **Области:** read API queries/DTO, frontend profiles/feed, tests.
- **Реализуем:** профиль, статистика, timeline, пагинация, авторы, фото, city/area labels, feed, encounter detail, корректный порядок observed_at vs created_at и первичный создатель.
- **Не реализуем:** комментарии-обсуждения, публичные профили пользователей, сортировку по AI/recommendations, materialized counters.
- **Dependencies:** Phase 6.
- **DoD:** несколько пользователей/встреч дают корректные числа без дублей join; pagination без повторов при равных датах; no-geo и unnamed карточки; accessibility и loading/error/empty states; frontend и backend checks проходят.
- **Commit:** `feat: add animal profiles and encounter feed`.

## Phase 8 — Map, reactions and collection

- **Цель:** исследование города и причина возвращаться.
- **Области:** map/reactions/profile endpoints, Leaflet UI, tests.
- **Реализуем:** bbox по последней public location Animal, карточки/группы одинаковых точек, configurable tiles + attribution, фильтр вида; idempotent reactions; профиль и уникальная коллекция/области.
- **Не реализуем:** новый matching algorithm (готов в Phase 4), live tracking, районы с полигонами, достижения, leaderboards, offline tiles.
- **Dependencies:** Phase 7.
- **DoD:** map никогда не использует private coordinate или старую точку вместо последней из-за bbox; reaction race tests и ограничения БД; self-reaction policy; уникальные animals/areas корректны; tile policy выполнена; mobile smoke test.
- **Commit:** `feat: add map exploration reactions and collections`.

## Phase 9 — Merge, sharing and security hardening

- **Цель:** безопасный управляемый пилот.
- **Области:** merge/audit CLI, share landing, privacy tests, cleanup/hide/delete operations, deployment configuration.
- **Реализуем:** operator dry-run + transactional merge; old-link redirects; удаление своей встречи; hide без публичного admin API; замена primary photo; exact-coordinate retention, photo cleanup, rate limits всех рискованных endpoints; проверка logs/CORS/media access. Стабильные URLs, заложенные ранее, доводятся до end-to-end Telegram sharing.
- **Не реализуем:** automatic merge, unmerge UI, сложные moderation/reports, открытый интернет без решения владельца.
- **Dependencies:** Phase 8. Владелец решил, что в первом пилоте ссылки доступны только Telegram-пользователям из allowlist. Основа auth/privacy уже реализована ранее.
- **DoD:** concurrency merge/create и повтор merge не теряют данные; старые Animal/Encounter links работают; приватность проверена на всех surfaces, включая thumbnails/errors/matching; удалённый контент не выдаётся; процедуры удаления и retention описаны и проверены.
- **Commit:** `feat: add safe animal merge and harden sharing privacy`.

## Phase 10 — Pilot readiness and deployment preparation

- **Цель:** воспроизводимый, восстанавливаемый запуск для друзей.
- **Области:** deploy/runbook, CI, configuration, smoke/e2e tests, README.
- **Реализуем:** полный сценарий Bot/Mini App, clean setup, production settings, HTTPS/domain, migration rollout, backups и restore drill, мониторинг ошибок без личных данных, graceful restart, измерение UX. S3 adapter добавляется здесь только если выбранное размещение не обеспечивает надёжный persistent volume.
- **Не реализуем:** горизонтальное масштабирование, очереди на будущее, платные подключения без согласования бюджета, новый продуктовый scope.
- **Dependencies:** Phase 9; владелец предоставляет bot/domain и выбирает размещение/бюджет перед внешними расходами. Deploy не выполняется автоматически из Phase 0.
- **DoD:** все обязательные проверки зелёные; новый разработчик запускает по README; восстановлена тестовая копия DB+photos; сценарии Android/iOS проверены; настройки secret/allowlist/retention/tiles проверены; нет известных критических дефектов; пользователям ясно, что карта приблизительная. Если live-check или restore не выполнены, остаётся явный blocker пилота.
- **Commit:** `chore: prepare PawSpot for the private pilot`.

## Общий Definition of Done MVP

Пользователь через Telegram добавляет фото, выбирает новое или существующее животное, получает карточку и рабочую ссылку; та же встреча доступна в timeline, feed и собственной collection, при наличии geo — на приблизительной карте. Реакции не дублируются, merge сохраняет историю, повтор запроса не создаёт дубликат. Авторизация, media/privacy, миграции, восстановление и критические пользовательские сценарии проверены фактически. Все незавершённые проверки названы, а не скрыты за формулировкой «код готов».

## Риски и контрольные точки

- Совместимость свежих версий и Windows binary wheels — Phase 1, до реализации приложения.
- Недостаточная скорость длинного диалога — замеры Phase 5, сокращение кликов без потери выбора животного.
- Слишком много/мало nearby candidates — пилотная проверка и настройка radius/lookback; AI не добавляется автоматически.
- Утечка географии через фото/текст — предупреждение, закрытый пилот, операторское скрытие; абсолютную защиту не обещаем.
- Telegram permissions/версии клиентов — реальные smoke tests Android/iOS с fallback «Пропустить».
- Доступность OSM tiles — конфигурируемый provider и отсутствие зависимости сохранения Encounter от карты.
- Публичность ссылок и расходы на хостинг — отдельные решения владельца в указанных фазах.
