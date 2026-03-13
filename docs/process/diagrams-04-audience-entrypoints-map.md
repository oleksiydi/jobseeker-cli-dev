# Диаграмма 04: Audience Entry Points Map

Audience: `Human + AI`  
Source of truth: `Yes` (карта входов для разных ролей)

```mermaid
flowchart TD
    H[Human: Developer/Reviewer] --> H1[README.md]
    H1 --> H2[docs/index.md]
    H2 --> H3[architecture/README.md]
    H2 --> H4[worklog/README.md]

    AI[AI Assistant / Operator] --> A1[AGENTS.md]
    A1 --> A2[assistant-operating-rules.md]
    A1 --> A3[architecture-change-checklist.md]
    A1 --> A4[PLANS.md]

    BOTH[Both] --> B1[00-runtime-contract.md]
    BOTH --> B2[02-pipelines-sequence.md]
    BOTH --> B3[synonyms-contract-ru.md]

    click H2 "/" "Открыть docs/index"
    click H3 "/architecture/" "Открыть карту architecture docs"
    click H4 "/worklog/" "Открыть worklog"
    click A2 "/process/assistant-operating-rules/" "Открыть Assistant Operating Rules"
    click A3 "/process/architecture-change-checklist/" "Открыть Architecture Change Checklist"
    click B1 "/architecture/00-runtime-contract/" "Открыть Runtime Contract"
    click B2 "/architecture/02-pipelines-sequence/" "Открыть Pipeline Sequence"
    click B3 "/process/synonyms-contract-ru/" "Открыть Synonyms Contract"
```

Примечание: узлы `README.md`, `AGENTS.md`, `PLANS.md` отражают файлы за пределами `docs/`, поэтому как ссылки в диаграмме не заданы.

## Правило обновления
- Если меняется “первый документ для роли” или добавляется новая роль:
  - обновить карту и секцию Audience Map в `README.md`/`docs/index.md`.
