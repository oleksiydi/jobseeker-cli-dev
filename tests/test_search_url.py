"""URL identity, lossless opaque filter handling and adaptive geography."""
import pytest
import json

from app.jobcenter.adaptive_search import AdaptiveSearch
from app.jobcenter.search_url import (
    advertised_total, configured_search_url, parse_search_url, result_window_limit,
)

BASE = 'https://www.arbeitsagentur.de/jobsuche/suche?'


def test_identity_normalizes_encoding_order_and_browser_pagination():
    first = parse_search_url(BASE + 'arbeitsort=T%C3%BCbingen&was=Data+Engineer&page=4')
    second = parse_search_url(BASE + 'was=Data%20Engineer&suchbereich=jobs&arbeitsort=Tübingen&size=10')
    assert first.search_id == second.search_id
    assert first.canonical == second.canonical
    assert first.original != second.original
    assert first.params['arbeitsort'] == 'Tübingen'
    assert first.params['size'] == '200'
    assert parse_search_url(BASE + 'arbeitsort=Berlin').search_id != first.search_id


def test_filters_are_not_interpreted_and_old_config_filters_do_not_leak():
    url = BASE + ('homeoffice=prozentual_45;nv_true&arbeitsort=Passau;T%C3%BCbingen'
                  '&arbeitszeit=tz&branche=11;29&befristung=1'
                  '&eintrittsdatum=26_12_01-27_01_01&future=x%2By&future=z&pavsuche=true')
    result = parse_search_url(url, {'wo': 'Regensburg', 'umkreis': 15,
                                     'pav': False, 'angebotsart': 1, 'size': '100'})
    assert result.original == url
    assert result.params == {
        'size': '100', 'facetten': 'false', 'suchbereich': 'jobs',
        'homeoffice': 'prozentual_45;nv_true', 'arbeitsort': 'Passau;Tübingen',
        'arbeitszeit': 'tz', 'branche': '11;29', 'befristung': '1',
        'eintrittsdatum': '26_12_01-27_01_01', 'future': ['x+y', 'z'], 'pavsuche': 'true',
    }


@pytest.mark.parametrize('query', [
    'https://evil.example/jobsuche/suche?wo=Berlin',
    'https://www.arbeitsagentur.de.evil.example/jobsuche/suche?wo=Berlin',
    'https://user:EXAMPLE_PASSWORD@www.arbeitsagentur.de/jobsuche/suche?wo=Berlin',
    BASE + 'token=EXAMPLE_TOKEN', BASE + 'API_KEY=EXAMPLE_TOKEN',
    BASE + 'password=EXAMPLE_PASSWORD', BASE + 'wo=%ZZ', BASE + 'wo=%FF',
    BASE + 'wo=Berlin#token=EXAMPLE_TOKEN', BASE + 'umkreis=-1',
    BASE + 'wo=Berlin&wo=Passau', BASE + 'umkreis=1.5',
])
def test_invalid_or_private_url_is_rejected_without_echoing_values(query):
    with pytest.raises(ValueError) as error:
        parse_search_url(query)
    assert 'EXAMPLE_' not in str(error.value)


def test_environment_override_and_empty_fallback(monkeypatch):
    config = {'search_url': BASE + 'wo=Berlin'}
    monkeypatch.setenv('JOBCENTER_SEARCH_URL', '  ')
    assert configured_search_url(config) == config['search_url']
    monkeypatch.setenv('JOBCENTER_SEARCH_URL', BASE + 'wo=Passau')
    assert configured_search_url(config) == BASE + 'wo=Passau'
    monkeypatch.delenv('JOBCENTER_SEARCH_URL')
    assert configured_search_url({}) is None


@pytest.mark.parametrize('value', [0, -1, '10000', True, None])
def test_window_requires_positive_integer(value):
    with pytest.raises(ValueError, match='positive integer'):
        result_window_limit({'result_window_limit': value})


def test_total_counts_require_nonnegative_integer():
    assert result_window_limit({}) == 10_000
    assert advertised_total({'maxErgebnisse': 10_000}) == 10_000
    assert advertised_total({'maxErgebnisse': 0}) == 0
    assert advertised_total({'maxErgebnisse': True}) is None


def test_sharding_guard_keeps_explicit_incompleteness(monkeypatch):
    monkeypatch.setattr('app.jobcenter.adaptive_search.resolve_bundesland_to_plz',
                        lambda *_: ['80331', '80333'])
    plan = AdaptiveSearch(parse_search_url(BASE + 'wo=Bayern'),
                          {'search_area': {'max_plz_per_run': 1}}, 3)
    query = plan.pending.popleft()
    assert not plan.redirect(query, {'maxErgebnisse': 3, 'woOutput': {'suchmodus': 'BUNDESLANDSUCHE'}})
    plan.record(query, total=3, received=3, status='window_limit')
    provenance = plan.provenance(True)
    assert not provenance['coverage_complete']
    assert any(w['code'] == 'sharding_unavailable' for w in provenance['warnings'])


@pytest.mark.parametrize('language', ['ru', 'de'])
def test_preview_tool_uses_url_and_does_not_resolve_geography(monkeypatch, capsys, language):
    import sys
    from tools import preview_list_query_expansion as preview

    monkeypatch.setattr(sys, 'argv', ['preview_list_query_expansion.py', '--output-language', language])
    monkeypatch.setenv('JOBCENTER_SEARCH_URL', BASE + 'wo=Deutschland&homeoffice=prozentual_5')
    monkeypatch.setattr(preview, 'load_app_config', lambda: {
        'jobcenter': {'list_api': {'params': {'wo': 'ignored'}}}})
    assert preview.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output['mode'] == 'url' and output['dry_run']
    assert output['params']['wo'] == 'Deutschland'
    assert any(w['code'] == 'homeoffice_passthrough' for w in output['warnings'])
    assert output['output_language'] == language
    if language == 'de':
        assert 'API-Antwort' in output['note']
        assert 'unverändert' in output['warnings'][0]['message']


@pytest.mark.parametrize('percent', [0, 1, 5, 45, 100])
def test_literal_homeoffice_does_not_rewrite_api_values(percent):
    value = f'prozentual_{percent};nv_true'
    search = parse_search_url(BASE + 'homeoffice=' + value)
    assert search.params['homeoffice'] == value


def test_large_geo_report_is_readable_without_truncating_stored_provenance():
    from app.jobcenter.search_provenance import attach_search_scope, coverage_rows

    effective = [{'wo': f'{i:05}', 'umkreis': 0} for i in range(100)]
    coverage = {'search_id': 'a' * 64, 'search_url': BASE + 'wo=Deutschland',
                'original_geography': {'wo': 'Deutschland'}, 'effective_geography': effective,
                'coverage_complete': True, 'geography_narrowed': False, 'warnings': []}
    scope = {}
    attach_search_scope(scope, {'search_provenance': coverage})
    text = json.dumps(coverage_rows(scope), ensure_ascii=False)
    assert '100 запросов' in text
    assert 'полный список' in text
    assert len(scope['collection_coverage']['effective_geography']) == 100
    assert scope['collection_coverage']['effective_geography'][-1]['wo'] == '00099'
    assert len(text) < 2000
