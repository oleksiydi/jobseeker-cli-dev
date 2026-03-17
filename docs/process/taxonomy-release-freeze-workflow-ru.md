# Taxonomy Release Freeze Workflow (RU)

Документ описывает, как после этапа `Merged Loader (release + auto)` готовить словарь к поставке в релиз.

## Цель
- Накопить словарь в `*.auto.json` в dev/benchmark режиме.
- Отфильтровать шум и зафиксировать стабильный снимок в `*.release.json`.
- Поставлять систему с готовым релизным словарем, не зависящим от локального `auto`.

## Предусловия
1. Реализован merged loader с приоритетом `auto > release`.
2. Profile и Vacancy нормализация используют один и тот же loader.
3. Learning-флаги работают в opt-in режиме:
- `ALLOW_PROFILE_LEARNING=false` (default)
- `ALLOW_VACANCY_LEARNING=false` (default)

## Что заполняем в dev
- `conf/normalization/taxonomy.auto.json`
- `conf/normalization/synonyms.auto.json`
- `logs/normalization/*` (pending/review/audit)

`release` файлы в этот момент не трогаем.

## Когда можно делать freeze
Минимальные критерии:
1. Повторный benchmark стабилен (нет всплеска `failed_vacancies`).
2. Доля явного шума контролируема (например `non_skill_conditions` не разрастается аномально).
3. pending/review обработан до согласованного порога.

## Процедура freeze (инструкция)
1. Зафиксировать входные артефакты:
- run id, модель, дату, токены, стоимость.

2. Выполнить ревью pending:
- отклонить спорные alias/термины;
- принять только уверенные и объяснимые правила.

3. Сформировать release-слой:
- `taxonomy.release.json` = текущий проверенный baseline + подтвержденные элементы из `taxonomy.auto.json`.
- `synonyms.release.json` = подтвержденные aliases из `synonyms.auto.json` (обязательно domain-aware).

4. Проверить детерминизм:
- запустить нормализацию profile+vacancy дважды;
- убедиться, что результаты идентичны.

5. Выключить learning для релизного профиля запуска:
- `ALLOW_PROFILE_LEARNING=false`
- `ALLOW_VACANCY_LEARNING=false`

6. Зафиксировать релизный snapshot:
- коммит только `*.release.json` + документы с метриками.
- `*.auto.json` в релиз не переносить как обязательную часть runtime.

7. Выполнить формальный freeze-скрипт:
```bash
ALLOW_PROFILE_LEARNING=false \
ALLOW_VACANCY_LEARNING=false \
bash tools/freeze_taxonomy_release.sh
```
Скрипт:
- проверяет, что learning-флаги выключены (или требует `--force`);
- переносит `taxonomy.auto.json -> taxonomy.release.json`;
- опционально переносит `synonyms.auto.json -> synonyms.release.json`;
- пишет snapshot и лог в `logs/normalization/freeze/<UTC_TS>/`.

## Пример практического цикла
1. Набор словаря:
```bash
ALLOW_VACANCY_LEARNING=true bash tools/run_taxonomy_benchmark.sh --load-env \
  --provider openrouter \
  --model qwen/qwen3-235b-a22b-2507 \
  --max-vacancies 1000 \
  --max-terms 10 \
  --llm-max-tokens 1400
```

2. Обновление taxonomy из unknown terms:
```bash
ALLOW_PROFILE_LEARNING=true .venv/bin/python tools/update_taxonomy_from_unknown_terms.py \
  --input logs/normalization/unknown_terms.jsonl \
  --taxonomy conf/normalization/taxonomy.auto.json \
  --processed-index logs/normalization/taxonomy_processed.json \
  --pending-out logs/normalization/taxonomy_pending.jsonl
```

3. Релизный режим (без записи):
```bash
ALLOW_PROFILE_LEARNING=false \
ALLOW_VACANCY_LEARNING=false \
.venv/bin/python tools/extract_profile_from_text.py --input conf/profiles/sample.resume.de.txt --dry-run
```

4. Финальная smoke-проверка флагов обучения:
```bash
bash tools/run_learning_flags_smoke.sh
```
Ускоренный локальный вариант (без внешних LLM-вызовов):
```bash
bash tools/run_learning_flags_smoke.sh --skip-vacancy
```
Ожидаемое поведение:
- `vacancy_learning_off`: нет write-артефактов;
- `vacancy_learning_on`: write-артефакты присутствуют;
- `profile_learning_off`: `taxonomy` не меняется;
- `profile_learning_on`: `taxonomy` меняется.

Артефакты smoke:
- `logs/normalization/smoke/final/<UTC_TS>/smoke.log`
- `logs/normalization/smoke/final/<UTC_TS>/results.jsonl`
- `logs/normalization/smoke/final/<UTC_TS>/summary.json`
- `logs/normalization/smoke/final/<UTC_TS>/cases/*`

Параметры модели:
- Можно запускать с ENV:
```bash
MODEL="gpt-4o-mini" bash tools/run_learning_flags_smoke.sh
```
- Для OpenAI `gpt-5*` провайдер использует Responses API.
- В `run_learning_flags_smoke.sh` для `gpt-5*` включены авто-default (если не переопределены CLI):
  - `max_terms=3`
  - `llm_max_tokens=1200`
  - `retry_on_length=1`

## Ответ на вопрос: сможем ли после Merged Loader наполнить словарь и поставлять как релизный?
Да.

Как именно:
1. Наполняем `auto` на больших объемах.
2. Проводим review/очистку.
3. Делаем freeze в `release`.
4. В релизе используем `release` как обязательную базу, `auto` как необязательный локальный overlay.

Такой процесс дает и “вау-эффект” (богатый словарь), и управляемость качества перед поставкой.
