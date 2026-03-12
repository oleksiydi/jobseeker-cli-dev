# Контракт синонимов (domain-aware)

Документ фиксирует контракт для этапа синонимов до внедрения полного runtime-применения по доменам.

## 1) Какие файлы считаются контрактом

- `conf/normalization/schemas/synonyms_pending_record.schema.json`
  - формат одной записи в `synonyms.pending.jsonl` (кандидат из LLM/правил/ручного ввода).
- `conf/normalization/schemas/synonyms_dictionary.schema.json`
  - формат релизного/авто словаря синонимов (`synonyms.release.json` / `synonyms.auto.json`).

## 2) Откуда берутся значения полей

### `alias`, `canonical`
- Источник: термины из `taxonomy_domain_aggregate.json` (`term_key`, `canonical`), плюс LLM-группировка.
- Дополнительно: ручной override через review/apply workflow.

### `domain`
- Источник: домен блока из агрегации вакансий.
- Базовый справочник доменов: `conf/normalization/domains.level1.de.json`.
- Нормализация домена: правила в `tools/taxonomy_transform.py` (`normalize_domain`, `DOMAIN_ALIASES`).

### `category`
- Фиксированный enum:
  - `skills`
  - `tools`
  - `stack`

### `facet`
- Фиксированный enum:
  - `skill_requirement`
  - `tool_or_platform`
  - `technology_stack`
  - `task_responsibility`
  - `benefit`
  - `work_condition`
  - `language_requirement`
  - `certification`
  - `interpersonal_competency`
  - `other`

### `skill_class`
- Фиксированный enum:
  - `professional`
  - `interpersonal`
  - `non_skill_conditions`
  - `not_applicable`
- Источник: выводится из (`category`, `facet`) по правилам `skill_class_for` в `tools/taxonomy_transform.py`.

### `confidence`
- Источник:
  - LLM-кластеризация (score модели), или
  - правило/эвристика (заданная confidence).
- Диапазон: `[0..1]`.

### `source`
- Фиксированный enum:
  - `llm`
  - `rule`
  - `manual`

### `review_status`
- Фиксированный enum:
  - `auto_suggested`
  - `approved`
  - `rejected`

## 3) Что фиксировано, а что нет

Фиксировано (контракт):
- `category`, `facet`, `skill_class`, `source`, `review_status` (строгие enum).

Не фиксировано (данные):
- `alias`, `canonical`, `domain`, `confidence`, `evidence`, `review_note`.

## 4) Пример pending-записи

```json
{
  "run_id": "syn-20260306T120000Z",
  "alias": "k8s",
  "canonical": "Kubernetes",
  "domain": "it_software_daten",
  "category": "stack",
  "facet": "technology_stack",
  "skill_class": "not_applicable",
  "confidence": 0.98,
  "source": "llm",
  "evidence": {
    "source_refs_count": 42,
    "sample_phrases": ["Erfahrung mit k8s in Produktion"],
    "reason": "Abbreviation of Kubernetes in IT context"
  },
  "review_status": "auto_suggested",
  "review_note": "",
  "reviewed_by": "",
  "reviewed_at": "",
  "created_at": "2026-03-06T12:00:00+00:00"
}
```

## 5) Пример записи в release/auto словаре

```json
{
  "version": 1,
  "entries": [
    {
      "alias": "k8s",
      "canonical": "Kubernetes",
      "domain": "it_software_daten",
      "category": "stack",
      "facet": "technology_stack",
      "skill_class": "not_applicable",
      "confidence": 0.98,
      "source": "llm",
      "updated_at": "2026-03-06T12:30:00+00:00"
    }
  ],
  "aliases": {
    "k8s": "kubernetes"
  }
}
```

Поле `aliases` оставлено как legacy-слой для плавной миграции текущего flat-runtime.

## 6) Как проверяется соблюдение контракта

Проверка идёт в двух слоях:

1. Статическая проверка схем/enum в тестах:
- `tests/test_synonyms_contract.py`
- контролирует, что enum-наборы синонимов синхронизированы с нормализацией
  (`CATEGORY_ALLOWED`, `FACET_ALLOWED`, `SKILL_CLASS_ALLOWED`).

2. Рантайм-валидатор записей/словаря:
- модуль: `app/normalization/synonyms_contract.py`
- CLI: `tools/validate_synonyms_contract.py`
- дополнительно к структуре проверяет домен против
  `conf/normalization/domains.level1.de.json`.

Примеры запуска:

```bash
.venv/bin/python tools/validate_synonyms_contract.py \
  --mode pending \
  --input logs/normalization/synonyms.pending.jsonl
```

```bash
.venv/bin/python tools/validate_synonyms_contract.py \
  --mode dictionary \
  --input conf/normalization/synonyms.auto.json
```

Если есть ошибки контракта, скрипт завершится с кодом `1`.

## 7) Где лежат входы, правила и код валидатора

### Входные данные для валидатора
- Pending-режим (`--mode pending`):
  - файл JSONL, 1 строка = 1 запись-кандидат.
  - типичный путь: `logs/normalization/synonyms.pending.jsonl`.
- Dictionary-режим (`--mode dictionary`):
  - один JSON-словарь с `version` и `entries`.
  - типичные пути:
    - `conf/normalization/synonyms.auto.json`
    - `conf/normalization/synonyms.release.json`

### Правила (source of truth)
- Домены: `conf/normalization/domains.level1.de.json`
- Категории/фасеты/skill_class:
  - `tools/taxonomy_transform.py`
  - константы: `CATEGORY_ALLOWED`, `FACET_ALLOWED`, `SKILL_CLASS_ALLOWED`
- JSON-контракты:
  - `conf/normalization/schemas/synonyms_pending_record.schema.json`
  - `conf/normalization/schemas/synonyms_dictionary.schema.json`

### Код валидатора
- Библиотечные функции:
  - `app/normalization/synonyms_contract.py`
  - `load_allowed_domains(...)`
  - `validate_pending_record(...)`
  - `validate_dictionary_payload(...)`
- CLI-обертка:
  - `tools/validate_synonyms_contract.py`

## 8) Как запускать валидатор (пошагово)

### Проверить pending-файл
```bash
.venv/bin/python tools/validate_synonyms_contract.py \
  --mode pending \
  --input logs/normalization/synonyms.pending.jsonl
```

### Проверить словарь (auto/release)
```bash
.venv/bin/python tools/validate_synonyms_contract.py \
  --mode dictionary \
  --input conf/normalization/synonyms.auto.json
```

### Проверить с альтернативным списком доменов
```bash
.venv/bin/python tools/validate_synonyms_contract.py \
  --mode dictionary \
  --input conf/normalization/synonyms.release.json \
  --domains-file conf/normalization/domains.level1.de.json
```

Успешный финал всегда печатает строку:
```text
validation_done mode=... input=... total=... errors=0 allowed_domains=...
```

## 9) Примеры ошибок и как их читать

### Пример 1: неизвестный домен
```text
line=17 errors=invalid_domain=health_it
```
Что это значит:
- `domain` не найден в `domains.level1.de.json`.

Что делать:
1. Исправить `domain` на валидный (`it_software_daten`, `gesundheitswesen_pflege_medizin`, ...).
2. Если домен действительно новый, сначала обновить `domains.level1.de.json`,
   затем повторить валидацию.

### Пример 2: отсутствует обязательное поле
```text
line=5 errors=missing_required=created_at,review_status
```
Что это значит:
- запись неполная и не соответствует контракту pending.

Что делать:
1. Добавить отсутствующие поля.
2. Повторно запустить валидатор.

### Пример 3: ошибка в dictionary entries
```text
entry[3]_invalid_facet=soft_skill
```
Что это значит:
- в `entries[3].facet` пришло значение вне enum.

Что делать:
- заменить на один из допустимых facet (`interpersonal_competency`, `skill_requirement`, ...).

## 10) Что делать, если проверка упала (рабочий протокол)

1. Запустить валидатор на том же файле повторно и сохранить вывод в лог:
```bash
.venv/bin/python tools/validate_synonyms_contract.py \
  --mode pending \
  --input logs/normalization/synonyms.pending.jsonl \
  > /tmp/synonyms_validation.log 2>&1 || true
```
2. Исправить ошибки по типам:
- `invalid_domain` -> сверка с `domains.level1.de.json`
- `invalid_*` enum -> сверка с контрактом и `taxonomy_transform.py`
- `missing_required` -> восстановить поля в генераторе или post-process шаге
- `invalid_json` -> исправить битую строку в JSONL
3. Перезапустить валидацию.
4. Только после `errors=0` продолжать `review/apply/freeze`.

## 11) Проверка контракта в CI/тестах

- `tests/test_synonyms_contract.py`:
  - проверяет синхрон enum-наборов между schema и runtime-константами.
- `tests/test_synonyms_validator.py`:
  - проверяет runtime-валидатор (валидный кейс + ошибки домена).

Это защищает от ситуации, когда:
- schema уже изменилась, а код валидатора или константы еще старые;
- доменные ограничения перестали совпадать с реальным словарем доменов.

## 12) Ограничения текущей версии

- Валидатор проверяет структуру и enum/домены, но не решает семантические конфликты
  (например, когда `alias` потенциально относится к двум разным `canonical`).
- Для семантики нужна отдельная стадия review/LLM-верификации.
