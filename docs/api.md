# PawSpot API: реализованный контракт пилота

Пилот закрыт: все маршруты `/api/v1`, кроме `POST /auth/telegram`, требуют `Authorization: Bearer <opaque session>`. Session выдаётся только после серверной проверки подписанного Telegram Mini App `initData` и allowlist. `/internal/v1` — адаптер бота с отдельным service token; наружу его не публикуют. UUID в URL не даёт права доступа без сессии.

## Просмотр

Служебные проверки: `GET /health` — process liveness, 200 без обращения к БД. `GET /ready` — PostgreSQL/PostGIS и соответствие Alembic revision текущей схемы head приложения; 200 с `{status: "ready", postgis_version: "…"}` либо 503 с `{detail: "Database is not ready"}`. Отсутствующая, пустая, устаревшая или неизвестная revision означает not ready. Эти проверки не требуют Telegram session; readiness не выдаёт revision/параметры БД, не меняет данные и не применяет миграции.

| Метод | Маршрут | Ответ |
| --- | --- | --- |
| GET | `/api/v1/feed?limit=20&cursor=…` | `items` и `next_cursor`; свежие Encounter по `created_at DESC, id DESC` |
| GET | `/api/v1/animals/{uuid}?limit=20&cursor=…` | Animal, статистика и `timeline` с `items`/`next_cursor` по `observed_at DESC, id DESC` |
| GET | `/api/v1/encounters/{uuid}/detail` | одна встреча с фото, автором, городом и реакцией |
| GET | `/api/v1/map/animals?south=…&west=…&north=…&east=…&species=cat` | максимум 200 Animal-маркеров по последней встрече **с геолокацией** |
| GET | `/api/v1/collection` | уникальные животные actor и число его личных встреч, максимум 200 карточек |
| GET | `/api/v1/users/me/profile` | display name, дата начала, уникальные животные, встречи, коты/собаки |
| GET | `/api/v1/photos/{uuid}/main` или `/thumbnail` | защищённый JPEG, `Cache-Control: private, no-store` |

`limit` для feed/timeline 1–50. Cursor непрозрачен клиенту и содержит пару timestamp/UUID; не является токеном доступа. При изменении набора данных между страницами это не snapshot-пагинация. Для map принимается обычный bbox без пересечения антимеридиана, с шириной/высотой до 2°; zoom out требует приблизить карту. Последняя локация выбирается **до** bbox-фильтра: животное не появляется по старой точке, если его последняя локализованная встреча была в другом месте. Отсутствие геолокации в новых встречах не отменяет последнюю известную публичную точку. Весь map использует только `public_location`.

Карточка Encounter содержит `public_id`, `animal_public_id`, `animal_name`, `species`, `photo_public_id`, `author_name`, `city_name`, `comment`, `observed_at`, `approximate_latitude/longitude` или `null`, `reaction_count`, `liked_by_me`, `is_mine`. Профиль Animal добавляет число встреч, фото, уникальных наблюдателей, лайков, даты первой/последней встречи, первого автора и страницу истории. Точные координаты, `private_location`, storage keys и Telegram credentials не входят ни в одну схему чтения. Имена и комментарии в Mini App выводятся React как текст.

## Реакция

| Метод | Маршрут | Действие |
| --- | --- | --- |
| PUT | `/api/v1/encounters/{uuid}/like` | Поставить лайк; повтор не создаёт дубликат |
| DELETE | `/api/v1/encounters/{uuid}/like` | Убрать собственный лайк; повтор безопасен |

Оба возвращают `{reaction_count, liked_by_me}`. Самому себе поставить лайк нельзя (403); недоступная встреча даёт 404. Один лайк на `(user, encounter)` ограничен уникальным ключом БД. Сейчас UI использует только тип `heart`; старые типы модели не публикуются в новом API.

## Создание через Bot

Общий backend workflow не менялся: `/internal/v1/encounter-drafts` и `/api/v1/encounter-drafts` создают/возобновляют draft, загружают фото, сохраняют вид/локацию/выбор животного и выполняют `commit`. Bot использует internal credential; Mini App использует bearer session. Фактическое добавление в закрытом пилоте по-прежнему идёт через бот. `GET /api/v1/encounters/{uuid}` и `/internal/v1/encounters/{uuid}` сохраняют прежнюю компактную карточку для совместимости бота.

Загрузка фото: `PUT /{draft_id}/photo` multipart с `expected_version`; допустимые JPEG/PNG/WebP нормализуются в JPEG с thumbnail. Draft-ответ включает `photo_public_id`, `city_name`, `city_timezone`, `observed_at` и `selected_animal_name` для preview бота. `PATCH /{draft_id}` принимает timezone-aware `observed_at` и отклоняет время более чем на пять минут в будущем. `POST /{draft_id}/matches` возвращает кандидатов с `thumbnail_photo_id`, датой последней встречи и числом встреч; координат в ответе нет. Фото доступны только авторизованным участникам пилота через защищённый `/photos/{uuid}/main`. `POST /{draft_id}/commit` создаёт Animal/Encounter атомарно; повтор commit возвращает тот же UUID. Точная локация draft не возвращается через обычный API.

`GET /internal/v1/feed/today?page=1&page_size=1` доступен только боту с internal service credential и allowlisted Telegram actor. День определяется timezone города по умолчанию; ответ содержит `encounters`, `dogs`, `cats`, `first_animals`, `timezone`, `page`, `page_size`, `items`. `page_size` ограничен 1–5. В `items` есть UUID встречи/животного/фото, имя, вид, время, комментарий, город, флаг наличия приблизительного места и флаг повторной встречи. Координаты и Telegram-персональные данные отсутствуют. `first_animals` считает Animal, впервые созданных в PawSpot сегодня; остальные показатели считают Encounter, наблюдавшиеся сегодня. Бот запрашивает по одной карточке и заменяет её при перелистывании.

`GET /internal/v1/users/me/collection-picker?species=dog&page=1&page_size=5&q=…` — выбор животного в боте после явного открытия личной коллекции. Обязательный `species`: `cat` или `dog`; `page`: 1–100000; `page_size`: 1–5. Поиск `q` до 80 символов, пробелы по краям удаляются; регистр учитывается согласно PostgreSQL collation, `%`, `_` и `/` ищутся буквально. Ответ: `items` (формат CandidateView), `page`, `page_size`, `has_next`. Даты и количество встреч относятся к текущему пользователю. SQL группирует активных Animal по неудалённым Encounter actor и сортирует по последней личной встрече DESC, UUID DESC; LIMIT/OFFSET применяется в БД. Следующая страница определяется одной дополнительной строкой. Это не snapshot: новые/удалённые встречи могут сдвинуть страницы; пустая страница в боте возвращает к первой.

`GET /internal/v1/users/me/collection-picker/{animal_id}?species=dog` возвращает одну карточку той же личной коллекции или 404. Перед привязкой Animal к черновику без места backend повторно проверяет вид, активность и наличие неудалённой встречи actor. Изменение места уже выбранного Animal в preview сохраняет выбор. Старый `/users/me/collection` и коллекция Mini App сохраняют свои контракты. Новые маршруты доступны только internal adapter с прежним service token и allowlist.

Бот без места сначала спрашивает «новое или знакомое». Список содержит до пяти подписей с датой и коротким UUID; фото загружается только при открытии карточки, выбор подтверждается отдельно. Запрос поиска — временное состояние бота; при resume он может сброситься, а фото, вид и отсутствие места остаются в серверном черновике.

Реальные ограничения доступа и ошибок заданы OpenAPI `/openapi.json` и интеграционными тестами. Неиспользуемые маршруты из первоначального проекта API не считаются реализованными.
