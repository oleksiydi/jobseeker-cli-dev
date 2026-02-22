# Smoke Test 05: One-off Migration `analysen` Unique Index

## Что тестируем

Проверяем корректность определения legacy/target unique-индекса для `analysen` в `tools/migrate_analysen_profile_scoped_index.py`.

## Набор данных

Unit-набор из `tests/test_import_tools.py`:
- synthetic list индексов Mongo;
- без подключения к реальной БД.

## Этапы

1. Поиск legacy unique-индекса `(referenznummer, input_hash)`.
2. Поиск target unique-индекса `(referenznummer, candidate_profile_key, input_hash)`.

## Что проверяется

1. Скрипт корректно находит legacy индекс для удаления.
2. Скрипт корректно определяет наличие target индекса.

## Что запускается

```bash
.venv/bin/pytest -q tests/test_import_tools.py -k "migrate_analysen"
```

## Ожидаемый результат

- Тесты проходят.
- Логика индексации готова к безопасному dry-run/apply.

## Возможные проблемы и ограничения

1. Это unit-валидация; операции `drop_index/create_index` в реальной Mongo не исполняются.
2. В продуктиве нужны отдельные dry-run/apply команды и контрольный повторный dry-run.

