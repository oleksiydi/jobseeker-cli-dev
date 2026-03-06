# jobseeker-cli

## Third-Party Licenses

- `THIRD_PARTY_LICENSES.md`

## Arbeitsjournal (Worklog)

- `docs/worklog/README.md`
- `docs/worklog/2026-02-19-session-phase1-matching.md`

## Architektur und Visualisierung

Für Entwicklerdokumentation mit visuellen Schemata:

- `docs/architecture/README.md`
- `docs/architecture/01-system-overview.md`
- `docs/architecture/02-pipelines-sequence.md`
- `docs/architecture/03-job-details-decision-and-state.md`
- `docs/architecture/04-maintenance-playbook.md`
- `docs/architecture/27-matching-embedding-and-ranking-flow-ru.md`
- `docs/architecture/28-reranker-bge-v2-m3-explained-ru.md`
- `docs/architecture/29-inference-layer-in-search-ru.md`
- `docs/architecture/30-embeddings-and-cosine-distance-ru.md`
- `docs/architecture/31-current-status-text-template-latency-ru.md`
- `docs/architecture/32-semantic-chunking-embedding-rerank-math-ru.md`
- `docs/architecture/adr/README.md`

## Prozess und Entscheidungslog

- Operative Regeln für Assistenz-/Engineering-Workflow:
  - `docs/process/assistant-operating-rules.md`
- DoD-Checkliste für Architekturänderungen:
  - `docs/process/architecture-change-checklist.md`
- Taxonomie-Release-Freeze (release + auto Workflow):
  - `docs/process/taxonomy-release-freeze-workflow-ru.md`
- ADR-Vorlage und Entscheidungsverlauf:
  - `docs/architecture/adr/ADR-TEMPLATE.md`
  - `docs/architecture/adr/README.md`
  - `docs/architecture/adr/ADR-20260224-01-model-rerank-fit-intent-split.md`

Alle Diagramme sind in Mermaid in Markdown gehalten und direkt in VS Code (Markdown Preview) lesbar.

Für eine strukturierte Web-Dokumentation aus allen Markdown-Dateien:

```bash
docker compose -f docker/docker-compose.docs.yml up
```

Danach unter `http://localhost:8000`.

Lokaler Docs-Check wie in CI:

```bash
mkdocs build --strict
```

## Docker Services

Basis-Stack (nur Runtime: `mongo`, `qdrant`):

```bash
docker compose -f docker/docker-compose.yml up -d
```

Analytik-Stack zusätzlich (`mongo-express`, `nosqlclient`, `metabase`):

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.analytics.yml up -d
```

Остановить и удалить контейнеры обоих контуров (без удаления данных):

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.analytics.yml down
```

Lokale LLM (CPU-only, `llama.cpp` + GGUF-Modell) zusätzlich:

1. GGUF-Datei auf Host ablegen:
- Verzeichnis: `models/`
- Dateiname laut `.env`: `LLAMA_CPP_MODEL_FILE` (Default: `Qwen3-8B-Q4_K_M.gguf`)

Optional automatisiert per Script herunterladen:
```bash
bash tools/download_llm_models.sh --model qwen8
```
Weitere Optionen:
```bash
bash tools/download_llm_models.sh --model mistral7
bash tools/download_llm_models.sh --model all
```

2. Service starten:
```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.llm.yml up -d llm
```

3. Healthcheck:
```bash
curl -s http://127.0.0.1:8081/health
```

Hinweise:
- Modell-Dateien liegen auf dem Host in `models/` und sind per `.gitignore` ausgeschlossen.
- Der LLM-Service ist nur lokal gebunden (`127.0.0.1`).

Profile-Normalisierung (dry-run, noch ohne Produktionsintegration):
```bash
.venv/bin/python tools/extract_profile_from_text.py \
  --input conf/profiles/sample.resume.de.txt \
  --dry-run \
  --timeout-seconds 1200 \
  --max-tokens 250
```
Hinweis:
- Unbekannte Begriffe aus `skills/tools/stack` werden automatisch in
  `logs/normalization/unknown_terms.jsonl` protokolliert (abschaltbar mit `--disable-unknown-terms-log`).
- Runtime-Taxonomie wird als merged layer geladen:
  - `conf/normalization/taxonomy.release.json` (Basis, optional)
  - `conf/normalization/taxonomy.auto.json` (Overlay, optional/lernbar)
  - Alias-Konflikte: `auto` überschreibt `release`.
  - Kategorien: Vereinigung aus beiden Layern.
- Optional pro Run:
```bash
.venv/bin/python tools/extract_profile_from_text.py \
  ... \
  --taxonomy-release-path conf/normalization/taxonomy.release.json \
  --taxonomy-auto-path conf/normalization/taxonomy.auto.json
```
- LLM-Provider ist umschaltbar:
  - lokal: `LLM_PROVIDER=llama_cpp` (Default)
  - extern: `LLM_PROVIDER=openai` (benötigt `OPENAI_API_KEY` und passendes `--model`)
  - extern (aggregiert): `LLM_PROVIDER=openrouter` (benötigt `OPENROUTER_API_KEY` und passendes `--model`)
- Optional können `--base-url` und `--api-key` pro Run explizit überschrieben werden.

Auto-Update der Taxonomie (Aliases + Kategorien) aus unbekannten Begriffen:
```bash
.venv/bin/python tools/update_taxonomy_from_unknown_terms.py \
  --input logs/normalization/unknown_terms.jsonl \
  --taxonomy conf/normalization/taxonomy.auto.json \
  --processed-index logs/normalization/taxonomy_processed.json \
  --pending-out logs/normalization/taxonomy_pending.jsonl
```
Hinweis:
- `confidence >= 0.9` wird automatisch in die Taxonomie übernommen.
- niedrigere confidence landet in `taxonomy_pending.jsonl` für spätere Prüfung.
- Schreibzugriff ist jetzt opt-in:
  - Default: `ALLOW_PROFILE_LEARNING=false` -> keine Dateiänderungen (`learning_disabled_skip_write=true` im Log).
  - Aktivieren: `ALLOW_PROFILE_LEARNING=true` oder CLI-Flag `--allow-learning`.
  - Erzwingen ohne Schreiben: CLI-Flag `--dry-run`.

Taxonomy aus Vakanzen (Benchmark/Bootstrap):
- `tools/build_taxonomy_from_vacancies.py` schreibt nur mit aktivem Learning.
- Default: `ALLOW_VACANCY_LEARNING=false`.
- Benchmark-Script `tools/run_taxonomy_benchmark.sh` setzt `--allow-learning` explizit.
- Vacancy-Terme nutzen denselben merged taxonomy layer wie Profil-Normalisierung
  (`taxonomy.release + taxonomy.auto`) für alias/category Mapping.
  Bei Bedarf abschaltbar: `--no-apply-runtime-taxonomy`.

Local CPU smoke-check (2 Vakanzen + 1 Profil):
```bash
bash tools/run_local_smoke_check.sh
```
Empfohlene Parameter für lokale CPU-Modelle:
- `--max-terms-per-vacancy 10`
- `--llm-max-tokens 1100`
- `--max-input-chars-vacancy 3000`
- `--max-input-chars-profile 4000`
- `--timeout-seconds 0` (kein Timeout)

Hinweis:
- Der Smoke-Script stoppt sofort bei Fehlern und schreibt START/STEP/END in:
  `logs/normalization/smoke/<UTC_TS>/smoke_run.log`.

### ENV Defaults und Override-Priorität

OpenRouter Provider-Defaults (falls ENV nicht gesetzt):
- `OPENROUTER_PROVIDER_SORT=price`
- `OPENROUTER_PROVIDER_DATA_COLLECTION=deny`
- `OPENROUTER_PROVIDER_ZDR=true`
- `OPENROUTER_REQUIRE_PARAMETERS=true` (bei strict structured output)

Wichtiger Hinweis zu ZDR:
- Manche Modelle/Provider haben keinen passenden ZDR-Endpunkt.
- Symptom: `404 No endpoints found matching your data policy (Zero data retention)`.
- Per-Run Override:
```bash
OPENROUTER_PROVIDER_ZDR=false bash tools/run_taxonomy_benchmark.sh ...
```

Priorität von Einstellungen:
1. CLI-Flags (höchste Priorität), z.B. `--allow-learning`, `--no-allow-learning`, `--dry-run`
2. ENV Variablen (z.B. `ALLOW_PROFILE_LEARNING`, `ALLOW_VACANCY_LEARNING`)
3. Code-Defaults (wenn weder CLI noch ENV gesetzt)

Für Learning gilt effektiv:
- `effective_dry_run = dry_run OR (NOT allow_learning)`
- Das heißt: `--dry-run` gewinnt immer und verhindert Schreiben.

## Erster Start (obligatorisch)

Vor dem Start der Pipelines müssen einmalig folgende Daten nach MongoDB geladen werden:
- Kandidatenprofil(e) in `profiles`
- default prompt/task in `analysis_prompts`

Befehle:
```bash
.venv/bin/python tools/import_profiles.py --file conf/profiles/default.profile.json
.venv/bin/python tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json
```

Prüfung ohne Schreibzugriff (optional):
```bash
.venv/bin/python tools/import_profiles.py --file conf/profiles/default.profile.json --dry-run
.venv/bin/python tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json --dry-run
```

Danach können die Pipelines gestartet werden:
```bash
.venv/bin/python -m app.job_list_collector
.venv/bin/python -m app.job_details_collector
.venv/bin/python -m app.analyze_jobs
```

## Temporäres UI starten

Für eine schnelle Sicht auf gruppierte Analysen (nach Arbeitgeber) kann ein temporäres Streamlit-UI gestartet werden:

```bash
.venv/bin/streamlit run app/ui_grouped.py --server.headless true --server.address 0.0.0.0 --server.port 8501
```

Danach ist das UI unter `http://localhost:8501` erreichbar.

## Typische Startfehler

- `MONGODB_URI is not set`
- Ursache: `.env` oder Umgebungsvariable ist nicht gesetzt.
- Prüfen:
```bash
grep -E '^MONGODB_URI=' .env
```

- `Prompt nicht gefunden: key='default'...`
- Ursache: default prompt/task wurde nicht nach `analysis_prompts` importiert.
- Lösung:
```bash
.venv/bin/python tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json
```

- `Profil mit key='default' wurde ... nicht gefunden`
- Ursache: Profil wurde nicht nach `profiles` importiert.
- Lösung:
```bash
.venv/bin/python tools/import_profiles.py --file conf/profiles/default.profile.json
```

- `dry_run=True, updated=...` erscheint bei jedem Lauf
- Ursache: `--dry-run` schreibt nicht nach MongoDB, Änderungen werden nur simuliert.
- Lösung:
```bash
.venv/bin/python tools/import_profiles.py --file conf/profiles/default.profile.json
.venv/bin/python tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json
```

## Konfigurationsrichtlinie (`.env` vs `conf/app.json`)

- `conf/app.json` und `conf/profiles/default.profile.json`:
- Enthalten nicht geheime Anwendungsparameter.
- Beispiele: Standard-DB-Name, Collection-Namen, API-URL/Parameter, Analyzer-Einstellungen.

- `.env`:
- Enthält nur Secrets und sensitive Werte.
- Minimum: `MONGODB_URI`, `OPENAI_API_KEY`.
- Jobcenter API-Keys können optional über ENV überschrieben werden:
  `JOBCENTER_LIST_API_KEY`, `JOBCENTER_DETAILS_API_KEY`.

- ENV-Override (optional):
- Nicht geheime Parameter können temporär per ENV überschrieben werden (`MONGODB_DB_NAME`, `COLLECTION_*`).
- Ohne Override gelten die Werte aus `conf/app.json`.

- Wichtig:
- `config/` wird nicht als aktive Konfigurationsquelle verwendet.
- Projektstandard ist `conf/`.

## Local Matching Phase 1 (Qdrant Bootstrap)

Dieser Schritt bereitet nur die Infrastruktur vor (Konfig + Qdrant Collection),
noch ohne eigentliche Embedding-Berechnung.

1. Lokalen Qdrant-Service starten:
```bash
docker compose -f docker/docker-compose.yml up -d qdrant
```

Optional prüfen:
```bash
curl -s http://127.0.0.1:6333/healthz
```

2. Matching-Parameter in `conf/app.json` prüfen:
- `matching.run_mode` (`nightly` oder `on_demand`)
- `matching.allow_manual_run`
- `matching.nightly_schedule_cron`
- `matching.embeddings.model_name`
- `matching.embeddings.vector_size`
- `matching.chunking.strategy`
- `matching.qdrant.url`
- `matching.qdrant.collection_name`

3. Optional per ENV überschreiben (z. B. für Docker):
```bash
export MATCHING_QDRANT_URL=http://127.0.0.1:6333
export MATCHING_QDRANT_COLLECTION=job_chunks_v1
export MATCHING_EMBEDDING_VECTOR_SIZE=1024
export MATCHING_RUN_MODE=on_demand
```

4. Qdrant bootstrap starten:
```bash
.venv/bin/python -m app.bootstrap_qdrant
```

Was die Kommando macht:
- prüft, ob Qdrant erreichbar ist (`healthcheck`);
- prüft, ob Collection bereits existiert;
- erstellt die Collection nur falls nötig.

Damit ist der Schritt idempotent und sicher für wiederholte Ausführung.

5. Inkrementellen Index-Build starten (Baseline):
```bash
.venv/bin/python -m app.build_job_index
```

Aktueller Stand von `build_job_index`:
- merged aktive Jobs (`stellenangebote + jobdetails`);
- berechnet `embedding_input_hash` + Versionen;
- entscheidet `skip/reindex` über Rule-Engine;
- berechnet lokale Embeddings via `fastembed`;
- macht delete+upsert der Chunk-Vektoren in Qdrant;
- schreibt Audit-Ereignisse in `matching_audit`;
- schreibt Index-Status in `matching_index_state`.

Nur Normalisierung/Qualitätsprüfung (ohne Embeddings/Upsert) ist auch möglich:
```bash
MATCHING_SKIP_EMBEDDINGS=true .venv/bin/python -m app.build_job_index
```

Optional kombiniert mit begrenzter Realdaten-Stichprobe:
```bash
MATCHING_SKIP_EMBEDDINGS=true MATCHING_BUILD_MAX_DOCS=200 .venv/bin/python -m app.build_job_index
```

Hinweis zur Qdrant-Anbindung:
- primär wird der offizielle Python-SDK `qdrant-client` verwendet (industry standard);
- wenn `qdrant-client` nicht installiert ist, stoppt der Index-Job mit klarer Fehlermeldung.

Hinweis zu semantic chunking:
- Semantic chunking ist im Indexer aktiv.
- Satzsegmentierung ist konfigurierbar: `spacy` (default), `syntok` oder `regex`.
- Token-Split ist ebenfalls konfigurierbar:
  - `transformers` (token-aware, default),
  - `char` (nur Hard-Char-Grenzen),
  - `disabled` (kein token-aware Split).
- Bei fehlenden lokalen Abhängigkeiten wird automatisch ein sicherer Fallback genutzt
  und als Runtime-Status in `matching_index_state` gespeichert.

Baseline retrieval + rerank (JSON-Ausgabe für Top-Kandidaten):
```bash
.venv/bin/python -m app.search_jobs --query "Data Engineer Python" --profile-key default --limit 20
```

## Diagnose von `404` in `jobdetails`

- Tool: `tools/check_list_seen.py`.
- Zweck: Prüft, ob problematische `referenznummer` im letzten List-Run (`list_seen`) vorhanden waren.

- Nummern aus dem `job_details_collector`-Log prüfen:
```bash
.venv/bin/python tools/check_list_seen.py --from-log /tmp/job_details_after_list.log --print-missing
```

- Konkrete Nummern prüfen:
```bash
.venv/bin/python tools/check_list_seen.py --ref 10001-1000943252-S --ref 10000-1205119986-S --print-missing --print-present
```

- Gegen einen konkreten `run_id` prüfen:
```bash
.venv/bin/python tools/check_list_seen.py --run-id list-20260206T232446-cc175cdc --from-log /tmp/job_details_after_list.log --print-missing
```

- Logik:
- `present_in_run`: Nummer war im letzten list-run vorhanden.
- `missing_in_run`: Nummer war im list-run nicht vorhanden (typischer Fall für einen Teil der `404` in Details).

Hinweis zur Aktivitätslogik:
- `is_active` ist scope-basiert (abhängig von `jobcenter.list_api.api_url + params`).
- Wenn sich der List-Scope zwischen zwei Runs ändert, überspringt `job_list_collector` absichtlich die Massen-Deaktivierung `list_missing`, um falsche `inactive`-Markierungen zu vermeiden.

## Smoke-Checks (schnell)

Ein kompakter Smoke-Run (in definierter Reihenfolge) steht als Skript bereit:

```bash
bash tools/smoke_run.sh
```

## Mongo Backup / Restore

Краткий runbook и команды:
- `docs/operations/mongo-backup-restore.md`

Быстрые команды:
```bash
bash tools/mongo_backup.sh --db jobcenter
bash tools/mongo_restore.sh --archive backups/mongo_jobcenter_YYYYMMDDTHHMMSSZ.archive.gz --db jobcenter
```
