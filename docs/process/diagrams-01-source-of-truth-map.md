# Диаграмма 01: Source of Truth Map

Audience: `Human + AI`  
Source of truth: `Yes` (навигационная карта приоритетов документации)

```mermaid
flowchart TD
    A[AGENTS.md\nProject rules / constraints] --> B[assistant-operating-rules.md\nDaily execution cycle]
    A --> C[architecture-change-checklist.md\nDoD checks]
    A --> D[PLANS.md\nLarge-task planning]

    E[README.md\nEntry commands + quick links] --> F[docs/index.md\nDocs entrypoint]
    F --> G[architecture/00-runtime-contract.md\nCurrent runtime truth]
    F --> H[architecture/02-pipelines-sequence.md\nPipeline order]
    F --> I[process/synonyms-contract-ru.md\nSynonyms contract + validator]

    D --> J[Implementation commits]
    J --> K[Tests / smoke]
    K --> L[Docs updates]

    G -.if conflict.-> M[Prefer Runtime Contract]

    click B "/process/assistant-operating-rules/" "Открыть Assistant Operating Rules"
    click C "/process/architecture-change-checklist/" "Открыть Architecture Change Checklist"
    click F "/" "Открыть docs/index"
    click G "/architecture/00-runtime-contract/" "Открыть Runtime Contract"
    click H "/architecture/02-pipelines-sequence/" "Открыть Pipeline Sequence"
    click I "/process/synonyms-contract-ru/" "Открыть Synonyms Contract"
```

Примечание: узлы `AGENTS.md`, `README.md`, `PLANS.md` относятся к файлам вне `docs/`, поэтому в диаграмме не сделаны кликабельными (в strict-режиме это ломает сборку).

## Правило обновления
- Если меняется приоритет документа или “кто источник истины” по теме:
  - обновить эту диаграмму в том же PR/коммите.
