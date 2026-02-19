# Local Matching Implementation Plan (CPU, Nightly Batch)

## Scope

Implement a high-quality local retrieval + rerank pipeline for vacancy matching and Anschreiben generation support:
1. CPU-first execution.
2. Nightly batch acceptable.
3. Incremental re-indexing only for changed inputs.
4. Cache-aware retrieval/rerank to avoid redundant work.
5. Strong German text preprocessing and semantic chunking.

Target environment:
- up to ~5100 vacancies;
- Intel i5-6300U baseline;
- full batch <= 8-12h.

## Final Architecture

1. Data source:
- Mongo `stellenangebote` + `jobdetails` (latest by `referenznummer`).

2. Processing:
- merge -> clean -> sentence segmentation (spaCy) -> semantic chunking.

3. Vectorization:
- E5 multilingual embeddings for chunks + document summary.

4. Index:
- Qdrant collection(s), payload filters, HNSW ANN.

5. Retrieval:
- query/profile embeddings -> chunk-level search -> job-level aggregation.

6. Rerank:
- multilingual cross-encoder over top-N jobs/chunks.

7. LLM handoff:
- only top-selected chunks for compact context.
8. LLM orchestration:
- provider-agnostic layer for `local` / `OpenAI API` / `Mistral API`.

## Decisions (Pinned)

1. Vector DB: Qdrant (Docker, pinned version, not latest in production).
2. Text preprocessing: spaCy (`de_dep_news_trf`) in nightly mode.
3. Chunking: semantic chunking default (night batch), fallback recursive splitter.
4. Embeddings: `intfloat/multilingual-e5-large` (fallback to `-base` via config flag).
5. Reranker: `BAAI/bge-reranker-v2-m3` (CPU top-N only).
6. LLM (local): Qwen2.5 7B GGUF for final generation stage.
7. External LLM providers supported from day 1:
- OpenAI API
- Mistral API
8. Unified LLM output JSON schema across all providers.

## Data Contracts

### Input (Merged Vacancy)

Required merged fields (with fallback):
1. `title`: `details.stellenangebotsTitel || meta.titel`
2. `role`: `details.hauptberuf || meta.beruf`
3. `description`: `details.stellenangebotsBeschreibung` (optional but critical)
4. `location`: `meta.arbeitsort.*`, `details.stellenlokationen.*`
5. `employment`: `details.arbeitszeit*`, `details.vertragsdauer`, `details.istGeringfuegigeBeschaeftigung`

Excluded from embedding text:
- `_id`, timestamps, `*_hash`, `*_dt`, service/debug fields.

### Hashing / Change Detection

Persist per vacancy:
1. `embedding_input_hash`
2. `embedding_model`
3. `chunking_version`
4. `preprocess_version`

Recompute embeddings iff any of these changed.

### Selection Cache Key

`selection_key = hash(profile_key + normalized_query + filters + retrieval_version + rerank_version)`

Cache invalidation triggers:
1. changed vacancy among cached top-20 (significant change);
2. model or chunking version changed;
3. profile/query/filter changed.

## Scoring Model

Pre-LLM composite score:

`pre_llm_score = 0.40*role_score + 0.30*chunk_score + 0.20*profile_score + 0.10*metadata_score - penalties`

Where:
1. `role_score`: title/beruf/hauptberuf alignment.
2. `chunk_score`: max/top-k mean similarity on relevant chunks.
3. `profile_score`: profile-to-job summary similarity.
4. `metadata_score`: location/employment/language alignment.
5. `penalties`: role mismatch, missing critical data, noisy-only chunks.

Store separately:
1. `pre_llm_score`
2. `rerank_score`
3. `llm_score` (if LLM step executed)

## Multi-Provider LLM Interface (Exact Contract)

### Provider modes

1. `local` (GGUF runtime)
2. `openai_api`
3. `mistral_api`

Switching must be config-driven (no business-logic branching in callers).

### Input modes

1. `compact`
- top chunks + compact summary + compact profile/query
2. `full_context`
- full vacancy description + full profile + full query + top chunks

### Required LLM tasks per request

1. Generate `einschreiben`
2. Evaluate vacancy-candidate match with numeric score
3. Explain relevant candidate experience for this vacancy
4. Produce interview prep guidance:
- likely interview topics/questions
- concrete preparation actions

### Unified output schema

```json
{
  "match_score": 0,
  "decision": "fit|partial|not_fit",
  "reasons": [],
  "risks": [],
  "interview_topics": [],
  "prep_plan": [],
  "einschreiben": "",
  "provider": "local|openai|mistral",
  "model": "",
  "input_mode": "compact|full_context"
}
```

### Validation and fallback

1. Validate mandatory fields/types before persistence.
2. If provider returns invalid JSON:
- retry with stricter instruction once;
- then fallback to secondary provider (if enabled);
- otherwise mark as generation error with diagnostics.

## Anti-Stagnation Selection Module (Exact Rules)

Problem:
- high-score jobs already processed with unchanged version can dominate ranking repeatedly.

Goal:
- keep top quality while forcing controlled exploration of new jobs.

Definitions:
1. `processed_same_version == true` iff:
- an Anschreiben exists for this vacancy;
- and vacancy `input_hash/version` unchanged since that generation.
2. `base_top`: first 20 jobs after standard scoring + rerank.
3. `fresh_pool`: jobs not in `base_top` and `processed_same_version == false`.

Selection rules:
1. Build `base_top = ranked[:20]`.
2. Compute `processed_in_top20`.
3. Compute:
- `extra_count = max(5, ceil(processed_in_top20 * 0.5))`
4. Append `extra_count` jobs from `fresh_pool` (best score first).
5. Cap final list:
- `len(final_list) <= 50`

Priority rules:
1. Preserve order of `base_top` positions 1..20.
2. Append exploratory jobs after position 20.
3. Do not fully remove processed unchanged jobs; avoid monopoly instead.

Pseudocode:

```text
ranked = sort_desc(score, candidates)
base_top = ranked[:20]

processed_in_top20 = count(base_top where processed_same_version)
extra_count = max(5, ceil(processed_in_top20 * 0.5))

fresh_pool = filter(ranked[20:], not processed_same_version)
extra = take(fresh_pool, extra_count)

final_list = take(base_top + extra, 50)
```

v2 extension (soft decay):
1. `staleness_days = now - last_processed_at`
2. `decay_penalty = min(0.15, staleness_days * 0.005)`
3. apply only when `processed_same_version == true`
4. reset penalty on any meaningful vacancy change.

## Chunk Strategy

1. Sentence segmentation:
- spaCy transformer for DE (+ optional RU routing by script detection).

2. Semantic chunking:
- preserve section coherence (`Aufgaben`, `Anforderungen`, `Benefits`).
- keep chunk sizes in practical bounds for embedding model throughput.
- first criterion is semantic coherence between neighboring sentences;
- char limits are hard safety bounds, not primary split logic.

3. Chunk metadata:
- `job_id`, `chunk_id`, `chunk_index`, `section_label`, `description_missing`.

4. LLM handoff:
- per selected job use top 2-3 chunks only.

## Phased Implementation

### Phase 1: Infrastructure & Config

Tasks:
1. Add config blocks in `conf/app.json` for:
- model names, chunking strategy, thresholds, cache TTL, nightly schedule flags.
2. Add Qdrant client module and health checks.
3. Add storage for embedding metadata/hash in Mongo.

Done criteria:
1. Feature flags controllable from config + ENV override.
2. Qdrant reachable and collection bootstrap works.

Current implementation notes:
1. `conf/app.json` now contains a dedicated `matching` block with defaults for:
- preprocess/chunking/embeddings/retrieval/rerank/cache/llm/qdrant.
2. Two run modes are configurable:
- `run_mode=nightly` for scheduled batch
- `run_mode=on_demand` for immediate/manual execution.
3. Semantic chunking config now separates:
- semantic parameters (`semantic_*`)
- hard fallback bounds (`hard_*`, `fallback_overlap_chars`).
4. ENV override resolution is centralized in `app/core/matching_config.py`.
5. Qdrant REST bootstrap is implemented in:
- `app/vector/qdrant_client.py`
- `app/bootstrap_qdrant.py`

### Phase 2: Index Builder (Nightly, Incremental)

Tasks:
1. Build merged vacancy stream (latest meta + latest details).
2. Normalize text and run spaCy segmentation.
3. Run semantic chunking.
4. Compute embeddings for chunks + summary.
5. Upsert into Qdrant only for changed hashes.

Done criteria:
1. Unchanged vacancies are skipped.
2. Batch report shows changed/skipped counts.

### Phase 3: Retrieval + Rerank API/CLI

Tasks:
1. Query/profile vectorization.
2. Chunk search in Qdrant (top-k).
3. Job-level aggregation.
4. Rerank top-N.
5. Return scored shortlist with explanatory components.

Done criteria:
1. Stable top-20 output.
2. Scores exposed (`role/chunk/profile/metadata/pre_llm/rerank`).

### Phase 4: Cache & Invalidation

Tasks:
1. Add selection cache by `(profile, query, filters)`.
2. Add invalidation on meaningful vacancy changes.
3. Skip retrieval/rerank when cache valid.

Done criteria:
1. Repeated same query/profile returns cached top-20.
2. Changed relevant vacancy invalidates cache correctly.

### Phase 5: LLM Handoff (Compact Context)

Tasks:
1. Build compact prompt context from top chunks only.
2. Include relevance evidence and scoring summary.
3. Store `llm_score` + generated output metadata.

Done criteria:
1. Context token size bounded and predictable.
2. No full raw vacancy dump to model.

### Phase 6: Provider-Agnostic LLM Layer

Tasks:
1. Implement adapter interface:
- `generate_analysis(input_payload, provider_config) -> unified_json`
2. Implement adapters:
- local runtime adapter
- OpenAI adapter
- Mistral adapter
3. Add mode selector:
- `input_mode=compact|full_context`
4. Add output validation and fallback chain.

Done criteria:
1. Same request works against all 3 providers.
2. Output persisted in unified schema.
3. Provider switching requires config change only.

## Metrics & Observability

Track per run:
1. `vacancies_total`, `vacancies_changed`, `vacancies_skipped`
2. `chunks_total`, `chunks_per_job_avg`
3. embedding runtime (total + per 100 docs)
4. retrieval latency p50/p95
5. rerank latency p50/p95
6. cache hit ratio
7. top-20 refresh ratio (how often unchanged cache reused)

Quality monitoring:
1. manual judged precision@20 (periodic)
2. role mismatch false-positive rate
3. LLM acceptance quality (human review sample)

## Fallback Strategy

If nightly batch exceeds 12h:
1. switch embedding model `e5-large -> e5-base`
2. reduce rerank candidate size (e.g., 50 -> 20)
3. fallback chunking strategy to recursive for ultra-long docs
4. parallelize batch in controlled worker count

If memory pressure:
1. lower batch size for embedding inference
2. limit max chunks per vacancy
3. reduce payload verbosity in Qdrant

## Risks

1. CPU-heavy preprocessing bottleneck (spaCy trf + semantic chunking).
2. Role-skill conflict false positives.
3. Overly aggressive cache causing stale shortlist.
4. Drifting quality after model/version updates.

Mitigations:
1. versioned configs and hashes.
2. conservative cache invalidation policy.
3. score decomposition for debugging.
4. periodic evaluation on fixed benchmark set.

## Deliverables

1. `app/build_job_index.py` (incremental indexer)
2. `app/search_jobs.py` (retrieval + rerank + scores)
3. `app/matching/scoring.py` (score components)
4. `app/matching/cache.py` (selection cache + invalidation)
5. `app/matching/text_prep.py` (cleaning/segmentation/chunking)
6. `app/matching/llm_router.py` (provider-agnostic orchestration)
7. `app/matching/llm_adapters/` (`local`, `openai`, `mistral`)
8. docs for operation + troubleshooting

## Acceptance Criteria

1. End-to-end nightly run on 5100 vacancies in <= 12h on baseline CPU.
2. Re-run with unchanged data skips >= 90% embedding work.
3. Top-20 selection cached and correctly invalidated on meaningful changes.
4. Pre-LLM score + rerank score + llm score all persisted for analysis.
