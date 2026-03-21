"""Tests for activity log write, read, and prune."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from cc_codex_bridge.activity_log import (
    LogEntry,
    LogChange,
    write_log_entry,
    read_log_entries,
)


def _make_entry(
    *,
    action: str = "reconcile",
    project: str = "/tmp/proj",
    changes: tuple[LogChange, ...] | None = None,
    timestamp: datetime | None = None,
) -> LogEntry:
    if changes is None:
        changes = (LogChange(type="create", artifact="skill", path="/tmp/skill"),)
    return LogEntry(
        timestamp=timestamp or datetime(2026, 3, 21, 14, 0, 0),
        action=action,
        project=project,
        changes=changes,
    )


def test_write_creates_daily_file(tmp_path):
    """Writing an entry creates YYYY-MM-DD.jsonl in the logs dir."""
    logs_dir = tmp_path / "logs"
    entry = _make_entry()
    write_log_entry(entry, logs_dir=logs_dir)
    expected = logs_dir / "2026-03-21.jsonl"
    assert expected.exists()


def test_write_appends_to_existing_file(tmp_path):
    """Multiple writes on same day append to the same file."""
    logs_dir = tmp_path / "logs"
    write_log_entry(_make_entry(action="reconcile"), logs_dir=logs_dir)
    write_log_entry(_make_entry(action="clean"), logs_dir=logs_dir)
    lines = (logs_dir / "2026-03-21.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2


def test_write_no_changes_does_not_create_file(tmp_path):
    """An entry with no changes produces no file."""
    logs_dir = tmp_path / "logs"
    entry = _make_entry(changes=())
    write_log_entry(entry, logs_dir=logs_dir)
    assert not (logs_dir / "2026-03-21.jsonl").exists()


def test_write_entry_is_valid_json(tmp_path):
    """Each written line is valid JSON."""
    logs_dir = tmp_path / "logs"
    write_log_entry(_make_entry(), logs_dir=logs_dir)
    line = (logs_dir / "2026-03-21.jsonl").read_text().strip()
    data = json.loads(line)
    assert data["action"] == "reconcile"
    assert data["summary"]["created"] == 1


def test_read_entries_from_date_range(tmp_path):
    """read_log_entries filters by date range."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    # Create two daily files
    entry_mar20 = _make_entry(
        action="reconcile",
        timestamp=datetime(2026, 3, 20, 10, 0, 0),
    )
    entry_mar21 = _make_entry(
        action="clean",
        timestamp=datetime(2026, 3, 21, 10, 0, 0),
    )
    write_log_entry(entry_mar20, logs_dir=logs_dir)
    write_log_entry(entry_mar21, logs_dir=logs_dir)

    # Read only March 21
    entries = read_log_entries(
        logs_dir=logs_dir,
        since=date(2026, 3, 21),
        until=date(2026, 3, 21),
    )
    assert len(entries) == 1
    assert entries[0].action == "clean"


def test_read_entries_empty_dir(tmp_path):
    """read_log_entries on empty or missing dir returns empty list."""
    entries = read_log_entries(logs_dir=tmp_path / "nope")
    assert entries == []
