# Project State Transfer (Server -> Laptop)

## Цель

Перенести рабочее состояние проекта на другую машину без ручной сборки по частям.

Покрываемый сценарий:
- на целевом ноутбуке нет Mongo/Qdrant данных, ключей и локальных конфигов;
- нужно быстро получить состояние, максимально близкое к серверному.

## Что входит в transfer bundle

Скрипт `tools/export_project_state_bundle.sh` включает:
- `.env` (включая секреты);
- `conf/`;
- `logs/normalization`;
- Mongo archive (`mongodump`);
- Qdrant storage snapshot (путь резолвится автоматически).

Не входит:
- `.venv`;
- Docker images;
- pip/system cache;
- прочие артефакты ОС.

## 1. Экспорт на сервере

Запускать из корня репозитория:

```bash
cd ~/projects/jobseeker-cli
bash tools/export_project_state_bundle.sh --mongo-db jobcenter
```

Резолв Qdrant path в export:
1. `--qdrant-data-path` (если передан);
2. mount из `docker inspect` для destination `/qdrant/storage`;
3. fallback: `docker/data/qdrant`, затем `data/qdrant`.

Результат:
- `backups/transfer/project_state_<UTC>.tar.gz`

Скопируйте этот файл на флэшку/ноутбук.

## 2. Импорт на ноутбуке

Запускать из корня репозитория:

```bash
cd ~/projects/jobseeker-cli
bash tools/import_project_state_bundle.sh \
  --bundle backups/transfer/project_state_<UTC>.tar.gz \
  --overwrite-env \
  --replace-conf \
  --restore-mongo-replace \
  --replace-logs \
  --replace-qdrant
```

Резолв path при import:
- Mongo reset path: `--mongo-data-path` -> `docker inspect` (`/data/db`) -> fallback.
- Qdrant restore path: `--qdrant-data-path` -> `docker inspect` (`/qdrant/storage`) -> fallback.

Что делает `--restore-mongo-replace`:
- `--reset-mongo-data` (очистка локальных mongo-данных);
- `--restore-mongo`;
- `--drop-mongo`.

Это рекомендуемый режим для чистого восстановления после переноса.

Опциональные override-флаги:
- `--qdrant-data-path /abs/path/to/qdrant/storage`
- `--mongo-data-path /abs/path/to/mongo/data`

## 3. Важное изменение поведения

Mongo restore в import-скрипте теперь **explicit-only**:
- restore выполняется только при явном `--restore-mongo` или `--restore-mongo-replace`;
- без этих флагов Mongo restore пропускается.

Дополнительно:
- если Qdrant path находится внутри Mongo data path, reset требует `--replace-qdrant`,
  иначе скрипт завершится с ошибкой для защиты от тихой потери данных.

## 4. Troubleshooting: Mongo auth error после переноса

Симптом:
- ошибка аутентификации при подключении к Mongo после импорта.

Типичная причина:
- старые локальные Mongo-данные в `data/` не соответствуют текущим credentials из `.env`.

Решение:
- используйте `--restore-mongo-replace` (рекомендуется), чтобы сбросить локальные mongo-данные и восстановить из архива.

## 5. Проверка после импорта

```bash
docker compose --env-file .env -f docker/docker-compose.yml ps
ls -lah logs/normalization | head
```

Проверка manifest в архиве:
- `includes_qdrant` теперь отражает факт включения snapshot;
- доступны служебные поля: `qdrant_path_resolved`, `qdrant_path_source`, `qdrant_include_reason`.

## Безопасность

Transfer bundle содержит `.env` и секреты.  
Храните и передавайте архив как секретные данные.
