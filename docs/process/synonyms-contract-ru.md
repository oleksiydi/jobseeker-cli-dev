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
