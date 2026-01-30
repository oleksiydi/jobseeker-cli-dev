#!/usr/bin/env python3  # Позволяет запускать файл как скрипт в Unix-системах
# -*- coding: utf-8 -*-  # Указываем кодировку файла (UTF-8), чтобы корректно показывать русские комментарии

"""
Получение деталей вакансий из Jobcenter с условием обращения к API:
- Если в jobdetails уже есть запись, сравниваем:
    stellenangebote.modifikationsTimestamp (из коллекции списка)
    VS jobdetails.aenderungsdatum (в деталях)
  К API обращаемся ТОЛЬКО если modifikationsTimestamp строго больше aenderungsdatum.
- Если записи в jobdetails нет — обращаемся к API и создаём запись.
- При обновлении сохраняем предыдущую версию документа в массиве old[].

Допущения:
- Даты приходят строками вида "2025-08-05T06:14:28.330" или "2025-08-05T06:14:28.33".
- Мы нормализуем дробную часть секунд до 6 знаков, чтобы корректно распарсить в datetime.
"""

import os, json, base64, time, logging, requests  # os/json/base64/time/logging/requests — стандартные задачи скрипта
from datetime import datetime, timezone  # datetime/timezone — для работы с датами/временем (UTC)
from dotenv import load_dotenv  # Загрузка переменных окружения из .env (например, MONGODB_URI)
from pymongo import MongoClient, ASCENDING  # Клиент MongoDB и создание индексов
from typing import Optional  # Optional — тип "может быть None"
import re  # re — регулярные выражения, пригодятся для нормализации даты
import random

# ---------- утилиты (вспомогательные функции) ----------

def script_dir():  # Функция для получения пути к папке, где лежит этот файл
    return os.path.dirname(os.path.abspath(__file__))  # Возвращаем абсолютный путь к директории текущего файла

def load_config():  # Функция загрузки единого конфига приложения
    path = os.path.join(script_dir(), '..', 'conf', 'app.json')  # Формируем путь к файлу conf/app.json
    with open(path, 'r', encoding='utf-8') as f:  # Открываем файл в UTF-8
        return json.load(f)  # Читаем JSON → dict

def mongo_db(cfg):  # Подключение к MongoDB
    load_dotenv()  # Загружаем переменные окружения из .env
    uri = os.getenv("MONGODB_URI")  # Берём строку подключения к MongoDB из переменной окружения
    client = MongoClient(uri)  # Создаём клиент MongoDB
    return client[cfg["mongodb"]["db_name"]]  # Возвращаем объект базы данных по имени из конфига

def ensure_indexes(db, cfg):  # Гарантированно создаём индексы в коллекции деталей
    det = db[cfg["collections"]["details"]]  # Получаем коллекцию jobdetails
    det.create_index([("referenznummer", ASCENDING)], unique=True)  # Уникальный индекс по referenznummer (без дублей)
    det.create_index([("aenderungsdatum", ASCENDING)])  # Индекс по дате изменения (ускоряет выборки/сортировки)

def to_b64(s: str) -> str:  # Кодирование строки в Base64 для URL (как требует API)
    return base64.b64encode(s.encode("utf-8")).decode("utf-8").rstrip("=")  # Кодируем и удаляем '=' в конце

def _pad_fraction_to_6(s: str) -> str:  # Нормализация: дополняем дробную часть секунд до 6 знаков (или обрезаем)
    # Регулярка ловит: датаTвремя + опциональную дробную часть + опциональный часовой пояс
    m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})?$', s or '')
    if not m:  # Если строка не совпала с ожидаемым шаблоном — возвращаем как есть (пусть парсер дальше попробует сам)
        return s
    base, frac, tz = m.group(1), m.group(2), m.group(3)  # Разбираем группы: основное время, дробная часть, таймзона
    if frac:  # Если есть дробная часть, например ".33" или ".330"
        digits = frac[1:]  # Берём цифры без точки
        digits = (digits + '000000')[:6]  # Дополняем нулями до 6 символов и обрезаем лишнее
        frac = '.' + digits  # Собираем обратно с точкой
    else:
        frac = ''  # Если дробной части не было — оставляем пусто
    tz = tz or ''  # Если таймзоны нет — оставляем пусто (будем считать UTC дальше)
    return base + frac + tz  # Собираем нормализованную строку времени

def parse_dt(value: Optional[str]) -> Optional[datetime]:  # Универсальный парсер ISO-дат в datetime
    if not value:  # Если дата пустая/None — возвращаем None
        return None
    v = _pad_fraction_to_6(value.strip())  # Сначала нормализуем дробную часть секунд до 6 знаков
    v = v.replace("z", "Z")  # На всякий случай приводим 'z' к 'Z'
    # Если есть суффикс 'Z' (UTC), заменим на '+00:00', чтобы fromisoformat понимал таймзону
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"  # "…Z" → "…+00:00"
    try:
        dt = datetime.fromisoformat(v)  # Пытаемся распарсить ISO-строку
        if dt.tzinfo is None:  # Если таймзоны нет (наивная дата)
            dt = dt.replace(tzinfo=timezone.utc)  # Считаем, что это UTC, и явно указываем tzinfo=UTC
        return dt  # Возвращаем datetime с tzinfo
    except Exception:
        # Фолбэк на несколько часто встречающихся форматов (без дробной части/с дробной — уже нормализовали выше)
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(v, fmt)  # Пробуем очередной формат
                if dt.tzinfo is None:  # Если tz нет — проставим UTC
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt  # Успех — возвращаем datetime
            except Exception:
                pass  # Не подошёл — пробуем следующий
    return None  # Совсем не получилось — вернём None

# ---------- основная логика ----------

def main():  # Главная функция, которая управляет всем процессом
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")  # Настройка логирования
    log = logging.getLogger("job_details_collector")  # Создаём именованный логгер

    cfg = load_config()  # Загружаем конфигурацию приложения
    db = mongo_db(cfg)  # Подключаемся к MongoDB
    ensure_indexes(db, cfg)  # Убеждаемся, что индексы созданы

    list_col = db[cfg["collections"]["list"]]      # Коллекция "списка" вакансий (stellenangebote)
    det_col  = db[cfg["collections"]["details"]]   # Коллекция деталей вакансий (jobdetails)

    api = cfg["jobcenter"]["details_api"]          # Параметры API для деталей
    headers = dict(api.get("headers", {}))         # Заголовки HTTP-запроса к API (копируем, чтобы не менять оригинал)
    url_tpl = api["url_template"]                   # Шаблон URL, куда подставим Base64(ref)

    # Забираем из списка только нужные поля: referenznummer/refnr и modifikationsTimestamp
    cursor = list_col.find({}, {"referenznummer": 1, "refnr": 1, "modifikationsTimestamp": 1})  # Итератор по всем вакансиям в списке
    processed = 0  # Счётчик обработанных (созданных/обновлённых) записей деталей

    for doc in cursor:  # Проходим по всем документам списка
        ref = doc.get("referenznummer") or doc.get("refnr")  # Берём референс-номер вакансии
        if not ref:  # Если референс-номер отсутствует — пропускаем
            continue  # Переходим к следующему документу

        sa_raw = doc.get("modifikationsTimestamp")  # Дата/время изменения в "списке" (как строка)
        sa_dt = parse_dt(sa_raw)  # Пробуем распарсить строку в объект datetime
        if sa_dt is None:  # Если распарсить не удалось — мы не можем корректно сравнить даты
            log.info(f"{ref}: нет корректного modifikationsTimestamp в stellenangebote — пропуск запроса")  # Сообщаем и пропускаем
            continue  # Переходим к следующей вакансии

        current = det_col.find_one({"referenznummer": ref})  # Ищем существующую запись в деталях по тому же референс-номеру

        if current:  # Если запись деталей уже существует
            jd_raw = current.get("aenderungsdatum")  # Достаём строковое поле даты изменения из деталей
            jd_dt = parse_dt(jd_raw)  # Парсим его в datetime (или получаем None)

            # --- КЛЮЧЕВОЕ УСЛОВИЕ: обращаемся к API ТОЛЬКО если дата в списке СТРОГО больше даты в деталях ---
            if jd_dt is not None and sa_dt <= jd_dt:  # Если в деталях дата есть и список НЕ новее (<=), то обновлять не нужно
                log.info(f"{ref}: список не новее (SA<=JD), пропускаем API")  # Логируем причину пропуска
                continue  # Переходим к следующей вакансии

            # Если в деталях даты нет (jd_dt is None) — считаем, что надо обновить (получить свежие детали)
            # Если sa_dt > jd_dt — тоже обновляем (эту ветку мы не пишем явно, условие выше уже отфильтровало обратное)

        else:
            # Если записи в деталях вообще нет — обязательно пойдём в API и создадим её
            pass  # Ничего делать не нужно — ниже общий код получения деталей

        # --- Если дошли сюда, значит надо идти в API за деталями (либо запись отсутствует, либо список новее) ---
        
        ref_b64 = to_b64(ref)  # Кодируем референс-номер в Base64 (требование API)
        url = url_tpl.format(ref_b64=ref_b64)  # Подставляем код в шаблон URL

        payload = None  # Здесь будет ответ API (детали вакансии)
        time.sleep(random.uniform(0.5, 2)) # Небольшая пауза (снизить шанс троттлинга; при необходимости можно убрать/изменить)
        for attempt in range(2):  # Делаем до 2 попыток на случай временных ошибок сети/сервера
            try:
                r = requests.get(url, headers=headers, timeout=10)  # Отправляем HTTP GET
                if r.status_code == 200:  # Успех — получили данные
                    payload = r.json()  # Парсим JSON → dict
                    break  # Выходим из цикла ретраев
                else:
                    log.warning(f"{ref}: HTTP {r.status_code}")  # Логируем неожиданный код ответа
            except Exception as e:  # Ловим любые сетевые ошибки/таймауты
                log.warning(f"{ref}: ошибка запроса: {e}")  # Пишем предупреждение в лог
            time.sleep(2 ** attempt ** random.uniform(0.9, 4))  # Ждём 1/2/4  * randon секунд между попытками (экспоненциальная пауза)

        if not payload:  # Если после всех попыток ничего не получили
            log.info(f"{ref}: нет данных от API (пропуск)")  # Сообщаем и идём дальше
            continue  # Переходим к следующей вакансии

        # Нормализуем полезную нагрузку (проставим referenznummer, убедимся что aenderungsdatum есть хоть None)
        payload["referenznummer"] = ref  # Явно указываем связку с референс-номером
        payload.setdefault("aenderungsdatum", None)  # Если поля нет — добавим с None (чтобы парсер не падал)
        new_dt = parse_dt(payload["aenderungsdatum"]) or datetime.now(timezone.utc)  # Парсим новую дату; если нет — берём "сейчас" (UTC)

        if current:  # Если документ уже существовал — решаем: обновлять/версировать или нет
            cur_dt = parse_dt(current.get("aenderungsdatum"))  # Парсим предыдущую дату из текущего документа

            if cur_dt and new_dt <= cur_dt:  # Дополнительная защита: если вдруг API вернул не более новую дату
                log.info(f"{ref}: детали от API не новее (new<=old), пропущено")  # Сообщаем и пропускаем обновление
                continue  # Идём к следующей вакансии

            old_copy = dict(current)  # Делаем копию текущего документа (для истории)
            old_copy.pop("_id", None)  # Убираем служебное поле _id (Mongo не даст вставить его как есть)

            det_col.update_one(  # Обновляем документ в базе
                {"_id": current["_id"]},  # Ищем по _id (он уникален)
                {
                    "$push": {"old": old_copy},  # Сохраняем старую версию в массив old[]
                    "$set": payload | {"updated_at": datetime.utcnow()}  # Обновляем полями из API + ставим новый updated_at (UTC)
                }
            )
            processed += 1  # Увеличиваем счётчик обработанных документов
            log.info(f"{ref}: обновлено (версия заархивирована)")  # Сообщаем об успешном обновлении
        else:
            # Если документа не было — просто вставляем новый
            now = datetime.utcnow()  # Текущее время UTC для служебных полей
            payload["created_at"] = now  # Метка создания
            payload["updated_at"] = now  # Метка обновления (равна созданию при первой вставке)
            det_col.insert_one(payload)  # Вставляем документ в коллекцию
            processed += 1  # Счётчик +1
            log.info(f"{ref}: вставлено")  # Сообщаем об успешной вставке

    log.info(f"Готово. Обновлено/добавлено: {processed}")  # Итоговый отчёт после завершения цикла

if __name__ == "__main__":  # Если файл запустили как основную программу
    main()  # Выполняем главную функцию
