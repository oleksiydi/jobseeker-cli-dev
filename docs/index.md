# Документация `jobseeker-cli`

Этот сайт собран из Markdown-файлов проекта через `MkDocs` + `Material for MkDocs`.

## Что внутри

- Архитектурные схемы и описание пайплайнов.
- Документы по логике `job_details_collector`.
- Материалы по локальным эмбеддингам и оптимизации LLM-запросов.
- Процессные документы по нормализации/таксономии/синонимам (включая валидацию контрактов).

## С чего читать в первую очередь

1. [Runtime Contract (Current)](architecture/00-runtime-contract.md)
Путь: [`docs/architecture/00-runtime-contract.md`](architecture/00-runtime-contract.md)
Назначение: текущий контракт runtime (активность, retry, уникальность анализа).

2. [System Overview](architecture/01-system-overview.md)
Путь: [`docs/architecture/01-system-overview.md`](architecture/01-system-overview.md)
Назначение: общая карта системы.

3. [Pipeline Sequence](architecture/02-pipelines-sequence.md)
Путь: [`docs/architecture/02-pipelines-sequence.md`](architecture/02-pipelines-sequence.md)
Назначение: порядок выполнения пайплайнов.

4. [Synonyms Contract (RU)](process/synonyms-contract-ru.md)
Путь: [`docs/process/synonyms-contract-ru.md`](process/synonyms-contract-ru.md)
Назначение: контракт и валидатор синонимов (правила, запуск, обработка ошибок).

## Общее для человека и ИИ (источники истины)

- [Runtime Contract (Current)](architecture/00-runtime-contract.md)
Путь: [`docs/architecture/00-runtime-contract.md`](architecture/00-runtime-contract.md)
Почему важно: текущее поведение системы в коде (приоритет при конфликте с другими docs).

- [Pipeline Sequence](architecture/02-pipelines-sequence.md)
Путь: [`docs/architecture/02-pipelines-sequence.md`](architecture/02-pipelines-sequence.md)
Почему важно: фактический порядок этапов пайплайна.

- `README.md`
Путь: `README.md`
Почему важно: рабочие команды запуска и операционные сценарии.

- `PLANS.md`
Путь: `PLANS.md`
Почему важно: текущие этапы реализации и статус "что уже сделано / что осталось".

## Для кого какие документы

Для человека (разработчик/ревьюер):
- `README.md`
Путь: `README.md`
Роль: основной вход, команды запуска, ссылки на ключевые контуры.

- [Architecture Docs Overview](architecture/README.md)
Путь: [`docs/architecture/README.md`](architecture/README.md)
Роль: карта архитектурных документов.

- [Worklog Overview](worklog/README.md)
Путь: [`docs/worklog/README.md`](worklog/README.md)
Роль: журнал решений и сессий.

Для ИИ-ассистента и оператора процесса:
- `AGENTS.md`
Путь: `AGENTS.md`
Роль: базовые проектные правила и ограничения.

- [Assistant Operating Rules](process/assistant-operating-rules.md)
Путь: [`docs/process/assistant-operating-rules.md`](process/assistant-operating-rules.md)
Роль: операционный цикл изменений.

- [Architecture Change Checklist](process/architecture-change-checklist.md)
Путь: [`docs/process/architecture-change-checklist.md`](process/architecture-change-checklist.md)
Роль: проверка готовности архитектурных изменений.

- `PLANS.md`
Путь: `PLANS.md`
Роль: планирование крупных задач и фиксация статуса этапов.

## Диаграммы документации и артефактов

- [Diagram 01: Source of Truth Map](process/diagrams-01-source-of-truth-map.md)
Путь: [`docs/process/diagrams-01-source-of-truth-map.md`](process/diagrams-01-source-of-truth-map.md)

- [Diagram 02: Doc Change Impact Flow](process/diagrams-02-doc-change-impact-flow.md)
Путь: [`docs/process/diagrams-02-doc-change-impact-flow.md`](process/diagrams-02-doc-change-impact-flow.md)

- [Diagram 03: Taxonomy/Synonyms Artifact Lifecycle](process/diagrams-03-taxonomy-synonyms-artifact-lifecycle.md)
Путь: [`docs/process/diagrams-03-taxonomy-synonyms-artifact-lifecycle.md`](process/diagrams-03-taxonomy-synonyms-artifact-lifecycle.md)

- [Diagram 04: Audience Entry Points Map](process/diagrams-04-audience-entrypoints-map.md)
Путь: [`docs/process/diagrams-04-audience-entrypoints-map.md`](process/diagrams-04-audience-entrypoints-map.md)

- [Diagram 05: Plan Lifecycle and Gates](process/diagrams-05-plan-lifecycle-and-gates.md)
Путь: [`docs/process/diagrams-05-plan-lifecycle-and-gates.md`](process/diagrams-05-plan-lifecycle-and-gates.md)

- [Documentation Linking and Validation Rules](process/documentation-linking-validation-rules.md)
Путь: [`docs/process/documentation-linking-validation-rules.md`](process/documentation-linking-validation-rules.md)

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
