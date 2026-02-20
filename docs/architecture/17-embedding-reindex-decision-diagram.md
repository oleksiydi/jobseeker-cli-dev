# Embedding Reindex Decision Diagram

## Purpose

Shows strict decision rules for `skip` vs `reindex`, including:
- catch-up reindex for records that are not yet truly indexed in Qdrant;
- missing-description fallback.

```mermaid
flowchart TD
    A[Start job indexing for vacancy] --> B[Build canonical embedding input]
    B --> C[Compute embedding_input_hash]
    C --> D{index_status != indexed?}
    D -- yes --> F[needs_reindex true reason record_not_indexed_yet]
    D -- no --> E{Hash/model/chunking/preprocess changed?}
    E -- no --> S[Skip vacancy skip_reason unchanged_inputs]
    E -- yes --> F

    F --> G{Description missing?}
    G -- yes --> H[Use synthetic chunk and set description_missing true]
    G -- no --> I[Build baseline chunk from description or canonical text]

    H --> J[Compute embeddings]
    I --> J
    J --> K[Upsert vectors into Qdrant]
    K --> L[Update Mongo metadata versions indexed_at]
    L --> M[Count as changed]

    S --> N[Run summary counters changed/skipped]
    M --> N
```
