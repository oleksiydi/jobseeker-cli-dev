"""Write compact, privacy-conscious vacancy intelligence reports."""

from __future__ import annotations

import csv
import html
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.core.i18n import localized_prefix, report_language, tr, translated
from app.matching.output_localization import localize_report, localize_result


MAX_RESULTS = 2000
_RESULT_FIELDS = (
    "ref", "title", "role", "location", "url", "score", "status", "role_type",
    "scores", "matched_skills", "missing_required", "unconfirmed_skills", "constraints",
    "unknowns", "occupation_refs", "source_input_hash", "profile_input_hash", "input_hash",
    "details_status", "decision_input_hash", "title_source", "requirement_modality_unknown",
    "related_skills", "relation_context_skills", "reference_relations_status",
)
_SENSITIVE_KEYS = re.compile(r"(?:raw.?profile|full.?cv|secret|api.?key|password|authorization)", re.I)
_ESCO_ATTRIBUTION = "This service uses the ESCO classification of the European Commission."
_CSV_COLUMNS = ('ref', 'title', 'role', 'location', 'url', 'score', 'status',
                'matched_skills', 'missing_required', 'unknowns')
_CSV_AUDIT_COLUMNS = ('role_type', 'details_status', 'source_input_hash', 'decision_input_hash')
_STATUS_RU = {
    "conflict": "обнаружено противоречие",
    "needs_review": "нужно проверить вручную",
    "no_known_conflict": "явных противоречий не найдено",
}


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return value


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Preserve report metadata and remove fields that can contain secrets or a full CV."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item, depth=depth + 1)
            for key, item in value.items()
            if not _SENSITIVE_KEYS.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value]
    return str(value)


def _pick(item: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {key: _safe_value(item[key]) for key in fields if key in item}


@report_language
def _report_json(report: dict[str, Any]) -> dict[str, Any]:
    profile = _pick(report.get("profile"), ("key", "profile_hash", "source", "search_text_length"))
    model = _pick(
        report.get("model"),
        (
            "provider", "model", "name", "revision", "version", "files_sha256", "runtime",
            "pooling", "prefixes", "dimensions", "input",
        ),
    ) if isinstance(report.get("model"), dict) else None
    references = []
    for manifest in report.get("references", []) if isinstance(report.get("references"), list) else []:
        if not isinstance(manifest, dict):
            continue
        reference = _safe_value(manifest)
        if isinstance(reference, dict):
            references.append(reference)

    results = []
    for result in report.get("results", []) if isinstance(report.get("results"), list) else []:
        if not isinstance(result, dict):
            continue
        row = _pick(result, _RESULT_FIELDS)
        row["url"] = _safe_url(row.get("url"))
        for list_field, child_fields in (
            ("matched_skills", ("label", "profile_evidence", "job_evidence")),
            ("missing_required", ("label", "job_evidence")),
            ("unconfirmed_skills", ("label", "job_evidence")),
            ("related_skills", ("label", "candidate_concept", "target_concept", "profile_evidence",
                                "job_evidence", "relation_path", "reason", "letter_eligible", "relation_explanation")),
            ("relation_context_skills", ("label", "candidate_concept", "target_concept", "profile_evidence",
                                        "job_evidence", "relation_path", "reason", "letter_eligible", "relation_explanation")),
            ("constraints", ("kind", "required", "profile_value", "status", "quote", "source", "text_span")),
            ("unknowns", ("quote", "reason", "source", "span", "text_span", "candidate_id", "candidateid")),
        ):
            children = row.get(list_field)
            if isinstance(children, list):
                projected = []
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    clean_child = _pick(child, child_fields)
                    if "profile_evidence" in clean_child and isinstance(clean_child["profile_evidence"], list):
                        clean_child["profile_evidence"] = [
                            _pick(evidence, ("source_path", "evidence_quote", "status", "last_used"))
                            for evidence in clean_child["profile_evidence"] if isinstance(evidence, dict)
                        ]
                    if "job_evidence" in clean_child and isinstance(clean_child["job_evidence"], list):
                        clean_child["job_evidence"] = [
                            _pick(
                                evidence,
                                (
                                    "quote", "context_quote", "source", "text_span", "modality", "taxonomy_source",
                                    "concept_id", "taxonomy_version", "model_version", "matcher", "confidence",
                                    "context_role", "needs_review",
                                ),
                            )
                            for evidence in clean_child["job_evidence"] if isinstance(evidence, dict)
                        ]
                    projected.append(clean_child)
                row[list_field] = projected
        results.append(row)

    adjacent = []
    for item in report.get("adjacent_roles", []) if isinstance(report.get("adjacent_roles"), list) else []:
        if not isinstance(item, dict):
            continue
        role = _pick(item, ("role", "transferable_skills", "evidence", "missing_required", "unknown_requirements", "ref", "url", "reason"))
        role["url"] = _safe_url(role.get("url"))
        adjacent.append(role)

    llm = report.get("llm") if isinstance(report.get("llm"), dict) else {}
    llm_usage = {key: int(llm.get(key, 0) or 0) for key in ("calls", "input_tokens", "output_tokens")}
    return localize_report({
        "schema_version": _safe_value(report.get("schema_version")),
        "engine_version": _safe_value(report.get("engine_version")),
        "profile": profile,
        "scope": _safe_value(report.get("scope", {})),
        "configuration": _pick(report.get("configuration"), (
            "schema_version", "weights", "score_weights", "ranking_weights", "candidate_limit", "limit",
            "vector_collection", "embedding_batch_size", "embedding_threads", "skill_evidence_target",
            "required_gap_penalty", "max_required_gap_penalty", "direct_role_threshold", "processing_batch_size",
            "document_export", "reference_relations", "output_language",
        )),
        "references": references,
        "reference_relations": _safe_value(report.get('reference_relations', {'status': 'disabled'})),
        "model": model,
        "llm": llm_usage,
        "metrics": _safe_value(report.get("metrics", {})),
        "results": results,
        "adjacent_roles": adjacent,
        "profession_ranking": [
            _pick(item, ('role', 'score', 'best_ref', 'url', 'status', 'vacancies_observed',
                         'role_type', 'transferable_skills'))
            for item in report.get('profession_ranking', []) if isinstance(item, dict)
        ],
    })


def _plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _md(value: Any) -> str:
    text = " ".join(_plain(value).split())
    text = html.escape(text, quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def _reason(value: str) -> str:
    if value.startswith('Ambiguous exact-span match'):
        return tr('Фраза допускает несколько толкований; смысл нужно уточнить')
    return {
        'Explicit requirement outside recognized vocabulary': tr(
            'Требование не удалось надёжно сопоставить с профилем',
        ),
        'Details are not available yet; requirements cannot be checked': tr(
            'Детали вакансии ещё не получены; требования проверить нельзя',
        ),
    }.get(value, value)


@report_language
def _markdown(report: dict[str, Any]) -> str:
    report = localize_report(report)
    from app.jobcenter.search_provenance import coverage_rows
    scope = report.get("scope") if isinstance(report.get("scope"), dict) else {}
    lines = [
        tr('# Обзор вакансий'),
        "",
        tr(
            'Оценка показывает относительное соответствие профиля описанию вакансии. Это не '
            'вероятность найма и не гарантия результата.',
        ),
    ]
    region = scope.get("region") or scope.get("location") or scope.get("locations")
    if region:
        lines.extend(["", tr('Регион запроса источника: {p0}.', p0=_md(region))])
    evaluated = report.get('metrics', {}).get('candidates_evaluated')
    if evaluated is not None:
        lines.extend(["", tr(
            'Оценено описаний: {p0}. Это рейтинг внутри выбранного источника, охват рынка не '
            'гарантирован.',
            p0=evaluated,
        )])
    if scope.get('scan_mode') == 'all':
        lines.extend(['', tr(
            'Обработан весь выбранный источник без предварительного отбора по названиям ролей.',
        ),
                      tr(
                          'Источник: {p0}. В этом документе показаны лучшие результаты.',
                          p0=_md(scope.get('vacancy_scope')),
                      )])
        if scope.get('all_vacancies_csv'):
            lines.append(tr(
                'Результаты по каждой обработанной вакансии: `{p0}`.',
                p0=scope['all_vacancies_csv'],
            ))
    if scope.get('source') == 'file':
        lines.extend(["", tr('Использован сохранённый набор. Текущая доступность вакансий не проверена.')])
    elif scope.get('source') == 'mongo':
        lines.extend(["", tr('Источник — MongoDB. Список и детали могут обновляться параллельно.')])
        lines.append(tr('Расстояние и все возможные рабочие локации отдельно не проверены.'))
        if not scope.get('list_run_id'):
            if scope.get('scan_mode') == 'all':
                lines.append(tr('Выбранный источник не ограничен географией последнего сбора списка.'))
            else:
                lines.append(tr('Завершённый сбор списка не найден: география этой выборки не подтверждена.'))
    if scope.get('invalid_records'):
        lines.append(tr('Записей без пригодных исходных полей пропущено: {p0}.', p0=scope['invalid_records']))
    coverage = coverage_rows(scope)
    if coverage:
        lines.extend(['', tr('## Поиск и ограничения сбора'), ''])
        lines.extend(f'- **{_md(label)}:** {_md(value)}' for label, value in coverage)
    professions = report.get('profession_ranking', [])
    if professions:
        lines.extend(["", tr('## Профессии с совпадениями в выбранном наборе'),
                      "", tr(
                          'Порядок задан лучшей наблюдаемой вакансией. Совпадения не подтверждают '
                          'квалификацию для всей профессии.',
                      ), ""])
        for index, item in enumerate(professions, 1):
            lines.append(tr(
                '{p0}. **{p1}** — {p2}; вакансий в наборе: {p3}.',
                p0=index,
                p1=_md(item['role']),
                p2=_md(item['score']),
                p3=item['vacancies_observed'],
            ))
    lines.extend(["", tr('## Вакансии')])
    results = report.get("results", [])
    if not results:
        lines.extend(["", tr('Подходящих описаний в выбранном охвате не найдено.')])
    for result in results:
        title = _md(result.get("title") or result.get("role") or tr('Вакансия'))
        lines.extend(["", f"### {title}"])
        if result.get('title_source') == 'occupation_fallback':
            lines.append(tr('Заголовок в источнике отсутствует; показано название профессии.'))
        location = result.get("location")
        if location:
            lines.append(tr('Регион: {p0}.', p0=_md(location)))
        status = result.get("status")
        status_text = translated(_STATUS_RU).get(status, status or tr('не указан'))
        lines.append(tr(
            'Относительная оценка: {p0}; статус: {p1}.',
            p0=_md(result.get('score', tr('нет оценки'))),
            p1=_md(status_text),
        ))
        if result.get('role_type') == 'weak':
            lines.append(tr('Слабое совпадение: общих навыков и достаточного совпадения роли не найдено.'))
        if result.get('requirement_modality_unknown'):
            lines.append(tr(
                'Для части упоминаний не удалось определить, являются ли они обязательными '
                'требованиями. Это нужно уточнить.',
            ))
        url = _safe_url(result.get("url"))
        if url:
            safe_link = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
            lines.append(tr('[Открыть вакансию]({p0})', p0=safe_link))
        matched = result.get("matched_skills", [])
        if matched:
            lines.extend(["", tr(
                'Совпадения с указанными навыками (часть сведений в профиле может быть '
                'самодекларацией):',
            )])
            for item in matched:
                label = _md(item.get("label", tr('Навык')))
                lines.append(f"- {label}")
                for evidence in item.get("job_evidence", []):
                    quote = evidence.get("quote")
                    context = evidence.get("context_quote")
                    if quote:
                        text = f"«{quote}»" + (tr(' Контекст: «{p0}».', p0=context) if context else "")
                        lines.append(tr('  - Цитата из вакансии: {p0}', p0=_md(text)))
                for evidence in item.get("profile_evidence", []):
                    quote = evidence.get("evidence_quote")
                    if quote:
                        lines.append(tr('  - Профиль: {p0}', p0=_md(quote)))
                    if evidence.get("status") == "project_evidence":
                        period = evidence.get("last_used")
                        note = tr(
                            'Период проекта: {p0}; текущая готовность навыка не проверена.',
                            p0=_md(period),
                        ) if period else tr(
                            'Текущая готовность навыка не проверена.',
                        )
                        lines.append(f"  - {_md(note)}")
        for field, heading in (
            ('related_skills', tr('Дополнительные заявленные навыки, связанные с вакансией')),
            ('relation_context_skills', tr('Контекстные связи для проверки (не добавляются в письмо)')),
        ):
            if result.get(field):
                lines.extend(['', heading + ':'])
                for item in result[field]:
                    lines.append('- ' + _md(item['label']) + ' → ' + _md(item['target_concept']['label'])
                                 + '. ' + _md(item['relation_explanation'])
                                 + tr('. Требуемый навык не считается подтверждённым.'))
                    for proof in item.get('relation_path', []):
                        lines.append(tr('  - Источник: ') + _md(proof['source']) + tr('; версия: ')
                                     + _md(proof['version']) + tr('; тип: ') + _md(proof['type']))
        missing = result.get("missing_required", [])
        if missing:
            lines.extend(["", tr('Требования, для которых в профиле не найдено подтверждения:')])
            for item in missing:
                lines.append(f"- {_md(item.get('label', tr('Требование')))}")
                for evidence in item.get("job_evidence", []):
                    if evidence.get("quote"):
                        lines.append(tr(
                            '  - Цитата из вакансии: {p0}',
                            p0=_md('«' + evidence['quote'] + '»'),
                        ))
        unconfirmed = result.get("unconfirmed_skills", [])
        if unconfirmed:
            lines.extend(["", tr('Навыки, требующие уточнения:')])
            for item in unconfirmed:
                lines.append(f"- {_md(item.get('label', tr('Навык')))}")
                for evidence in item.get("job_evidence", []):
                    if evidence.get("quote"):
                        lines.append(tr(
                            '  - Цитата из вакансии: {p0}',
                            p0=_md('«' + evidence['quote'] + '»'),
                        ))
        unknowns = result.get("unknowns", [])
        if unknowns:
            lines.extend(["", tr('Неизвестное и неоднозначное:')])
            for item in unknowns[:5]:
                detail = f" — {_reason(item['reason'])}" if item.get("reason") else ""
                quote = tr(' Цитата: «{p0}».', p0=item.get('quote')) if item.get("quote") else ""
                lines.append(f"- {_md(detail + quote).lstrip(' —')}")
            if len(unknowns) > 5:
                lines.append(tr(
                    'Ещё неоднозначных фрагментов: {p0}; полный список в JSON.',
                    p0=len(unknowns) - 5,
                ))
        constraints = result.get("constraints", [])
        if constraints:
            lines.extend(["", tr('Условия:')])
            for item in constraints:
                detail = item.get("quote") or item.get("profile_value") or item.get("required") or item.get("status") or ""
                status = item.get("status", "unknown")
                lines.append(tr(
                    '- {p0}: {p1} — статус: **{p2}**.',
                    p0=_md(item.get('kind', tr('Условие'))),
                    p1=_md(detail),
                    p2=_md(status),
                ))

    adjacent = report.get("adjacent_roles", [])
    if adjacent:
        lines.extend(["", tr('## Возможные смежные роли')])
        for item in adjacent:
            lines.extend(["", f"### {_md(item.get('role', tr('Смежная роль')))}"])
            if item.get("reason"):
                lines.append(_md(item["reason"]))
            for label, field in ((tr(
                'Переносимые навыки',
            ), "transferable_skills"), (tr(
                'Обязательные требования без подтверждения в профиле',
            ), "missing_required"), (tr(
                'Неизвестные требования',
            ), "unknown_requirements")):
                values = item.get(field, [])
                if values:
                    lines.append(f"{label}: {_md(', '.join(map(str, values)))}.")
            for skill in item.get('evidence', [])[:3]:
                job_quotes = '; '.join(e.get('quote', '') for e in skill.get('job_evidence', []))
                profile_quotes = '; '.join(e.get('evidence_quote', '') for e in skill.get('profile_evidence', []))
                lines.append(tr(
                    '- {p0}: вакансия «{p1}»; профиль «{p2}».',
                    p0=_md(skill['label']),
                    p1=_md(job_quotes),
                    p2=_md(profile_quotes),
                ))
            url = _safe_url(item.get("url"))
            if url:
                lines.append(tr('[Источник]({p0})', p0=url.replace(' ', '%20')))

    references = report.get("references", [])
    if references:
        lines.extend(["", tr('## Источники справочных данных')])
        for reference in references:
            if not isinstance(reference, dict):
                continue
            source = reference.get("source") or reference.get("name") or tr('Справочник')
            version = reference.get("version") or reference.get("snapshot_date") or reference.get("date")
            label = _md(source) + (f" ({_md(version)})" if version else "")
            lines.append(f"- {label}")
            attribution = reference.get("attribution")
            if attribution:
                lines.append(f"  - {_md(attribution)}")
            elif "esco" in str(source).lower():
                lines.append(f"  - {_md(tr(_ESCO_ATTRIBUTION))}")
    return "\n".join(lines).rstrip() + "\n"


def _csv(report: dict[str, Any]) -> str:
    from io import StringIO

    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(_CSV_COLUMNS)
    writer.writerows(_csv_rows(report.get('results', [])))
    return output.getvalue()


def _csv_rows(results, *, audit: bool = False):
    for result in results:
        result = localize_result(result)
        cells = [
            result.get("ref"), result.get("title"), result.get("role"), result.get("location"),
            result.get("url"), result.get("score"), result.get("status"),
            "; ".join(item.get("label", "") for item in result.get("matched_skills", [])),
            "; ".join(item.get("label", "") for item in result.get("missing_required", [])),
            "; ".join(item.get("quote", "") or item.get("reason", "") for item in result.get("unknowns", [])),
        ]
        if audit:
            cells.extend(result.get(k) for k in _CSV_AUDIT_COLUMNS)
        yield [_csv_cell(cell) for cell in cells]


@contextmanager
def all_vacancies_csv(prefix: Path):
    """Stream every result; publish the file only after a successful full scan."""
    path = localized_prefix(prefix).with_suffix('.all.csv')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='',
                                         dir=path.parent, delete=False) as stream:
            temporary_path = stream.name
            writer = csv.writer(stream)
            writer.writerow((*_CSV_COLUMNS, *_CSV_AUDIT_COLUMNS))
            yield path, lambda rows: writer.writerows(_csv_rows(rows, audit=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _csv_cell(value: Any) -> str:
    text = _plain(value)
    if text.lstrip("\ufeff\t\r\n ").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
            temporary_path = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


@report_language
def write_reports(report: dict, prefix: Path) -> dict:
    """Atomically write Markdown, JSON, and CSV views of a report."""
    if not isinstance(report, dict):
        raise TypeError("report must be a dict")
    prefix = localized_prefix(prefix)
    projected = _report_json(report)
    paths = {"markdown": prefix.with_suffix(".md"), "json": prefix.with_suffix(".json"), "csv": prefix.with_suffix(".csv")}
    _atomic_write(paths["markdown"], _markdown(projected))
    _atomic_write(paths["json"], json.dumps(projected, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _atomic_write(paths["csv"], _csv(projected))
    return paths
