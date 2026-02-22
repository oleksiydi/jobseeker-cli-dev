# Smoke Test 02: Details Collector (Prefilter + Backoff + Write Isolation)

## Что тестируем

Проверяем, что `job_details_collector`:
- корректно отсекает гарантированные skip-кандидаты на prefilter этапе;
- использует предсказуемый exponential backoff;
- не падает целиком из-за ошибки записи по одной вакансии.

## Набор данных

Используется in-memory набор из `tests/test_job_details_collector_prefilter.py` и `tests/test_job_details_collector_active_filter.py`:
- список активных вакансий;
- текущие `jobdetails` для части вакансий;
- fake details API-ответы;
- коллекции с имитацией ошибок записи.

## Этапы

1. Формирование batch и prefilter кандидатов.
2. Проверка skip при `SA <= JD`.
3. Проверка retry/backoff поведения.
4. Проверка `continue` при ошибке Mongo write для одной вакансии.

## Что проверяется

1. В API идут только кандидаты после prefilter.
2. Backoff-формула соответствует ожидаемому exponential шаблону.
3. Ошибка записи для одного кандидата не останавливает обработку следующего.

## Что запускается

```bash
.venv/bin/pytest -q tests/test_job_details_collector_prefilter.py tests/test_job_details_collector_active_filter.py
```

## Ожидаемый результат

- Все тесты проходят.
- Нет регрессии по фильтрации активных вакансий.

## Возможные проблемы и ограничения

1. Это unit-smoke на fake коллекциях, не полноценный e2e с Mongo/внешним API.
2. Не проверяет поведение при длительных сетевых деградациях.
3. Не измеряет нагрузочные характеристики.

