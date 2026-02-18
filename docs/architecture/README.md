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

Rules:

- When changing pipeline logic, update the corresponding diagram in the same PR.
- Keep diagrams code-driven: if a detail is not in code, do not mark it as guaranteed behavior.
- Use these docs as onboarding entry point before touching ETL logic.
