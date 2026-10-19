# jobseeker-cli

A local-first command-line tool for collecting German job listings, storing their
details, and producing reproducible candidate-to-vacancy analyses.

## What it does

- Collects vacancy lists and details from the Bundesagentur für Arbeit API.
- Stores current data in MongoDB and avoids unnecessary detail requests.
- Analyses vacancies with a configurable profile and optional LLM provider.
- Provides local skill matching and PDF, RTF, JSON, and Markdown exports.

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Set the required connection values in `.env`, then create your profile from
`conf/profiles/default.profile.json` or provide one with `--profile-file`.

```bash
.venv/bin/python -m app.job_list_collector
.venv/bin/python -m app.job_details_collector
.venv/bin/python -m app.analyze_jobs
```

Configuration lives in `conf/`. Environment variables override configuration;
CLI options override both. Never commit `.env`, private profiles, API keys, or
database credentials.

## Validation

```bash
pytest -q
```

## License notices

See `THIRD_PARTY_LICENSES.md`.
