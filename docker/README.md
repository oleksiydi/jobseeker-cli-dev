 
 mongodump   --host 127.0.0.1:27017   -u root -p 'example' --authenticationDatabase admin   --db jobcenter   --archive=./jobcenter_$(date +%F).archive   --gzip

mongorestore \
  --host new-host:27017 \
  -u root -p 'example' --authenticationDatabase admin \
  --db jobcenter \
  --drop \
  --archive=/backup/jobcenter-08-22.archive \
  --gzip

## Конфигурации проекта

- Основные конфиги хранятся в `conf/`.
- Рабочий файл приложения: `conf/app.json`.
- Рабочий профиль по умолчанию: `conf/profiles/default.profile.json`.
- Через ENV можно задать явный путь к конфигам:
- `APP_CONFIG_PATH` для файла приложения.
- `PROFILE_CONFIG_PATH` для файла профиля.
- Директория `config/` больше не используется как автоматический fallback.

