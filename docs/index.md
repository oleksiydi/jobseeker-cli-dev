# Документация `jobseeker-cli`

Этот сайт собран из Markdown-файлов проекта через `MkDocs` + `Material for MkDocs`.

## Что внутри

- Архитектурные схемы и описание пайплайнов.
- Документы по логике `job_details_collector`.
- Материалы по локальным эмбеддингам и оптимизации LLM-запросов.

## Как запускать локально

Через Docker Compose:

```bash
docker compose -f docker/docker-compose.docs.yml up
```

Документация будет доступна по адресу:

```text
http://localhost:8000
```

Остановка:

```bash
docker compose -f docker/docker-compose.docs.yml down
```
