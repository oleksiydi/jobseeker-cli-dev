# 37 Runtime Request Flow

Тип документа: `current`

## Purpose

L1 runtime-поток одного поиска: от входного запроса до финального top-N и объясняющих полей.

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant S as search_jobs
    participant M as MongoDB
    participant Q as Qdrant
    participant R as Rerank
    participant L as LLM (optional)

    U->>S: query + profile_key + filters
    S->>M: load profile / config / states
    S->>Q: vector search top_k
    Q-->>S: chunk hits
    S->>M: fetch vacancy meta/details
    S->>R: compute role/profile/metadata + pre_llm
    R-->>S: rerank_score + top_chunks
    S->>L: optional compact context scoring
    L-->>S: llm_score (if enabled)
    S-->>U: top results JSON + evidence
```

## See Also

- [23 Retrieval + Rerank Runtime Flow](23-retrieval-rerank-runtime-flow.md)
- [24 Cache + LLM Handoff Explained](24-cache-llm-handoff-explained-ru.md)
- [25 Cache/Index Invalidation Overview](25-cache-index-invalidation-overview-ru.md)
