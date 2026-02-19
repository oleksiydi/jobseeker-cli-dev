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

Rules:

- When changing pipeline logic, update the corresponding diagram in the same PR.
- Keep diagrams code-driven: if a detail is not in code, do not mark it as guaranteed behavior.
- Use these docs as onboarding entry point before touching ETL logic.
