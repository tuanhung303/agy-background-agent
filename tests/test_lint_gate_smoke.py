"""
tests.test_lint_gate_smoke - Smoke test for sage.journal file creation.
"""
import json
import os
from sage import journal


def test_journal_write_creates_new_file(tmp_path, monkeypatch):
    journal_file = str(tmp_path / "events.jsonl")
    monkeypatch.setenv("AGY_SAGE_JOURNAL", journal_file)
    assert not os.path.exists(journal_file)

    journal.write("test_smoke_event", conv_id="conv_smoke_1", detail="smoke_detail")

    assert os.path.exists(journal_file)
    with open(journal_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "test_smoke_event"
    assert record["conv_id"] == "conv_smoke_1"
    assert record["detail"] == "smoke_detail"
