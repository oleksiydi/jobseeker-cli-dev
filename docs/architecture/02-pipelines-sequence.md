# Pipeline Sequence

## End-to-End Run Order

```mermaid
sequenceDiagram
    autonumber
    participant L as job_list_collector
    participant API1 as BA List API
    participant SA as stellenangebote
    participant SH as stellenangebote_history
    participant D as job_details_collector
    participant API2 as BA Details API
    participant JD as jobdetails
    participant JH as jobdetails_history
    participant A as analyze_jobs
    participant OAI as OpenAI API
    participant AN as analysen

    L->>API1: GET page jobs (with timeout/retry limits)
    API1-->>L: items[]
    L->>SA: upsert current meta (diff + meta_hash)
    L->>SH: list_seen/content_change/list_missing/list_restored

    D->>SA: read active vacancies + modifikationsTimestamp(_dt)
    D->>JD: batch read current details by referenznummer ($in)
    Note over D: prefilter candidates by SA vs JD timestamps
    D->>API2: GET details only for candidates
    API2-->>D: details / 404 / error
    D->>JD: insert/update only when needed
    D->>JH: no_content_change/content_change/remote_not_found

    A->>SA: aggregate active meta
    A->>JD: lookup current details
    A->>OAI: analyze candidate payload
    OAI-->>A: strict JSON response
    A->>AN: versioned upsert by input_hash
```

## Data Freshness Contract

- `job_list_collector` is source of truth for active vacancies.
- `job_details_collector` respects first-level date gate before details API.
- `analyze_jobs` creates a new analysis version only when input changes (`input_hash`).
