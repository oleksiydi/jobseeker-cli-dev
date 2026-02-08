# План: хранение SYSTEM_PROMPT и ANALYSIS_TASK в MongoDB

## Цель
- Убрать жестко зашитые `SYSTEM_PROMPT` и `ANALYSIS_TASK` из runtime-логики.
- Хранить шаблоны запросов в отдельной коллекции MongoDB.
- Поддержать создание новых версий prompt/task без правки кода.
- Использовать prompt по умолчанию, если в запуске явно не задан другой.

## Предлагаемый вариант (рекомендуемый)
- Новая коллекция: `analysis_prompts`.
- Документы immutable по версии: новая редакция создается новой `version`, старые не переписываются.
- Выбор prompt в анализаторе:
- `analyzer.prompt_key` из `conf/app.json` (или ENV override).
- `analyzer.prompt_version` опционально.
- Если версия не задана: берется последняя активная (`is_active=true`) версия по ключу.

## Схема документа `analysis_prompts`
- `key` (string): логический ключ, например `default`.
- `version` (int): номер версии внутри `key`.
- `name` (string): короткое имя.
- `description` (string): краткое описание.
- `system_prompt` (string): текст system-инструкции.
- `analysis_task` (string): текст task для user payload.
- `prompt_hash` (string): sha256 от нормализованной пары `system_prompt + analysis_task`.
- `is_active` (bool): доступность для использования.
- `created_at` (datetime UTC), `updated_at` (datetime UTC).
- `created_by` (string, optional).

## Индексы
- Unique: `(key, version)`.
- Index: `(key, is_active, version desc)`.
- Optional unique partial: `(key)` where `is_default=true` (если решим хранить default отдельным флагом).

## Файл по умолчанию
- Новый файл: `conf/prompts/default.prompt_task.json`.
- Содержит один документ prompt версии `1` для ключа `default`.

Пример:
```json
{
  "key": "default",
  "version": 1,
  "name": "Default strict recruiter prompt",
  "description": "Базовый промпт для оценки вакансий и кандидата",
  "system_prompt": "...",
  "analysis_task": "Bewerte die Passung und erstelle Anschreiben. score=0..100 (ohne Prozentzeichen).",
  "is_active": true
}
```

## Утилита импорта
- Новый скрипт: `tools/import_analysis_prompts.py`.
- Поведение:
- читает JSON (объект или массив) из `conf/prompts/default.prompt_task.json` по умолчанию;
- upsert по `(key, version)`;
- проставляет `created_at`/`updated_at`;
- пересчитывает `prompt_hash`;
- создает нужные индексы;
- печатает `inserted/updated/unchanged`.

## Изменения в анализаторе
- `app/analyze_jobs.py`:
- убрать зависимость от констант как источника prompt по умолчанию;
- загрузка prompt из `analysis_prompts` по выбранному `key/version`;
- fallback-логика: если не задано явно, использовать `analyzer.prompt_key="default"` и latest active version;
- fail-fast с понятной ошибкой, если prompt не найден.

## Конфиг
- `conf/app.json`:
- добавить `collections.analysis_prompts` (по умолчанию `analysis_prompts`);
- добавить `analyzer.prompt_key` (по умолчанию `default`);
- добавить `analyzer.prompt_version` (nullable).

## Совместимость и rollout
- Этап 1 (безопасный): добавить коллекцию, файл, импорт и чтение из БД; при отсутствии данных явная ошибка с инструкцией запуска импорта.
- Этап 2: удалить/минимизировать hardcoded fallback в коде.

## Почему этот вариант лучший
- Управление prompt без деплоя кода.
- Прозрачная версионность и воспроизводимость.
- Легко масштабировать A/B-подходы по `prompt_key`.
- Встроенная совместимость с текущим `input_hash` (prompt станет частью входа через `prompt_hash`/snapshot).
