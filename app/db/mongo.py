from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.database import Database

COLLECTION_ENV_OVERRIDES: dict[str, str] = {
    "list": "COLLECTION_STELLENANGEBOTE",
    "details": "COLLECTION_JOBDETAILS",
    "analysis": "COLLECTION_ANALYSEN",
    "profiles": "COLLECTION_PROFILES",
    "analysis_prompts": "COLLECTION_ANALYSIS_PROMPTS",
    "normalization_synonyms_state": "COLLECTION_NORMALIZATION_SYNONYMS_STATE",
    "normalization_synonyms_audit": "COLLECTION_NORMALIZATION_SYNONYMS_AUDIT",
}


def _resolve_db_name(cfg: dict[str, Any]) -> str:
    env_name = os.getenv("MONGODB_DB_NAME")
    if env_name:
        return env_name
    return cfg.get("mongodb", {}).get("db_name", "jobcenter")


def get_database(cfg: dict[str, Any]) -> Database:
    """Create MongoDB database handle from env + config."""
    load_dotenv()
    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        raise RuntimeError("MONGODB_URI is not set. Configure it in .env")

    client = MongoClient(mongo_uri)
    return client[_resolve_db_name(cfg)]


def resolve_collection_name(
    cfg: dict[str, Any], key: str, env_var: str | None = None, default: str | None = None
) -> str:
    """Resolve collection name from ENV override, then config, then default."""
    if env_var is None:
        env_var = COLLECTION_ENV_OVERRIDES.get(key)

    if env_var:
        env_value = os.getenv(env_var)
        if env_value:
            return env_value

    collection_name = cfg.get("collections", {}).get(key)
    if collection_name:
        return collection_name

    if default:
        return default
    raise RuntimeError(f"Collection name is not configured for key: {key}")
