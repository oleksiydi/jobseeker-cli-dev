#!/usr/bin/env bash
set -euo pipefail

# Why this script exists:
# - We need repeatable A/B runs for taxonomy extraction (same dataset, different model/provider).
# - We keep each run in a separate benchmark folder so results are comparable later.
# - We avoid manual copy/paste mistakes when switching between OpenAI/OpenRouter models.

usage() {
  cat <<'EOF'
Usage:
  bash tools/run_taxonomy_benchmark.sh [options]

Purpose:
  1) Optional: snapshot current baseline artifacts.
  2) Run taxonomy extraction via build_taxonomy_from_vacancies.py.
  3) Save run artifacts into logs/normalization/benchmarks/<run_tag>/.
  4) Print compact metrics summary for quick model comparison.

Options:
  --provider NAME          LLM provider (default: openrouter)
  --model NAME             Model id for provider (default: mistralai/mistral-small-24b-instruct-2501)
  --max-vacancies N        Max active vacancies (default: 30)
  --max-terms N            Max terms per vacancy before preset cap (default: 10)
  --llm-max-tokens N       Max output tokens per LLM call (default: 900)
  --timeout-seconds N      Per-call timeout, 0 disables timeout (default: 1800)
  --preset NAME            Extraction preset (cpu_safe|quality_pass, default: cpu_safe)
  --checkpoint-every N     Aggregate checkpoint interval (default: 5)
  --tag NAME               Custom run tag suffix (default: auto timestamp)
  --snapshot-current       Copy current *.openai.* artifacts into a baseline snapshot folder.
  --load-env               Load .env before run (useful when running from clean shell).
  -h, --help               Show this help.

Required env for OpenRouter:
  OPENROUTER_API_KEY
Optional:
  OPENROUTER_BASE_URL (defaults to https://openrouter.ai/api/v1 in provider)

Examples:
  # Typical OpenRouter run with .env loading:
  bash tools/run_taxonomy_benchmark.sh --load-env \
    --provider openrouter \
    --model mistralai/mistral-small-24b-instruct-2501 \
    --max-vacancies 30

  # Compare another model on same setup:
  bash tools/run_taxonomy_benchmark.sh --load-env \
    --provider openrouter \
    --model mistralai/mistral-large-3.1 \
    --max-vacancies 30
EOF
}

PROVIDER="openrouter"
MODEL="mistralai/mistral-small-24b-instruct-2501"
MAX_VACANCIES="30"
MAX_TERMS="10"
LLM_MAX_TOKENS="900"
TIMEOUT_SECONDS="1800"
PRESET="cpu_safe"
CHECKPOINT_EVERY="5"
TAG=""
SNAPSHOT_CURRENT="false"
LOAD_ENV="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider) PROVIDER="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --max-vacancies) MAX_VACANCIES="$2"; shift 2 ;;
    --max-terms) MAX_TERMS="$2"; shift 2 ;;
    --llm-max-tokens) LLM_MAX_TOKENS="$2"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="$2"; shift 2 ;;
    --preset) PRESET="$2"; shift 2 ;;
    --checkpoint-every) CHECKPOINT_EVERY="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --snapshot-current) SNAPSHOT_CURRENT="true"; shift ;;
    --load-env) LOAD_ENV="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "${LOAD_ENV}" == "true" ]]; then
  if [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
  else
    echo "WARN: .env not found, skipping --load-env" >&2
  fi
fi

if [[ "${PROVIDER}" == "openrouter" && -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "ERROR: OPENROUTER_API_KEY is not set. Export it or use --load-env." >&2
  exit 1
fi

if [[ "${PROVIDER}" == "openai" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "ERROR: OPENAI_API_KEY is not set. Export it or use --load-env." >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_SUFFIX="${TAG:-${TS}}"
RUN_TAG="${PROVIDER}_$(echo "${MODEL}" | tr '/:' '__')_${RUN_SUFFIX}"
RUN_DIR="logs/normalization/benchmarks/${RUN_TAG}"
mkdir -p "${RUN_DIR}"

if [[ "${SNAPSHOT_CURRENT}" == "true" ]]; then
  BASELINE_DIR="logs/normalization/benchmarks/baseline_snapshot_${TS}"
  mkdir -p "${BASELINE_DIR}"
  for f in \
    "logs/normalization/vacancy_terms.openai.jsonl" \
    "logs/normalization/vacancy_taxonomy_llm_calls.openai.jsonl" \
    "logs/normalization/taxonomy_domain_aggregate.openai.json"; do
    if [[ -f "${f}" ]]; then
      cp "${f}" "${BASELINE_DIR}/"
    fi
  done
  echo "Baseline snapshot saved to: ${BASELINE_DIR}"
fi

OUT_TERMS="${RUN_DIR}/vacancy_terms.jsonl"
OUT_AGG="${RUN_DIR}/taxonomy_domain_aggregate.json"
OUT_CALLS="${RUN_DIR}/vacancy_taxonomy_llm_calls.jsonl"

echo "Running benchmark:"
echo "  provider=${PROVIDER}"
echo "  model=${MODEL}"
echo "  max_vacancies=${MAX_VACANCIES}"
echo "  max_terms=${MAX_TERMS}"
echo "  llm_max_tokens=${LLM_MAX_TOKENS}"
echo "  preset=${PRESET}"
echo "  run_dir=${RUN_DIR}"

LLM_PROVIDER="${PROVIDER}" .venv/bin/python tools/build_taxonomy_from_vacancies.py \
  --provider "${PROVIDER}" \
  --model "${MODEL}" \
  --max-vacancies "${MAX_VACANCIES}" \
  --max-terms-per-vacancy "${MAX_TERMS}" \
  --llm-max-tokens "${LLM_MAX_TOKENS}" \
  --timeout-seconds "${TIMEOUT_SECONDS}" \
  --preset "${PRESET}" \
  --checkpoint-every "${CHECKPOINT_EVERY}" \
  --allow-learning \
  --overwrite-output \
  --out-terms-jsonl "${OUT_TERMS}" \
  --out-aggregate-json "${OUT_AGG}" \
  --llm-calls-log "${OUT_CALLS}"

echo ""
echo "Benchmark metrics:"
.venv/bin/python - <<PY
import json
from pathlib import Path
from collections import Counter
from statistics import median

terms_path = Path("${OUT_TERMS}")
calls_path = Path("${OUT_CALLS}")
agg_path = Path("${OUT_AGG}")

rows = []
if terms_path.exists():
    with terms_path.open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))

calls = []
if calls_path.exists():
    with calls_path.open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                calls.append(json.loads(ln))

agg = {}
if agg_path.exists():
    agg = json.loads(agg_path.read_text(encoding="utf-8"))

print(f"run_tag={Path('${RUN_DIR}').name}")
if agg:
    print(f"aggregate.input={agg.get('input', {})}")
    print(f"aggregate.terms_len={len(agg.get('terms', []))}")
cat = Counter(r.get("category", "") for r in rows)
facet = Counter(r.get("facet", "") for r in rows)
skill_class = Counter(r.get("skill_class", "") for r in rows if r.get("skill_class", ""))
domain = Counter(r.get("domain_hint", "") for r in rows)
print(f"term_rows={len(rows)}")
print(f"category_counts={dict(cat)}")
print(f"top_facets={dict(facet.most_common(8))}")
if skill_class:
    print(f"skill_class_counts={dict(skill_class)}")
    non_skill_rows = int(skill_class.get("non_skill_conditions", 0))
    print(f"non_skill_condition_rows={non_skill_rows}")
print(f"top_domains={dict(domain.most_common(8))}")

status = Counter(c.get("status", "ok") for c in calls)
pt = ct = tt = 0
cost_total = 0.0
elapsed_ok = []
length_count = 0
parse_error_count = 0
error_kinds = Counter()
for c in calls:
    u = c.get("usage") or {}
    pt += int(u.get("prompt_tokens") or 0)
    ct += int(u.get("completion_tokens") or 0)
    tt += int(u.get("total_tokens") or 0)
    cost_total += float(u.get("cost") or 0.0)
    finish_reason = c.get("finish_reason") or c.get("response_finish_reason") or ""
    if str(finish_reason).lower() == "length":
        length_count += 1
    if c.get("status") == "ok":
        elapsed = c.get("elapsed_ms")
        if isinstance(elapsed, (int, float)):
            elapsed_ok.append(float(elapsed))
    else:
        err = str(c.get("error_message") or "")
        if "not valid JSON object" in err:
            parse_error_count += 1
        error_kinds[str(c.get("error_type") or "unknown")] += 1

def pct(part, whole):
    return round((part / whole) * 100.0, 2) if whole else 0.0

def p95(values):
    if not values:
        return 0.0
    vals = sorted(values)
    idx = int(0.95 * (len(vals) - 1))
    return vals[idx]

print(f"llm_calls={len(calls)}")
print(f"status_counts={dict(status)}")
print(f"tokens_prompt={pt} tokens_completion={ct} tokens_total={tt}")
print(f"cost_total_usd={round(cost_total, 6)}")
processed = int((agg.get("input") or {}).get("processed_vacancies") or status.get("ok", 0) or 0)
scanned = int((agg.get("input") or {}).get("scanned_vacancies") or 0)
if processed:
    print(f"cost_per_processed_usd={round(cost_total / processed, 6)}")
    print(f"tokens_per_processed={round(tt / processed, 2)}")
if scanned:
    print(f"cost_per_scanned_usd={round(cost_total / scanned, 6)}")
print(f"finish_reason_length={length_count}/{len(calls)} ({pct(length_count, len(calls))}%)")
print(f"json_parse_errors={parse_error_count}")
if elapsed_ok:
    print(
        "latency_ms_p50={:.2f} latency_ms_p95={:.2f} latency_ms_max={:.2f}".format(
            median(elapsed_ok), p95(elapsed_ok), max(elapsed_ok)
        )
    )
if error_kinds:
    print(f"error_types={dict(error_kinds)}")
PY

echo ""
echo "Done. Artifacts:"
echo "  ${OUT_TERMS}"
echo "  ${OUT_CALLS}"
echo "  ${OUT_AGG}"
