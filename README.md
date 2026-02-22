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
