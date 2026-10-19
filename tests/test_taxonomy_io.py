import json

from tools.taxonomy_io import append_checkpoint, append_process_log, load_checkpoint


def test_process_log_summary_skips_term_normalized(tmp_path):
    out = tmp_path / "process.jsonl"
    append_process_log(out, {"event": "term_normalized", "run_id": "r1"}, level="summary")
    assert not out.exists()
    append_process_log(out, {"event": "run_started", "run_id": "r1"}, level="summary")
    assert out.exists()
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "run_started"


def test_checkpoint_roundtrip(tmp_path):
    out = tmp_path / "checkpoint.jsonl"
    append_checkpoint(out, run_id="r1", referenznummer="A", status="ok")
    append_checkpoint(out, run_id="r1", referenznummer="B", status="error")
    loaded = load_checkpoint(out)
    assert loaded["A"] == "ok"
    assert loaded["B"] == "error"
