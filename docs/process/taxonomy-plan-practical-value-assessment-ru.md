# Оценка Практической Ценности Плана Taxonomy/Synonyms

Audience: `Human + AI`  
Status: `assessment`  
Last updated: `2026-03-24`

## Контекст оценки

Оценивается не абстрактная идея, а текущий план и фактическая реализация в проекте:
- основной целевой документ: `docs/process/taxonomy-synonyms-runtime-plan-ru.md`;
- фактические прогоны по логам в `logs/normalization/*`.

Текущая фактическая база:
- успешный стабильный прогон: `mistral-nemo-300` (300/300 обработано, 2 транспортные ошибки);
- `mistral-small-3.2-24b-300` — высокий error-rate и неполный прогон;
- `gpt-5-nano-30` и `grok-4-fast-300` — в текущих тестах непригодны как основные для этого пайплайна.

## 1) Практичность и нужность на рынке

### Вывод

Идея практична и рыночно полезна, если позиционировать ее как:
- прикладной слой нормализации вакансий/профилей для matching/retrieval;
- domain-aware словарь синонимов и canonical mapping для DE/EN смешанных текстов.

### Где реальная прикладная ценность

- Снижение шума в matching (один смысл в разных формулировках -> один canonical).
- Повторяемость и управляемость (runtime без LLM).
- Контролируемое масштабирование качества (auto-gates, versioning, release freeze).
- Бизнес-польза для HR-tech и labor-market analytics: меньше ложных несовпадений по навыкам.

### Честная граница

Сама по себе “еще одна таксономия навыков” без сильной операционной части имеет слабую рыночную уникальность.  
Уникальность появляется в комбинации:
- регулярное обновление из живых вакансий;
- domain-aware synonym governance;
- deterministic runtime интеграция в сквозной pipeline.

## 2) Потенциал как продукта

### Вариант A: самостоятельный продукт (только словарь)

Плюсы:
- понятный артефакт, можно распространять как dataset/API.

Минусы:
- высокий риск конкуренции с уже существующими крупными классификаторами;
- сложно монетизировать без дополнительных сервисов (обновление, quality SLA, интеграции).

Вердикт:
- как standalone — средний потенциал, если не добавить сильный differentiation.

### Вариант B: компонент более крупного продукта (рекомендуется)

Плюсы:
- максимальная жизнеспособность;
- прямая ценность в основном pipeline (normalization -> retrieval -> ranking -> letter generation);
- легче доказать ROI на метриках unknown-rate, recall, стабильность top-N.

Вердикт:
- лучший вариант для текущего проекта.

### Вариант C: open-source asset

Плюсы:
- сильный trust signal;
- ускоряет внешнюю валидацию и вклад сообщества.

Минусы:
- нужно поддерживать качество и release governance;
- часть коммерческой ценности уходит в публичное поле.

Вердикт:
- полезно как стратегия доверия/портфолио, но лучше публиковать framework + контракт + benchmark, а не “сырой словарь”.

## 3) Демонстрация компетентности (portfolio/showcase)

### Что уже выглядит сильно

- Архитектурное мышление: разделение extraction, aggregate, synonyms, release.
- Инженерная дисциплина: checkpoint/resume, логи, versioning, contract-first.
- Практичность: фокус на runtime без LLM и управляемую стоимость.

### Что пока выглядит сырым

- Нет полноценного quality benchmark для synonym precision/recall.
- Не закрыт вопрос cross-domain collisions в полностью автоматическом режиме на больших объемах.
- Ограниченная доказательная база по масштабу (300 — хороший pilot, но не production-scale).

### Честный вывод

Уже сейчас это **сильный инженерный артефакт**, но для “очень сильного showcase” нужно:
- показать результаты минимум на 20k+;
- добавить quality report с формальными метриками;
- продемонстрировать стабильный runtime-effect в основном matching pipeline.

## 4) Сравнение с существующими открытыми решениями

### Что уже существует

- ESCO (EU): большая открытая классификация occupations/skills, API и версии.
- O*NET (US): открытая occupational/skills база и регулярные релизы.
- ISCO (ILO): международный стандарт классификации профессий.
- Skills-OVATE (Cedefop/Eurostat): крупный мониторинг спроса по OJA.

### Риск “делаем то, что уже есть”

Риск есть, если пытаться конкурировать “в лоб” как универсальная глобальная таксономия.

### Где ваш подход может выигрывать

- Операционная интеграция прямо в runtime matching-контур.
- Domain-aware synonyms, ориентированные на конкретный поток вакансий.
- Быстрый инкрементальный цикл обновления из реальных job postings.
- Детерминированный runtime и объяснимый контракт для production.

### Стратегия позиционирования

Позиционировать не как замену ESCO/O*NET, а как:
- “operational normalization layer” поверх/рядом с ними;
- локально-адаптированный словарь для конкретного рынка/языкового микса и use-case.

## 5) Научная и профессиональная опора

### Trustworthy источники, на которые реально стоит опираться

- ESCO official docs/API и версии классификатора.
- O*NET official database/docs.
- ILO ISCO standard.
- Cedefop/Eurostat Skills-OVATE и JRC материалы по OJA.
- ACL/NAACL публикации по skill extraction, например SkillSpan.

### Практические best practices из этих источников и отрасли

- Использовать существующие стандарты как внешний anchor (crosswalk, mapping).
- Разводить extraction и normalization (не смешивать “распознавание” и “каноникализацию”).
- Явно учитывать смещения OJA-данных (white-collar bias и source bias).
- Вести versioned release с reproducible пайплайном и quality gates.
- Мерить качество на holdout-наборах, а не только по внутреннему ощущению.

## Интегральный честный вывод

### Practically useful?

Да. В текущем направлении это practically useful, если:
- удержать фокус на runtime utility, а не на “универсальном справочнике всего”;
- довести auto-governance и benchmark-метрики.

### Может ли быть продуктом?

- как standalone словарь — ограниченно;
- как компонент end-to-end системы подбора — высоко жизнеспособно;
- как open-source framework + contracts + benchmark — сильный вариант для доверия и портфолио.

### Сильный демонстрационный артефакт?

Да, при условии закрытия 3 пунктов:
- масштаб 20k+ с отчетом по качеству;
- отсутствие ручной приемки (автоматический adjudication pipeline);
- измеримый эффект на downstream matching.

## Рекомендация по дальнейшим действиям

1. Продолжать подход, но позиционировать его как `operational normalization component`.
2. Убрать ручную приемку полностью (LLM-adjudication для сложных кейсов + строгие auto-gates).
3. На ближайшем этапе зафиксировать milestone на 20k вакансий с quality report.
4. Добавить mapping/crosswalk к ESCO/O*NET для внешней объяснимости и совместимости.
5. Перед 100k/700k провести throughput hardening (параллельность, rate-limit стратегия, retry budget).

## Открытые источники (для опоры и сверки)

- ESCO API and documentation: https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/esco-api
- O*NET database (official): https://www.onetcenter.org/database.html
- O*NET descriptor scales (official): https://www.onetonline.org/help/online/scales
- JRC: skills intelligence from online job ads: https://joint-research-centre.ec.europa.eu/projects-and-activities/employment/skills-intelligence-online-job-advertisements_en
- Cedefop/Eurostat Skills-OVATE (overview): https://cros.ec.europa.eu/topic/skills-ovate
- SkillSpan paper (ACL Anthology): https://aclanthology.org/2022.naacl-main.366/
