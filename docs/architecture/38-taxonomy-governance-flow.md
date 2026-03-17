# 38 Taxonomy Governance Flow

Тип документа: `planned/current hybrid`

## Purpose

L1-карта управления словарями taxonomy/synonyms: набор, review, freeze, runtime-использование.

```mermaid
flowchart LR
    V[Vacancies] --> EXT[build_taxonomy_from_vacancies]
    P[Profiles] --> UPD[update_taxonomy_from_unknown_terms]

    EXT --> T1[(vacancy_terms.jsonl)]
    EXT --> T2[(taxonomy_domain_aggregate.json)]
    UPD --> T3[(taxonomy_pending.jsonl)]

    T2 --> RV[review + curation]
    T3 --> RV
    RV --> AUTO[(taxonomy.auto.json)]
    RV --> SALIAS[(synonyms.auto.json)]

    AUTO --> FRZ[freeze release]
    SALIAS --> FRZ
    FRZ --> REL[(taxonomy.release.json)]
    FRZ --> SREL[(synonyms.release.json)]

    REL --> LOAD[merged loader release + auto]
    AUTO --> LOAD
    SREL --> LOAD
    SALIAS --> LOAD

    LOAD --> RUN[profile/vacancy normalization runtime]

    click FRZ "/process/taxonomy-release-freeze-workflow-ru/" "Freeze workflow"
    click LOAD "/architecture/00-runtime-contract/" "Runtime contract"
    click RV "/process/synonyms-contract-ru/" "Synonyms contract"
```

## Operational Rules

- Release режим: learning флаги выключены по умолчанию.
- Dev режим: допускается наполнение `*.auto.json` с review.
- Перед релизом обязателен freeze и детерминизм-check.
