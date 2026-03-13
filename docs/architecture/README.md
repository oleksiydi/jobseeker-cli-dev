# Architecture Docs

This folder contains the visual system documentation for developers.
All diagrams are written in Mermaid so they can be viewed directly in VS Code Markdown Preview.

## Start Here (decomposed overview)

1. [35-system-context-map](35-system-context-map.md)
2. [36-data-lifecycle-map](36-data-lifecycle-map.md)
3. [37-runtime-request-flow](37-runtime-request-flow.md)
4. [38-taxonomy-governance-flow](38-taxonomy-governance-flow.md)

Contents:

- [00-runtime-contract](00-runtime-contract.md): authoritative current runtime contract (activity semantics, retry behavior, analysis uniqueness, known mismatches).
- [01-system-overview](01-system-overview.md): modules, data stores, external systems.
- [02-pipelines-sequence](02-pipelines-sequence.md): end-to-end pipeline flow.
- [03-job-details-decision-and-state](03-job-details-decision-and-state.md): decision logic and states for details collector.
- [04-maintenance-playbook](04-maintenance-playbook.md): how to use docs for troubleshooting and safe system evolution.
- [05-embeddings-and-llm-cost-optimization-ru](05-embeddings-and-llm-cost-optimization-ru.md): Russian guide for local embeddings, shortlist retrieval, and LLM cost optimization.
- [06-embeddings-and-llm-cost-optimization-ru-explained](06-embeddings-and-llm-cost-optimization-ru-explained.md): same guide with in-place explanations of terms, technologies, and which project problems they solve.
- [07-local-matching-plan-ru-beginner](07-local-matching-plan-ru-beginner.md): beginner-friendly detailed local matching plan (CPU/nightly batch, change-aware indexing, chunk delivery for LLM).
- [08-local-matching-implementation-plan](08-local-matching-implementation-plan.md): implementation-first technical plan with phases, scoring, metrics, and acceptance criteria.
- [09-retrieval-pipeline](09-retrieval-pipeline.md): diagram of end-to-end retrieval flow (merge -> chunk -> embed -> search -> rerank -> shortlist).
- [10-vacancy-version-state](10-vacancy-version-state.md): state machine for vacancy indexing/version transitions and reindex triggers.
- [11-selection-cache-state](11-selection-cache-state.md): cache lifecycle for `(profile/query/filters/versions)` selection reuse.
- [12-top20-expansion-logic](12-top20-expansion-logic.md): anti-stagnation top-20 expansion to include new candidates.
- [13-llm-context-assembly](13-llm-context-assembly.md): compact context-building flow for local LLM handoff.
- [14-phase2-index-builder-step-by-step-ru](14-phase2-index-builder-step-by-step-ru.md): Phase 2 explained in Russian, step-by-step in plain language (what to do, why, and done criteria).
- [15-embedding-chunking-spec-v1-ru](15-embedding-chunking-spec-v1-ru.md): strict RU spec for field priority, canonical embedding input, semantic chunking, fallback rules, and reindex criteria.
- [16-embedding-chunking-flow-diagram](16-embedding-chunking-flow-diagram.md): Mermaid flow of merge -> semantic chunks -> vectors -> Qdrant/Mongo metadata.
- [17-embedding-reindex-decision-diagram](17-embedding-reindex-decision-diagram.md): Mermaid decision flow for skip/reindex and missing-description fallback.
- [18-canonical-text-payload-llm-examples-ru](18-canonical-text-payload-llm-examples-ru.md): detailed RU examples of canonical text formation, Qdrant payload, Mongo metadata, and LLM input/output formats.
- [19-rules-stack-implementation-plan-ru](19-rules-stack-implementation-plan-ru.md): practical plan for production-style rules stack (`Pydantic + Pandera + JSONLogic`), including execution model, responsibilities, and migration steps.
- [20-phase2-chunking-runtime-flow](20-phase2-chunking-runtime-flow.md): sequence diagram for runtime chunk construction (`rule flags -> spaCy -> semantic merge -> token-aware -> hard limits -> labels`).
- [21-phase2-chunking-system-integration](21-phase2-chunking-system-integration.md): system-level map of how chunking interacts with audit/state/Qdrant/retrieval.
- [22-chunking-smoke-scenarios](22-chunking-smoke-scenarios.md): practical smoke runbook for `spacy`, `syntok`, and `regex+char` modes with expected `matching_index_state` fields.
- [23-retrieval-rerank-runtime-flow](23-retrieval-rerank-runtime-flow.md): runtime flow of implemented `app/search_jobs.py` (`query/profile -> Qdrant -> aggregate -> score -> rerank -> top_chunks`).
- [24-cache-llm-handoff-explained-ru](24-cache-llm-handoff-explained-ru.md): Russian practical explanation of significant input/version changes, selection cache invalidation, long/short vacancy handoff payload, chunk-group impact, and current baseline rerank formula.
- [25-cache-index-invalidation-overview-ru](25-cache-index-invalidation-overview-ru.md): Russian end-to-end map of project cache/index contours, invalidation triggers by domain, and target retrieval cache key lifecycle.
- [26-matching-runtime-presets-ru](26-matching-runtime-presets-ru.md): practical presets (`speed` / `balanced` / `quality`) for matching runtime with usage steps and expected speed/quality tradeoffs.
- [27-matching-embedding-and-ranking-flow-ru](27-matching-embedding-and-ranking-flow-ru.md): current implemented flow of embedding/indexing/search with practical examples from real runs.
- [28-reranker-bge-v2-m3-explained-ru](28-reranker-bge-v2-m3-explained-ru.md): practical explanation of reranker model role, inputs, limits, and expected gains.
- [29-inference-layer-in-search-ru](29-inference-layer-in-search-ru.md): what inference layer means in search and how model-rerank integration should be wired.
- [30-embeddings-and-cosine-distance-ru](30-embeddings-and-cosine-distance-ru.md): embeddings and cosine similarity/distance explained with formulas and terminology.
- [31-current-status-text-template-latency-ru](31-current-status-text-template-latency-ru.md): current gap analysis on model-rerank text template, runtime cost, and latency trade-offs.
- [32-semantic-chunking-embedding-rerank-math-ru](32-semantic-chunking-embedding-rerank-math-ru.md): detailed math and algorithm of current semantic chunking + labeling + target rerank evolution.
- [34-domain-attribution-layer-plan-ru](34-domain-attribution-layer-plan-ru.md): planned domain attribution layer to improve ranking quality for cross-domain roles (e.g., IT + healthcare) and reduce term ambiguity noise.
- [35-system-context-map](35-system-context-map.md): L0 context map with major contours only (no low-level detail).
- [36-data-lifecycle-map](36-data-lifecycle-map.md): L1 lifecycle of vacancy/profile/search artifacts.
- [37-runtime-request-flow](37-runtime-request-flow.md): L1 runtime request sequence from input to top-N output.
- [38-taxonomy-governance-flow](38-taxonomy-governance-flow.md): L1 governance of taxonomy/synonyms from extraction to release freeze.

Architecture decision history:
- [adr/README](adr/README.md)
- [adr/ADR-TEMPLATE](adr/ADR-TEMPLATE.md)
- [adr/ADR-20260224-01-model-rerank-fit-intent-split](adr/ADR-20260224-01-model-rerank-fit-intent-split.md)

Rules:

- When changing pipeline logic, update the corresponding diagram in the same PR.
- Keep diagrams code-driven: if a detail is not in code, do not mark it as guaranteed behavior.
- Use these docs as onboarding entry point before touching ETL logic.
- If a conflict appears between docs, treat `00-runtime-contract.md` as current truth and update dependent docs in the same PR.
