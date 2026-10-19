from app.jobcenter import list_query_expansion as expansion


def test_expand_list_query_legacy_mode_keeps_base_params():
    result = expansion.expand_list_query(
        {
            "params": {"wo": "Regensburg", "umkreis": 15, "size": "200"},
        }
    )

    assert result.mode == "legacy"
    assert result.logical_query_mode == "legacy"
    assert result.max_pages_per_physical_query is None
    assert len(result.physical_queries) == 1
    assert result.physical_queries[0].params == {"wo": "Regensburg", "umkreis": 15, "size": "200"}


def test_expand_list_query_uses_explicit_plz_before_other_fields():
    result = expansion.expand_list_query(
        {
            "params": {"wo": "legacy", "umkreis": 15, "size": "200"},
            "search_area": {
                "mode": "plz",
                "plz": "80331 80333",
                "bundesland": "Bayern",
                "city": "München",
                "plz_query_umkreis": 0,
                "max_pages_per_physical_query": 7,
            },
        }
    )

    assert result.mode == "plz"
    assert result.logical_query_mode == "explicit_plz"
    assert result.max_pages_per_physical_query == 7
    assert result.resolved_plz == ["80331", "80333"]
    assert [query.params["wo"] for query in result.physical_queries] == ["80331", "80333"]
    assert all(query.params["umkreis"] == 0 for query in result.physical_queries)


def test_expand_list_query_bundesland_city_resolves_via_plz_module(monkeypatch):
    monkeypatch.setattr(expansion, "resolve_city_to_plz", lambda **kwargs: ["80331", "80333"])

    result = expansion.expand_list_query(
        {
            "params": {"wo": "legacy", "umkreis": 15, "size": "200"},
            "search_area": {
                "mode": "plz",
                "bundesland": "Bayern",
                "city": "München",
                "plz_query_umkreis": 0,
                "max_plz_per_run": 10,
                "max_pages_per_physical_query": 9,
            },
        }
    )

    assert result.logical_query_mode == "bundesland_city"
    assert result.max_pages_per_physical_query == 9
    assert result.resolved_plz == ["80331", "80333"]
    assert result.scope_payload["base_params_without_location"] == {"size": "200"}
    assert result.scope_payload["max_pages_per_physical_query"] == 9


def test_expand_list_query_rejects_too_many_plz(monkeypatch):
    monkeypatch.setattr(expansion, "resolve_bundesland_to_plz", lambda *args, **kwargs: ["1", "2", "3"])

    try:
        expansion.expand_list_query(
            {
                "params": {"size": "200"},
                "search_area": {
                    "mode": "plz",
                    "bundesland": "Bayern",
                    "max_plz_per_run": 2,
                },
            }
        )
    except ValueError as exc:
        assert "exceeds max_plz_per_run" in str(exc)
    else:
        raise AssertionError("Expected ValueError for too many PLZ.")


def test_expand_list_query_requires_search_area_input():
    try:
        expansion.expand_list_query(
            {
                "params": {"size": "200"},
                "search_area": {"mode": "plz"},
            }
        )
    except ValueError as exc:
        assert "requires either explicit plz" in str(exc)
    else:
        raise AssertionError("Expected ValueError for missing PLZ search area.")


def test_expand_list_query_rejects_unknown_mode():
    try:
        expansion.expand_list_query(
            {
                "params": {"size": "200"},
                "search_area": {"mode": "unexpected"},
            }
        )
    except ValueError as exc:
        assert "Unsupported search_area.mode" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported mode.")
