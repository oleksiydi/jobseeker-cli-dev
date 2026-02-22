# MongoDB Backup / Restore (Docker Compose)

## Цель

Короткий runbook для резервного копирования и восстановления MongoDB в текущем проектном окружении (`docker/docker-compose.yml`, сервис `mongo`).

## Инструменты

- `tools/mongo_backup.sh`
- `tools/mongo_restore.sh`

Оба скрипта используют `mongodump` / `mongorestore` внутри контейнера `mongo`.

## Быстрый backup (одна БД)

```bash
bash tools/mongo_backup.sh --db jobcenter
```

По умолчанию архив создается в `backups/` с UTC timestamp.

## Backup всех БД

```bash
bash tools/mongo_backup.sh --all-databases
```

## Быстрый restore (одна БД)

```bash
bash tools/mongo_restore.sh --archive backups/mongo_jobcenter_YYYYMMDDTHHMMSSZ.archive.gz --db jobcenter
```

## Restore с очисткой коллекций

```bash
bash tools/mongo_restore.sh --archive backups/mongo_jobcenter_YYYYMMDDTHHMMSSZ.archive.gz --db jobcenter --drop
```

`--drop` удаляет существующие коллекции перед восстановлением документов.

## Restore всех БД из архива

```bash
bash tools/mongo_restore.sh --archive backups/mongo_all_YYYYMMDDTHHMMSSZ.archive.gz --all-databases
```

## Важные параметры

- `--compose-file` (default: `docker/docker-compose.yml`)
- `--service` (default: `mongo`)
- `--username` (default: `root`)
- `--password` (default: `example`)
- `--auth-db` (default: `admin`)
- `--db` (default: `jobcenter`, если не `--all-databases`)

## Перед запуском

Убедись, что контейнер `mongo` запущен:

```bash
docker compose -f docker/docker-compose.yml up -d mongo
```

## Ограничения

1. Скрипты работают только с docker-compose окружением проекта.
2. Для `restore --drop` нужна осторожность: это потенциально разрушительная операция.
3. Если в `.env` используются другие креды, передай их явно через аргументы скриптов.

