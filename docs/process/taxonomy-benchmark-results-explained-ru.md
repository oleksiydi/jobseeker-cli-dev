# Разбор результатов taxonomy benchmark (на русском)

Документ объясняет:
- что означают итоговые цифры в benchmark;
- что значит каждая запись в артефактах;
- как эти данные будут использоваться дальше в системе.

Использованы реальные результаты:
- `logs/normalization/benchmarks/openrouter_mistralai_mistral-large-2512_20260301T020119Z/*`
- `logs/normalization/benchmarks/baseline_snapshot_20260301T014313Z/*`

## 1) Что означают итоговые цифры

### Текущий прогон (OpenRouter + Mistral Large 2512)
Источник: `logs/normalization/benchmarks/openrouter_mistralai_mistral-large-2512_20260301T020119Z/taxonomy_domain_aggregate.json`

- `scanned_vacancies = 30`
- `processed_vacancies = 30`
- `failed_vacancies = 0`
- `term_rows = 299`
- `terms_len = 248`

Расшифровка:
- `scanned_vacancies`: сколько вакансий взяли в работу из БД.
- `processed_vacancies`: сколько вакансий успешно отдали в LLM и получили валидный JSON.
- `failed_vacancies`: сколько вакансий не удалось обработать (ошибка API, timeout, битый JSON и т.д.).
- `term_rows`: сколько всего строк-терминов извлечено (сумма по всем вакансиям, с повторами).
- `terms_len`: сколько уникальных `term_key` получилось после агрегации.

Ключевая идея:
- `term_rows` показывает **объём сырого извлечения**.
- `terms_len` показывает **размер нормализованного словаря**.

### Baseline (OpenAI GPT-4o-mini)
Источник: `logs/normalization/benchmarks/baseline_snapshot_20260301T014313Z/taxonomy_domain_aggregate.openai.json`

- `scanned_vacancies = 30`
- `processed_vacancies = 29`
- `failed_vacancies = 1`
- `term_rows = 374`
- `terms_len = 312`

Интерпретация по сравнению:
- OpenRouter/Mistral: стабильнее по обработке (`0` ошибок).
- GPT-4o-mini baseline: больше терминов (`374`/`312`), то есть выше полнота в этом прогоне.

## 2) Что значит запись в `vacancy_terms.jsonl`

Файл: `.../vacancy_terms.jsonl`  
Формат: **1 строка = 1 найденный термин в 1 конкретной вакансии**.

Пример (реальный):
```json
{
  "run_id": "vac-taxo-20260301T020120Z",
  "referenznummer": "18398-DP7YAHY9Z36ZT7EJ-S",
  "term": "MAG-Schweißen",
  "term_key": "mag schweißen",
  "canonical": "MAG-Schweißen",
  "category": "skills",
  "facet": "skill_requirement",
  "domain_hint": "handwerk_bau_ausbau",
  "domain_confidence": 0.95,
  "context_phrase": "MAG-Schweißer/in für Stahlkonstruktionen",
  "vacancy_domain_top": "handwerk_bau_ausbau",
  "vacancy_domain_confidence": 0.95,
  "provider": "openrouter",
  "model": "mistralai/mistral-large-2512",
  "created_at": "2026-03-01T02:01:31.451961+00:00"
}
```

Поля:
- `referenznummer`: id вакансии-источника.
- `term`: как термин выдал LLM.
- `term_key`: нормализованный ключ для дедупликации (lowercase/пробелы и т.д.).
- `canonical`: канонический вид термина для отображения.
- `category`: грубая категория (`skills|tools|stack`).
- `facet`: более точный тип (`skill_requirement`, `benefit`, `work_condition`, `tool_or_platform`, ...).
- `domain_hint`: к какому домену отнесён термин.
- `domain_confidence`: уверенность модели по домену термина.
- `context_phrase`: короткий фрагмент, где термин встретился.
- `vacancy_domain_top`: главный домен всей вакансии.
- `provider/model`: какой LLM это извлёк.

Как это используется дальше:
- как сырьё для построения агрегированной таксономии;
- для анализа качества извлечения (ошибки категорий/доменов);
- для explainability (показать, откуда термин взялся).

## 3) Что значит запись в `taxonomy_domain_aggregate.json`

Файл: `.../taxonomy_domain_aggregate.json`  
Формат: **агрегация по `term_key`**.

Пример (реальный):
```json
{
  "term_key": "2d autocad",
  "canonical": "2D-AutoCAD",
  "primary_category": "tools",
  "primary_facet": "tool_or_platform",
  "category_votes": { "tools": 1 },
  "facet_votes": { "tool_or_platform": 1 },
  "domain_votes": {
    "ingenieurwesen_technik": {
      "count": 1,
      "avg_confidence": 0.95
    }
  },
  "source_refs_count": 1,
  "sample_phrases": [
    "Erstellung von Konstruktionszeichnungen mittels 2D-AutoCAD"
  ]
}
```

Что это даёт:
- `primary_category/primary_facet`: итоговая “основная” классификация термина.
- `category_votes/facet_votes`: видно неоднозначность (если были разные варианты).
- `domain_votes`: в каких доменах встречался термин и с какой средней уверенностью.
- `source_refs_count`: насколько термин “массовый” (сколько вакансий подтверждают).
- `sample_phrases`: примеры контекста для ревью человеком.

Как это используется дальше:
- это и есть база для `taxonomy.auto.json` (или следующей версии словаря);
- основа правил нормализации профиля кандидата и вакансий;
- доменный сигнал в ранжировании (чтобы одинаковые слова не смешивались между доменами).

## 4) Что значит запись в `vacancy_taxonomy_llm_calls.jsonl`

Файл: `.../vacancy_taxonomy_llm_calls.jsonl`  
Формат: **1 строка = 1 LLM-вызов на 1 вакансию**.

Пример (реальный):
```json
{
  "run_id": "vac-taxo-20260301T020120Z",
  "status": "ok",
  "referenznummer": "18398-DP7YAHY9Z36ZT7EJ-S",
  "provider": "openrouter",
  "model": "mistralai/mistral-large-2512",
  "response_id": "gen-1772330480-gFxj5BJfP8FX89vp5T8Z",
  "finish_reason": "stop",
  "elapsed_ms": 11268.47,
  "usage": {
    "prompt_tokens": 829,
    "completion_tokens": 837,
    "total_tokens": 1666,
    "cost": 0.00167
  }
}
```

Поля:
- `status`: `ok` или `error`.
- `finish_reason`: почему генерация завершилась (`stop`, `length`, и т.д.).
- `elapsed_ms`: фактическая латентность.
- `usage.*tokens`: расход токенов.
- `usage.cost`: стоимость конкретного вызова (если провайдер вернул).

Как это используется:
- контроль затрат;
- SLA по времени;
- поиск деградаций (`finish_reason=length`, parse-errors, скачки latency).

## 5) Как это используется в целевом пайплайне

1. Из вакансий извлекаются термины (`vacancy_terms.jsonl`).
2. Термины агрегируются в устойчивую таксономию (`taxonomy_domain_aggregate.json`).
3. На основе агрегации формируется/обновляется рабочий словарь нормализации.
4. При нормализации профиля кандидата:
- термины маппятся к каноническим;
- учитываются `facet` и `domain_hint`;
- уменьшается путаница одинаковых слов в разных областях.
5. При матчингe вакансий:
- используются нормализованные признаки + домены;
- выдача становится ближе к целевой цели: “куда кандидата с большей вероятностью возьмут”.

## 6) Пример практической пользы

Термин `Monitoring` без домена неоднозначен:
- в IT: мониторинг сервисов/метрик;
- в медицине: мониторинг пациента.

С доменной агрегацией система может хранить два сигнала:
- `monitoring` + `it_software_daten`
- `monitoring` + `gesundheitswesen_pflege_medizin`

И дальше ранжировать вакансии и профиль точнее, без грубого смешивания.

## 7) Что важно контролировать при следующих прогонах

- Доля `failed_vacancies` (цель: близко к 0).
- Частота `finish_reason=length` (если растёт, JSON будет ломаться).
- Баланс категорий (`skills/tools/stack`) и фасетов (чтобы не всё падало в `skills`).
- Долю `domain_hint=sonstiges` (слишком много = слабая доменная полезность).
- Цена и латентность по `llm_calls`.

## 8) Зафиксированные результаты последнего прогона (2026-03-01)

Run:
- `logs/normalization/benchmarks/openrouter_mistralai_mistral-large-2512_20260301T020119Z`

Итоги:
- `scanned_vacancies = 30`
- `processed_vacancies = 30`
- `failed_vacancies = 0`
- `term_rows = 299`
- `terms_len = 248`

Токены и стоимость (сумма по `vacancy_taxonomy_llm_calls.jsonl`):
- `calls = 30`
- `prompt_tokens = 31_809`
- `completion_tokens = 22_273`
- `total_tokens = 54_082`
- `cost_total = 0.049314` (как вернул OpenRouter)

Короткий вывод:
- Прогон стабильный (ошибок нет).
- По полноте извлечения уступает baseline GPT-4o-mini, но быстрее и с меньшим total tokens.

### Попытка прогона `stepfun/step-3.5-flash` (OpenRouter)

Run:
- `logs/normalization/benchmarks/openrouter_stepfun_step-3.5-flash_20260301T152615Z`

Итоги:
- `scanned_vacancies = 30`
- `processed_vacancies = 0`
- `failed_vacancies = 30`
- `term_rows = 0`

Типовая ошибка:
- `404 ... No endpoints found that can handle the requested parameters`

Причина в контексте нашей системы:
- в OpenRouter-провайдере включён fail-fast (`require_parameters=true`) для обязательного structured output;
- для данной модели/маршрута OpenRouter не нашёл endpoint, который принимает текущий набор параметров (`response_format` со schema).

Вывод:
- этот прогон нельзя сравнивать по качеству с Mistral/GPT-mini, так как фактического извлечения не было.
