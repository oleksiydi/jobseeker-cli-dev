# Документация `jobseeker-cli`

Этот сайт собран из Markdown-файлов проекта через `MkDocs` + `Material for MkDocs`.

## Что внутри

- Архитектурные схемы и описание пайплайнов.
- Документы по логике `job_details_collector`.
- Материалы по локальным эмбеддингам и оптимизации LLM-запросов.
- Процессные документы по нормализации/таксономии/синонимам (включая валидацию контрактов).

## С чего читать в первую очередь

1. `docs/architecture/00-runtime-contract.md` — текущий контракт runtime (активность, retry, уникальность анализа).
2. `docs/architecture/01-system-overview.md` — общая карта системы.
3. `docs/architecture/02-pipelines-sequence.md` — порядок выполнения пайплайнов.
4. `docs/process/synonyms-contract-ru.md` — контракт и валидатор синонимов (где лежат правила, как запускать, как чинить ошибки).

## Типы документов

- `current`: описывает фактическое поведение кода сейчас.
- `planned`: целевые изменения и проектные шаги.
- `historical`: журналы с ходом работ и решениями по датам.

## Как запускать локально

Через Docker Compose:

```bash
docker compose -f docker/docker-compose.docs.yml up
```

Документация будет доступна по адресу:

```text
http://localhost:8000
```

Остановка:

```bash
docker compose -f docker/docker-compose.docs.yml down
```
