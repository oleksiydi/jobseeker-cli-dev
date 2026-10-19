import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import load_app_config
from app.db.mongo import get_database, resolve_collection_name

def safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=2)


def build_pipeline(
    min_score: int,
    limit_groups: int,
    limit_jobs_per_group: int,
    profile_key: Optional[str],
) -> List[Dict[str, Any]]:
    match_query: Dict[str, Any] = {
        "is_latest": True,
        "score": {"$gt": int(min_score)},
    }
    if profile_key:
        match_query["candidate_profile_key"] = profile_key

    pipeline: List[Dict[str, Any]] = [
        {"$match": match_query},
        {"$sort": {"job_payload.meta.arbeitgeber": 1, "score": -1}},
        {
            "$project": {
                "_id": 0,
                "score": 1,
                "referenznummer": 1,
                "version": 1,
                "prompt_version": 1,
                "model": 1,
                "candidate_profile_key": 1,
                "arbeitgeber": "$job_payload.meta.arbeitgeber",
                "beruf": "$job_payload.meta.beruf",
                "stellenangebotsTitel": "$job_payload.details.stellenangebotsTitel",
                "stellenangebotsBeschreibung": "$job_payload.details.stellenangebotsBeschreibung",
                "result": 1,
            }
        },
        {
            "$group": {
                "_id": "$arbeitgeber",
                "jobs": {"$push": "$$ROOT"},
                "maxScore": {"$max": "$score"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"maxScore": -1}},
        {"$limit": int(limit_groups)},
        {
            "$project": {
                "_id": 0,
                "arbeitgeber": "$_id",
                "count": 1,
                "maxScore": 1,
                "jobs": {"$slice": ["$jobs", int(limit_jobs_per_group)]},
            }
        },
    ]
    return pipeline


@st.cache_resource(show_spinner=False)
def get_cfg() -> Dict[str, Any]:
    return load_app_config()


@st.cache_data(show_spinner=True, ttl=30)
def load_profile_keys() -> List[str]:
    cfg = get_cfg()
    db = get_database(cfg)
    analysis_collection = resolve_collection_name(cfg, key="analysis", default="analysen")
    col = db[analysis_collection]
    keys = col.distinct("candidate_profile_key")
    values = sorted([k for k in keys if isinstance(k, str) and k.strip()])
    return values


@st.cache_data(show_spinner=True, ttl=30)
def load_grouped(
    min_score: int,
    limit_groups: int,
    limit_jobs_per_group: int,
    profile_key: Optional[str],
) -> List[Dict[str, Any]]:
    cfg = get_cfg()
    db = get_database(cfg)
    analysis_collection = resolve_collection_name(cfg, key="analysis", default="analysen")
    col = db[analysis_collection]
    pipeline = build_pipeline(min_score, limit_groups, limit_jobs_per_group, profile_key)
    return list(col.aggregate(pipeline))


def shorten(s: Any, n: int = 120) -> str:
    if not isinstance(s, str):
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s if len(s) <= n else s[:n] + "…"


def get_selected_row(event: Any) -> Optional[int]:
    try:
        rows = event.selection.rows
    except Exception:
        return None
    if not rows:
        return None
    idx = rows[0]
    if idx is None:
        return None
    try:
        return int(idx)
    except (TypeError, ValueError):
        return None


st.set_page_config(page_title="Analysen nach Arbeitgeber", layout="wide")
st.title("Gruppierung nach Arbeitgeber (Score > N)")

with st.sidebar:
    st.header("Filter")
    min_score = st.number_input("Score >", min_value=0, max_value=1000, value=50, step=1)
    limit_groups = st.number_input("Limit Arbeitgeber", min_value=1, max_value=2000, value=200, step=10)
    limit_jobs = st.number_input("Limit Stellen pro Gruppe", min_value=1, max_value=500, value=50, step=5)
    profile_options = load_profile_keys()
    selected_profile = st.selectbox(
        "Profil (candidate_profile_key)",
        options=["(alle)"] + profile_options,
        index=1 if profile_options else 0,
    )
    selected_profile_key = None if selected_profile == "(alle)" else selected_profile

    if st.button("Aktualisieren"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

pipeline = build_pipeline(min_score, limit_groups, limit_jobs, selected_profile_key)

try:
    groups = load_grouped(min_score, limit_groups, limit_jobs, selected_profile_key)
except Exception as exc:
    st.error(f"Fehler bei der Abfrage: {exc}")
    st.code(traceback.format_exc())
    st.stop()

if not groups:
    st.warning("Keine Daten gefunden.")
    st.stop()

gdf = pd.DataFrame(
    [
        {
            "arbeitgeber": group.get("arbeitgeber"),
            "anzahl": group.get("count"),
            "max_score": group.get("maxScore"),
        }
        for group in groups
    ]
)

st.subheader("Arbeitgeber")
st.caption("Zeile auswählen, um darunter die zugehörigen Stellen anzuzeigen (nach Score sortiert).")

g_event = st.dataframe(
    gdf[["arbeitgeber", "anzahl", "max_score"]],
    width="stretch",
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)
selected_group_index = get_selected_row(g_event)

st.divider()

if selected_group_index is None or selected_group_index < 0 or selected_group_index >= len(groups):
    st.info("Bitte zuerst einen Arbeitgeber in der Tabelle oben auswählen.")
    st.stop()

group = groups[selected_group_index]
jobs = group.get("jobs", [])

st.subheader(
    f"Stellen: {group.get('arbeitgeber')} (Top-Score: {group.get('maxScore')}, Anzahl: {group.get('count')})"
)

jdf = pd.DataFrame(
    [
        {
            "score": job.get("score"),
            "referenznummer": job.get("referenznummer"),
            "beruf": job.get("beruf"),
            "titel": job.get("stellenangebotsTitel"),
            "beschreibung_kurz": shorten(job.get("stellenangebotsBeschreibung"), 160),
        }
        for job in jobs
    ]
)

j_event = st.dataframe(
    jdf[["score", "referenznummer", "beruf", "titel", "beschreibung_kurz"]],
    width="stretch",
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)
selected_job_index = get_selected_row(j_event)

st.divider()
st.subheader("Stellendetails")

if selected_job_index is None or selected_job_index < 0 or selected_job_index >= len(jobs):
    st.info("Bitte zuerst eine Stelle in der Tabelle oben auswählen.")
else:
    job = jobs[selected_job_index]
    st.write(f"**Score:** {job.get('score')}")
    st.write(f"**Referenznummer:** {job.get('referenznummer')}")
    st.write(f"**Arbeitgeber:** {job.get('arbeitgeber')}")
    st.write(f"**Beruf:** {job.get('beruf')}")
    st.write(f"**Titel:** {job.get('stellenangebotsTitel')}")
    with st.expander("Beschreibung (vollständig)", expanded=True):
        st.text(job.get("stellenangebotsBeschreibung") or "")
    with st.expander("JSON (Debug)"):
        st.json(job)

with st.expander("Pipeline (Kontrolle)"):
    st.code(safe_json(pipeline), language="json")
