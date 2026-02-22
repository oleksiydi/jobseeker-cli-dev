# Smoke Test 06: Analyze Jobs (Profile-Scoped Uniqueness + Strict Active)

## Что тестируем

Проверяем ключевые инварианты `analyze_jobs`:
- profile-scoped уникальность `input_hash`;
- корректный skip при неизменном входе в рамках того же профиля;
- обработка только `is_active == True`.

## Набор данных

Unit-набор `tests/test_analyze_jobs.py`:
- fake Mongo collections;
- fake OpenAI client;
- разные сценарии профилей и версий.

## Этапы

1. Создание/проверка индексов в `ensure_indexes`.
2. Сценарий skip при том же hash и том же профиле.
3. Сценарий “тот же hash, другой профиль” (должен анализироваться).
4. Проверка strict active фильтра.

## Что проверяется

1. Unchanged detection работает по `(referenznummer, candidate_profile_key, input_hash)`.
2. Нет ложного skip между разными профилями.
3. Неактивные вакансии не попадают в анализ.

## Что запускается

```bash
.venv/bin/pytest -q tests/test_analyze_jobs.py
```

## Ожидаемый результат

- Все тесты модуля проходят.
- Контракт profile-scoped анализа соблюдается.

## Возможные проблемы и ограничения

1. Это unit-набор, а не интеграционный прогон с реальным OpenAI/Mongo.
2. Не проверяет фактическую скорость/стоимость LLM-вызовов.

