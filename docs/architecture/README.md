# Architecture Docs

This folder contains the visual system documentation for developers.
All diagrams are written in Mermaid so they can be viewed directly in VS Code Markdown Preview.

Contents:

- `docs/architecture/01-system-overview.md`: modules, data stores, external systems.
- `docs/architecture/02-pipelines-sequence.md`: end-to-end pipeline flow.
- `docs/architecture/03-job-details-decision-and-state.md`: decision logic and states for details collector.
- `docs/architecture/04-maintenance-playbook.md`: how to use docs for troubleshooting and safe system evolution.
- `docs/architecture/05-embeddings-and-llm-cost-optimization-ru.md`: Russian guide for local embeddings, shortlist retrieval, and LLM cost optimization.
- `docs/architecture/06-embeddings-and-llm-cost-optimization-ru-explained.md`: same guide with in-place explanations of terms, technologies, and which project problems they solve.
- `docs/architecture/07-local-matching-plan-ru-beginner.md`: beginner-friendly detailed local matching plan (CPU/nightly batch, change-aware indexing, chunk delivery for LLM).
- `docs/architecture/08-local-matching-implementation-plan.md`: implementation-first technical plan with phases, scoring, metrics, and acceptance criteria.
- `docs/architecture/09-retrieval-pipeline.md`: diagram of end-to-end retrieval flow (merge -> chunk -> embed -> search -> rerank -> shortlist).
- `docs/architecture/10-vacancy-version-state.md`: state machine for vacancy indexing/version transitions and reindex triggers.
- `docs/architecture/11-selection-cache-state.md`: cache lifecycle for `(profile/query/filters/versions)` selection reuse.
- `docs/architecture/12-top20-expansion-logic.md`: anti-stagnation top-20 expansion to include new candidates.
- `docs/architecture/13-llm-context-assembly.md`: compact context-building flow for local LLM handoff.
- `docs/architecture/14-phase2-index-builder-step-by-step-ru.md`: Phase 2 explained in Russian, step-by-step in plain language (what to do, why, and done criteria).
- `docs/architecture/15-embedding-chunking-spec-v1-ru.md`: strict RU spec for field priority, canonical embedding input, semantic chunking, fallback rules, and reindex criteria.
- `docs/architecture/16-embedding-chunking-flow-diagram.md`: Mermaid flow of merge -> semantic chunks -> vectors -> Qdrant/Mongo metadata.
- `docs/architecture/17-embedding-reindex-decision-diagram.md`: Mermaid decision flow for skip/reindex and missing-description fallback.
- `docs/architecture/18-canonical-text-payload-llm-examples-ru.md`: detailed RU examples of canonical text formation, Qdrant payload, Mongo metadata, and LLM input/output formats.
- `docs/architecture/19-rules-stack-implementation-plan-ru.md`: practical plan for production-style rules stack (`Pydantic + Pandera + JSONLogic`), including execution model, responsibilities, and migration steps.
- `docs/architecture/20-phase2-chunking-runtime-flow.md`: sequence diagram for runtime chunk construction (`rule flags -> spaCy -> semantic merge -> token-aware -> hard limits -> labels`).
- `docs/architecture/21-phase2-chunking-system-integration.md`: system-level map of how chunking interacts with audit/state/Qdrant/retrieval.
- `docs/architecture/22-chunking-smoke-scenarios.md`: practical smoke runbook for `spacy`, `syntok`, and `regex+char` modes with expected `matching_index_state` fields.
- `docs/architecture/23-retrieval-rerank-runtime-flow.md`: runtime flow of implemented `app/search_jobs.py` (`query/profile -> Qdrant -> aggregate -> score -> rerank -> top_chunks`).
- `docs/architecture/24-cache-llm-handoff-explained-ru.md`: Russian practical explanation of significant input/version changes, selection cache invalidation, long/short vacancy handoff payload, chunk-group impact, and current baseline rerank formula.
- `docs/architecture/25-cache-index-invalidation-overview-ru.md`: Russian end-to-end map of project cache/index contours, invalidation triggers by domain, and target retrieval cache key lifecycle.

Rules:

- When changing pipeline logic, update the corresponding diagram in the same PR.
- Keep diagrams code-driven: if a detail is not in code, do not mark it as guaranteed behavior.
- Use these docs as onboarding entry point before touching ETL logic.
