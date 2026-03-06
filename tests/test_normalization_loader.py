from pathlib import Path

from app.normalization.loader import load_merged_normalization_dicts, load_merged_taxonomy


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
    synonyms_release.write_text('{"version":1,"aliases":{"k8s":"kubernetes"}}' + "\n", encoding="utf-8")
    synonyms_auto.write_text('{"version":1,"aliases":{"kubectl":"kubernetes"}}' + "\n", encoding="utf-8")

    merged = load_merged_normalization_dicts(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=synonyms_auto,
    )
    assert merged["synonyms"]["aliases"]["k8s"] == "kubernetes"
    assert merged["synonyms"]["aliases"]["kubectl"] == "kubernetes"
