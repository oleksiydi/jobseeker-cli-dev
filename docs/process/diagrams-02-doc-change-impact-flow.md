# Диаграмма 02: Doc Change Impact Flow

Audience: `Human + AI`  
Source of truth: `Yes` (какие документы и проверки обновлять при изменениях)

```mermaid
flowchart TD
    X[Code/config change] --> Y{Change type}

    Y -->|Runtime contract / algorithm| A[Update architecture docs]
    Y -->|ENV/CLI defaults| B[Update .env.example + README]
    Y -->|Process/ops workflow| C[Update process docs]
    Y -->|Large scope| D[Update PLANS.md first]

    A --> E[Update 00-runtime-contract if behavior changed]
    B --> F[Document precedence: CLI > ENV > defaults]
    C --> G[Update checklists/runbooks]
    D --> H[Track step status and open items]

    E --> T[Run tests/smoke]
    F --> T
    G --> T
    H --> T

    T --> Z[Ready for review]

    click A "/architecture/README/" "Открыть карту архитектурных документов"
    click C "/process/assistant-operating-rules/" "Открыть процессные правила"
    click D "/" "Открыть docs/index"
    click E "/architecture/00-runtime-contract/" "Открыть Runtime Contract"
    click G "/process/architecture-change-checklist/" "Открыть checklist"
    click Z "/process/architecture-change-checklist/" "Перейти к checklist перед review"
```

Примечание: шаги про `.env.example`, `README.md`, `PLANS.md` остаются текстовыми в диаграмме, так как это файлы вне каталога `docs/`.

## Правило обновления
- При добавлении нового типа изменений или нового обязательного артефакта:
  - добавить ветку в flow и соответствующий check.
