# Декомпозиция taxonomy pipeline (без overengineering)

Документ фиксирует новую минимальную архитектуру извлечения таксономии из вакансий.

## Цели
- уменьшить риск регрессий в одном большом скрипте;
- упростить unit-тестирование;
- сохранить простой CLI-энтрпоинт (`tools/build_taxonomy_from_vacancies.py`).

## Структура модулей

### `tools/taxonomy_extract.py`
Ответственность:
- сбор prompt;
- вызов LLM;
- строгий parse JSON-ответа.

Правила:
- только extraction-layer;
- не знает про checkpoint и запись файлов;
- при невалидном JSON бросает `ValueError`.

Ключевые функции:
- `request_extraction(...)` — один вызов LLM;
- `extract_json_object(...)` — parse/валидация;
- `build_system_prompt(...)`, `build_user_prompt(...)`.

### `tools/taxonomy_transform.py`
Ответственность:
- нормализация и sanitize термов;
- маппинг domain/facet/category/skill_class;
- runtime-apply merged taxonomy (`release+auto`);
- построение агрегата по `term_key`.

Правила:
- чистые преобразования данных;
- без побочных эффектов IO;
- deterministic output для одинакового input.

Ключевые функции:
- `sanitize_term_record(...)`;
- `apply_runtime_taxonomy_to_row(...)`;
- `extract_vacancy_domain_top(...)`;
- `build_aggregate(...)`.

### `tools/taxonomy_io.py`
Ответственность:
- append/write JSON/JSONL;
- process logging (`summary|full`);
- checkpoint load/save для resume.

Правила:
- единое место всех file IO операций pipeline;
- `summary`-режим скрывает `term_normalized`, чтобы не раздувать логи.

Ключевые функции:
- `append_jsonl(...)`, `write_json(...)`;
- `append_process_log(..., level=...)`;
- `load_checkpoint(...)`, `append_checkpoint(...)`.

### `tools/build_taxonomy_from_vacancies.py` (CLI-обертка)
Ответственность:
- orchestration;
- чтение аргументов/конфига;
- цикл по вакансиям;
- retry policy;
- dry-run/learning режимы.

## Новые 80/20 механизмы

### 1) Retry on `finish_reason=length`
- Параметр: `--retry-on-length` (по умолчанию 1).
- При retry автоматически уменьшается:
  - `max_terms_per_vacancy`,
  - `max_input_chars`.
- Цель: снизить долю пустых прогонов с обрезанным JSON.

### 2) Preset для CPU
- Параметр: `--preset cpu_safe|quality_pass`.
- `cpu_safe`:
  - cap `max_terms_per_vacancy <= 10`,
  - cap `max_input_chars <= 3000`.
- `quality_pass`:
  - cap `max_terms_per_vacancy <= 15`,
  - cap `max_input_chars <= 3000`.

### 3) Checkpoint/Resume
- `--checkpoint-path` — JSONL c `referenznummer,status`.
- `--resume-from-checkpoint` — skip тех, у кого `status=ok`.
- Польза: продолжение после падения без повторной обработки уже успешных вакансий.

### 4) Управляемая детализация process log
- `--process-log-level summary|full`
- `summary`:
  - `run_started`, `vacancy_failed`, `vacancy_skipped_checkpoint`, `run_finished`, `retry_on_length`.
- `full`:
  - плюс `term_normalized` по каждому терму.

## DEV -> RELEASE схема работы

1. DEV bootstrap:
- запускать extraction на вакансиях в `dry-run`/learning-режиме;
- собирать:
  - `vacancy_terms.jsonl`,
  - `vacancy_taxonomy_llm_calls.jsonl`,
  - `vacancy_taxonomy_process.jsonl`,
  - checkpoint.

2. DEV review:
- проверка агрегата и pending;
- очистка шумов;
- фиксация aliases/category/domain.

3. Freeze:
- перенос проверенного слоя в `taxonomy.release.json`;
- `taxonomy.auto.json` оставить как рабочий incremental слой.

4. RELEASE runtime:
- читать merged: `taxonomy.release + taxonomy.auto`;
- в production обычно learning выключен, если политика проекта этого требует.

## Рекомендованные команды

Локальный smoke:
```bash
bash tools/run_local_smoke_check.sh
```

Benchmark (CPU-safe по умолчанию):
```bash
bash tools/run_taxonomy_benchmark.sh --load-env --provider openrouter --model mistralai/mistral-small-24b-instruct-2501
```

Resume-run:
```bash
.venv/bin/python tools/build_taxonomy_from_vacancies.py \
  --resume-from-checkpoint \
  --checkpoint-path logs/normalization/vacancy_taxonomy_checkpoint.jsonl
```
