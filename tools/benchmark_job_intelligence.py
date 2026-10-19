"""Run a fixed, offline relevance benchmark for local job intelligence.

The evaluation labels in ``conf/local-demo/relevance.json`` were reviewed by
agents and still need human validation. This runner never calls a generative
API and never clears MongoDB or Qdrant data. A normal invocation runs each
profile/mode twice in sequence; ``--skip-run`` only recomputes the summary from
the saved reports and GNU time logs.

    .venv/bin/python tools/benchmark_job_intelligence.py
    .venv/bin/python tools/benchmark_job_intelligence.py --skip-run
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROFILE_FILE = ROOT / "conf/local-demo/profiles.json"
RELEVANCE_FILE = ROOT / "conf/local-demo/relevance.json"
VACANCIES_FILE = ROOT / "logs/local-intelligence/demo-vacancies.json"
SETTINGS_FILE = ROOT / "conf/job-intelligence.json"
OUTPUT_DIR = ROOT / "logs/local-intelligence/benchmark"
TIME_BINARY = Path("/usr/bin/time")
EXPECTED_PROFILE_COUNT = 3
EXPECTED_VACANCY_COUNT = 37
EXPECTED_JUDGED_COUNT = 36
REPORT_METRICS = (
    "cache_hits",
    "cache_misses",
    "vectors_computed",
    "vectors_reused",
    "query_vectors_reused",
    "qdrant_upserts",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_inputs() -> tuple[list[dict], dict, dict[str, dict[int, int]]]:
    for path in (PROFILE_FILE, RELEVANCE_FILE, VACANCIES_FILE, SETTINGS_FILE):
        if not path.is_file():
            raise FileNotFoundError(f"Benchmark input is missing: {path.relative_to(ROOT)}")

    profiles = _read_json(PROFILE_FILE)
    relevance = _read_json(RELEVANCE_FILE)
    vacancies = _read_json(VACANCIES_FILE)
    if not isinstance(profiles, list) or len(profiles) != EXPECTED_PROFILE_COUNT:
        raise ValueError(f"Expected {EXPECTED_PROFILE_COUNT} local demo profiles")
    if not isinstance(vacancies, list) or len(vacancies) != EXPECTED_VACANCY_COUNT:
        raise ValueError(f"Expected {EXPECTED_VACANCY_COUNT} fixed demo vacancies")
    if not relevance.get("not_expert_ground_truth"):
        raise ValueError("Relevance fixture must declare that it is not expert ground truth")

    duplicate = relevance.get("duplicate_test", {})
    source_id = duplicate.get("source_reference_id")
    synthetic_id = duplicate.get("synthetic_reference_id")
    row_ids = {str(row.get("ref") or row.get("id") or row.get("referenznummer") or "")
               for row in vacancies if isinstance(row, dict)}
    if not source_id or not synthetic_id or source_id not in row_ids or synthetic_id not in row_ids:
        raise ValueError("Duplicate fixture mapping does not match the fixed vacancy file")

    rank_ids = {synthetic_id: source_id}
    judgments: dict[str, dict[int, int]] = {}
    for profile in relevance.get("profiles", []):
        key = profile.get("profile_key")
        evaluations = profile.get("evaluations", [])
        if not key or len(evaluations) != EXPECTED_JUDGED_COUNT:
            raise ValueError(f"Expected {EXPECTED_JUDGED_COUNT} judgments for each profile")
        grades = {}
        for evaluation in evaluations:
            ref = rank_ids.get(evaluation.get("reference_id"), evaluation.get("reference_id"))
            grade = evaluation.get("grade")
            if not isinstance(ref, str) or not isinstance(grade, int) or not 0 <= grade <= 3:
                raise ValueError(f"Invalid relevance judgment for profile {key}")
            grades[ref] = grade
        if len(grades) != EXPECTED_JUDGED_COUNT:
            raise ValueError(f"Expected {EXPECTED_JUDGED_COUNT} unique judged refs for profile {key}")
        judgments[key] = grades

    profile_keys = {p.get("key") for p in profiles}
    if set(judgments) != profile_keys:
        raise ValueError("Profile keys and independently labeled profile keys differ")
    selected = set(relevance.get("selected_reference_ids", []))
    if any(set(grades) != selected for grades in judgments.values()):
        raise ValueError("Every selected vacancy must have a judgment for every profile")
    return profiles, relevance, judgments


def _run_id(profile_key: str, mode: str, warm: bool) -> str:
    return f"{profile_key}-{mode}{'-warm' if warm else ''}"


def _prepare_config(output_dir: Path) -> Path:
    settings = _read_json(SETTINGS_FILE)
    settings.update({"candidate_limit": 100, "limit": 36, "embedding_threads": 2})
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "job-intelligence-threads2.json"
    config_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def _invoke(profile_key: str, mode: str, warm: bool, output_dir: Path, config_path: Path) -> None:
    run_id = _run_id(profile_key, mode, warm)
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report_prefix = run_dir / "report"
    command = [
        str(TIME_BINARY), "-v", sys.executable, "-m", "app.job_intelligence",
        "--profile-file", str(PROFILE_FILE), "--profile-key", profile_key,
        "--vacancies-file", str(VACANCIES_FILE), "--output", str(report_prefix),
        "--config", str(config_path), "--candidate-limit", "100", "--limit", "36", "--no-llm",
    ]
    if mode == "baseline":
        command.append("--no-embeddings")

    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    with (run_dir / "stdout.txt").open("w", encoding="utf-8") as stdout, \
            (run_dir / "time.txt").open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(command, cwd=ROOT, env=env, stdout=stdout, stderr=stderr, check=False)
    if completed.returncode:
        raise RuntimeError(
            f"{run_id} failed with exit code {completed.returncode}; see {run_dir.relative_to(ROOT)}"
        )
    report_path = report_prefix.with_suffix(".json")
    if not report_path.is_file():
        raise FileNotFoundError(f"The CLI did not write its JSON report for {run_id}")


def _parse_wall_seconds(time_log: str) -> float | None:
    match = re.search(r"Elapsed \(wall clock\) time .*?:\s*(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)", time_log)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _read_run(run_id: str, profile_key: str, mode: str, warm: bool,
              output_dir: Path, grades: dict[str, int]) -> dict:
    run_dir = output_dir / run_id
    report = _read_json(run_dir / "report.json")
    time_log = (run_dir / "time.txt").read_text(encoding="utf-8", errors="replace")
    time_wall = _parse_wall_seconds(time_log)
    rss_match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", time_log)
    results = report.get("results", [])

    duplicate = _read_json(RELEVANCE_FILE)["duplicate_test"]
    duplicate_map = {duplicate["synthetic_reference_id"]: duplicate["source_reference_id"]}
    rank_by_ref: dict[str, int] = {}
    for rank, result in enumerate(results, start=1):
        ref = duplicate_map.get(result.get("ref"), result.get("ref"))
        if ref in grades:
            rank_by_ref[ref] = min(rank_by_ref.get(ref, rank), rank)

    relevant_total = sum(grade >= 2 for grade in grades.values())
    hits_at_10 = sum(grade >= 2 and rank_by_ref.get(ref, 10**9) <= 10
                     for ref, grade in grades.items())
    dcg = sum((2**grades[ref] - 1) / math.log2(rank + 1)
              for ref, rank in rank_by_ref.items() if rank <= 10)
    ideal_gains = sorted((2**grade - 1 for grade in grades.values()), reverse=True)[:10]
    idcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(ideal_gains))
    relevant_ranks = [rank_by_ref[ref] for ref, grade in grades.items()
                      if grade >= 2 and ref in rank_by_ref]
    report_metrics = report.get("metrics", {})
    candidate_count = int(report_metrics.get("candidates_evaluated", len(results)) or 0)
    recognized_jobs = int(report_metrics.get("jobs_with_recognized_skills", 0) or 0)
    unknowns = sum(len(result.get("unknowns", [])) for result in results)
    return {
        "profile_key": profile_key,
        "mode": mode,
        "run": "warm" if warm else "first-run",
        "run_id": run_id,
        "relevance": {
            "recall_at_10_grade_ge_2": hits_at_10 / relevant_total if relevant_total else 0.0,
            "ndcg_at_10": dcg / idcg if idcg else 0.0,
            "mrr_grade_ge_2": 1 / min(relevant_ranks) if relevant_ranks else 0.0,
            "relevant_total_grade_ge_2": relevant_total,
            "judged_total": len(grades),
            "ranked_judged": len(rank_by_ref),
        },
        "resources": {
            "wall_seconds_gnu_time": time_wall,
            "peak_rss_kib_gnu_time": int(rss_match.group(1)) if rss_match else None,
            "wall_seconds_cli": report_metrics.get("wall_seconds"),
            "peak_rss_kib_cli": report_metrics.get("peak_rss_kib"),
        },
        "coverage_and_evidence": {
            "candidate_count": candidate_count,
            "jobs_with_recognized_skills": recognized_jobs,
            "recognized_skill_job_coverage": recognized_jobs / candidate_count if candidate_count else 0.0,
            "skill_mentions": report_metrics.get("skill_mentions", 0),
            "unknown_modality_mentions": report_metrics.get("unknown_modality", 0),
            "ambiguous_mentions": report_metrics.get("ambiguous_mentions", 0),
            "surfaced_unknowns": unknowns,
        },
        "cache_counts": {name: report_metrics.get(name, 0) for name in REPORT_METRICS},
        "llm_calls": int(report.get("llm", {}).get("calls", 0) or 0),
        "model": report.get("model"),
        "report": str((run_dir / "report.json").relative_to(ROOT)),
        "time_log": str((run_dir / "time.txt").relative_to(ROOT)),
    }


def _score_profiles(relevance: dict) -> dict[str, dict[str, int]]:
    duplicate = relevance["duplicate_test"]
    id_map = {duplicate["synthetic_reference_id"]: duplicate["source_reference_id"]}
    scored = {}
    for profile in relevance["profiles"]:
        grades = {}
        for item in profile["evaluations"]:
            ref = id_map.get(item["reference_id"], item["reference_id"])
            grades[ref] = item["grade"]
        scored[profile["profile_key"]] = grades
    return scored


def _write_summary(records: list[dict], relevance: dict, output_dir: Path) -> None:
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "annotation_status": relevance.get("annotation_status"),
        "annotation_scope": relevance.get("annotation_scope"),
        "not_expert_ground_truth": relevance.get("not_expert_ground_truth"),
        "fixed_vacancies": EXPECTED_VACANCY_COUNT,
        "judged_vacancies_per_profile": EXPECTED_JUDGED_COUNT,
        "duplicate_expected_unique": True,
        "records": records,
    }
    json_path = output_dir / "summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Локальный benchmark job intelligence",
        "",
        "Оценки основаны на независимой агентской разметке; это не экспертная human ground truth.",
        "37 фиксированных строк должны дать 36 уникальных вакансий. LLM отключён; API не вызывается.",
        "Первый прогон в этом запуске не означает пустой Mongo/Qdrant cache: данные и индексы не очищались.",
        "",
        "| Профиль | Режим | Прогон | Recall@10 (grade ≥2) | nDCG@10 | MRR (grade ≥2) | Wall, с | Peak RSS, KiB | Known coverage | Unknown / ambiguity | Cache hits/misses | Векторы вычислены/переиспользованы | LLM |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in records:
        coverage = item["coverage_and_evidence"]
        cache = item["cache_counts"]
        resource = item["resources"]
        rel = item["relevance"]
        lines.append(
            f"| {item['profile_key']} | {item['mode']} | {item['run']} "
            f"| {rel['recall_at_10_grade_ge_2']:.3f} | {rel['ndcg_at_10']:.3f} "
            f"| {rel['mrr_grade_ge_2']:.3f} | {resource['wall_seconds_gnu_time']} "
            f"| {resource['peak_rss_kib_gnu_time']} "
            f"| {coverage['jobs_with_recognized_skills']}/{coverage['candidate_count']} "
            f"| {coverage['surfaced_unknowns']} / {coverage['ambiguous_mentions']} "
            f"| {cache['cache_hits']}/{cache['cache_misses']} "
            f"| {cache['vectors_computed']}/{cache['vectors_reused']} | {item['llm_calls']} |"
        )
    lines.extend(["", "Метрики ресурсов GNU time находятся в `time.txt`, stdout CLI — в `stdout.txt` внутри каталога каждого прогона.", ""])
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-run", action="store_true", help="Recompute summary from saved reports and GNU time logs")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    profiles, relevance, _ = _load_inputs()
    grades_by_profile = _score_profiles(relevance)
    config_path = output_dir / "job-intelligence-threads2.json"

    if not args.skip_run:
        if not TIME_BINARY.is_file():
            parser.error("/usr/bin/time is required for RSS/wall measurements")
        config_path = _prepare_config(output_dir)
        for profile in profiles:
            key = profile["key"]
            for mode in ("baseline", "hybrid"):
                for warm in (False, True):
                    _invoke(key, mode, warm, output_dir, config_path)

    records = []
    for profile in profiles:
        key = profile["key"]
        for mode in ("baseline", "hybrid"):
            for warm in (False, True):
                run_id = _run_id(key, mode, warm)
                records.append(_read_run(run_id, key, mode, warm, output_dir, grades_by_profile[key]))
    _write_summary(records, relevance, output_dir)
    print(json.dumps({"runs": len(records), "summary": str((output_dir / "summary.json").resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
