# PawSpot: проект API

Phase 0. Endpoints ниже ещё не реализованы. Prefix `/api/v1`; JSON; UUID в строках; даты ISO 8601 UTC. Авторизация — opaque Bearer session. Исключения: auth exchange и технический health. Для пилота весь контент и media требуют allowlist/session.

## Контракт и доступ

Actor берётся из проверенного principal, никогда из user_id в пользовательском body. Internal adapter бота под `/internal/v1` принимает actor.telegram_id только с service credential и применяет те же ограничения allowlist, ownership и лимиты. Его маршруты зеркалят только необходимые боту операции drafts, photos, matching, commit, чтение карточки и собственной коллекции. Бизнес-реализация общая с `/api/v1`; различается получение principal. Internal prefix не публикуется через внешний reverse proxy.

Ответы ошибок: `{error: {code, message, request_id}}`. Основные статусы: 401 invalid/expired session; 403 доступ запрещён; 404 недоступный чужой/скрытый ресурс; 409 stale version/state conflict; 413 слишком большой файл; 415 неподдерживаемое изображение; 422 невалидные поля; 429 лимит с Retry-After; 503 временная недоступность. Validation errors не повторяют initData, токены, координаты и полное body.

Списки используют cursor `(sort_time,id)`, `limit` default 20, max 50. Cursor не является авторизацией. Никаких неограниченных timeline/feed. Feed сортируется по created_at, timeline по observed_at, с устойчивым tie-break UUID.

## Основные операции

| Метод и маршрут | Use case / существенные поля |
| --- | --- |
| POST /auth/telegram | raw init_data → проверка Telegram, User upsert, session token + expires_at; ответ no-store |
| DELETE /auth/session | Отозвать текущую сессию |
| GET /cities | Поддерживаемые города, timezone, центр для начального вида карты |
| GET /users/me | Собственное display_name и статистика; без raw Telegram credentials |
| GET /users/me/collection | Уникальные Animal по видимым собственным Encounter; species filter, cursor |
| POST /encounter-drafts | Вернуть существующий active draft либо создать новый; возможен выбранный animal_public_id из профиля |
| GET /encounter-drafts/current | Продолжить текущий draft; приватные coordinates не возвращаются |
| GET /encounter-drafts/{id} | Состояние и next_actions, только владельцу |
| PATCH /encounter-drafts/{id} | expected_version + изменённые поля: species, location/null, city_public_id/null, animal_public_id ИЛИ new_animal, comment, observed_at |
| PUT /encounter-drafts/{id}/photo | Multipart image + expected_version; ownership, normalization, новый photo_public_id, updated version |
| POST /encounter-drafts/{id}/matches | Кандидаты по данным собственного draft; никаких чужих точных точек и произвольного radius |
| POST /encounter-drafts/{id}/commit | expected_version → Encounter, canonical Animal и share links; повтор возвращает прежний результат |
| DELETE /encounter-drafts/{id} | Отмена active draft; идемпотентно; committed нельзя отменить как draft |
| GET /animals/{id} | Профиль, aggregate stats, canonical_public_id при merge, безопасная область |
| GET /animals/{id}/encounters | Timeline с cursor |
| GET /encounters/{id} | Отдельная карточка встречи |
| DELETE /encounters/{id} | Автор удаляет свою встречу; повтор идемпотентен; обновляется видимость/primary photo |
| GET /feed | Лента, optional city/species; встречи без geo не исчезают из общей ленты |
| GET /map/animals | Ограниченный bbox по public points, species, cursor; до 200 точек на страницу, признак next_cursor |
| PUT /encounters/{id}/reactions/{type} | Идемпотентно добавить одну реакцию выбранного типа |
| DELETE /encounters/{id}/reactions/{type} | Идемпотентно убрать собственную реакцию |
| GET /photos/{id}/{variant} | Проверка доступа; main/thumbnail, нормализованные bytes либо разрешённый короткий signed URL |

Для map ограничиваем площадь bbox и разрешённые значения; bbox, пересекающий ±180, разбивается сервером на две области. Marker — последняя видимая географическая встреча каждого Animal; выбираем её **до** bbox-фильтра, иначе одна собака появится в старом месте только из-за перемещения карты. Отдельно возвращаем last_encounter_at и last_located_encounter_at. Aggregate stats считаются по всем видимым встречам. Плотные одинаковые точки отображаются группой; на первом пилоте достаточно группировки по public_cell_key, отдельный clustering backend не нужен.

Не вводим прямые POST /animals и POST /encounters: общий draft/commit обеспечивает первую встречу, photo ownership и идемпотентность. Отдельная таблица collection, CRUD пользователей, endpoint для arbitrary photo URL и публичный merge отсутствуют.

## Черновик и создание встречи

Логические состояния: need_photo → need_species → need_location_or_skip → choose_animal → optional_details → ready → committed. Состояние вычисляется backend из полей; Bot не хранит отдельную копию бизнес-правил. Можно вернуться назад; при смене species сбрасывается несовместимый animal selection, при смене location обновляются кандидаты.

Пример PATCH для нового животного (координаты в запросе разрешены, в ответ не копируются):

```json
{
  "expected_version": 3,
  "species": "dog",
  "location": {"latitude": 41.71, "longitude": 44.82},
  "new_animal": {"name": "Гиви"},
  "comment": "Встретил у пекарни"
}
```

Для существующего — animal_public_id вместо new_animal; backend проверяет species и canonical status. UUID фото передаётся через upload endpoint, пользователь не может привязать чужой готовый файл. Optional location явно null означает пропуск, отсутствие поля PATCH означает оставить как есть. Empty name нормализуется в null. Все длины считаются после trim, но пользовательский текст не интерпретируется как HTML.

Commit сначала проверяет уже сохранённый result; для committed draft повтор с прежней version возвращает его, а не 409. Для active — проверяется version. Ответ первого commit 201, повторного 200. Формат включает encounter_public_id, animal_public_id, share_url и telegram_url; final card можно перечитать.

## Публичное представление

Encounter DTO: public_id, animal summary, author `{public_id,display_name}`, нормализованное photo URL, comment, observed_at, created_at, approximate_location либо null, reaction counts и own reactions. approximate_location: public latitude/longitude, cell key, label «Приблизительная область», policy scale. Ни private_location, ни telegram_id, ни исходный filename, ни точный matching distance не выдаются.

Animal DTO: public_id, canonical_public_id, species, nullable name, primary photo, city, approximate_location, encounters_count, photos_count, observers_count, reactions_count, first_observed_at, last_observed_at, created_by, created_at, last_located_encounter_at. Timeline отдельным paginated запросом, а не бесконечным nested JSON.

Свой профиль не получает расширенные приватные координаты через «удобный» общий serializer. Для внутреннего бота ответы столь же безопасны: ему не нужно читать сохранённую private location обратно.

## Проверяемые сценарии

1. Новый Animal и первая встреча без имени/заметки/геолокации; есть в коллекции, нет на карте.
2. Встреча с существующим Animal: одна сущность, две записи timeline, два автора.
3. Timeout после commit + retry: прежний UUID, один Encounter.
4. Одновременный commit из Bot/Mini App: один результат; конфликтующие PATCH дают 409.
5. Подмена actor/photo/draft, просроченная сессия и initData отклоняются.
6. Повтор PUT reaction не увеличивает счётчик; DELETE безопасно повторяется.
7. Merge не ломает Animal/Encounter share routes и пересчитывает коллекцию.
8. Изменение private_location при неизменной public_location не влияет на public выдачи/matches.
9. Фото после удаления встречи недоступно новым запросам; lifetime уже выданного signed URL ограничен и документирован.
10. Повторные запросы и разные читатели получают одинаковую public point.

Точная OpenAPI-схема появляется с реализацией. Этот документ обновляется вместе с контрактом, не заменяет интеграционные тесты.
