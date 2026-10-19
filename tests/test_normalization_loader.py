from pathlib import Path

from app.normalization.loader import (
    build_runtime_taxonomy,
    load_merged_normalization_dicts,
    load_merged_taxonomy,
    load_runtime_taxonomy,
)


def test_load_merged_taxonomy_auto_overrides_alias_and_unions_categories(tmp_path: Path):
    release = tmp_path / "taxonomy.release.json"
    auto = tmp_path / "taxonomy.auto.json"
    release.write_text(
        """
{
  "version": 1,
  "aliases": {
    "k8s": "Kubernetes",
    "js": "JavaScript"
  },
  "categories": {
    "skills": ["CI/CD"],
    "tools": ["Jira"],
    "stack": ["Docker"]
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    auto.write_text(
        """
{
  "version": 1,
  "aliases": {
    "k8s": "K8s Platform",
    "py": "Python"
  },
  "categories": {
    "skills": ["Monitoring"],
    "tools": ["Confluence"],
    "stack": ["Kubernetes"]
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    merged = load_merged_taxonomy(release_path=release, auto_path=auto)
    assert merged["aliases"]["k8s"] == "K8s Platform"
    assert merged["aliases"]["js"] == "JavaScript"
    assert merged["aliases"]["py"] == "Python"
    assert merged["categories"]["skills"] == {"CI/CD", "Monitoring"}
    assert merged["categories"]["tools"] == {"Jira", "Confluence"}
    assert merged["categories"]["stack"] == {"Docker", "Kubernetes"}


def test_load_merged_normalization_dicts_merges_synonyms_layers(tmp_path: Path):
    taxonomy_release = tmp_path / "taxonomy.release.json"
    taxonomy_auto = tmp_path / "taxonomy.auto.json"
    synonyms_release = tmp_path / "synonyms.release.json"
    synonyms_auto = tmp_path / "synonyms.auto.json"

    taxonomy_release.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    taxonomy_auto.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    synonyms_release.write_text('{"version":1,"aliases":{"k8s":"Kubernetes"}}' + "\n", encoding="utf-8")
    synonyms_auto.write_text('{"version":1,"aliases":{"kubectl":"Kubernetes"}}' + "\n", encoding="utf-8")

    merged = load_merged_normalization_dicts(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=synonyms_auto,
    )
    assert merged["synonyms"]["aliases"]["k8s"] == "Kubernetes"
    assert merged["synonyms"]["aliases"]["kubectl"] == "Kubernetes"


def test_load_runtime_taxonomy_applies_entries_primary_synonyms(tmp_path: Path):
    taxonomy_release = tmp_path / "taxonomy.release.json"
    taxonomy_auto = tmp_path / "taxonomy.auto.json"
    synonyms_release = tmp_path / "synonyms.release.json"
    synonyms_auto = tmp_path / "synonyms.auto.json"

    taxonomy_release.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    taxonomy_auto.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    synonyms_release.write_text('{"version":1,"entries":[],"aliases":{}}' + "\n", encoding="utf-8")
    synonyms_auto.write_text(
        (
            '{"version":1,"entries":['
            '{"alias":"k8s","canonical":"Kubernetes","domain":"it_software_daten","category":"stack","facet":"technology_stack","skill_class":"not_applicable","domain_scope":"global_safe","facet_scope":"specific"},'
            '{"alias":"jira","canonical":"Jira","domain":"sonstiges","category":"tools","facet":"tool_or_platform","skill_class":"not_applicable","domain_scope":"specific","facet_scope":"specific"}'
            ']}'
            + "\n"
        ),
        encoding="utf-8",
    )

    runtime_taxonomy = load_runtime_taxonomy(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=synonyms_auto,
    )

    assert runtime_taxonomy["aliases"]["k8s"] == "Kubernetes"
    assert "jira" not in runtime_taxonomy["aliases"]
    assert runtime_taxonomy["aliases_global_safe"]["k8s"] == "Kubernetes"
    assert "jira" not in runtime_taxonomy["aliases_global_safe"]
    assert runtime_taxonomy["categories"]["stack"] == {"Kubernetes"}
    assert runtime_taxonomy["categories"]["tools"] == {"Jira"}
    assert runtime_taxonomy["entries"][0]["domain_scope"] == "global_safe"
    assert runtime_taxonomy["entries"][0]["facet_scope"] == "specific"


def test_build_runtime_taxonomy_overlays_synonyms_on_taxonomy():
    taxonomy = {
        "aliases": {"js": "JavaScript"},
        "categories": {
            "skills": {"CI/CD"},
            "tools": set(),
            "stack": set(),
        },
    }
    synonyms = {
        "aliases": {"k8s": "kubernetes"},
        "categories": {
            "skills": set(),
            "tools": set(),
            "stack": {"Kubernetes"},
        },
    }
    runtime_taxonomy = build_runtime_taxonomy(taxonomy=taxonomy, synonyms=synonyms)
    assert runtime_taxonomy["aliases"]["js"] == "JavaScript"
    assert runtime_taxonomy["aliases"]["k8s"] == "kubernetes"
    assert runtime_taxonomy["categories"]["skills"] == {"CI/CD"}
    assert runtime_taxonomy["categories"]["stack"] == {"Kubernetes"}


def test_load_runtime_taxonomy_marks_ambiguous_aliases_and_keeps_them_out_of_global_aliases(tmp_path: Path):
    taxonomy_release = tmp_path / "taxonomy.release.json"
    taxonomy_auto = tmp_path / "taxonomy.auto.json"
    synonyms_release = tmp_path / "synonyms.release.json"
    synonyms_auto = tmp_path / "synonyms.auto.json"

    taxonomy_release.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    taxonomy_auto.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    synonyms_release.write_text('{"version":1,"entries":[],"aliases":{}}' + "\n", encoding="utf-8")
    synonyms_auto.write_text(
        (
            '{"version":1,"entries":['
            '{"alias":"ROM","canonical":"Read-Only Memory","domain":"it_software_daten","category":"skills","facet":"technology_stack","skill_class":"professional"},'
            '{"alias":"ROM","canonical":"Range of Motion","domain":"gesundheitswesen_pflege_medizin","category":"skills","facet":"skill_requirement","skill_class":"professional"}'
            ']}'
            + "\n"
        ),
        encoding="utf-8",
    )

    runtime_taxonomy = load_runtime_taxonomy(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=synonyms_auto,
    )

    assert "rom" not in runtime_taxonomy["aliases"]
    assert "rom" not in runtime_taxonomy["aliases_global_safe"]
    assert "rom" in runtime_taxonomy["aliases_by_domain"]
    assert runtime_taxonomy["aliases_by_domain"]["rom"]["it_software_daten"] == "Read-Only Memory"
    assert runtime_taxonomy["aliases_by_domain"]["rom"]["gesundheitswesen_pflege_medizin"] == "Range of Motion"
    assert "rom" in runtime_taxonomy["ambiguous_aliases"]


def test_load_runtime_taxonomy_keeps_specific_entries_out_of_global_fallback(tmp_path: Path):
    taxonomy_release = tmp_path / "taxonomy.release.json"
    taxonomy_auto = tmp_path / "taxonomy.auto.json"
    synonyms_release = tmp_path / "synonyms.release.json"
    synonyms_auto = tmp_path / "synonyms.auto.json"

    taxonomy_release.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    taxonomy_auto.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    synonyms_release.write_text('{"version":1,"entries":[],"aliases":{}}' + "\n", encoding="utf-8")
    synonyms_auto.write_text(
        (
            '{"version":1,"entries":['
            '{"alias":"Ausbildung","canonical":"Ausbildung","domain":"handwerk_bau_ausbau","category":"skills","facet":"work_condition","skill_class":"non_skill_conditions","domain_scope":"specific","facet_scope":"specific"}'
            ']}'
            + "\n"
        ),
        encoding="utf-8",
    )

    runtime_taxonomy = load_runtime_taxonomy(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=synonyms_auto,
    )

    assert "ausbildung" not in runtime_taxonomy["aliases_global_safe"]
    assert runtime_taxonomy["aliases_by_domain"]["ausbildung"]["handwerk_bau_ausbau"] == "Ausbildung"


def test_load_runtime_taxonomy_excludes_unstable_facet_from_domain_facet_index(tmp_path: Path):
    taxonomy_release = tmp_path / "taxonomy.release.json"
    taxonomy_auto = tmp_path / "taxonomy.auto.json"
    synonyms_release = tmp_path / "synonyms.release.json"
    synonyms_auto = tmp_path / "synonyms.auto.json"

    taxonomy_release.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    taxonomy_auto.write_text(
        '{"version":1,"aliases":{},"categories":{"skills":[],"tools":[],"stack":[]}}' + "\n",
        encoding="utf-8",
    )
    synonyms_release.write_text('{"version":1,"entries":[],"aliases":{}}' + "\n", encoding="utf-8")
    synonyms_auto.write_text(
        (
            '{"version":1,"entries":['
            '{"alias":"Ausbildung","canonical":"Ausbildung","domain":"handwerk_bau_ausbau","category":"skills","facet":"work_condition","skill_class":"non_skill_conditions","domain_scope":"cross_domain_candidate","facet_scope":"unstable"}'
            ']}'
            + "\n"
        ),
        encoding="utf-8",
    )

    runtime_taxonomy = load_runtime_taxonomy(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=synonyms_auto,
    )

    assert "ausbildung" not in runtime_taxonomy["aliases_by_domain_facet"]
    assert runtime_taxonomy["aliases_by_domain"]["ausbildung"]["handwerk_bau_ausbau"] == "Ausbildung"
