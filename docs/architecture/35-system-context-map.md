# 35 System Context Map

Тип документа: `current`

## Purpose

L0-карта: только крупные контуры системы и их связи.

```mermaid
flowchart LR
    U[User / Operator]

    subgraph App[Application Runtime]
      CLI[CLI / Scripts]
      UI[Streamlit UI]
      ETL[ETL Pipelines]
      MATCH[Search & Matching Runtime]
      NORM[Normalization Runtime]
    end

    subgraph Store[Storage]
      M[(MongoDB)]
      Q[(Qdrant)]
      FS[(Conf + Logs)]
    end

    subgraph External[External Services]
      BA[BA Job APIs]
      LLM[LLM Providers]
    end

    U --> CLI
    U --> UI

    CLI --> ETL
    CLI --> NORM
    UI --> MATCH

    ETL --> BA
    ETL --> M
    ETL --> FS

    NORM --> LLM
    NORM --> M
    NORM --> FS

    MATCH --> Q
    MATCH --> M
    MATCH --> LLM

    Q --> MATCH
    M --> MATCH

    click ETL "/architecture/02-pipelines-sequence/" "Pipeline end-to-end"
    click MATCH "/architecture/23-retrieval-rerank-runtime-flow/" "Retrieval + rerank runtime"
    click NORM "/architecture/00-runtime-contract/" "Normalization/runtime contract"
    click BA "/architecture/03-job-details-decision-and-state/" "Details API decision logic"
```

## Read Next

1. [36 Data Lifecycle Map](36-data-lifecycle-map.md)
2. [37 Runtime Request Flow](37-runtime-request-flow.md)
3. [38 Taxonomy Governance Flow](38-taxonomy-governance-flow.md)
