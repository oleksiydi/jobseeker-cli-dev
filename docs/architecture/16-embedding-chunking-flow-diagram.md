# Embedding/Chunking Flow Diagram

## Purpose

Shows how merged vacancy data is transformed into chunks and vectors.

Current implementation status:
- `spaCy + semantic chunking` is implemented.
- `syntok` and `regex` sentence split fallback modes are supported.
- token-aware chunk cap is enabled before hard char limits.

```mermaid
flowchart TD
    A[Load latest meta + details by referenznummer] --> B[Build merged vacancy doc]
    B --> C[Apply field priority title and role fallbacks]
    C --> D[Normalize canonical text]
    D --> E{Description exists?}

    E -- no --> F[Create synthetic chunk from title role location employment]
    E -- yes --> P[Sentence segmentation mode spacy syntok regex]
    P --> Q[Semantic chunking by sentence similarity]
    Q --> T[Token-aware cap by tokenizer max/overlap]
    T --> U[Hard char limits merge split]
    U --> S[Assign section_label by rules or unknown with confidence]

    F --> K[Build chunk payload metadata]
    S --> K

    K --> L[Compute chunk embeddings passage prefix]
    L --> N[Upsert chunk points to Qdrant]
    N --> O[Persist embedding metadata/hash/version in Mongo]
```
