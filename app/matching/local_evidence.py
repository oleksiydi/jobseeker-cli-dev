"""Extract exact evidence for phrases in a local skills/occupations catalogue."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import spacy
from spacy.attrs import NORM
from spacy.matcher import PhraseMatcher
from spacy.util import compile_infix_regex


MODEL_VERSION = "spacy-phrase-matcher-v2"
_CLAUSE_BREAKS = re.compile(r"[;.!?\n\r•●▪◦]+")
_HYPHENS = "-‐‑‒–—﹘﹣－"
_REQUIREMENT_HEADERS = {
    "sie bringen mit",
    "das bringen sie mit",
    "ihr profil",
    "qualifikation",
    "anforderungen",
}
_REQUIREMENT_HEADER_CUE = re.compile(
    r"\b(?:sie\s+bringen\s+mit|das\s+bringen\s+sie\s+mit|ihr\s+profil|qualifikation|anforderungen)\b",
    re.I,
)
_TASK_HEADERS = {"aufgaben"}
_BENEFIT_HEADERS = {"wir bieten", "benefits", "benefit", "mitarbeitervorteile", "ihre vorteile"}
_MODALITY_CUES = {
    "negated": re.compile(
        r"\b(?:nicht\s+(?:zwingend\s+)?(?:erforderlich|vorausgesetzt|notwendig)|keine\s+pflicht|nicht\s+erforderlich)\b"
        r"|\bkeine\b.{0,100}\berforderlich\b"
        r"|\bohne\b.{0,80}\bkenntnisse\b"
        r"|\b(?:nicht\s+erforderlich|keine\s+(?:kenntnisse|erfahrung))\b",
        re.IGNORECASE,
    ),
    "preferred": re.compile(r"\b(?:von\s+vorteil|wünschenswert|idealerweise)\b", re.IGNORECASE),
    "required": re.compile(
        r"\b(?:erforderlich|zwingend|vorausgesetzt|notwendig|muss|müssen)\b",
        re.IGNORECASE,
    ),
}
_MODALITY_PRIORITY = {"negated": 0, "preferred": 1, "required": 2}


def _distance(start: int, end: int, cue_start: int, cue_end: int) -> int:
    if cue_end < start:
        return start - cue_end
    if end < cue_start:
        return cue_start - end
    return 0


class EvidenceMatcher:
    """Match imported labels/aliases without loading a statistical language model."""

    def __init__(self, terms: list[dict[str, Any]]) -> None:
        self._nlp = spacy.blank("de")
        # Tokenize hyphens uniformly while preserving the source's character offsets.
        hyphen_infix = rf"[{re.escape(_HYPHENS)}]"
        infix_re = compile_infix_regex([*self._nlp.Defaults.infixes, hyphen_infix])
        self._nlp.tokenizer.infix_finditer = infix_re.finditer
        hyphen_translation = str.maketrans({character: "-" for character in _HYPHENS})
        self._nlp.vocab.lex_attr_getters[NORM] = lambda value: value.casefold().translate(hyphen_translation)
        self._matcher = PhraseMatcher(self._nlp.vocab, attr="NORM")
        self._terms_by_match_id: dict[int, dict[str, str]] = {}
        self.add_terms(terms)

    def add_terms(self, terms: list[dict[str, Any]]) -> None:
        """Add terms to this matcher without rebuilding its existing patterns."""
        for term in terms:
            label = str(term.get("label") or "").strip()
            concept_id = str(term.get("id") or "").strip()
            if not label or not concept_id:
                continue

            metadata = {
                "concept_id": concept_id,
                "label": label,
                "taxonomy_source": str(term.get("source") or ""),
                "taxonomy_version": str(term.get("version") or ""),
            }
            aliases = [label, *(term.get("aliases") or [])]
            patterns = []
            seen_aliases = set()
            for alias in aliases:
                phrase = str(alias or "").strip()
                if not phrase or phrase.casefold() in seen_aliases:
                    continue
                seen_aliases.add(phrase.casefold())
                patterns.append(self._nlp.make_doc(phrase))

            if not patterns:
                continue
            match_label = f"LOCAL_TERM_{len(self._terms_by_match_id)}"
            self._matcher.add(match_label, patterns)
            self._terms_by_match_id[self._nlp.vocab.strings[match_label]] = metadata

    @staticmethod
    def _header_role(line: str) -> str | None:
        """Return the role of a plain or Markdown section header."""
        candidate = line.strip()
        candidate = re.sub(r"^\s*#{1,6}\s*", "", candidate)
        candidate = re.sub(r"^[*_]+|[*_]+$", "", candidate).strip()
        candidate = re.sub(r"[*_]+", "", candidate).strip()
        normalized = re.sub(r"\s*:\s*$", "", candidate).casefold().strip()
        if normalized in _REQUIREMENT_HEADERS:
            return "requirement"
        if normalized in _TASK_HEADERS:
            return "task"
        if normalized in _BENEFIT_HEADERS:
            return "benefit"
        # Any other explicit heading ends the preceding section's scope.
        is_markdown_heading = line.lstrip().startswith("#") or (
            line.strip().startswith("**") and line.strip().endswith("**")
        )
        is_plain_heading = bool(re.fullmatch(r"\s*[^.!?]{1,80}:\s*", candidate))
        if is_markdown_heading or is_plain_heading:
            return "other"
        return None

    @staticmethod
    def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
        left = 0
        right = len(text)
        for separator in _CLAUSE_BREAKS.finditer(text):
            if separator.end() <= start:
                left = separator.end()
            elif separator.start() >= end:
                right = separator.start()
                break
        return left, right

    @staticmethod
    def _modality(text: str, start: int, end: int) -> tuple[str, str]:
        clause_start, clause_end = EvidenceMatcher._clause_bounds(text, start, end)
        clause = text[clause_start:clause_end]
        local_start = start - clause_start
        local_end = end - clause_start
        cues = []
        for modality, pattern in _MODALITY_CUES.items():
            for match in pattern.finditer(clause):
                cues.append(
                    (
                        _distance(local_start, local_end, match.start(), match.end()),
                        _MODALITY_PRIORITY[modality],
                        modality,
                        match.group(0),
                    )
                )
        # Local cues compete by distance; a profile heading is only a fallback.
        if cues:
            _, _, modality, cue = min(cues, key=lambda item: (item[0], item[1]))
            return modality, f"Matched modality cue: {cue}"
        header = _REQUIREMENT_HEADER_CUE.search(clause)
        if header:
            return "required", f"Matched modality cue: {header.group(0)}"
        return "unknown", "No explicit modality cue in the same sentence or list item"

    @staticmethod
    def _context_role(text: str, start: int, end: int) -> tuple[str | None, bool]:
        lines = text.splitlines(keepends=True)
        line_start = 0
        current_index = 0
        for index, line in enumerate(lines):
            if line_start <= start < line_start + len(line):
                current_index = index
                break
            line_start += len(line)
        current = lines[current_index].strip("\r\n •●▪◦\t")
        current_role = EvidenceMatcher._header_role(current)
        if current_role in {"benefit", "task", "requirement"}:
            return current_role, current_role == "benefit"
        # Scope continues over prose and blank lines, then ends at any heading.
        for index in range(current_index - 1, -1, -1):
            raw_line = lines[index]
            line = raw_line.strip("\r\n \t")
            if not line:
                continue
            if re.match(r"^\s*(?:[-*•●▪◦]|\d+[.)])\s+", raw_line):
                continue
            role = EvidenceMatcher._header_role(line)
            if role == "other":
                return None, False
            if role:
                return role, role == "benefit"
        return None, False

    def extract(self, text: str, source: str = "description") -> list[dict[str, Any]]:
        """Return exact spans and conservative modality for known local concepts."""
        if not text:
            return []

        doc = self._nlp.make_doc(text)
        matches_by_span: dict[tuple[int, int], dict[str, dict[str, str]]] = defaultdict(dict)
        for match_id, start_token, end_token in self._matcher(doc):
            span = doc[start_token:end_token]
            metadata = self._terms_by_match_id[match_id]
            matches_by_span[(span.start_char, span.end_char)].setdefault(
                metadata["concept_id"], metadata
            )

        spans = sorted(
            matches_by_span.items(),
            key=lambda item: (-(item[0][1] - item[0][0]), item[0][0], item[0][1]),
        )
        selected: list[tuple[int, int, dict[str, dict[str, str]]]] = []
        for (start, end), concepts in spans:
            if any(start < chosen_end and chosen_start < end for chosen_start, chosen_end, _ in selected):
                continue
            selected.append((start, end, concepts))

        results = []
        for start, end, concepts in sorted(selected, key=lambda item: (item[0], item[1])):
            # Literal user-declared phrases need no semantic alias decision. Other
            # taxonomy hits remain unresolved; this does not merge their concepts.
            literal = {key: value for key, value in concepts.items() if value['taxonomy_source'] == 'profile'}
            if literal:
                concepts = literal
            ambiguous = len(concepts) > 1
            modality, reason = self._modality(text, start, end)
            context_role, is_benefit = self._context_role(text, start, end)
            if is_benefit:
                modality = "irrelevant"
                reason = "Skill appears in a benefits section"
            elif context_role == "requirement" and modality == "unknown":
                modality = "required"
                reason = "Inherited from the preceding profile heading"
            if ambiguous:
                modality = "unknown"
                concept_names = ", ".join(sorted(concepts))
                reason = f"Ambiguous exact-span match for concepts: {concept_names}"
            quote = text[start:end]
            context_start, context_end = self._clause_bounds(text, start, end)
            for concept_id, metadata in sorted(concepts.items()):
                results.append(
                    {
                        **metadata,
                        "source": source,
                        "text_span": [start, end],
                        "quote": quote,
                        "context_quote": text[context_start:context_end].strip(),
                        "context_span": [context_start, context_end],
                        "taxonomy_id": concept_id,
                        "confidence": None,
                        "matcher": "spacy.PhraseMatcher",
                        "model_version": MODEL_VERSION,
                        "modality": modality,
                        "reason": reason,
                        "context_role": context_role,
                        "needs_review": ambiguous or modality == "negated",
                    }
                )
        return results
