# Maintenance Playbook

## How developers should use these docs

1. Start with `01-system-overview.md` to understand boundaries and ownership.
2. Use `02-pipelines-sequence.md` before changing cross-pipeline behavior.
3. Use `03-job-details-decision-and-state.md` when touching skip/update/date-gate logic.
4. Update diagrams in the same PR as code changes.

## Troubleshooting Map

- Symptom: too many details API calls.
- Check: `job_details_collector` prefilter and SA/JD date fields.
- Diagram: `03-job-details-decision-and-state.md`.

- Symptom: repeated analysis duplicates.
- Check: `input_hash` generation and `analysen` unique index behavior per `(referenznummer, input_hash)` (profile-independent in current implementation).
- Diagram: `02-pipelines-sequence.md`.

- Symptom: vacancy missing in analysis.
- Check: `is_active` in `stellenangebote`, presence of `jobdetails`, profile/prompt availability.
- Diagram: `01-system-overview.md`.

- Symptom: frequent details `404`.
- Check: `stellenangebote_history` events (`list_seen`) for same run, and `jobdetails_history` `remote_not_found`.
- Diagram: `02-pipelines-sequence.md`.

## Change Checklist

- Did module responsibility change?
- Update `01-system-overview.md`.

- Did pipeline order, API contract, or collection interactions change?
- Update `02-pipelines-sequence.md`.

- Did details decision logic or states change?
- Update `03-job-details-decision-and-state.md`.
