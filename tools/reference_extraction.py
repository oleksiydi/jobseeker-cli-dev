"""Lossless residual batching and validated cached extraction before publication."""
from __future__ import annotations

from dataclasses import dataclass

from jsonschema import Draft202012Validator

from tools.llm_artifact_cache import input_hash
from tools.taxonomy_extract import (
    build_reference_system_prompt, build_system_prompt, build_user_prompt, request_extraction,
)
from tools.taxonomy_io import append_jsonl
from tools.taxonomy_transform import (
    apply_runtime_taxonomy_to_row, extract_vacancy_domain_top, normalize_surface_text,
    now_utc_iso, sanitize_term_record,
)


@dataclass
class Fragment:
    referenznummer: str
    text: str
    known_terms: tuple[str, ...] = ()

    def canonical_text(self):
        return self.text


def batches(text: str, limit: int):
    """No character is discarded; long lines retain overlap for boundary terms."""
    prefix, _, description = text.partition('DESCRIPTION:\n')
    header = prefix + 'DESCRIPTION:\n'
    if len(header) >= limit // 2:
        header, description = '', text
    budget = limit - len(header)
    current = ''
    for line in description.splitlines(keepends=True):
        if len(line) > budget:
            if current:
                yield header + current
                current = ''
            start = 0
            overlap = min(200, budget // 4)
            while start < len(line):
                yield header + line[start:start + budget]
                if start + budget >= len(line):
                    break
                start += budget - overlap
        elif len(current) + len(line) > budget:
            yield header + current
            current = line
        else:
            current += line
    if current or not description:
        yield header + current


def extract(vacancy, index, runtime, args, domain_list, schema, cache, run_id, *, totals=None):
    """Keep local terms plus residual model terms; never cache a partial result."""
    plan = index.plan(vacancy)
    known_terms = tuple(dict.fromkeys(o['canonical'] for o in plan.observations
                                    if o.get('omitted_from_prompt')))[:20]
    validator = Draft202012Validator(schema)
    payloads, keys = [], []
    metrics = {'reference_local_terms': len(plan.local_terms), 'reference_removed_segments': plan.removed_segments,
               'reference_input_chars': plan.input_chars, 'reference_retained_chars': plan.retained_chars,
               'reference_prompt_chars': 0, 'reference_baseline_prompt_chars': len(
                   build_system_prompt(domain_list, args.max_terms_per_vacancy)
                   + build_user_prompt(vacancy, args.max_input_chars)),
               'reference_api_calls': 0, 'reference_cache_hits': 0, 'reference_rejected_terms': 0}
    if totals is not None:
        for key, value in metrics.items():
            totals[key] = totals.get(key, 0) + value

    def count(key, value):
        metrics[key] = metrics.get(key, 0) + value
        if totals is not None:
            totals[key] = totals.get(key, 0) + value

    def record_response(response):
        # Count provider usage even when JSON parsing subsequently fails.
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            value = (response.usage or {}).get(field)
            if type(value) is int and value >= 0:
                count('reference_usage_' + field, value)
        append_jsonl(args.llm_calls_log, [{
            'run_id': run_id, 'referenznummer': vacancy.referenznummer,
            'stage': 'reference_residual',
            'status': 'complete' if response.finish_reason == 'stop' else 'incomplete',
            'provider': response.provider, 'model': response.model,
            'finish_reason': response.finish_reason, 'usage': response.usage,
            'elapsed_ms': response.elapsed_ms}])

    for chunk in batches(plan.residual, args.max_input_chars):
        fragment = Fragment(vacancy.referenznummer, chunk, known_terms)
        payload, response = None, None
        complete = False
        attempt = 0
        while attempt <= args.retry_on_length:
            attempt += 1
            term_limit = args.max_terms_per_vacancy if attempt == 1 else max(3, args.max_terms_per_vacancy // 2)
            system_prompt = build_reference_system_prompt(domain_list, term_limit)
            key = input_hash({'reference_version': index.version, 'provider': args.provider,
                              'model': args.model, 'base_url': args.base_url, 'schema': schema,
                              'max_tokens': args.llm_max_tokens,
                              'system': system_prompt,
                              'user': build_user_prompt(fragment, args.max_input_chars)})
            keys.append(key)
            payload = None if args.overwrite_output else cache.get(key)
            if payload is not None:
                count('reference_cache_hits', 1)
                response = None
                complete = True
                break
            count('reference_api_calls', 1)
            count('reference_prompt_chars', len(system_prompt
                                                + build_user_prompt(fragment, args.max_input_chars)))
            observed = False

            def on_response(value):
                nonlocal observed
                observed = True
                record_response(value)

            payload, response = request_extraction(
                provider=args.provider, model=args.model, base_url=args.base_url, api_key=args.api_key,
                timeout_seconds=args.timeout_seconds, llm_max_tokens=args.llm_max_tokens,
                extraction_schema=schema, domain_list=domain_list, max_terms_per_vacancy=term_limit,
                max_input_chars=args.max_input_chars, vacancy=fragment,
                system_prompt=system_prompt, on_response=on_response)
            if not observed:
                record_response(response)
            if response.finish_reason == 'stop':
                complete = True
                break
            if response.finish_reason != 'length':
                break
        if not complete:
            raise ValueError('Incomplete residual taxonomy response')
        validator.validate(payload)
        allowed = normalize_surface_text(chunk)
        original = normalize_surface_text(vacancy.canonical_text())
        validated = []
        for item in payload.get('terms', []):
            context = normalize_surface_text(str(item.get('context_phrase') or ''))
            if context and context in allowed and context in original:
                validated.append(item)
            else:
                count('reference_rejected_terms', 1)
        payload = {**payload, 'terms': validated}
        if response is not None and response.finish_reason == 'stop':
            cache.put(key, payload)
        payloads.append(payload)
    domain_votes = [d for payload in payloads for d in payload.get('vacancy_domains', [])]
    domain, confidence = extract_vacancy_domain_top(domain_votes)
    local = [{**row, 'domain_hint': domain, 'domain_confidence': confidence} for row in plan.local_terms]
    records, seen = [], set()
    llm_rows = [item for payload in payloads for item in payload.get('terms', [])]
    for item, origin in [(row, 'reference_prefilter') for row in local] + [(row, 'llm') for row in llm_rows]:
        row = sanitize_term_record(raw=item, referenznummer=vacancy.referenznummer,
                                  vacancy_domain_top=domain, vacancy_domain_confidence=confidence)
        if row is None or row.domain_hint not in domain_list:
            count('reference_rejected_terms', 1)
            continue
        if runtime is not None:
            row = apply_runtime_taxonomy_to_row(row, runtime)
        identity = (row.term_key, row.category, row.facet, row.domain_hint)
        if identity in seen:
            continue
        seen.add(identity)
        records.append((row, {**row.__dict__, 'origin': origin, 'reference_version': index.version,
                              'modality': index.matcher._modality(row.context_phrase, 0, len(row.context_phrase))[0],
                              'reference_observations': [o for o in plan.observations if o['term'] == row.term],
                              'run_id': run_id, 'input_hash': input_hash(keys),
                              'provider': args.provider if origin == 'llm' else 'local_reference',
                              'model': args.model if origin == 'llm' else index.version,
                              'created_at': now_utc_iso(), 'attempt': 0}))
    return records, metrics
