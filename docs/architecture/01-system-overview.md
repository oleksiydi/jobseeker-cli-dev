# System Overview

## Purpose

The system collects job meta, enriches job details, analyzes vacancies with LLM, and exposes grouped results in Streamlit.

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
    end

    A1 --> P1
    P1 --> C1
    P1 --> C2

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

- `job_list_collector`: idempotent upsert of vacancy meta, list presence events, active/inactive state transitions.
- `job_details_collector`: early date-based prefilter, API verification only for candidates, hash-based update and history.
- `analyze_jobs`: joins current meta+details, computes `input_hash`, skips unchanged inputs, versioned writes to `analysen`.
- `ui_grouped`: read-only grouped presentation of analysis results.
