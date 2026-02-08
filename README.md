# jobseeker-cli

## Политика конфигов (`.env` vs `conf/app.json`)

- `conf/app.json` и `conf/profiles/default.profile.json`:
- Храним несекретные параметры приложения.
- Примеры: имя БД по умолчанию, имена коллекций, API URL/параметры, настройки анализатора.

- `.env`:
- Храним только секреты и чувствительные значения.
- Минимум: `MONGODB_URI`, `OPENAI_API_KEY`.

- ENV override (опционально):
- При необходимости можно временно переопределить несекретные параметры через ENV (`MONGODB_DB_NAME`, `COLLECTION_*`).
- Если override не задан, используются значения из `conf/app.json`.

- Важно:
- `config/` не используется как рабочий источник конфигурации.
- Единый стандарт проекта: `conf/`.

## Диагностика `404` в `jobdetails`

- Новый инструмент: `tools/check_list_seen.py`.
- Назначение: проверить, были ли проблемные `referenznummer` в последнем запуске сборщика списка (`list_seen`).

- Проверить номера из лога `job_details_collector`:
```bash
.venv/bin/python tools/check_list_seen.py --from-log /tmp/job_details_after_list.log --print-missing
```

- Проверить конкретные номера:
```bash
.venv/bin/python tools/check_list_seen.py --ref 10001-1000943252-S --ref 10000-1205119986-S --print-missing --print-present
```

- Проверить относительно конкретного `run_id`:
```bash
.venv/bin/python tools/check_list_seen.py --run-id list-20260206T232446-cc175cdc --from-log /tmp/job_details_after_list.log --print-missing
```

- Логика:
- `present_in_run`: номер был в последнем list-run.
- `missing_in_run`: номер отсутствовал в list-run (типичный случай для части `404` в деталях).
