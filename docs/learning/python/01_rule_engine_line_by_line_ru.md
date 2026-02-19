# Построчный разбор `rule_engine.py` (синхронизировано с v2)

Файлы, к которым относится этот разбор:
1. `app/matching/rule_engine.py` (production)
2. `docs/learning/python/01_rule_engine_explained_ru.py` (учебная копия)

Ниже разбор по текущим строкам production-файла.

## 1) Импорты и типовые алиасы

1. `from __future__ import annotations`  
Что делает: откладывает вычисление type hints.  
Зачем: можно писать рекурсивные типы (`JSONValue`) проще.

2. `import json`, `from pathlib import Path`, `from dataclasses import dataclass`, `from typing import Any, Callable`  
Зачем:
- `json` — загрузка правил из JSON;
- `Path` — удобные пути;
- `dataclass` — компактные структуры конфигурации;
- `Callable` — тип для таблицы обработчиков операторов.

3. `JSONScalar`, `JSONValue`, `Context`  
Зачем: уменьшить шум `Any` и сделать код читаемее.

Java-аналог:
```java
Object / Map<String, Object> / functional interfaces
```

## 2) Dataclass-блок compile-step

1. `CompiledRule`  
Хранит уже подготовленное правило:
- `id`, `when`, `set_patch`, `reason`.

2. `LabelRule`  
Хранит правило маркировки чанка:
- `id`, `when`, `label`, `confidence`.

3. `CompiledRuleset` и `CompiledLabelRuleset`  
Зачем: в runtime не парсить сырые dict каждый раз.

Почему это лучше:
- быстрее;
- меньше `dict.get(...)` по всему коду;
- меньше ошибок типов.

## 3) `SimpleJsonLogicEvaluator`

### 3.1 Конструктор

1. `MAX_DEPTH = 64`  
Ограничение глубины вложенности выражения.

2. `__init__(max_depth: int | None = None)`  
Устанавливает лимит глубины и строит dispatch-таблицу:
```python
self._handlers = {"==": self._op_eq, ...}
```

Зачем dispatch:
- проще расширять;
- чище, чем длинная if-цепочка.

### 3.2 `evaluate(...)`

1. Проверка глубины `_check_depth(depth)`.
2. Базовые случаи:
- если `expr` не `dict`:
  - если `list` -> рекурсивно вычислить элементы;
  - иначе вернуть атомарное значение как есть.
3. Проверка JSONLogic-формы: dict с одним оператором.
4. Достаём `op`, `args`.
5. Ищем handler по `op`.
6. Вызываем handler.

Это классический рекурсивный интерпретатор дерева выражения.

### 3.3 Проверка глубины `_check_depth`

Если `depth > max_depth`, выбрасываем понятную ошибку.  
Зачем: защита от некорректных/вредных сверхглубоких выражений.

### 3.4 `_resolve_var`

Идём по пути вида `"a.b.c"` в контексте `data`.  
Если путь не найден — возвращаем `default`.

### 3.5 `_eval_if`

Реализация JSONLogic формата:
`[cond, then, cond2, then2, else]`.

### 3.6 Операторные методы `_op_*`

1. `_eval_binary` — общий helper для бинарных операторов.
2. `_op_eq/_op_ne/_op_gt/...` — сравнения.
3. `_op_and/_op_or/_op_not` — логика.
4. `_op_in` — membership/substring.
5. `_op_if` — делегирует в `_eval_if`.

## 4) Утилиты вне класса

### 4.1 `_deep_update`

Рекурсивный merge словарей:
- если обе стороны dict -> углубляемся;
- иначе перезаписываем значение.

### 4.2 `_resolve_rules_path`

Берёт путь rule-файлов из конфига:
`matching.rules.path` или fallback `conf/rules`.

### 4.3 `_RULE_FILE_CACHE` и `_load_rule_file`

1. `_RULE_FILE_CACHE` кэширует уже прочитанные JSON.
2. `_load_rule_file`:
- валидирует наличие имени файла;
- проверяет, что файл существует;
- читает JSON;
- кладёт в кэш;
- возвращает копию (чтобы runtime случайно не мутировал кэш).

## 5) Compile helpers

### 5.1 `_compile_ruleset(raw)`

Преобразует “сырой JSON ruleset” в `CompiledRuleset`.

### 5.2 `_compile_label_ruleset(raw)`

Преобразует label-rules в `CompiledLabelRuleset` с default-полями.

## 6) `MatchingRuleEngine`

### 6.1 Конструктор

1. Сохраняет cfg.
2. Создаёт evaluator.
3. Загружает и компилирует 4 ruleset:
- normalize
- fallback
- label
- reindex
4. Читает `rule_set_version`.

### 6.2 `evaluate_vacancy(...)`

Алгоритм:
1. Собираем `context` из `MergedVacancyNormalized`.
2. Добавляем `change_flags` (если есть).
3. Создаём `RuleEvaluationResult`.
4. Применяем rulesets по очереди:
- normalize -> fallback -> reindex.
5. Ставим `needs_reindex`/`skip_reason`.
6. Если нужен synthetic chunk — генерируем.
7. Возвращаем результат.

### 6.3 `infer_section_label(...)`

1. Нормализуем текст чанка в lowercase.
2. Идём по label rules.
3. Если правило совпало — возвращаем `(label, confidence, rule_id)`.
4. Если нет — default (`unknown`, default_confidence, default_reason).

### 6.4 `build_synthetic_chunk(...)`

Собирает fallback-текст из основных полей вакансии.

### 6.5 `_apply_ruleset(...)`

Для каждого правила:
1. считаем условие `when`;
2. если true:
- пишем `rule_id` в `applied_rules`;
- пишем `reason` в `decision_reasons`;
- применяем `set_patch` в `context` и `result.flags`.

## 7) Что важно новичку помнить

1. В Python нет `{}` для блоков — только отступы.
2. Type hints помогают читать код, но не заменяют runtime-валидацию.
3. `_name` — не настоящий private, а соглашение.
4. Рекурсия здесь естественна, потому что JSONLogic — дерево.
5. Этот файл показывает production-паттерн:
- parse -> compile -> evaluate -> explain.
