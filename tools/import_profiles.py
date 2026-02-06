#!/usr/bin/env python3  # Позволяет запускать файл как исполняемый скрипт в Unix-системах
# -*- coding: utf-8 -*-  # Указываем кодировку исходника (UTF-8), чтобы русские комментарии отображались корректно

"""
Импорт профиля(ей) в MongoDB (коллекция profiles).
По умолчанию читает: ../conf/profiles/default.profile.json.
Дружит с UTF-8 BOM, делает upsert по полю "key".
"""  # Докстрока: краткое описание назначения скрипта и поведения по умолчанию

import os  # Модуль для работы с переменными окружения и путями
import json  # Модуль для чтения/записи JSON
import argparse  # Модуль для удобного парсинга аргументов командной строки
from pathlib import Path  # Удобная работа с путями в виде объектов Path
from datetime import datetime, timezone  # Работа с датой/временем (timezone-aware время)

from dotenv import load_dotenv  # Функция для загрузки переменных окружения из .env
from pymongo import MongoClient, ASCENDING  # Клиент MongoDB и константа для индексов по возрастанию

# Пути по умолчанию
SCRIPT_DIR = Path(__file__).resolve().parent  # Папка, где лежит этот скрипт (абсолютный путь)
ROOT_DIR = SCRIPT_DIR.parent  # Корень проекта — берем родительскую папку относительно tools/


def resolve_default_profile_file() -> Path:
    """
    Возвращает стандартный путь к профилю в conf/.
    """
    return ROOT_DIR / "conf" / "profiles" / "default.profile.json"


DEFAULT_FILE = resolve_default_profile_file()

def debug_path(p: Path) -> str:  # Вспомогательная функция для красивого вывода пути (полного)
    try:
        return str(p.resolve())  # Пытаемся вернуть абсолютный нормализованный путь
    except Exception:
        return str(p)  # Если что-то пошло не так — возвращаем простой str-путь

def load_docs(path: Path):  # Функция загрузки документов из JSON-файла (может быть 1 объект или массив объектов)
    """Читает JSON-файл: объект или массив объектов. Поддерживает UTF-8 с BOM."""  # Комментарий к функции
    if not path.exists():  # Проверяем, существует ли файл по указанному пути
        raise FileNotFoundError(f"Файл не найден: {debug_path(path)}")  # Если нет — бросаем понятную ошибку
    if path.stat().st_size == 0:  # Проверяем, не пустой ли файл (размер 0 байт)
        raise ValueError(f"Файл пустой: {debug_path(path)}")  # Если пустой — сообщаем об этом

    try:
        with path.open("r", encoding="utf-8-sig") as f:  # Открываем файл на чтение; 'utf-8-sig' автоматически уберёт BOM
            data = json.load(f)  # Парсим JSON в Python-объекты (dict или list)
    except json.JSONDecodeError as e:  # Ловим ошибки парсинга JSON
        head = path.open("rb").read(200)  # Читаем первые 200 байт файла (для диагностики)
        try:
            head_txt = head.decode("utf-8", errors="replace")  # Пытаемся показать эти байты как текст UTF-8
        except Exception:
            head_txt = str(head)  # Если не получилось — покажем как сырые байты
        raise ValueError(
            f"Некорректный JSON в {debug_path(path)}: {e}\n"
            f"Первые 200 байт файла:\n{head_txt}"
        ) from e  # Бросаем подробную ошибку с контекстом, перекидывая оригинальное исключение

    if isinstance(data, dict):  # Если в файле один JSON-объект (словарь)
        return [data]  # Возвращаем список из одного элемента для унификации обработки
    if isinstance(data, list):  # Если в файле массив JSON-объектов
        if not all(isinstance(x, dict) for x in data):  # Проверяем, что каждый элемент массива — объект (словарь)
            raise ValueError("В массиве должны быть объекты JSON ({}), не строки/числа.")  # Иначе сообщаем об ошибке
        return data  # Возвращаем список документов
    raise ValueError("Ожидался JSON-объект или массив объектов.")  # Если формат не dict и не list — ошибка

def get_db():  # Функция подключения к MongoDB, возвращает объект базы данных
    """Подключение к MongoDB из .env."""  # Комментарий к функции
    load_dotenv()  # Загружаем переменные окружения из файла .env (если он существует)
    uri = os.getenv("MONGODB_URI", "mongodb://root:example@localhost:27017/?authSource=admin")  # Берём строку подключения (или дефолт)
    db_name = os.getenv("MONGODB_DB_NAME", "jobcenter")  # Имя базы данных (по умолчанию jobcenter)
    client = MongoClient(uri)  # Создаём клиент MongoDB
    return client[db_name]  # Возвращаем объект базы с указанным именем

def main():  # Главная функция — точка входа скрипта
    ap = argparse.ArgumentParser(description="Импорт профилей в MongoDB (коллекция profiles).")  # Описываем CLI-интерфейс
    ap.add_argument("--file", "-f", type=Path, default=DEFAULT_FILE,  # Аргумент: путь к JSON-файлу (по умолчанию дефолтный)
                    help=f"Путь к JSON-файлу (по умолчанию: {debug_path(DEFAULT_FILE)})")
    ap.add_argument("--collection", "-c", default=os.getenv("COLLECTION_PROFILES", "profiles"),  # Аргумент: имя коллекции (по умолчанию profiles)
                    help="Имя коллекции (по умолчанию 'profiles')")
    ap.add_argument("--upsert-key", default="key",  # Аргумент: поле, по которому делаем upsert (ключ уникальности)
                    help="Поле для upsert (по умолчанию 'key')")
    ap.add_argument("--key", default="default",  # Аргумент: значение key, если его нет в документе
                    help="Значение 'key', если его нет в документе (по умолчанию 'default')")
    args = ap.parse_args()  # Парсим аргументы командной строки

    print(f"Читаю файл: {debug_path(args.file)}")  # Выводим пользователю, какой файл читаем

    docs = load_docs(args.file)  # Загружаем документы из указанного JSON-файла (список словарей)
    db = get_db()  # Подключаемся к базе данных
    col = db[args.collection]  # Получаем коллекцию с именем, переданным в аргументах (или по умолчанию)

    # Уникальный индекс по upsert-ключу
    col.create_index([(args.upsert_key, ASCENDING)], unique=True)  # Создаём (если нет) уникальный индекс по полю upsert-ключа

    inserted = updated = unchanged = 0  # Счётчики результатов: сколько вставили/обновили/оставили без изменений
    for d in docs:  # Идём по каждому документу из файла
        # key по умолчанию
        d.setdefault("key", args.key)  # Если в документе нет поля "key", подставляем значение из аргумента (по умолчанию 'default')

        # время UTC с таймзоной
        now = datetime.now(timezone.utc)  # Текущее время в UTC с tzinfo (современно и безопасно)

        # если в документ уже вручную положили created_at — уважаем; иначе ставим сейчас
        created_at = d.get("created_at", now)  # Берём created_at из документа, либо используем текущее время

        # updated_at всегда текущее (не трогаем created_at)
        d["updated_at"] = now  # Поле updated_at обновляется на текущее время при каждом запуске

        # ВАЖНО: чтобы не конфликтовать с $setOnInsert, удаляем created_at из $set
        set_doc = dict(d)  # Делаем копию документа, который пойдёт в $set (чтобы можно было убрать поле)
        set_doc.pop("created_at", None)  # Удаляем created_at из $set, он будет ставиться только через $setOnInsert

        uk = d.get(args.upsert_key)  # Достаём значение upsert-ключа (например, key)
        if uk is None:  # Если ключа нет — нечем определять уникальность
            # нет ключа для upsert — просто вставим, с обоими полями времени
            set_doc["created_at"] = created_at  # Вставим created_at напрямую, т.к. не используем $setOnInsert без ключа
            col.insert_one(set_doc)  # Обычная вставка документа в коллекцию
            inserted += 1  # Увеличиваем счётчик вставок
            continue  # Переходим к следующему документу

        res = col.update_one(  # Пытаемся обновить документ или вставить новый (upsert=True)
            {args.upsert_key: uk},  # Фильтр: документ с тем же значением upsert-ключа
            {
                "$setOnInsert": {"created_at": created_at},  # Если вставка — выставим created_at один раз
                "$set": set_doc  # Всегда обновляем остальные поля (без created_at) и updated_at
            },
            upsert=True  # Разрешаем вставку, если документа с таким ключом нет
        )

        if res.matched_count == 0 and res.upserted_id:  # Если совпадений не было, но документ создан (есть upserted_id)
            inserted += 1  # Считаем как вставку
        elif res.modified_count:  # Если существующий документ был изменён
            updated += 1  # Считаем как обновление
        else:
            unchanged += 1  # Иначе изменений не было (данные те же)

    print(f"Готово. inserted={inserted}, updated={updated}, unchanged={unchanged}")  # Итоговая статистика по операциям
    print(f"Коллекция: {args.collection}")  # Уточняем, в какую коллекцию вносили документы

if __name__ == "__main__":  # Этот блок выполнится только при прямом запуске скрипта (не при импорте)
    main()  # Вызываем главную функцию
