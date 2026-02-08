#!/usr/bin/env python3  # Позволяет запускать файл как скрипт в Unix-системах
# -*- coding: utf-8 -*-  # Указываем кодировку файла (UTF-8), чтобы корректно показывать русские комментарии

"""
Получение деталей вакансий из Jobcenter с условием обращения к API:
- Если в jobdetails уже есть запись, сравниваем:
    stellenangebote.modifikationsTimestamp (из коллекции списка)
    VS jobdetails.aenderungsdatum (в деталях)
  К API обращаемся ТОЛЬКО если modifikationsTimestamp строго больше aenderungsdatum.
- Если записи в jobdetails нет — обращаемся к API и создаём запись.
- При изменениях содержимого сохраняем прошлое состояние в отдельную коллекцию history.
- Если содержимое не изменилось, сохраняем lightweight-событие обновления времени.

Допущения:
- Даты приходят строками вида "2025-08-05T06:14:28.330" или "2025-08-05T06:14:28.33".
- Мы нормализуем дробную часть секунд до 6 знаков, чтобы корректно распарсить в datetime.
"""

import base64
import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone  # datetime/timezone — для работы с датами/временем (UTC)
from typing import Any

import requests
from pymongo import ASCENDING
from pymongo.collection import Collection
from pymongo.database import Database

# Позволяет запускать файл напрямую: python app/job_details_collector.py
if __package__ is None or __package__ == "":
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config
from app.core.logging import get_logger
from app.core.time_utils import parse_dt
from app.db.mongo import get_database, resolve_collection_name

# ---------- утилиты (вспомогательные функции) ----------

SERVICE_FIELDS = {
    "_id",
    "created_at",
    "updated_at",
    "details_hash",
    "old",
    "last_seen_remote_updated_at",
    "last_checked_at",
}
CONTENT_HASH_EXCLUDE_FIELDS = {"aenderungsdatum"}


def _history_collection_name(cfg: dict[str, Any]) -> str:
    env_value = os.getenv("COLLECTION_JOBDETAILS_HISTORY")
    if env_value:
        return env_value
    return cfg.get("history_collections", {}).get("details", "jobdetails_history")


def ensure_indexes(
    db: Database, details_collection_name: str, history_collection_name: str
) -> None:
    """Гарантированно создаём индексы в коллекциях деталей и их архива."""
    det = db[details_collection_name]
    det.create_index([("referenznummer", ASCENDING)], unique=True)
    det.create_index([("aenderungsdatum", ASCENDING)])
    det.create_index([("details_hash", ASCENDING)])

    history = db[history_collection_name]
    history.create_index([("referenznummer", ASCENDING), ("occurred_at", ASCENDING)])
    history.create_index([("occurred_at", ASCENDING)])
    history.create_index([("event_type", ASCENDING)])

def to_b64(s: str) -> str:  # Кодирование строки в Base64 для URL (как требует API)
    return base64.b64encode(s.encode("utf-8")).decode("utf-8").rstrip("=")  # Кодируем и удаляем '=' в конце


def _details_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Оставляет только бизнес-поля для сравнения изменений."""
    return {k: v for k, v in item.items() if k not in SERVICE_FIELDS}


def _content_payload_for_hash(payload: dict[str, Any]) -> dict[str, Any]:
    """Поля для hash содержимого (без временных меток удаленного обновления)."""
    return {k: v for k, v in payload.items() if k not in CONTENT_HASH_EXCLUDE_FIELDS}


def _compute_details_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_diff_update(
    existing_doc: dict[str, Any], new_payload: dict[str, Any], new_hash: str, now: datetime
) -> dict[str, Any]:
    existing_payload = _details_payload(existing_doc)
    set_fields: dict[str, Any] = {"updated_at": now, "details_hash": new_hash}
    unset_fields: dict[str, str] = {}

    for key, new_value in new_payload.items():
        if existing_payload.get(key) != new_value:
            set_fields[key] = new_value

    for key in existing_payload.keys():
        if key not in new_payload:
            unset_fields[key] = ""

    update_doc: dict[str, Any] = {"$set": set_fields}
    if unset_fields:
        update_doc["$unset"] = unset_fields
    return update_doc


def _build_history_snapshot(
    existing_doc: dict[str, Any], new_hash: str, remote_updated_at: Any, now: datetime
) -> dict[str, Any]:
    snapshot = dict(existing_doc)
    snapshot.pop("_id", None)
    return {
        "referenznummer": existing_doc.get("referenznummer"),
        "occurred_at": now,
        "event_type": "content_change",
        "source": "jobcenter_details_api",
        "previous_remote_updated_at": existing_doc.get("last_seen_remote_updated_at"),
        "remote_updated_at": remote_updated_at,
        "previous_details_hash": existing_doc.get("details_hash"),
        "new_details_hash": new_hash,
        "snapshot": snapshot,
    }


def _build_no_content_change_event(
    referenznummer: str,
    details_hash: str,
    previous_remote_updated_at: Any,
    remote_updated_at: Any,
    now: datetime,
) -> dict[str, Any]:
    return {
        "referenznummer": referenznummer,
        "occurred_at": now,
        "event_type": "no_content_change",
        "source": "jobcenter_details_api",
        "details_hash": details_hash,
        "previous_remote_updated_at": previous_remote_updated_at,
        "remote_updated_at": remote_updated_at,
    }


def _build_remote_not_found_event(
    referenznummer: str,
    previous_remote_updated_at: Any,
    list_modifikations_timestamp: Any,
    now: datetime,
) -> dict[str, Any]:
    return {
        "referenznummer": referenznummer,
        "occurred_at": now,
        "event_type": "remote_not_found",
        "source": "jobcenter_details_api",
        "http_status": 404,
        "previous_remote_updated_at": previous_remote_updated_at,
        "list_modifikations_timestamp": list_modifikations_timestamp,
    }


# ---------- основная логика ----------

def main():  # Главная функция, которая управляет всем процессом
    log = get_logger("job_details_collector")  # Создаём именованный логгер

    cfg = load_app_config()  # Загружаем конфигурацию приложения
    db = get_database(cfg)  # Подключаемся к MongoDB
    list_collection_name = resolve_collection_name(cfg, key="list", default="stellenangebote")
    details_collection_name = resolve_collection_name(cfg, key="details", default="jobdetails")
    history_collection_name = _history_collection_name(cfg)
    ensure_indexes(db, details_collection_name, history_collection_name)

    list_col: Collection = db[list_collection_name]
    det_col: Collection = db[details_collection_name]
    det_history_col: Collection = db[history_collection_name]

    api = cfg["jobcenter"]["details_api"]          # Параметры API для деталей
    headers = dict(api.get("headers", {}))         # Заголовки HTTP-запроса к API (копируем, чтобы не менять оригинал)
    url_tpl = api["url_template"]                   # Шаблон URL, куда подставим Base64(ref)

    # Берем только активные вакансии (совместимость: отсутствие is_active считаем активной).
    cursor = list_col.find(
        {"is_active": {"$ne": False}},
        {"referenznummer": 1, "refnr": 1, "modifikationsTimestamp": 1},
    )  # Итератор по кандидатам для обновления details
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
            jd_raw = current.get("last_seen_remote_updated_at") or current.get("aenderungsdatum")
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
        remote_not_found = False
        time.sleep(random.uniform(0.5, 2)) # Небольшая пауза (снизить шанс троттлинга; при необходимости можно убрать/изменить)
        for attempt in range(2):  # Делаем до 2 попыток на случай временных ошибок сети/сервера
            try:
                r = requests.get(url, headers=headers, timeout=10)  # Отправляем HTTP GET
                if r.status_code == 200:  # Успех — получили данные
                    payload = r.json()  # Парсим JSON → dict
                    break  # Выходим из цикла ретраев
                if r.status_code == 404:
                    remote_not_found = True
                    log.info(f"{ref}: HTTP 404 (детали недоступны у источника), пропуск без ретрая")
                    break
                else:
                    log.warning(f"{ref}: HTTP {r.status_code}")  # Логируем неожиданный код ответа
            except Exception as e:  # Ловим любые сетевые ошибки/таймауты
                log.warning(f"{ref}: ошибка запроса: {e}")  # Пишем предупреждение в лог
            time.sleep(2 ** attempt ** random.uniform(0.9, 4))  # Ждём 1/2/4  * randon секунд между попытками (экспоненциальная пауза)

        if remote_not_found:
            now = datetime.utcnow()
            det_history_col.insert_one(
                _build_remote_not_found_event(
                    referenznummer=ref,
                    previous_remote_updated_at=(
                        current.get("last_seen_remote_updated_at") if current else None
                    ),
                    list_modifikations_timestamp=sa_raw,
                    now=now,
                )
            )
            continue

        if not payload:  # Если после всех попыток ничего не получили
            log.info(f"{ref}: нет данных от API (пропуск)")  # Сообщаем и идём дальше
            continue  # Переходим к следующей вакансии

        # Нормализуем полезную нагрузку (проставим referenznummer, убедимся что aenderungsdatum есть хоть None)
        payload["referenznummer"] = ref  # Явно указываем связку с референс-номером
        payload.setdefault("aenderungsdatum", None)  # Если поля нет — добавим с None (чтобы парсер не падал)
        remote_updated_raw = payload.get("aenderungsdatum")
        new_dt = parse_dt(remote_updated_raw) or datetime.now(timezone.utc)  # Для сравнений в date-gate

        if current:  # Если документ уже существовал — решаем: обновлять/версировать или нет
            cur_dt = parse_dt(current.get("last_seen_remote_updated_at") or current.get("aenderungsdatum"))

            if cur_dt and new_dt <= cur_dt:  # Дополнительная защита: если вдруг API вернул не более новую дату
                log.info(f"{ref}: детали от API не новее (new<=old), пропущено")  # Сообщаем и пропускаем обновление
                continue  # Идём к следующей вакансии

            business_payload = _details_payload(payload)
            business_payload["referenznummer"] = ref
            content_payload = _content_payload_for_hash(business_payload)
            new_hash = _compute_details_hash(content_payload)
            if current.get("details_hash") == new_hash:
                now = datetime.utcnow()
                previous_remote_updated_at = current.get("last_seen_remote_updated_at")
                det_history_col.insert_one(
                    _build_no_content_change_event(
                        referenznummer=ref,
                        details_hash=new_hash,
                        previous_remote_updated_at=previous_remote_updated_at,
                        remote_updated_at=remote_updated_raw,
                        now=now,
                    )
                )
                det_col.update_one(
                    {"_id": current["_id"]},
                    {
                        "$set": {
                            "aenderungsdatum": remote_updated_raw,
                            "last_seen_remote_updated_at": remote_updated_raw,
                            "last_checked_at": now,
                        }
                    },
                )
                log.info(f"{ref}: содержимое без изменений, обновлены метки времени и история события")
                continue

            now = datetime.utcnow()
            business_payload["last_seen_remote_updated_at"] = remote_updated_raw
            business_payload["last_checked_at"] = now
            det_history_col.insert_one(
                _build_history_snapshot(current, new_hash, remote_updated_raw, now)
            )
            det_col.update_one(
                {"_id": current["_id"], "details_hash": {"$ne": new_hash}},
                _build_diff_update(current, business_payload, new_hash, now),
            )
            processed += 1  # Увеличиваем счётчик обработанных документов
            log.info(f"{ref}: обновлено (предыдущее состояние вынесено в {history_collection_name})")
        else:
            # Если документа не было — просто вставляем новый
            now = datetime.utcnow()  # Текущее время UTC для служебных полей
            business_payload = _details_payload(payload)
            business_payload["referenznummer"] = ref
            business_payload["details_hash"] = _compute_details_hash(_content_payload_for_hash(business_payload))
            business_payload["last_seen_remote_updated_at"] = remote_updated_raw
            business_payload["last_checked_at"] = now
            business_payload["created_at"] = now
            business_payload["updated_at"] = now
            det_col.insert_one(business_payload)
            processed += 1  # Счётчик +1
            log.info(f"{ref}: вставлено")  # Сообщаем об успешной вставке

    log.info(f"Готово. Обновлено/добавлено: {processed}")  # Итоговый отчёт после завершения цикла

if __name__ == "__main__":  # Если файл запустили как основную программу
    main()  # Выполняем главную функцию
