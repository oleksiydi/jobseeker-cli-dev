# Каноничный Текст, Payload, Mongo и LLM: подробные примеры (RU)

## Зачем этот документ

В `15-embedding-chunking-spec-v1-ru.md` зафиксированы правила.  
Этот файл показывает, как правила работают на реальных примерах:
1. как из сырых полей получить каноничный текст вакансии;
2. как формируются чанки и payload для Qdrant;
3. что именно пишется в Mongo;
4. что в каком виде отправляем в LLM.

## 1) Быстрый ориентир: pipeline данных

1. `meta + details` -> merged vacancy.
2. merged vacancy -> каноничный текст (`TITLE/ROLE/ALT_ROLES/LOCATION/EMPLOYMENT/DESCRIPTION`).
3. `DESCRIPTION` -> предложения -> semantic chunks.
4. chunks -> embeddings -> Qdrant points (с payload).
5. служебные метаданные индексации -> Mongo.
6. retrieval/rerank -> top chunks -> LLM input (`compact` или `full_context`).

---

## 2) Пример A: хорошие полные данные

### 2.1 Сырой merged-вход (упрощённо)

```json
{
  "referenznummer": "10001-1001158614-S",
  "meta": {
    "titel": "Mitarbeiter (m/w/d) Instandhaltung",
    "beruf": "Kraftfahrzeugelektriker/in",
    "arbeitsort": {
      "plz": "93053",
      "ort": "Regensburg",
      "region": "Bayern",
      "land": "Deutschland"
    }
  },
  "details": {
    "stellenangebotsTitel": "Mitarbeiter (m/w/d) Instandhaltung",
    "hauptberuf": "Kraftfahrzeugelektriker/in",
    "alternativBeruf1": "Kraftfahrzeugmechatroniker/in - Nutzfahrzeugtechnik",
    "stellenangebotsBeschreibung": "Ihre Aufgaben: Wartung und Instandhaltung unserer Schienenfahrzeuge, planmäßige Prüfungen und Messungen mit Lasermesstechnik, Fehlersuche und Fehlerbehebung bei Störungen der Fahrzeugtechnik, Begleitung von Probefahrten. Ihr Profil: Abgeschlossene technische Berufsausbildung im Bereich Mechanik, Mechatronik oder Elektronik, Bereitschaft zur Früh- und Spätschicht (Mo-Fr), Führerschein Klasse B. Wir bieten: Unbefristeter Vertrag, 30+ Urlaubstage, Weiterbildungen zu elektrischen Antrieben und Steuerungssystemen, Jobticket-Zuschuss, Bikeleasing.",
    "arbeitszeitVollzeit": true,
    "vertragsdauer": "UNBEFRISTET",
    "stellenlokationen": [
      {
        "adresse": {
          "plz": "93053",
          "ort": "Regensburg",
          "region": "BAYERN",
          "land": "DEUTSCHLAND"
        }
      }
    ]
  }
}
```

### 2.2 Каноничный текст (после нормализации)

```text
TITLE: Mitarbeiter (m/w/d) Instandhaltung
ROLE: Kraftfahrzeugelektriker/in
ALT_ROLES: Kraftfahrzeugmechatroniker/in - Nutzfahrzeugtechnik
LOCATION: DE|Bayern|Regensburg|93053
EMPLOYMENT: vollzeit=true; teilzeit=false; vertragsdauer=UNBEFRISTET; minijob=false
DESCRIPTION:
Ihre Aufgaben: Wartung und Instandhaltung unserer Schienenfahrzeuge, planmäßige Prüfungen und Messungen mit Lasermesstechnik, Fehlersuche und Fehlerbehebung bei Störungen der Fahrzeugtechnik, Begleitung von Probefahrten. Ihr Profil: Abgeschlossene technische Berufsausbildung im Bereich Mechanik, Mechatronik oder Elektronik, Bereitschaft zur Früh- und Spätschicht (Mo-Fr), Führerschein Klasse B. Wir bieten: Unbefristeter Vertrag, 30+ Urlaubstage, Weiterbildungen zu elektrischen Antrieben und Steuerungssystemen, Jobticket-Zuschuss, Bikeleasing.
```

### 2.3 Что будет в чанках (пример)

1. `chunk_0` (`tasks`):  
   `Wartung und Instandhaltung unserer Schienenfahrzeuge. Planmäßige Prüfungen und Messungen mit Lasermesstechnik. Fehlersuche und Fehlerbehebung bei Störungen der Fahrzeugtechnik. Begleitung von Probefahrten.`
2. `chunk_1` (`requirements`):  
   `Abgeschlossene technische Berufsausbildung im Bereich Mechanik, Mechatronik oder Elektronik. Bereitschaft zur Früh- und Spätschicht (Mo-Fr). Führerschein Klasse B.`
3. `chunk_2` (`benefits`):  
   `Unbefristeter Vertrag. 30+ Urlaubstage. Weiterbildungen zu elektrischen Antrieben und Steuerungssystemen. Jobticket-Zuschuss und Bikeleasing.`

### 2.4 Qdrant payload (пример для `chunk_1`)

```json
{
  "referenznummer": "10001-1001158614-S",
  "chunk_id": "10001-1001158614-S#1",
  "chunk_index": 1,
  "section_label": "requirements",
  "section_confidence": 0.93,
  "description_missing": false,
  "embedding_input_hash": "a6f50e22167b7a91c0d2c9651211de4d7c2ed3af80c29f1f7317fdc95a69fd91",
  "embedding_model": "intfloat/multilingual-e5-large",
  "chunking_version": "v1",
  "preprocess_version": "v1"
}
```

### 2.5 Что пишем в Mongo (служебно)

```json
{
  "referenznummer": "10001-1001158614-S",
  "embedding_input_hash": "a6f50e22167b7a91c0d2c9651211de4d7c2ed3af80c29f1f7317fdc95a69fd91",
  "embedding_model": "intfloat/multilingual-e5-large",
  "chunking_version": "v1",
  "preprocess_version": "v1",
  "indexed_at": "2026-02-19T13:40:00Z",
  "chunks_indexed": 3
}
```

---

## 3) Пример B: избыточные данные (много воды/маркетинга)

### 3.1 Ситуация

Описание на 6-8 тысяч символов, много повторов “мы лучшая компания”, мало конкретики.

### 3.2 Что делаем

1. Нормализация текста убирает шум форматирования.
2. Semantic chunking выделяет реальные смысловые секции, например:
- `about_company`: история компании и ценности;
- `tasks`: реальные обязанности;
- `requirements`: требования;
- `benefits`: условия и бонусы.
3. Блоки вроде `Wir sind ein dynamisches Team mit flachen Hierarchien und familiärer Atmosphäre` с низкой полезностью помечаются как `section_label=marketing`.
4. Если чанков слишком много, включается лимит `MAX_CHUNKS_PER_JOB`, и в индекс идут:
- лучшие требования/задачи/условия;
- первый контекстный чанк.

### 3.3 Почему это важно

Если оставить весь текст без контроля:
1. индекс раздуется;
2. вырастет время CPU;
3. в LLM улетит шум вместо полезных фактов.

---

## 4) Пример C: неполные данные

### 4.1 Ситуация

Есть `title` и `role`, но `description` пустое.

### 4.2 Каноничный текст

```text
TITLE: DBA Administrator (m/w/d)
ROLE: Datenbankadministrator/in
ALT_ROLES:
LOCATION: DE|Bayern|Regensburg|93047
EMPLOYMENT: vollzeit=true; teilzeit=false; vertragsdauer=UNBEFRISTET; minijob=false
DESCRIPTION:
```

### 4.3 Fallback-чанк

Synthetic chunk:

```text
Role summary: DBA Administrator (m/w/d). Hauptberuf: Datenbankadministrator/in. Alternativberufe: IT-Administrator/in.
Location: Regensburg (Bayern, DE), PLZ 93047. Employment: Vollzeit=true, Teilzeit=false, Vertragsdauer=UNBEFRISTET, Minijob=false.
```

Payload получает:
- `description_missing=true`

Это значит:
1. вакансия не теряется в поиске;
2. но в confidence/score учитывается, что данных мало.

---

## 4.4 Отдельный случай: `alternativBeruf1 = "false"` или пусто

Сырой фрагмент:

```json
{
  "details": {
    "alternativBeruf1": "false",
    "alternativBeruf2": "",
    "alternativBeruf3": "  "
  }
}
```

После нормализации:
1. `"false"` удаляется как псевдозначение;
2. пустые строки удаляются;
3. итоговый список `ALT_ROLES` становится пустым.

Каноничный фрагмент:

```text
ALT_ROLES:
```

Это корректно и не считается ошибкой.

---

## 5) Пример D: плохие/грязные данные

### 5.1 Ситуация

1. “сломан” unicode (`Sam Kullman?s`);
2. много спецсимволов и дублированных переносов;
3. часть полей приходит как `null` строкой (`"null"`).

### 5.2 Что делаем

1. Нормализуем unicode/пробелы.
2. `"null"` -> пустое значение.
3. Служебные поля удаляем из embedding-входа.
4. Считаем hash только по очищенному каноничному представлению.

Результат:
- меньше ложных reindex;
- устойчивое качество semantic chunking.

---

## 6) Что отправляем в LLM и в каком виде

Ниже не “сырой дамп вакансии”, а контролируемый payload.

### 6.1 Режим `compact` (по умолчанию)

Идея:
- отправляем только top chunks + краткую выжимку.

Пример входа в LLM:

```json
{
  "mode": "compact",
  "query": "DevOps remote Bayern",
  "profile": {
    "summary": "Senior DevOps Engineer, Kubernetes, AWS, CI/CD",
    "constraints": "DE/EN, remote/hybrid"
  },
  "job": {
    "referenznummer": "10001-1001158614-S",
    "title": "Mitarbeiter (m/w/d) Instandhaltung",
    "role": "Kraftfahrzeugelektriker/in",
    "location": "Regensburg",
    "employment": "Vollzeit"
  },
  "top_chunks": [
    {
      "chunk_index": 1,
      "section_label": "requirements",
      "section_confidence": 0.93,
      "text": "Abgeschlossene technische Berufsausbildung im Bereich Mechanik, Mechatronik oder Elektronik. Bereitschaft zur Früh- und Spätschicht (Mo-Fr). Führerschein Klasse B.",
      "chunk_score": 0.73
    },
    {
      "chunk_index": 0,
      "section_label": "tasks",
      "section_confidence": 0.89,
      "text": "Wartung und Instandhaltung unserer Schienenfahrzeuge. Planmäßige Prüfungen und Messungen mit Lasermesstechnik. Fehlersuche und Fehlerbehebung bei Störungen der Fahrzeugtechnik.",
      "chunk_score": 0.69
    }
  ],
  "scores": {
    "role_score": 0.58,
    "chunk_score": 0.71,
    "profile_score": 0.62,
    "metadata_score": 0.80,
    "pre_llm_score": 0.66,
    "rerank_score": 0.64
  }
}
```

### 6.2 Режим `full_context`

Идея:
- отправляем полный профиль + полный запрос + полный текст вакансии;
- плюс top chunks как "быстрые якоря" для модели.

Пример:

```json
{
  "mode": "full_context",
  "query_full_text": "Ищу работу DevOps Engineer в регионе Bayern, желательно remote или hybrid, интересуют позиции с Kubernetes, CI/CD и облаками AWS/Azure.",
  "profile_full_text": "Кандидат: Senior DevOps Engineer. Опыт: 7 лет. Стек: Kubernetes, Docker, Terraform, Ansible, GitLab CI, Jenkins, AWS, Linux, Bash, Python. Языки: DE B2, EN B2, RU C2. Предпочтения: remote/hybrid, Bayern.",
  "job_full_text": "TITLE: Mitarbeiter (m/w/d) Instandhaltung\nROLE: Kraftfahrzeugelektriker/in\nALT_ROLES: Kraftfahrzeugmechatroniker/in - Nutzfahrzeugtechnik\nLOCATION: DE|Bayern|Regensburg|93053\nEMPLOYMENT: vollzeit=true; teilzeit=false; vertragsdauer=UNBEFRISTET; minijob=false\nDESCRIPTION:\nIhre Aufgaben: Wartung und Instandhaltung unserer Schienenfahrzeuge, planmäßige Prüfungen und Messungen mit Lasermesstechnik, Fehlersuche und Fehlerbehebung bei Störungen der Fahrzeugtechnik, Begleitung von Probefahrten. Ihr Profil: Abgeschlossene technische Berufsausbildung im Bereich Mechanik, Mechatronik oder Elektronik, Bereitschaft zur Früh- und Spätschicht (Mo-Fr), Führerschein Klasse B. Wir bieten: Unbefristeter Vertrag, 30+ Urlaubstage, Weiterbildungen zu elektrischen Antrieben und Steuerungssystemen, Jobticket-Zuschuss, Bikeleasing.",
  "top_chunks": [
    {
      "chunk_index": 1,
      "section_label": "requirements",
      "section_confidence": 0.93,
      "text": "Abgeschlossene technische Berufsausbildung im Bereich Mechanik, Mechatronik oder Elektronik. Bereitschaft zur Früh- und Spätschicht (Mo-Fr). Führerschein Klasse B.",
      "chunk_score": 0.73
    },
    {
      "chunk_index": 0,
      "section_label": "tasks",
      "section_confidence": 0.89,
      "text": "Wartung und Instandhaltung unserer Schienenfahrzeuge. Planmäßige Prüfungen und Messungen mit Lasermesstechnik. Fehlersuche und Fehlerbehebung bei Störungen der Fahrzeugtechnik.",
      "chunk_score": 0.69
    }
  ],
  "scores": {
    "role_score": 0.58,
    "chunk_score": 0.71,
    "profile_score": 0.62,
    "metadata_score": 0.80,
    "pre_llm_score": 0.66,
    "rerank_score": 0.64
  }
}
```

Когда использовать:
1. сложные кейсы и спорные вакансии;
2. когда `compact` даёт нестабильный результат;
3. когда важна максимальная глубина анализа.

---

## 7) Что ожидаем на выходе от LLM (унифицированно)

Минимальный JSON:

```json
{
  "match_score": 71,
  "decision": "partial",
  "reasons": [
    "Опыт кандидата в DevOps и cloud не совпадает с ключевой ролью вакансии (электромеханика).",
    "Часть навыков по диагностике и обслуживанию техники может быть переносимой, но профиль роли другой."
  ],
  "risks": [
    "Высокий риск отказа на этапе первичного скрининга из-за ролевого mismatch.",
    "В вакансии требуется профильное техническое образование в области механики/электроники."
  ],
  "interview_topics": [
    "Диагностика неисправностей электрических систем",
    "Практика технического обслуживания и измерений",
    "Готовность к сменному графику"
  ],
  "prep_plan": [
    "Подготовить короткое объяснение, почему интересна смена домена с DevOps на техническую эксплуатацию.",
    "Освежить базовые темы по электротехнике и промышленной безопасности.",
    "Подготовить примеры реальной диагностики технических проблем (если есть)."
  ],
  "einschreiben": "Sehr geehrte Damen und Herren, ich bewerbe mich für die Position Mitarbeiter (m/w/d) Instandhaltung in Regensburg. Ich habe langjährige technische Erfahrung in der systematischen Fehleranalyse, arbeite strukturiert und zuverlässig und bin bereit, mich gezielt in Ihre fachlichen Prozesse einzuarbeiten. Besonders motiviert mich die Möglichkeit, in einem stabilen Team an sicherheitskritischen und technisch anspruchsvollen Aufgaben mitzuwirken. Über die Einladung zu einem persönlichen Gespräch freue ich mich sehr.",
  "provider": "local",
  "model": "qwen2.5-7b-instruct",
  "input_mode": "compact"
}
```

Почему важен единый формат:
1. одинаковая обработка локальной и API-моделей;
2. проще хранить, сравнивать и валидировать результаты.

## 7.1 Если секцию определить не удалось

Реалистичный вариант payload:

```json
{
  "chunk_index": 4,
  "section_label": "unknown",
  "section_confidence": 0.34,
  "text": "Wir sind ein wachsendes Unternehmen mit spannenden Herausforderungen und einem motivierten Team.",
  "chunk_score": 0.41
}
```

Это не ошибка. Это нормальный исход при слабо структурированном тексте.
В ранжировании такой чанк обычно получает меньший вес.

---

## 8) Практические правила для команды

1. Если данных мало -> не выбрасываем вакансию, а снижаем confidence.
2. Если данных много -> режем по смыслу, не по “голым” символам.
3. Каноничный текст должен быть стабильным и воспроизводимым.
4. В LLM отправляем только то, что нужно для решения задачи, без лишнего шума.

## 9) Связанные документы

1. Базовые правила: `docs/architecture/15-embedding-chunking-spec-v1-ru.md`
2. Схема потока: `docs/architecture/16-embedding-chunking-flow-diagram.md`
3. Решение skip/reindex: `docs/architecture/17-embedding-reindex-decision-diagram.md`
4. Сборка LLM-контекста: `docs/architecture/13-llm-context-assembly.md`
