# Embedding/Chunking Flow Diagram

## Purpose

Shows how merged vacancy data is transformed into chunks and vectors.

Current implementation status:
- Baseline chunking is implemented (single robust chunk per vacancy).
- `spaCy + semantic chunking` is planned as next phase.

```mermaid
flowchart TD
    A[Load latest meta + details by referenznummer] --> B[Build merged vacancy doc]
    B --> C[Apply field priority title and role fallbacks]
    C --> D[Normalize canonical text]
    D --> E{Description exists?}

    E -- no --> F[Create synthetic chunk from title role location employment]
    E -- yes --> G[Build baseline chunk from description or canonical text]
    G --> S[Assign section_label by rules or unknown with confidence]

    F --> K[Build chunk payload metadata]
    S --> K

    K --> L[Compute chunk embeddings passage prefix]
    L --> N[Upsert chunk points to Qdrant]
    N --> O[Persist embedding metadata/hash/version in Mongo]

    %% Planned path (not implemented yet):
    G -. future .-> P[Sentence segmentation via spaCy]
    P -. future .-> Q[Semantic chunking by sentence similarity]
    Q -. future .-> S
```
