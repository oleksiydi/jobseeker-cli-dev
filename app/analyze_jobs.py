#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аналитика + сопроводительное письмо в MongoDB.
- Профиль: из коллекции profiles по analyzer.profile_key
- Вакансия: meta (stellenangebote) + details (jobdetails) через $lookup
- В OpenAI уходит ТОЛЬКО актуальное состояние (без _id/old)
- Ответ модели строго в JSON; сохраняем в collections.analysis
"""

import os, json, re, logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from bson import ObjectId
from openai import OpenAI

# ---------- конфиг / подключение ----------

def script_dir():
    return os.path.dirname(os.path.abspath(__file__))

def load_config():
    path = os.path.join(script_dir(), '..', 'conf', 'app.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def mongo_db(cfg):
    load_dotenv()
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URI не задан в .env")
    client = MongoClient(uri)
    return client[cfg["mongodb"]["db_name"]]

def ensure_indexes(db, cfg):
    col = db[cfg["collections"]["analysis"]]
    col.create_index([("referenznummer", ASCENDING)], unique=True)

# ---------- утилиты ----------

def extract_numbers(text: str, limit=3):
    """Извлекает до 3 чисел из текста (учитывает запятые/точки)."""
    if not text:
        return []
    nums = re.findall(r'(?<!\w)(?:\d{1,3}(?:[.,]\d+)?)', text)
    out = []
    for s in nums:
        s = s.replace(',', '.')
        try:
            v = float(s)
        except Exception:
            continue
        out.append(v)
        if len(out) >= limit:
            break
    return out

def json_default(o):
    """Сериализация нестандартных типов для json.dumps."""
    if isinstance(o, ObjectId):
        return str(o)
    if isinstance(o, datetime):
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        return o.isoformat()
    return str(o)

def make_json_safe(x):
    """Рекурсивно убирает служебные поля и приводит к JSON-безопасному виду."""
    if isinstance(x, dict):
        return {k: make_json_safe(v)
                for k, v in x.items()
                if k not in {"_id", "old"}}
    if isinstance(x, list):
        return [make_json_safe(i) for i in x]
    if isinstance(x, tuple):
        return [make_json_safe(i) for i in x]
    if isinstance(x, ObjectId):
        return str(x)
    if isinstance(x, datetime):
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.isoformat()
    return x

# ---------- основная логика ----------

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    log = logging.getLogger("analyze_jobs")

    cfg = load_config()
    db = mongo_db(cfg)
    ensure_indexes(db, cfg)

    col_list = db[cfg["collections"]["list"]]       # stellenangebote
    col_det  = db[cfg["collections"]["details"]]    # jobdetails
    col_an   = db[cfg["collections"]["analysis"]]   # analysen
    col_prof = db[cfg["collections"]["profiles"]]   # profiles

    # профиль кандидата
    key = cfg["analyzer"]["profile_key"]
    profile = col_prof.find_one({"key": key}, {"_id": 0})
    if not profile:
        raise RuntimeError(f"Профиль с key='{key}' не найден в '{cfg['collections']['profiles']}'")

    # клиент OpenAI
    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = cfg["analyzer"]["model"]

    # агрегат: сшиваем meta+details, сразу выбрасываем _id/old (только актуальное состояние)
    cursor = col_list.aggregate([
        {
            "$lookup": {
                "from": cfg["collections"]["details"],
                "let": {"ref": "$referenznummer"},
                "pipeline": [
                    { "$match": { "$expr": { "$eq": ["$referenznummer", "$$ref"] } } },
                    { "$project": { "_id": 0, "old": 0 } }
                ],
                
                "as": "details"
            }
        },
        { "$addFields": { "details": { "$first": "$details" } } },
        { "$project": { "_id": 0, "old": 0 } }
    ])

    processed = 0
    for doc in cursor:
        ref = doc.get("referenznummer") or doc.get("refnr")
        if not ref:
            continue

        title = (doc.get("stellenangebotsTitel") or "").strip()
        firma = (doc.get("arbeitgeber") or doc.get("firma") or "").strip()

        # уже анализировали?
        if col_an.find_one({"referenznummer": ref}):
            log.info(f"{ref}: уже обработана — {title} bei {firma}")
            continue
        else:
            log.info(f"{ref}: новая вакансия — {title} bei {firma}")

        details = doc.get("details") or {}
        job_meta = {k: v for k, v in doc.items() if k != "details"}

        merged = {
            "meta": job_meta,
            "details": details
        }

        # JSON-безопасные копии для отправки модели
        safe_profile = make_json_safe(profile)
        safe_merged  = make_json_safe(merged)

        system = (
             "Du bist Senior-Recruiter/HRBP auf Unternehmensseite und triffst eine strenge Vorauswahl. "
             "Du arbeitest ausschließlich im Interesse des Arbeitgebers: Risiko minimieren, nur Kandidaten mit klar belegtem Fit weiterleiten. "
             "Kein Coaching-Ton, keine Nettigkeiten. Nur belegbare Aussagen.\n\n"
             "Regeln:\n"
             "- Nutze primär Nachweise/Ergebnisse (Rollen, Verantwortung, Projekte, Zahlen). Behauptungen ohne Beleg zählen schwach.\n"
             "- Wenn Informationen fehlen: klar als 'fehlender Nachweis' markieren. Keine Spekulation als факт.\n"
             "- Prüfe Must-haves zuerst. Wenn Must-haves fehlen -> decision meist 'reject' oder 'hold'.\n\n"
             "Score 0–100 (kalibriert): "
             "0–39 Reject, 40–59 Schwach/hohes Risiko, 60–74 Möglich aber Lücken, 75–89 Stark, 90–100 Außergewöhnlich (selten).\n"
             "Gewichtung: Must-haves 60%, relevante Erfahrung/Impact 25%, Risiken/Red Flags 15%.\n\n"
             "Ausgabeformat: ausschließlich ein gültiges JSON-Objekt mit exakt diesen Schlüsseln:\n"
             "- score (0-100, integer)\n"
             "- decision (\"reject\" | \"hold\" | \"shortlist\")\n"
             "- reasons (Liste mit genau 5 kurzen Punkten, max. 140 Zeichen je Punkt)\n"
             "- risks (Liste mit 2 bis 4 kurzen Punkten)\n"
             "- missing_evidence (Liste mit 2 bis 4 kurzen Punkten)\n"
             "- notes (optional kurzer Text)\n\n"
             "Keine zusätzlichen Keys, kein Markdown, keine Texte außerhalb von JSON."
             )


        user_payload = {
            "candidate_profile": safe_profile,
            "job_payload": safe_merged,
            "task": "Bewerte die Passung und erstelle Anschreiben. score=0..100 (ohne Prozentzeichen)."
        }
    
        try:
            req = {
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=json_default)}
                ],
                #"max_completion_tokens": 1100,
                #"temperature": 0.2,  # хотим, но не все модели поддерживают
                "reasoning_effort":"low",
                "verbosity":"low"
            }

            try:
                resp = client.chat.completions.create(**req)
            except Exception as e:
                # gpt-5-nano / gpt-5-* часто не дают менять temperature
                if "Unsupported value: 'temperature'" in str(e):
                    req.pop("temperature", None)
                    resp = client.chat.completions.create(**req)
                else:
                    raise

            content = resp.choices[0].message.content
            data = json.loads(content)

        except Exception as e:
            log.error(f"{ref}: ошибка вызова/парсинга OpenAI: {e}")
            continue


        # нормализуем оценку
        score = data.get("score")
        try:
            score = float(score) if score is not None else None
        except Exception:
            score = None

        # «первые встретившиеся числа» (до 3) из всех текстов
        concat_text = " ".join(filter(None, [
            data.get("notes", ""),
            data.get("cover_letter", ""),
            " ".join(data.get("reasons", []) or []),
            " ".join(data.get("improvements", []) or []),
        ]))
        probabilities = []
        if score is not None:
            probabilities.append(score)
        for n in extract_numbers(concat_text, limit=3):
            if n not in probabilities:
                probabilities.append(n)
        probabilities = probabilities[:3]

        out_doc = {
            "referenznummer": ref,
            "created_at": datetime.now(timezone.utc),
            "candidate_profile_key": key,

            "job_payload": make_json_safe(merged),

            #"candidate_profile": make_json_safe(profile),  # можно убрать, если документ слишком большой
           
            "score": score,
            "score_": int(data.get("score", 0)),
            #"wahrscheinlichkeit": score,
            #"probabilities": probabilities,
            "decision": data.get("decision", "hold"),
            "reasons": (data.get("reasons") or [])[:5],
            "risks": (data.get("risks") or [])[:4],
            "missing_evidence": (data.get("missing_evidence") or [])[:4],
            "notes": data.get("notes", ""),
            #"improvements": (data.get("improvements") or [])[:3],
            #"cover_letter": data.get("cover_letter", "")
            "openai_raw": data,     # ответ модели в JSON
        }

        col_an.insert_one(out_doc)
        processed += 1
        log.info(f"{ref}: анализ сохранён")

    log.info(f"Готово. Проанализировано: {processed}")

if __name__ == "__main__":
    main()
