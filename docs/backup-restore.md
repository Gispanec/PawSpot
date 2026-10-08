# Локальный backup/restore DB + photos

Копия содержит данные PostgreSQL (включая пользователей, точные координаты и hashes сессий) и JPEG main/thumbnail. `.env`, Telegram/service tokens и другие файлы не копируются. Это **чувствительная копия**: храните на защищённом диске с ограниченным доступом; не отправляйте её в Git или публичные сервисы. Автоматического расписания/retention нет. Устаревшие копии оператор удаляет вручную с учётом нужного срока восстановления.

Нужны работающий Docker-контейнер PostgreSQL/PostGIS, uv и установленные зависимости. Скрипт использует pg_dump/pg_restore из того же контейнера через локальное PostgreSQL-соединение; пароль не передаётся в аргументах. Если local authentication требует пароль, команда завершится ошибкой: настройте контейнер отдельно, не помещайте пароль в командную строку. Имя контейнера/БД/роли и каталог photos должны соответствовать вашей конфигурации; значения ниже — стандартные локальные.

Инструменты работают с `--no-password`, без интерактивного запроса credentials. При ошибке консоль показывает инструмент и exit code; полный stderr сохраняется в отдельном `.local/backup-restore-errors/<id>.log`. Он может содержать приватные данные: не публикуйте его. Ошибки не игнорируются, лог не входит в backup.

## 1. Создать копию

Остановите через Ctrl+C **Backend и Bot**, а также cleanup/любые другие DB/photo writers. PostgreSQL оставьте работающим. Дождитесь завершения запросов. Это нужно для согласованности DB snapshot и photos; скрипт не останавливает процессы автоматически. `-WritersStopped` — подтверждение оператора, а не обнаружение процессов.

Из корня проекта, PowerShell:

```powershell
./scripts/backup-pawspot.ps1 -Operation Backup -WritersStopped `
  -Container infra-postgres-1 -Database pawspot -User pawspot -Photos media
```

Snapshot: `backups/<UTC-date>-<random-suffix>/database.dump`, `photos/`, `manifest.json`. Каталог уникален, существующие копии не перезаписываются. Manifest записывается последним и содержит SHA-256. При сбое может остаться неполный каталог: он не считается backup и не подходит для restore. Если фото пока нет, создайте пустой media-каталог перед backup. Не используйте каталог проекта вместо media: посторонние файлы запрещены.

## 2. Проверить копию

```powershell
./scripts/backup-pawspot.ps1 -Operation Verify -Snapshot backups/<snapshot>
```

Verify проверяет формат dump, набор файлов, безопасные пути и hashes; Docker для этой команды не нужен. Это обнаружение повреждений, а не доказательство восстановления. Полная проверка — restore в отдельное окружение ниже. Копируйте весь snapshot, включая manifest, на защищённый носитель; проверьте hashes после переноса.

## 3. Restore только в новое окружение

Предпочтительно используйте отдельный временный test-контейнер (см. README), который не содержит пилотной БД:

```powershell
docker compose -f infra/compose.test.yaml up -d --wait
./scripts/backup-pawspot.ps1 -Operation Restore -Snapshot backups/<snapshot> `
  -Container pawspot-tests-postgres-1 -User pawspot_test -Database pawspot_restore_check
```

Разрешено только явное имя **pawspot_restore_<suffix>**. Скрипт создаёт новую БД через createdb; существующая БД всегда приводит к отказу. DROP/--clean/замены пилота нет. Photos записываются только в новый `.local/restore/<database>/media`, существующий каталог запрещён. При ошибке restore новая БД может остаться для диагностики; повторяйте с новым suffix. DB восстанавливается в одной транзакции, ошибки не игнорируются. Восстановленный SQL dump должен происходить из доверенного backup: hashes не защищают от намеренно подменённого manifest/SQL.

## 4. Убедиться в восстановлении

```powershell
docker exec pawspot-tests-postgres-1 psql -U pawspot_test -d pawspot_restore_check -c "SELECT PostGIS_Version(); SELECT count(*) FROM animals; SELECT count(*) FROM encounters; SELECT count(*) FROM photos;"
```

Сравните counts с исходной копией при остановленных writers. Restore автоматически сравнивает SHA-256 каждого восстановленного JPEG. Для визуальной проверки откройте main/thumbnail только из нового `.local/restore/.../media`. Для smoke-test API используйте отдельный процесс/порт и явно задайте PAWSPOT_DB_HOST/PORT/NAME/USER/PASSWORD и PAWSPOT_MEDIA_DIR восстановленного окружения; не запускайте там Bot и не меняйте пилотный .env. Сессии и allowlist остаются закрытыми.

POSIX: те же операции доступны через `uv run --locked --all-packages --all-extras python -m pawspot.backup backup|verify|restore --help`; команды Docker одинаковые. Frontend и launcher для backup/restore не требуются.
