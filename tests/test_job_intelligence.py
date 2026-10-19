from copy import deepcopy

from app.matching.intelligence import IntelligenceMatcher, RankingAccumulator, adjacent_roles, profession_ranking


class FakeEvidenceCache:
    """Small derived-cache fake that records writes and returns independent values."""

    def __init__(self):
        self.values = {}
        self.writes = []

    def get(self, key):
        value = self.values.get(key)
        return deepcopy(value)

    def put(self, key, payload, kind="evidence"):
        self.writes.append((key, kind))
        self.values.setdefault(key, deepcopy(payload))


def _profile(*, skills=None, constraints=None, profile_hash="profile-v1"):
    return {
        "key": "candidate",
        "profile_hash": profile_hash,
        "roles": ["Linux Administrator"],
        "target_roles": ["Linux Administrator"],
        "skills": skills if skills is not None else [
            {
                "label": "Linux",
                "status": "project_evidence",
                "source_path": "experience_projects[0].tech_stack",
                "evidence_quote": "Maintained Linux servers",
                "last_used": "2013–2021",
            }
        ],
        "languages": {},
        "licenses": [],
        "constraints": constraints if constraints is not None else {"on_call": False},
    }


def _catalogue(*, version="catalogue-v1"):
    return [
        {"id": "skill:linux", "label": "Linux", "aliases": [], "source": "test", "version": version, "kind": "skill"},
        {"id": "skill:python", "label": "Python", "aliases": [], "source": "test", "version": version, "kind": "skill"},
        {"id": "skill:docker", "label": "Docker", "aliases": [], "source": "test", "version": version, "kind": "skill"},
        {"id": "occupation:admin", "label": "Linux Administrator", "aliases": [], "source": "test", "version": version, "kind": "occupation"},
    ]


def _settings():
    return {
        "skill_evidence_target": 4,
        "weights": {"role": 0.35, "skills": 0.45, "semantic": 0.20},
        "required_gap_penalty": 0.15,
        "max_required_gap_penalty": 0.35,
        "direct_role_threshold": 0.35,
    }


def _job(ref, description, *, role="Linux Administrator", location="Berlin"):
    return {
        "ref": ref,
        "title": role,
        "role": role,
        "location": location,
        "url": f"https://jobs.example/{ref}",
        "description": description,
        "source_input_hash": f"source-{ref}-1",
    }


def _matcher(profile=None, terms=None, cache=None, settings=None):
    cache = cache or FakeEvidenceCache()
    matcher = IntelligenceMatcher(profile or _profile(), terms or _catalogue(), cache, settings or _settings())
    return matcher, cache


def test_hard_constraint_conflict_ranks_below_compatible_even_with_perfect_semantic_score():
    matcher, _ = _matcher()
    jobs = [
        _job("conflict", "Linux erforderlich. Rufbereitschaft erforderlich."),
        _job("compatible", "Linux erforderlich."),
    ]

    ranked = matcher.rank(jobs, semantic={"conflict": 1.0, "compatible": 0.0})

    assert [item["ref"] for item in ranked] == ["compatible", "conflict"]
    assert ranked[0]["status"] != "conflict"
    assert ranked[1]["status"] == "conflict"
    assert ranked[1]["constraints"][0]["status"] == "conflict"


def test_required_preferred_negated_and_unknown_requirements_stay_distinct():
    matcher, _ = _matcher()
    job = _job(
        "modalities",
        "Linux zwingend erforderlich. Python wünschenswert. Docker nicht erforderlich. QuuxNet zwingend.",
    )

    result = matcher.rank([job])[0]
    evidence_by_label = {item["label"]: item for item in result["evidence"]}
    matched = {item["label"] for item in result["matched_skills"]}
    preferred = {item["label"] for item in result["unconfirmed_skills"]}

    assert evidence_by_label["Linux"]["modality"] == "required"
    assert evidence_by_label["Python"]["modality"] == "preferred"
    assert evidence_by_label["Docker"]["modality"] == "negated"
    assert "Linux" in matched
    assert "Python" in preferred
    assert "Docker" not in matched | preferred | {item["label"] for item in result["missing_required"]}
    assert any("QuuxNet zwingend" in item["quote"] for item in result["unknowns"])


def test_unstated_qualification_remains_unknown_not_met():
    matcher, _ = _matcher(profile=_profile(skills=[]))
    result = matcher.rank([_job("qualification", "IHK-Abschluss erforderlich.")])[0]

    qualification = next(item for item in result["constraints"] if item["kind"] == "certificate")
    assert qualification["profile_value"] is None
    assert qualification["status"] == "unknown"


def test_project_evidence_period_survives_into_ranked_match():
    matcher, _ = _matcher()
    result = matcher.rank([_job("project-fact", "Linux erforderlich.")])[0]

    evidence = result["matched_skills"][0]["profile_evidence"][0]
    assert evidence["status"] == "project_evidence"
    assert evidence["last_used"] == "2013–2021"
    assert evidence["evidence_quote"] == "Maintained Linux servers"


def test_adjacent_role_uses_shared_skill_evidence_without_constraint_conflict():
    matcher, _ = _matcher()
    job = _job("adjacent", "Linux is part of the current platform.", role="Warehouse Coordinator")

    ranked = matcher.rank([job])
    adjacent = adjacent_roles(ranked)

    assert ranked[0]["matched_skills"][0]["job_evidence"][0]["quote"] == "Linux"
    assert ranked[0]["status"] != "conflict"
    assert adjacent
    assert adjacent[0]["role"] == "Warehouse Coordinator"
    assert "Linux" in adjacent[0]["transferable_skills"]
    assert "conflict" not in adjacent[0]
    assert adjacent[0]['evidence'][0]['job_evidence'][0]['quote'] == 'Linux'
    assert adjacent[0]['evidence'][0]['profile_evidence'][0]['last_used'] == '2013–2021'


def test_same_observed_occupation_is_not_both_direct_and_adjacent():
    matcher, _ = _matcher()
    jobs = [
        _job('direct', 'Linux erforderlich.'),
        {**_job('other-title', 'Linux erforderlich.'), 'title': 'Platform Specialist'},
    ]
    ranked = matcher.rank(jobs)
    ranked[1]['role_type'] = 'adjacent_candidate'
    assert adjacent_roles(ranked) == []


def test_repeat_run_reuses_evidence_cache_and_inputs_or_rules_invalidate_it():
    cache = FakeEvidenceCache()
    profile = _profile()
    job = _job("cache", "Linux erforderlich.")
    first_matcher, _ = _matcher(profile=profile, cache=cache)
    first = first_matcher.rank([job])
    writes_after_first = len(cache.writes)
    repeated = first_matcher.rank([job])

    assert repeated[0]["evidence"] == first[0]["evidence"]
    assert len(cache.writes) == writes_after_first

    changed_job = {**job, "description": "Linux und Python erforderlich."}
    first_matcher.rank([changed_job])
    assert len(cache.writes) == writes_after_first + 1

    changed_profile = _profile(
        skills=[*profile["skills"], {"label": "Python", "status": "declared", "source_path": "skills[1]", "evidence_quote": "Python"}],
        profile_hash="profile-v2",
    )
    profile_matcher, _ = _matcher(profile=changed_profile, cache=cache)
    profile_matcher.rank([job])
    assert len(cache.writes) == writes_after_first + 2

    changed_rules, _ = _matcher(profile=profile, terms=_catalogue(version="catalogue-v2"), cache=cache)
    changed_rules.rank([job])
    assert len(cache.writes) == writes_after_first + 3


def test_profession_ranking_groups_observations_and_excludes_conflicts():
    matcher, _ = _matcher()
    ranked = matcher.rank([
        _job('first', 'Linux erforderlich.'),
        _job('second', 'Linux erforderlich.'),
        _job('conflict', 'Linux erforderlich. Rufbereitschaft erforderlich.', role='Platform Engineer'),
    ])
    roles = profession_ranking(ranked)
    assert len(roles) == 1
    assert roles[0]['role'] == 'Linux Administrator'
    assert roles[0]['vacancies_observed'] == 2
    assert roles[0]['best_ref'] == 'first'


def test_model_identity_changes_decision_hash_without_reextracting_evidence():
    matcher, cache = _matcher()
    job = _job('model-change', 'Linux erforderlich.')
    first = matcher.rank([job], {'model-change': .5}, model_identity={'revision': 'one'})[0]
    writes = len(cache.writes)
    second = matcher.rank([job], {'model-change': .5}, model_identity={'revision': 'two'})[0]
    assert first['decision_input_hash'] != second['decision_input_hash']
    assert len(cache.writes) == writes


def test_csharp_does_not_become_cpp_via_profile_literal_identity():
    profile = _profile(skills=[{'label': 'C#', 'status': 'declared',
                               'source_path': 'skills[0]', 'evidence_quote': 'C#'}])
    terms = [{'id': 'cpp', 'label': 'C++', 'aliases': [], 'source': 'test',
              'version': 'one', 'kind': 'skill'}]
    matcher, _ = _matcher(profile=profile, terms=terms)
    result = matcher.rank([_job('cpp', 'C++ erforderlich.')])[0]
    assert result['matched_skills'] == []
    assert [s['label'] for s in result['missing_required']] == ['C++']


def test_shared_skill_without_interpretable_modality_still_requires_review():
    matcher, _ = _matcher()
    result = matcher.rank([_job('unknown-modality', 'The team works with Linux.')])[0]
    assert result['matched_skills']
    assert result['requirement_modality_unknown'] is True
    assert result['status'] == 'needs_review'


def test_batched_global_ranking_keeps_best_jobs_role_counts_and_transfer_evidence():
    matcher, _ = _matcher()
    jobs = [_job('direct-1', 'Linux erforderlich.'),
            _job('direct-2', 'Linux erforderlich.'),
            _job('adjacent', 'Linux erforderlich.', role='Warehouse Coordinator'),
            _job('conflict', 'Linux erforderlich. Rufbereitschaft erforderlich.')]
    ranked = matcher.rank(jobs)
    accumulated = RankingAccumulator(limit=2)
    for job in reversed(ranked):
        accumulated.add([job])
    assert [j['ref'] for j in accumulated.results] == [j['ref'] for j in ranked[:2]]
    assert accumulated.professions() == profession_ranking(ranked)
    assert accumulated.adjacent() == adjacent_roles(ranked)
    assert accumulated.count == 4
    assert all('description' not in j and 'evidence' not in j for j in accumulated.results)
