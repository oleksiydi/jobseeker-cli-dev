# System Overview

## Purpose

The system collects job meta, enriches job details, analyzes vacancies with LLM, and exposes grouped results in Streamlit.

## Decomposition Entry Points

This page keeps the legacy high-level overview, but the “general scheme” is now split into focused maps to reduce visual noise and line crossings.

Read in this order:

1. [35 System Context Map](35-system-context-map.md)
What it shows: L0 contours only (major blocks and integrations).

2. [36 Data Lifecycle Map](36-data-lifecycle-map.md)
What it shows: lifecycle of vacancy/profile/search artifacts and storage flow.

3. [37 Runtime Request Flow](37-runtime-request-flow.md)
What it shows: request-time path from query to ranked top-N output.

4. [38 Taxonomy Governance Flow](38-taxonomy-governance-flow.md)
What it shows: taxonomy/synonyms extraction, review, freeze, and runtime loader usage.

Why keep this page:
- fast legacy orientation in one diagram;
- backward-compatible links for existing docs;
- handoff reference while teams migrate to 35 -> 36 -> 37 -> 38.

## Component Map

```mermaid
flowchart LR
    subgraph External
        A1[BA Job List API]
        A2[BA Job Details API]
        A3[OpenAI API]
    end

    subgraph Pipelines
        P1[app/job_list_collector.py]
        P2[app/job_details_collector.py]
        P3[app/analyze_jobs.py]
        UI[app/ui_grouped.py]
    end

    subgraph MongoDB
        C1[(stellenangebote)]
        C2[(stellenangebote_history)]
        C3[(jobdetails)]
        C4[(jobdetails_history)]
        C5[(profiles)]
        C6[(analysis_prompts)]
        C7[(analysen)]
        C8[(pipeline_state)]
    end

    A1 --> P1
    P1 --> C1
    P1 --> C2
    P1 --> C8

    C1 --> P2
    A2 --> P2
    P2 --> C3
    P2 --> C4

    C1 --> P3
    C3 --> P3
    C5 --> P3
    C6 --> P3
    A3 --> P3
    P3 --> C7

    C7 --> UI
```

## Module Responsibility

- `job_list_collector`: idempotent upsert of vacancy meta, list presence events, and guarded active/inactive transitions. Missing-mark is applied only after a full scan and only when list query scope (`query_hash`) is unchanged vs previous run.
- `job_details_collector`: early date-based prefilter, API verification only for candidates, hash-based update and history.
- `analyze_jobs`: joins current meta+details, computes `input_hash`, skips unchanged inputs by `(referenznummer, candidate_profile_key, input_hash)`, and writes profile-scoped versioned documents.
- `ui_grouped`: read-only grouped presentation of analysis results.
