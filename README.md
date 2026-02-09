# jobseeker-cli

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

- ENV-Override (optional):
- Nicht geheime Parameter können temporär per ENV überschrieben werden (`MONGODB_DB_NAME`, `COLLECTION_*`).
- Ohne Override gelten die Werte aus `conf/app.json`.

- Wichtig:
- `config/` wird nicht als aktive Konfigurationsquelle verwendet.
- Projektstandard ist `conf/`.

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
