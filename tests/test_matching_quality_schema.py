import pytest

pytest.importorskip("pandera")

from app.matching.quality_schema import run_quality_checks


def test_quality_checks_pass_on_valid_batch() -> None:
    records = [
        {
            "referenznummer": "A-1",
            "chunk_id": "A-1:0",
            "chunk_index": 0,
            "text": "Wartung und Instandhaltung der Systeme im Tagesbetrieb.",
            "section_label": "tasks",
            "section_confidence": 0.8,
            "description_missing": False,
        },
        {
            "referenznummer": "A-1",
            "chunk_id": "A-1:1",
            "chunk_index": 1,
            "text": "Abgeschlossene technische Ausbildung und Bereitschaft zu Schichtarbeit.",
            "section_label": "requirements",
            "section_confidence": 0.9,
            "description_missing": False,
        },
    ]

    report = run_quality_checks(records)
    assert report.hard_passed is True
    assert report.soft_passed is True
    assert report.hard_errors == []
    assert report.soft_warnings == []


def test_quality_checks_fail_hard_for_empty_text_without_fallback() -> None:
    records = [
        {
            "referenznummer": "A-2",
            "chunk_id": "A-2:0",
            "chunk_index": 0,
            "text": "",
            "section_label": "unknown",
            "section_confidence": 0.0,
            "description_missing": False,
        }
    ]
    report = run_quality_checks(records)
    assert report.hard_passed is False
    assert any("text must be non-empty" in err for err in report.hard_errors)


def test_quality_checks_fail_soft_when_unknown_ratio_is_too_high() -> None:
    records = [
        {
            "referenznummer": "A-3",
            "chunk_id": "A-3:0",
            "chunk_index": 0,
            "text": "Langer Text für валидный чанк без fallback.",
            "section_label": "unknown",
            "section_confidence": 0.2,
            "description_missing": False,
        },
        {
            "referenznummer": "A-3",
            "chunk_id": "A-3:1",
            "chunk_index": 1,
            "text": "Еще один длинный текст чтобы пройти hard checks.",
            "section_label": "unknown",
            "section_confidence": 0.1,
            "description_missing": False,
        },
    ]
    report = run_quality_checks(records, unknown_label_threshold=0.2)
    assert report.hard_passed is True
    assert report.soft_passed is False
    assert any("unknown label ratio" in warning for warning in report.soft_warnings)
