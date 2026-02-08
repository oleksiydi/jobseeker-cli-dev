#!/usr/bin/env python3  # Указывает, что скрипт можно запускать как исполняемый в Unix
# -*- coding: utf-8 -*-  # Объявляем кодировку файла (UTF-8)

"""
Скрипт: сбор списка вакансий из Jobcenter -> запись в MongoDB (без файлов).

Конфиг ищется в приоритете:
1) APP_CONFIG_PATH (env)
2) conf/app.json
"""

import hashlib
import json
import os
import time  # Паузы между запросами для вежливости к API
from datetime import datetime  # Метки времени для created_at/updated_at
import random
from uuid import uuid4
from typing import Any

import requests  # HTTP-запросы к API Jobcenter
from pymongo import ASCENDING, UpdateOne, errors  # Индексы и ошибки MongoDB
from pymongo.errors import OperationFailure
from pymongo.collection import Collection
from pymongo.database import Database

# Позволяет запускать файл напрямую: python app/job_list_collector.py
if __package__ is None or __package__ == "":
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config
from app.core.logging import get_logger
from app.core.time_utils import parse_remote_dt, utcnow_naive
from app.db.mongo import get_database, resolve_collection_name


SERVICE_FIELDS = {"_id", "created_at", "updated_at", "meta_hash", "is_active", "missing_since"}


def _history_collection_name(cfg: dict[str, Any]) -> str:
    env_value = os.getenv("COLLECTION_STELLENANGEBOTE_HISTORY")
    if env_value:
        return env_value
    return cfg.get("history_collections", {}).get("list", "stellenangebote_history")


def _list_seen_ttl_days(cfg: dict[str, Any]) -> int:
    env_value = os.getenv("STELLENANGEBOTE_LIST_SEEN_TTL_DAYS")
    if env_value is not None:
        try:
            return max(int(env_value), 1)
        except ValueError:
            pass
    cfg_value = cfg.get("history_ttl_days", {}).get("list_seen")
    if cfg_value is not None:
        try:
            return max(int(cfg_value), 1)
        except (ValueError, TypeError):
            pass
    # Храним долго, чтобы можно было анализировать длительные тренды доступности.
    return 540


def _ensure_indexes(
    db: Database,
    list_collection_name: str,
    history_collection_name: str,
    list_seen_ttl_days: int,
) -> None:
    """Создаёт индексы для актуальных и архивных документов списка вакансий."""
    col = db[list_collection_name]
    col.create_index([("referenznummer", ASCENDING)], unique=True)
    col.create_index([("meta_hash", ASCENDING)])
    col.create_index([("is_active", ASCENDING)])
    col.create_index([("updated_at", ASCENDING)])
    col.create_index([("modifikationsTimestamp_dt", ASCENDING)])

    history = db[history_collection_name]
    history.create_index([("referenznummer", ASCENDING), ("changed_at", ASCENDING)])
    history.create_index([("changed_at", ASCENDING)])
    history.create_index([("event_type", ASCENDING), ("run_id", ASCENDING), ("referenznummer", ASCENDING)])
    history.create_index([("run_id", ASCENDING)])
    history.create_index([("event_type", ASCENDING), ("seen_at", ASCENDING)])
    ttl_seconds = list_seen_ttl_days * 24 * 60 * 60
    try:
        history.create_index(
            [("seen_at", ASCENDING)],
            name="list_seen_ttl_idx",
            expireAfterSeconds=ttl_seconds,
            partialFilterExpression={
                "event_type": "list_seen",
                "seen_at": {"$type": "date"},
            },
        )
    except OperationFailure:
        # Индекс может уже существовать с другими параметрами на старой среде.
        pass

def _normalize_referenznummer(item: dict) -> str | None:
    """Приводит референс-номер к единому полю 'referenznummer' и возвращает его (или None)."""
    ref = item.get("referenznummer") or item.get("refnr")  # Иногда поле называется refnr — подстрахуемся
    if ref is not None:  # Если референс-номер есть
        ref = str(ref).strip()  # Приводим к строке и убираем пробелы
        item["referenznummer"] = ref  # Записываем под ключом 'referenznummer' для единообразия
    return ref  # Возвращаем нормализованное значение (или None)


def _meta_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Возвращает только бизнес-поля для сравнения изменений."""
    return {k: v for k, v in item.items() if k not in SERVICE_FIELDS}


def _compute_meta_hash(payload: dict[str, Any]) -> str:
    """Стабильный hash для детекта изменений по meta-входу."""
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_diff_update(
    existing_doc: dict[str, Any], new_payload: dict[str, Any], new_hash: str, now: datetime
) -> dict[str, Any]:
    existing_payload = _meta_payload(existing_doc)
    set_fields: dict[str, Any] = {"updated_at": now, "meta_hash": new_hash}
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
    existing_doc: dict[str, Any], new_hash: str, now: datetime, run_id: str
) -> dict[str, Any]:
    snapshot = dict(existing_doc)
    snapshot.pop("_id", None)
    return {
        "event_type": "content_change",
        "run_id": run_id,
        "source": "job_list_collector",
        "referenznummer": existing_doc.get("referenznummer"),
        "changed_at": now,
        "previous_meta_hash": existing_doc.get("meta_hash"),
        "new_meta_hash": new_hash,
        "snapshot": snapshot,
    }


def _build_list_seen_event(
    ref: str,
    run_id: str,
    page: int,
    seen_at: datetime,
    item: dict[str, Any],
    meta_hash: str,
) -> dict[str, Any]:
    return {
        "event_type": "list_seen",
        "source": "job_list_collector",
        "run_id": run_id,
        "referenznummer": ref,
        "page": page,
        "seen_at": seen_at,
        "remote_updated_at": item.get("aktualisierungsdatum") or item.get("aenderungsdatum"),
        "meta_hash": meta_hash,
    }


def _build_list_missing_event(ref: str, run_id: str, now: datetime, missing_since: datetime) -> dict[str, Any]:
    return {
        "event_type": "list_missing",
        "source": "job_list_collector",
        "run_id": run_id,
        "referenznummer": ref,
        "occurred_at": now,
        "missing_since": missing_since,
    }


def _build_list_restored_event(ref: str, run_id: str, now: datetime) -> dict[str, Any]:
    return {
        "event_type": "list_restored",
        "source": "job_list_collector",
        "run_id": run_id,
        "referenznummer": ref,
        "occurred_at": now,
    }

def main() -> None:
    """Основная функция: читает конфиг, ходит по страницам API и делает idempotent update."""
    log = get_logger("job_list_collector")  # Именованный логгер

    cfg = load_app_config()  # Загружаем единый конфиг в память
    list_api = cfg["jobcenter"]["list_api"]  # Берём раздел конфигурации, отвечающий за список вакансий

    api_url = list_api["api_url"]  # URL конечной точки API Jobcenter для списка вакансий
    api_key = list_api.get("api_key", "")  # Ключ API (обычно 'jobboerse-jobsuche'); может быть пустым
    base_params = dict(list_api.get("params", {}))  # Базовые параметры поиска (город, радиус, размер страницы и т.п.)
    base_headers = dict(list_api.get("headers", {}))  # Базовые заголовки HTTP-запроса
    max_requests = int(list_api.get("max_requests", 1000))  # Максимум страниц/запросов, чтобы не зациклиться
    _ = list_api.get("output_filenames", {})  # Для совместимости: в конфиге есть, но здесь НЕ используется

    db = get_database(cfg)  # Подключаемся к базе
    list_collection_name = resolve_collection_name(
        cfg,
        key="list",
        default="stellenangebote",
    )
    history_collection_name = _history_collection_name(cfg)
    list_seen_ttl_days = _list_seen_ttl_days(cfg)
    _ensure_indexes(db, list_collection_name, history_collection_name, list_seen_ttl_days)
    col: Collection = db[list_collection_name]
    col_history: Collection = db[history_collection_name]
    run_id = f"list-{utcnow_naive().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    log.info(
        "Запуск list_collector: run_id=%s, list_seen_ttl_days=%s",
        run_id,
        list_seen_ttl_days,
    )

    session = requests.Session()  # Создаём сессию HTTP (повторное использование соединений ускоряет работу)
    headers = dict(base_headers)  # Копируем базовые заголовки, чтобы не портить исходный словарь
    if api_key:  # Если ключ API задан
        headers["X-API-Key"] = api_key  # Добавляем заголовок X-API-Key, как требует сервис

    total_inserted = 0
    total_updated = 0
    total_skipped = 0
    total_archived = 0
    total_seen = 0
    total_restored = 0
    total_marked_missing = 0
    seen_refs_in_run: set[str] = set()
    completed_full_scan = False
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
            completed_full_scan = True
            break  # Прерываем цикл по страницам

        refs: list[str] = []
        prepared_items: list[dict[str, Any]] = []
        for raw in items:
            ref = _normalize_referenznummer(raw)
            if not ref:
                continue
            refs.append(ref)
            prepared_items.append(raw)

        if not prepared_items:
            log.info(f"Страница {page}: нет валидных записей с referenznummer.")
            page += 1
            made += 1
            time.sleep(random.uniform(0.5, 5))
            continue

        existing_docs = col.find({"referenznummer": {"$in": refs}})
        existing_by_ref = {doc["referenznummer"]: doc for doc in existing_docs if doc.get("referenznummer")}

        main_ops: list[UpdateOne] = []
        history_docs: list[dict[str, Any]] = []
        list_seen_docs: list[dict[str, Any]] = []
        inserted_this_page = 0
        updated_this_page = 0
        skipped_this_page = 0
        archived_this_page = 0
        seen_this_page = 0
        restored_this_page = 0

        for raw in prepared_items:
            ref = raw["referenznummer"]
            seen_refs_in_run.add(ref)
            now = utcnow_naive()
            raw["modifikationsTimestamp_dt"] = parse_remote_dt(raw.get("modifikationsTimestamp"))
            payload = _meta_payload(raw)
            payload["referenznummer"] = ref
            new_hash = _compute_meta_hash(payload)
            list_seen_docs.append(
                _build_list_seen_event(
                    ref=ref,
                    run_id=run_id,
                    page=page,
                    seen_at=now,
                    item=raw,
                    meta_hash=new_hash,
                )
            )
            seen_this_page += 1

            current = existing_by_ref.get(ref)
            if not current:
                main_ops.append(
                    UpdateOne(
                        {"referenznummer": ref},
                        {
                            "$setOnInsert": {"created_at": now},
                            "$set": payload
                            | {"updated_at": now, "meta_hash": new_hash, "is_active": True},
                            "$unset": {"missing_since": ""},
                        },
                        upsert=True,
                    )
                )
                inserted_this_page += 1
                continue

            if current.get("meta_hash") == new_hash:
                if current.get("is_active") is False:
                    history_docs.append(_build_list_restored_event(ref=ref, run_id=run_id, now=now))
                    main_ops.append(
                        UpdateOne(
                            {"_id": current["_id"], "is_active": False},
                            {
                                "$set": {"is_active": True, "updated_at": now},
                                "$unset": {"missing_since": ""},
                            },
                        )
                    )
                    restored_this_page += 1
                skipped_this_page += 1
                continue

            history_docs.append(
                _build_history_snapshot(current, new_hash=new_hash, now=now, run_id=run_id)
            )
            if current.get("is_active") is False:
                history_docs.append(_build_list_restored_event(ref=ref, run_id=run_id, now=now))
                restored_this_page += 1
            archived_this_page += 1
            diff_update = _build_diff_update(current, payload, new_hash, now)
            diff_update["$set"]["is_active"] = True
            if current.get("missing_since") is not None:
                diff_update["$unset"] = diff_update.get("$unset", {})
                diff_update["$unset"]["missing_since"] = ""
            main_ops.append(
                UpdateOne(
                    {"_id": current["_id"], "meta_hash": {"$ne": new_hash}},
                    diff_update,
                )
            )
            updated_this_page += 1

        try:
            if main_ops:
                col.bulk_write(main_ops, ordered=False)
            all_history_docs = history_docs + list_seen_docs
            if all_history_docs:
                col_history.insert_many(all_history_docs, ordered=False)
        except errors.DuplicateKeyError:
            log.warning(f"Страница {page}: обнаружены гонки по уникальному ключу, продолжаем.")
        except Exception as e:
            log.warning(f"Страница {page}: ошибка записи в Mongo: {e}")

        total_inserted += inserted_this_page
        total_updated += updated_this_page
        total_skipped += skipped_this_page
        total_archived += archived_this_page
        total_seen += seen_this_page
        total_restored += restored_this_page
        log.info(
            "Страница %s: seen=%s, inserted=%s, updated=%s, skipped=%s, archived=%s, restored=%s",
            page,
            seen_this_page,
            inserted_this_page,
            updated_this_page,
            skipped_this_page,
            archived_this_page,
            restored_this_page,
        )

        page += 1  # Переходим к следующей странице
        made += 1  # Считаем отправленный запрос
        time.sleep(random.uniform(0.5, 5)) # Небольшая пауза (снизить шанс троттлинга; при необходимости можно убрать/изменить)

    if completed_full_scan:
        missing_filter: dict[str, Any] = {"is_active": {"$ne": False}}
        if seen_refs_in_run:
            missing_filter["referenznummer"] = {"$nin": sorted(seen_refs_in_run)}

        missing_docs = list(
            col.find(missing_filter, {"_id": 1, "referenznummer": 1, "missing_since": 1})
        )
        if missing_docs:
            now = utcnow_naive()
            missing_ops: list[UpdateOne] = []
            missing_history_docs: list[dict[str, Any]] = []
            for doc in missing_docs:
                ref = doc.get("referenznummer")
                if not ref:
                    continue
                missing_since = doc.get("missing_since") or now
                missing_ops.append(
                    UpdateOne(
                        {"_id": doc["_id"], "is_active": {"$ne": False}},
                        {
                            "$set": {
                                "is_active": False,
                                "updated_at": now,
                                "missing_since": missing_since,
                            }
                        },
                    )
                )
                missing_history_docs.append(
                    _build_list_missing_event(
                        ref=ref,
                        run_id=run_id,
                        now=now,
                        missing_since=missing_since,
                    )
                )
            if missing_ops:
                try:
                    col.bulk_write(missing_ops, ordered=False)
                    total_marked_missing = len(missing_ops)
                    if missing_history_docs:
                        col_history.insert_many(missing_history_docs, ordered=False)
                except Exception as e:
                    log.warning("run_id=%s: ошибка деактивации отсутствующих вакансий: %s", run_id, e)
    else:
        log.warning(
            "run_id=%s завершен без полного обхода страниц, пропускаем деактивацию отсутствующих вакансий",
            run_id,
        )

    log.info(
        "Готово. run_id=%s, seen=%s, inserted=%s, updated=%s, skipped=%s, archived=%s, restored=%s, marked_missing=%s",
        run_id,
        total_seen,
        total_inserted,
        total_updated,
        total_skipped,
        total_archived,
        total_restored,
        total_marked_missing,
    )


if __name__ == "__main__":  # Если файл запущен как скрипт
    main()  # Вызываем основную функцию
