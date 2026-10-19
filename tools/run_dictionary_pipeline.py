"""Один запуск строит и публикует словарь; подходит для cron без участия Codex."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.dictionary_settings import dictionary_settings
from app.llm.factory import resolve_provider_name

LOGGER = logging.getLogger("dictionary_pipeline")


def parse_args() -> argparse.Namespace:
    settings = dictionary_settings()
    parser = argparse.ArgumentParser(description="Автономно собрать таксономию и словарь синонимов.")
    parser.add_argument("--provider", default=settings["extraction_provider"])
    parser.add_argument("--model", default=settings["extraction_model"])
    parser.add_argument("--review-provider", default=settings["review_provider"])
    parser.add_argument("--review-model", default=settings["review_model"])
    parser.add_argument("--escalation-provider", default=settings["escalation_provider"])
    parser.add_argument("--escalation-model", default=settings["escalation_model"])
    parser.add_argument("--max-vacancies", type=int, default=settings["max_vacancies"])
    parser.add_argument('--reference-first', action=argparse.BooleanOptionalAction,
                        default=settings['reference_first'], help='Resolve known terms locally before LLM')
    parser.add_argument("--work-dir", type=Path, default=Path("logs/normalization"))
    parser.add_argument("--out", type=Path, default=Path("conf/normalization/synonyms.auto.json"))
    parser.add_argument("--allow-learning", action=argparse.BooleanOptionalAction,
                        default=os.getenv("ALLOW_VACANCY_LEARNING", "false").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--dry-run", action="store_true", help="Показать этапы; без Mongo, API и записи.")
    return parser.parse_args()


def build_commands(args: argparse.Namespace) -> list[list[str]]:
    work = args.work_dir
    common = ["--provider", args.provider, "--model", args.model]
    reference_flag = '--reference-first' if getattr(args, 'reference_first', True) else '--no-reference-first'
    terms = str(work / "vacancy_terms.jsonl")
    aggregate = str(work / "taxonomy_domain_aggregate.json")
    steps = [
        ["tools.build_taxonomy_from_vacancies", *common, reference_flag, "--allow-learning",
         "--max-vacancies", str(args.max_vacancies), "--out-terms-jsonl", terms,
         "--out-aggregate-json", aggregate, "--cache-path", str(work / "taxonomy_cache.sqlite3"),
         "--checkpoint-path", str(work / "vacancy_taxonomy_checkpoint.jsonl"),
         "--llm-calls-log", str(work / "vacancy_taxonomy_llm_calls.jsonl"),
         "--process-log", str(work / "vacancy_taxonomy_process.jsonl")],
        ["tools.build_synonyms_from_taxonomy", *common, reference_flag, "--mode", "llm", "--allow-learning",
         "--review-provider", args.review_provider, "--review-model", args.review_model,
         "--escalation-provider", args.escalation_provider, "--escalation-model", args.escalation_model,
         "--terms-jsonl", terms, "--aggregate-json", aggregate,
         "--snapshot-jsonl", str(work / "synonyms_state_snapshot.jsonl"),
         "--cache-path", str(work / "synonyms_cache.sqlite3")],
        ["tools.classify_synonyms_candidates"],
        ["tools.adjudicate_synonyms_candidates", "--provider", args.review_provider,
         "--model", args.review_model, "--stage", "flash",
         "--cache-path", str(work / "synonyms_cache.sqlite3")],
        ["tools.adjudicate_synonyms_candidates", "--stage", "pro",
         "--provider", args.escalation_provider, "--model", args.escalation_model,
         "--flash-model", args.review_model,
         "--cache-path", str(work / "synonyms_cache.sqlite3")],
        ["tools.publish_synonyms_auto", "--out", str(args.out), "--validate"],
    ]
    return [[sys.executable, "-m", *step] for step in steps]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = parse_args()
    args.provider = resolve_provider_name(args.provider)
    args.review_provider = resolve_provider_name(args.review_provider)
    args.escalation_provider = resolve_provider_name(args.escalation_provider)
    for provider, model in ((args.provider, args.model), (args.review_provider, args.review_model),
                            (args.escalation_provider, args.escalation_model)):
        if provider == "openrouter" and (model.endswith(":free") or model == "openrouter/free"):
            raise ValueError("Dictionary pipeline requires paid OpenRouter models")
    commands = build_commands(args)
    if args.dry_run or not args.allow_learning:
        for command in commands:
            LOGGER.info("planned_stage=%s", command[2])
        LOGGER.info("dry_run=true api_calls=0 writes=0")
        return
    # flock — системный lock: параллельный cron не запустит ту же очередь дважды.
    import fcntl
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with (args.work_dir / "dictionary_pipeline.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Dictionary pipeline is already running") from None
        for command in commands:
            LOGGER.info("stage_started=%s", command[2])
            subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        LOGGER.info("dictionary_pipeline_finished=true")


if __name__ == "__main__":
    main()
