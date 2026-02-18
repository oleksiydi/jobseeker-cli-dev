# MongoDB Playgrounds

Набор готовых MongoDB Playground-запросов для проекта `jobseeker-cli`.

## Как пользоваться в VS Code

1. Открой файл `*.mongodb.js`.
2. Проверь, что выбран нужный connection string MongoDB.
3. Нажми `Run` (или `Ctrl+Shift+P` -> `MongoDB: Run All`).

## Правила оформления файлов

- Один сценарий = один файл.
- Имена в формате: `NNN_short_description.mongodb.js`.
- В начале файла всегда указываем `use("jobcenter");`.
- Внутри запроса даем короткие русские комментарии по этапам пайплайна.

## Список запросов

- `001_latest_stellenangebote_with_latest_jobdetails.mongodb.js`
Описание: джойн `stellenangebote` + `jobdetails` по `referenznummer` с выбором последних версий из обеих коллекций.

- `002_count_non_empty_field_in_unique_latest_records.mongodb.js`
Описание: считает, у скольких уникальных актуальных записей (последние версии `stellenangebote` + `jobdetails`) целевое поле заполнено/пусто.

- `003_field_completeness_profile_unique_latest.mongodb.js`
Описание: строит профиль заполненности по всем полям для уникальных актуальных записей, включая оценку "осмысленной заполненности" и отсечение системных полей.

- `004_field_quality_score.mongodb.js`
Описание: оценивает качество полей через итоговый `quality_score` (осмысленное покрытие + вариативность + текстовая информативность), чтобы выбрать поля для retrieval/ranking.
