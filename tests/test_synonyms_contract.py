from __future__ import annotations

import json
from pathlib import Path

from tools.taxonomy_transform import CATEGORY_ALLOWED, FACET_ALLOWED, SKILL_CLASS_ALLOWED


def _load_schema(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_synonyms_pending_schema_enums_match_taxonomy_transform():
    schema = _load_schema("conf/normalization/schemas/synonyms_pending_record.schema.json")
    category_enum = set(schema["properties"]["category"]["enum"])
    facet_enum = set(schema["properties"]["facet"]["enum"])
    skill_class_enum = set(schema["properties"]["skill_class"]["enum"])

    assert category_enum == CATEGORY_ALLOWED
    assert facet_enum == FACET_ALLOWED
    assert skill_class_enum == SKILL_CLASS_ALLOWED


def test_synonyms_dictionary_schema_enums_match_taxonomy_transform():
    schema = _load_schema("conf/normalization/schemas/synonyms_dictionary.schema.json")
    item_props = schema["properties"]["entries"]["items"]["properties"]
    category_enum = set(item_props["category"]["enum"])
    facet_enum = set(item_props["facet"]["enum"])
    skill_class_enum = set(item_props["skill_class"]["enum"])

    assert category_enum == CATEGORY_ALLOWED
    assert facet_enum == FACET_ALLOWED
    assert skill_class_enum == SKILL_CLASS_ALLOWED
