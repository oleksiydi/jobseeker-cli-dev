# 36 Data Lifecycle Map

Тип документа: `current`

## Purpose

L1-карта жизненного цикла данных: вакансии, детали, анализ, индексация, нормализационные артефакты.

```mermaid
flowchart TD
    A[BA list API] --> B[job_list_collector]
    B --> C[(stellenangebote)]
    B --> C1[(stellenangebote_history)]
    B --> C2[(pipeline_state)]

    C --> D[job_details_collector]
    D --> E[(jobdetails)]
    D --> E1[(jobdetails_history)]

    C --> F[analyze_jobs]
    E --> F
    F --> G[(analysen)]

    C --> H[build_job_index]
    E --> H
    H --> I[(matching_index_state)]
    H --> J[(Qdrant vectors)]

    K[profile normalization] --> L[(profiles)]
    K --> M[(taxonomy/synonyms auto/release)]

    M --> F
    M --> H

    click B "/architecture/02-pipelines-sequence/" "ETL order"
    click D "/architecture/03-job-details-decision-and-state/" "Details state/decisions"
    click H "/architecture/10-vacancy-version-state/" "Version/reindex lifecycle"
    click I "/architecture/11-selection-cache-state/" "Selection/index state"
    click M "/architecture/34-domain-attribution-layer-plan-ru/" "Domain/taxonomy evolution"
```

## Key Invariant

- Источники данных и runtime должны оставаться идемпотентными.
- Изменения в lifecycle синхронизируются с [00 Runtime Contract](00-runtime-contract.md).
