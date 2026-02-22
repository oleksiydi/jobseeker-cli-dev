# MongoDB Backup / Restore (Docker Compose)

## Цель

Короткий runbook для резервного копирования и восстановления MongoDB в текущем проектном окружении (`docker/docker-compose.yml`, сервис `mongo`).

## Инструменты

- `tools/mongo_backup.sh`
- `tools/mongo_restore.sh`

Оба скрипта используют `mongodump` / `mongorestore` внутри контейнера `mongo`.

## Быстрый backup (одна БД)

```bash
bash tools/mongo_backup.sh --env-file .env --db jobcenter
```

По умолчанию архив создается в `backups/` с UTC timestamp.

## Backup всех БД

```bash
bash tools/mongo_backup.sh --env-file .env --all-databases
```

## Быстрый restore (одна БД)

```bash
bash tools/mongo_restore.sh --env-file .env --archive backups/mongo_jobcenter_YYYYMMDDTHHMMSSZ.archive.gz --db jobcenter
```

## Restore с очисткой коллекций

```bash
bash tools/mongo_restore.sh --env-file .env --archive backups/mongo_jobcenter_YYYYMMDDTHHMMSSZ.archive.gz --db jobcenter --drop
```

`--drop` удаляет существующие коллекции перед восстановлением документов.

## Restore всех БД из архива

```bash
bash tools/mongo_restore.sh --env-file .env --archive backups/mongo_all_YYYYMMDDTHHMMSSZ.archive.gz --all-databases
```

## Важные параметры

- `--env-file` (default: `.env` в корне репозитория)
- `--compose-file` (default: `docker/docker-compose.yml`)
- `--service` (default: `mongo`)
- `--username` (default: `${MONGO_INITDB_ROOT_USERNAME:-root}`)
- `--password` (default: `${MONGO_INITDB_ROOT_PASSWORD}`; обязательно задать)
- `--auth-db` (default: `${MONGO_AUTH_DB:-admin}`)
- `--db` (default: `jobcenter`, если не `--all-databases`)

Если пароль не задан, скрипт завершится с ошибкой до запуска `mongodump/mongorestore`.

## Перед запуском

Убедись, что контейнер `mongo` запущен:

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d mongo
```

## Ограничения

1. Скрипты работают только с docker-compose окружением проекта.
2. Для `restore --drop` нужна осторожность: это потенциально разрушительная операция.
3. Если в `.env` используются другие креды, передай их явно через аргументы скриптов.
