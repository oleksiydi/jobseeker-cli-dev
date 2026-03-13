# Диаграмма 05: Plan Lifecycle and Gates

Audience: `Human + AI`  
Source of truth: `Yes` (обязательные gate-этапы для крупных изменений)

```mermaid
flowchart LR
    Planned[Planned\nAdd/Update plan in PLANS.md] --> InProgress[In Progress\nStart implementation step]
    InProgress --> Verify[Verify\nRun tests/smoke]
    Verify --> DocsUpdate[Docs Update\nUpdate docs/contracts/checklists]
    DocsUpdate --> ReadyForReview[Ready For Review\nSummarize done/open items]
    ReadyForReview --> Completed[Completed\nMerge/Finalize]

    Verify -->|Failures found| InProgress
    DocsUpdate -->|Missing doc links/contracts| InProgress

    click Verify "/process/assistant-operating-rules/" "Открыть правила операционного цикла"
    click DocsUpdate "/process/architecture-change-checklist/" "Открыть checklist обновления"
    click ReadyForReview "/process/architecture-change-checklist/" "Проверить готовность к review"
```

Примечание: этап `Planned` ссылается на `PLANS.md` (файл вне `docs/`), поэтому в диаграмме оставлен без клика.

## Правило обновления
- Если добавляется новый обязательный gate (например новый quality gate):
  - добавить его в lifecycle и в checklist (`architecture-change-checklist.md`).
