# jobseeker-cli

## Первый запуск (обязательно)

Перед запуском пайплайнов нужно один раз загрузить в MongoDB:
- профиль(и) кандидата в `profiles`;
- default prompt/task в `analysis_prompts`.

Команды:
```bash
.venv/bin/python tools/import_profiles.py --file conf/profiles/default.profile.json
.venv/bin/python tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json
```

Проверка без записи (опционально):
```bash
.venv/bin/python tools/import_profiles.py --file conf/profiles/default.profile.json --dry-run
.venv/bin/python tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json --dry-run
```

После этого можно запускать пайплайны:
```bash
.venv/bin/python -m app.job_list_collector
.venv/bin/python -m app.job_details_collector
.venv/bin/python -m app.analyze_jobs
```

## Типовые ошибки старта

- `MONGODB_URI is not set`
- Причина: не настроен `.env` или переменная окружения.
- Что проверить:
```bash
grep -E '^MONGODB_URI=' .env
```

- `Prompt не найден: key='default'...`
- Причина: не загружен default prompt/task в `analysis_prompts`.
- Что сделать:
```bash
.venv/bin/python tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json
```

- `Профиль с key='default' не найден`
- Причина: не загружен профиль в `profiles`.
- Что сделать:
```bash
.venv/bin/python tools/import_profiles.py --file conf/profiles/default.profile.json
```

- `dry_run=True, updated=...` повторяется каждый запуск
- Причина: `--dry-run` не пишет в Mongo, поэтому изменения только “планируются”.
- Что сделать:
```bash
.venv/bin/python tools/import_profiles.py --file conf/profiles/default.profile.json
.venv/bin/python tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json
```

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
