# Job Details: Decision Flow and States

## Decision Flow (`job_details_collector`)

```mermaid
flowchart TD
    S[Active vacancy from stellenangebote] --> R{referenznummer exists?}
    R -- no --> X1[Skip]
    R -- yes --> DT{modifikationsTimestamp parseable?}
    DT -- no --> X2[Skip: missing SA datetime]
    DT -- yes --> B[Add to prefilter batch]

    B --> Q[Load existing jobdetails by $in refs]
    Q --> G{existing JD and SA <= JD?}
    G -- yes --> X3[Guaranteed skip before API]
    G -- no --> V[Candidate for verify]

    V --> API[Call details API]
    API --> H{HTTP 404?}
    H -- yes --> E1[Write remote_not_found event]
    H -- no --> P{Payload received?}
    P -- no --> X4[Skip: no payload]
    P -- yes --> N{new_dt <= cur_dt?}
    N -- yes --> X5[Skip: non-newer API payload]
    N -- no --> CH{details_hash changed?}
    CH -- no --> E2[Write no_content_change + touch timestamps]
    CH -- yes --> U[Write content_change snapshot + diff update]
```

## State Diagram (per vacancy details)

```mermaid
stateDiagram-v2
    [*] --> NotPresent
    NotPresent --> Current: first successful details payload
    Current --> Current: guaranteed skip (SA <= JD)
    Current --> Current: no_content_change (hash equal)
    Current --> Current: content_change (hash differs, update applied)
    Current --> Current: non-newer API payload (new_dt <= cur_dt)
    Current --> Current: remote_not_found event
```

## Why this matters

- Expensive checks are executed only for uncertain candidates.
- API load is controlled by date gate and prefilter.
- History events preserve explainability of skipped/updated decisions.
