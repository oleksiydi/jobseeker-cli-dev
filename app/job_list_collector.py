#!/usr/bin/env python3  # Указывает, что скрипт можно запускать как исполняемый в Unix
# -*- coding: utf-8 -*-  # Объявляем кодировку файла (UTF-8)

"""
Скрипт: сбор списка вакансий из Jobcenter -> запись в MongoDB (без файлов).
Берёт настройки из единого конфигурационного файла config/app.json, раздел jobcenter.list_api,
где сохранены ИСХОДНЫЕ ключи: api_url, api_key, params, headers, max_requests, output_filenames.
Поля output_filenames сохранены, но здесь НЕ используются.

Перед запуском:
  1) Установить зависимости (requests, pymongo, python-dotenv).
  2) Заполнить .env с MONGODB_URI (и опционально MONGODB_DB_NAME, COLLECTION_STELLENANGEBOTE).
  3) Запустить: python -m app.job_list_collector
"""

import os  # Работа с путями и переменными окружения
import json  # Загрузка конфигурации из JSON
import logging  # Логирование (информация, предупреждения, ошибки)
import time  # Паузы между запросами для вежливости к API
from datetime import datetime  # Метки времени для created_at/updated_at
import random

import requests  # HTTP-запросы к API Jobcenter
from dotenv import load_dotenv  # Загрузка переменных из файла .env
from pymongo import MongoClient, ASCENDING, errors  # Клиент MongoDB, индексы, ошибки MongoDB

def _script_dir() -> str:
    """Возвращает абсолютный путь к директории текущего файла."""
    return os.path.dirname(os.path.abspath(__file__))  # Определяем путь к текущему файлу и берём его директорию

def _load_config() -> dict:
    """Загружает единый конфиг config/app.json и возвращает как dict."""
    path = os.path.join(_script_dir(), '..', 'config', 'app.json')  # Строим путь до конфиг-файла относительно скрипта
    with open(path, 'r', encoding='utf-8') as f:  # Открываем файл в UTF-8
        return json.load(f)  # Читаем JSON в словарь Python

def _mongo_db():
    """Создаёт подключение к MongoDB из .env и возвращает (db, имя_коллекции_для_списка)."""
    load_dotenv()  # Подгружаем переменные окружения из .env (если есть)
    mongo_uri = os.getenv("MONGODB_URI")  # Берём строку подключения к MongoDB
    if not mongo_uri:  # Если переменная не задана — это критическая ошибка
        raise RuntimeError("MONGODB_URI не задан. Укажи его в .env (пример: mongodb://root:example@localhost:27017/?authSource=admin)")
    db_name = os.getenv("MONGODB_DB_NAME", "jobcenter")  # Имя базы (по умолчанию jobcenter)
    col_name = os.getenv("COLLECTION_STELLENANGEBOTE", "stellenangebote")  # Имя коллекции списка (по умолчанию stellenangebote)
    client = MongoClient(mongo_uri)  # Создаём клиент MongoDB
    db = client[db_name]  # Получаем объект базы данных
    return db, col_name  # Возвращаем базу и имя коллекции

def _ensure_indexes(db, collection_name: str) -> None:
    """Создаёт необходимые индексы (уникальный по referenznummer, обычный по updated_at)."""
    col = db[collection_name]  # Получаем коллекцию по имени
    col.create_index([("referenznummer", ASCENDING)], unique=True)  # Уникальный индекс: не допускаем дублей по referenznummer
    col.create_index([("updated_at", ASCENDING)])  # Индекс по времени обновления (удобно для сортировок/запросов)

def _normalize_referenznummer(item: dict) -> str | None:
    """Приводит референс-номер к единому полю 'referenznummer' и возвращает его (или None)."""
    ref = item.get("referenznummer") or item.get("refnr")  # Иногда поле называется refnr — подстрахуемся
    if ref is not None:  # Если референс-номер есть
        ref = str(ref).strip()  # Приводим к строке и убираем пробелы
        item["referenznummer"] = ref  # Записываем под ключом 'referenznummer' для единообразия
    return ref  # Возвращаем нормализованное значение (или None)

def main() -> None:
    """Основная функция: читает конфиг, ходит по страницам API, upsert в MongoDB."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")  # Настройка логов
    log = logging.getLogger("job_list_collector")  # Именованный логгер

    cfg = _load_config()  # Загружаем единый конфиг в память
    list_api = cfg["jobcenter"]["list_api"]  # Берём раздел конфигурации, отвечающий за список вакансий

    api_url = list_api["api_url"]  # URL конечной точки API Jobcenter для списка вакансий
    api_key = list_api.get("api_key", "")  # Ключ API (обычно 'jobboerse-jobsuche'); может быть пустым
    base_params = dict(list_api.get("params", {}))  # Базовые параметры поиска (город, радиус, размер страницы и т.п.)
    base_headers = dict(list_api.get("headers", {}))  # Базовые заголовки HTTP-запроса
    max_requests = int(list_api.get("max_requests", 1000))  # Максимум страниц/запросов, чтобы не зациклиться
    _ = list_api.get("output_filenames", {})  # Для совместимости: в конфиге есть, но здесь НЕ используется

    db, collection_name = _mongo_db()  # Подключаемся к базе и узнаём имя коллекции для списка
    _ensure_indexes(db, collection_name)  # Гарантируем, что индексы созданы
    col = db[collection_name]  # Получаем саму коллекцию (ссылку на неё)

    session = requests.Session()  # Создаём сессию HTTP (повторное использование соединений ускоряет работу)
    headers = dict(base_headers)  # Копируем базовые заголовки, чтобы не портить исходный словарь
    if api_key:  # Если ключ API задан
        headers["X-API-Key"] = api_key  # Добавляем заголовок X-API-Key, как требует сервис

    total_upserts = 0  # Суммарное количество вставок/обновлений документов
    page = 1  # Номер текущей страницы (начинаем с 1, как в API)
    made = 0  # Сколько запросов уже отправили (для ограничения max_requests)

    while made < max_requests:  # Цикл, ограниченный максимальным числом запросов
        params = {**base_params, "page": page}  # Берём базовые параметры и добавляем номер страницы

        try:
            resp = session.get(api_url, headers=headers, params=params, timeout=20)  # Отправляем GET-запрос с таймаутом
            resp.raise_for_status()  # Бросит исключение, если статус-код не 2xx
            data = resp.json()  # Парсим JSON-ответ в словарь
        except Exception as e:  # Любая ошибка запроса/парсинга
            log.error(f"Страница {page}: ошибка запроса/парсинга: {e}")  # Пишем в лог причину
            break  # Прерываем цикл (можно сделать ретраи — упрощаем)

        items = data.get("stellenangebote") or []  # Забираем список вакансий из поля 'stellenangebote'
        if not items:  # Если список пуст — дальше ничего нет
            log.info(f"Страница {page}: вакансий нет — завершаем.")  # Сообщаем и выходим из цикла
            break  # Прерываем цикл по страницам

        upserts_this_page = 0  # Локальный счётчик вставок/обновлений на текущей странице

        for raw in items:  # Идём по каждой вакансии на странице
            ref = _normalize_referenznummer(raw)  # Нормализуем и достаём referenznummer
            if not ref:  # Если у вакансии нет референс-номера — пропускаем
                continue  # Переходим к следующей записи

            now = datetime.utcnow()  # Текущее время по UTC (для меток created_at/updated_at)
            raw["updated_at"] = now  # Всегда обновляем updated_at при upsert

            try:
                col.update_one(  # Выполняем upsert в MongoDB
                    {"referenznummer": ref},  # Ищем документ по уникальному референс-номеру
                    {
                        "$setOnInsert": {"created_at": now},  # Если документ новый — проставим created_at
                        "$set": raw  # Обновляем/ставим все поля, пришедшие из API
                    },
                    upsert=True  # Если не нашли — вставим новый документ
                )
                upserts_this_page += 1  # Увеличиваем счётчик успешных upsert на странице
            except errors.DuplicateKeyError:  # Если вдруг конфликт уникальности (редко, но бывает)
                pass  # Игнорируем и идём дальше
            except Exception as e:  # Любая другая ошибка Mongo
                log.warning(f"{ref}: ошибка upsert в Mongo: {e}")  # Логируем и продолжаем

        total_upserts += upserts_this_page  # Прибавляем результат страницы к общему счёту
        log.info(f"Страница {page}: upsert={upserts_this_page}, всего={total_upserts}")  # Отчёт по странице

        page += 1  # Переходим к следующей странице
        made += 1  # Считаем отправленный запрос
        time.sleep(random.uniform(0.5, 5)) # Небольшая пауза (снизить шанс троттлинга; при необходимости можно убрать/изменить)

    log.info(f"Готово. Всего вставлено/обновлено документов: {total_upserts}")  # Итоговый отчёт


if __name__ == "__main__":  # Если файл запущен как скрипт
    main()  # Вызываем основную функцию
