"""
Учебная копия файла app/matching/rule_engine.py (синхронизированная версия)
===========================================================================

Этот файл намеренно повторяет production-код почти 1:1.

Зачем:
1) чтобы новичок учился на реальном коде, который сейчас действительно используется;
2) чтобы не было расхождений между "учебным" и "боевым" вариантом;
3) чтобы line-by-line разбор из markdown был применим к актуальной реализации.

Где смотреть подробные объяснения:
- docs/learning/python/01_rule_engine_line_by_line_ru.md

Важно:
- рабочая версия: app/matching/rule_engine.py
- при изменении production-файла эту копию нужно синхронно обновлять.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.config import project_root
from app.matching.models import MergedVacancyNormalized, RuleEvaluationResult, normalize_text


JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
Context = dict[str, Any]


@dataclass(frozen=True)
class CompiledRule:
    """Предобработанное правило для более простого и быстрого runtime-цикла."""

    id: str
    when: JSONValue
    set_patch: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class LabelRule:
    """Предобработанное правило маркировки чанка."""

    id: str
    when: JSONValue
    label: str
    confidence: float


@dataclass(frozen=True)
class CompiledRuleset:
    """Нормализованный ruleset с типизированными списками правил."""

    rules: tuple[CompiledRule, ...]
    default_skip_reason: str | None = None


@dataclass(frozen=True)
class CompiledLabelRuleset:
    """Нормализованный ruleset для section label."""

    rules: tuple[LabelRule, ...]
    default_label: str
    default_confidence: float
    default_reason: str


class SimpleJsonLogicEvaluator:
    """
    Легковесный интерпретатор JSONLogic-выражений для текущих правил проекта.

    Почему встроенный:
    - позволяет сразу запускать проект без внешней зависимости;
    - закрывает ограниченный, но достаточный набор операций для наших rule-файлов.
    """

    MAX_DEPTH = 64

    def __init__(self, max_depth: int | None = None) -> None:
        self.max_depth = max_depth if max_depth is not None else self.MAX_DEPTH
        self._handlers: dict[str, Callable[[list[Any], Context, int], Any]] = {
            "var": self._op_var,
            "==": self._op_eq,
            "!=": self._op_ne,
            ">": self._op_gt,
            "<": self._op_lt,
            ">=": self._op_gte,
            "<=": self._op_lte,
            "and": self._op_and,
            "or": self._op_or,
            "!": self._op_not,
            "in": self._op_in,
            "if": self._op_if,
        }

    def evaluate(self, expr: JSONValue, data: Context, depth: int = 0) -> Any:
        self._check_depth(depth)

        if not isinstance(expr, dict):
            if isinstance(expr, list):
                return [self.evaluate(item, data, depth + 1) for item in expr]
            return expr

        if len(expr) != 1:
            raise ValueError(f"JSONLogic expression must have single operator, got: {expr}")

        op, args = next(iter(expr.items()))
        if not isinstance(args, list):
            args = [args]

        handler = self._handlers.get(op)
        if handler is None:
            raise ValueError(f"Unsupported JSONLogic operator: {op}")
        return handler(args, data, depth + 1)

    def _check_depth(self, depth: int) -> None:
        if depth > self.max_depth:
            raise ValueError(
                f"JSONLogic expression nesting is too deep (depth={depth}, limit={self.max_depth})"
            )

    @staticmethod
    def _resolve_var(args: list[Any], data: Context) -> Any:
        if not args:
            return data
        var_name = args[0]
        default = args[1] if len(args) > 1 else None
        if not isinstance(var_name, str):
            return default
        current: Any = data
        for part in var_name.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def _eval_if(self, args: list[Any], data: Context, depth: int) -> Any:
        # Формат JSONLogic: [cond, then, cond2, then2, else]
        index = 0
        while index + 1 < len(args):
            if bool(self.evaluate(args[index], data, depth)):
                return self.evaluate(args[index + 1], data, depth)
            index += 2
        if index < len(args):
            return self.evaluate(args[index], data, depth)
        return None

    def _eval_binary(self, args: list[Any], data: Context, depth: int) -> tuple[Any, Any]:
        if len(args) < 2:
            raise ValueError(f"Binary operator expects 2 args, got: {args}")
        return self.evaluate(args[0], data, depth), self.evaluate(args[1], data, depth)

    def _op_var(self, args: list[Any], data: Context, _: int) -> Any:
        return self._resolve_var(args, data)

    def _op_eq(self, args: list[Any], data: Context, depth: int) -> bool:
        left, right = self._eval_binary(args, data, depth)
        return left == right

    def _op_ne(self, args: list[Any], data: Context, depth: int) -> bool:
        left, right = self._eval_binary(args, data, depth)
        return left != right

    def _op_gt(self, args: list[Any], data: Context, depth: int) -> bool:
        left, right = self._eval_binary(args, data, depth)
        return left > right

    def _op_lt(self, args: list[Any], data: Context, depth: int) -> bool:
        left, right = self._eval_binary(args, data, depth)
        return left < right

    def _op_gte(self, args: list[Any], data: Context, depth: int) -> bool:
        left, right = self._eval_binary(args, data, depth)
        return left >= right

    def _op_lte(self, args: list[Any], data: Context, depth: int) -> bool:
        left, right = self._eval_binary(args, data, depth)
        return left <= right

    def _op_and(self, args: list[Any], data: Context, depth: int) -> bool:
        return all(bool(self.evaluate(item, data, depth)) for item in args)

    def _op_or(self, args: list[Any], data: Context, depth: int) -> bool:
        return any(bool(self.evaluate(item, data, depth)) for item in args)

    def _op_not(self, args: list[Any], data: Context, depth: int) -> bool:
        if not args:
            return True
        return not bool(self.evaluate(args[0], data, depth))

    def _op_in(self, args: list[Any], data: Context, depth: int) -> bool:
        needle, haystack = self._eval_binary(args, data, depth)
        if isinstance(haystack, str):
            return str(needle) in haystack
        return needle in haystack

    def _op_if(self, args: list[Any], data: Context, depth: int) -> Any:
        return self._eval_if(args, data, depth)


def _deep_update(target: Context, patch: Context) -> Context:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target


def _resolve_rules_path(cfg: Context) -> Path:
    matching = cfg.get("matching", {})
    rules = matching.get("rules", {})
    rules_path = rules.get("path", "conf/rules")
    return project_root() / rules_path


_RULE_FILE_CACHE: dict[tuple[Path, str], dict[str, Any]] = {}


def _load_rule_file(cfg: Context, key: str) -> dict[str, Any]:
    matching = cfg.get("matching", {})
    rules_cfg = matching.get("rules", {})
    files = rules_cfg.get("files", {})
    file_name = files.get(key)
    if not file_name:
        raise RuntimeError(f"matching.rules.files.{key} is not configured")

    path = _resolve_rules_path(cfg) / file_name
    if not path.exists():
        raise FileNotFoundError(f"Rule file not found: {path}")

    cache_key = (path.resolve(), key)
    cached = _RULE_FILE_CACHE.get(cache_key)
    if cached is not None:
        # Возвращаем копию, чтобы runtime-код не мутировал кэш.
        return json.loads(json.dumps(cached))

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    _RULE_FILE_CACHE[cache_key] = payload
    return json.loads(json.dumps(payload))


def _compile_ruleset(raw: dict[str, Any]) -> CompiledRuleset:
    compiled_rules: list[CompiledRule] = []
    for raw_rule in raw.get("rules", []):
        compiled_rules.append(
            CompiledRule(
                id=str(raw_rule.get("id", "unknown_rule")),
                when=raw_rule.get("when", False),
                set_patch=raw_rule.get("set", {}) if isinstance(raw_rule.get("set", {}), dict) else {},
                reason=str(raw_rule.get("reason", raw_rule.get("id", "unknown_rule"))),
            )
        )
    return CompiledRuleset(
        rules=tuple(compiled_rules),
        default_skip_reason=raw.get("default_skip_reason"),
    )


def _compile_label_ruleset(raw: dict[str, Any]) -> CompiledLabelRuleset:
    compiled: list[LabelRule] = []
    for raw_rule in raw.get("rules", []):
        compiled.append(
            LabelRule(
                id=str(raw_rule.get("id", "label_rule")),
                when=raw_rule.get("when", False),
                label=str(raw_rule.get("label", "unknown")),
                confidence=float(raw_rule.get("confidence", 0.5)),
            )
        )
    default = raw.get("default", {})
    return CompiledLabelRuleset(
        rules=tuple(compiled),
        default_label=str(default.get("label", "unknown")),
        default_confidence=float(default.get("confidence", 0.35)),
        default_reason=str(default.get("reason", "no_section_markers_detected")),
    )


class MatchingRuleEngine:
    """
    Применяет rule-файлы к нормализованной вакансии.

    Возвращает объяснимый результат:
    - какие правила сработали;
    - какие флаги выставлены;
    - нужно ли делать reindex.
    """

    def __init__(self, cfg: Context) -> None:
        self.cfg = cfg
        self.evaluator = SimpleJsonLogicEvaluator()
        self.normalize_rules = _compile_ruleset(_load_rule_file(cfg, "normalize"))
        self.fallback_rules = _compile_ruleset(_load_rule_file(cfg, "fallback"))
        self.label_rules = _compile_label_ruleset(_load_rule_file(cfg, "label"))
        self.reindex_rules = _compile_ruleset(_load_rule_file(cfg, "reindex"))
        self.rule_set_version = str(
            cfg.get("matching", {}).get("rules", {}).get("ruleset_version", "v1")
        )

    def evaluate_vacancy(
        self,
        vacancy: MergedVacancyNormalized,
        change_flags: Context | None = None,
    ) -> RuleEvaluationResult:
        context = {
            "referenznummer": vacancy.referenznummer,
            "title": vacancy.title,
            "role": vacancy.role,
            "alt_roles_count": len(vacancy.alt_roles),
            "description_missing": vacancy.description_missing,
            "location_summary": vacancy.location_summary,
            "employment_summary": vacancy.employment_summary,
            "description": vacancy.description,
        }
        if change_flags:
            context.update(change_flags)

        result = RuleEvaluationResult(rule_set_version=self.rule_set_version)
        result.flags = {}

        self._apply_ruleset(self.normalize_rules, context, result)
        self._apply_ruleset(self.fallback_rules, context, result)
        self._apply_ruleset(self.reindex_rules, context, result)

        result.needs_reindex = bool(result.flags.get("needs_reindex", False))
        if not result.needs_reindex:
            result.skip_reason = self.reindex_rules.default_skip_reason or "unchanged_inputs"

        if bool(result.flags.get("create_synthetic_chunk", False)):
            result.flags["synthetic_chunk_text"] = self.build_synthetic_chunk(vacancy)

        return result

    def infer_section_label(self, chunk_text: str) -> tuple[str, float, str]:
        """
        Определить секцию чанка через правила и вернуть:
        `(label, confidence, reason)`.
        """
        context = {"chunk_text_lc": normalize_text(chunk_text).lower()}
        for rule in self.label_rules.rules:
            condition = rule.when
            matched = bool(self.evaluator.evaluate(condition, context))
            if matched:
                return (
                    rule.label,
                    rule.confidence,
                    rule.id,
                )

        return (
            self.label_rules.default_label,
            self.label_rules.default_confidence,
            self.label_rules.default_reason,
        )

    @staticmethod
    def build_synthetic_chunk(vacancy: MergedVacancyNormalized) -> str:
        alt_roles = ", ".join(vacancy.alt_roles)
        return (
            f"Role summary: {vacancy.title}. Hauptberuf: {vacancy.role}. "
            f"Alternativberufe: {alt_roles}. "
            f"Location: {vacancy.location_summary}. "
            f"Employment: {vacancy.employment_summary}."
        ).strip()

    def _apply_ruleset(
        self,
        ruleset: CompiledRuleset,
        context: Context,
        result: RuleEvaluationResult,
    ) -> None:
        for rule in ruleset.rules:
            condition = rule.when
            matched = bool(self.evaluator.evaluate(condition, context))
            if not matched:
                continue

            rule_id = rule.id
            result.applied_rules.append(rule_id)
            reason = rule.reason
            result.decision_reasons.append(reason)

            set_patch = rule.set_patch
            if isinstance(set_patch, dict):
                _deep_update(context, set_patch)
                _deep_update(result.flags, set_patch)
