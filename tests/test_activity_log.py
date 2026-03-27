"""Tests for activity log write, read, and prune."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from cc_codex_bridge.activity_log import (
    LogEntry,
    LogChange,
    build_log_entry_from_changes,
    write_log_entry,
    read_log_entries,
    filter_entries,
    format_log_entries,
    prune_logs,
)
from cc_codex_bridge.reconcile import Change


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


def test_read_entries_skips_malformed_lines(tmp_path):
    """read_log_entries skips malformed JSONL lines instead of crashing."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    good_entry = _make_entry(timestamp=datetime(2026, 3, 21, 10, 0, 0))
    good_line = good_entry.to_json_line()
    content = f"not valid json\n{good_line}\n{{\"missing\": \"fields\"}}\n"
    (logs_dir / "2026-03-21.jsonl").write_text(content)

    entries = read_log_entries(logs_dir=logs_dir)
    assert len(entries) == 1
    assert entries[0].action == "reconcile"


def test_read_entries_skips_unreadable_files(tmp_path):
    """read_log_entries skips files with bad permissions instead of crashing."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    good_entry = _make_entry(timestamp=datetime(2026, 3, 21, 10, 0, 0))
    write_log_entry(good_entry, logs_dir=logs_dir)
    bad_file = logs_dir / "2026-03-20.jsonl"
    bad_file.write_text('{"test": true}\n')
    bad_file.chmod(0o000)

    entries = read_log_entries(logs_dir=logs_dir)
    assert len(entries) == 1
    assert entries[0].action == "reconcile"
    bad_file.chmod(0o644)  # cleanup


def test_read_entries_skips_invalid_timestamps(tmp_path):
    """read_log_entries skips lines with malformed timestamps."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    good_entry = _make_entry(timestamp=datetime(2026, 3, 21, 10, 0, 0))
    good_line = good_entry.to_json_line()
    bad_line = '{"timestamp": "not-a-date", "action": "reconcile", "project": "/x", "changes": [], "summary": {}}'
    content = f"{bad_line}\n{good_line}\n"
    (logs_dir / "2026-03-21.jsonl").write_text(content)

    entries = read_log_entries(logs_dir=logs_dir)
    assert len(entries) == 1
    assert entries[0].action == "reconcile"


def test_filter_by_project(tmp_path):
    """filter_entries filters by project path."""
    logs_dir = tmp_path / "logs"
    ts = datetime(2026, 3, 21, 10, 0, 0)
    write_log_entry(_make_entry(project="/a", timestamp=ts), logs_dir=logs_dir)
    write_log_entry(_make_entry(project="/b", timestamp=ts), logs_dir=logs_dir)

    entries = read_log_entries(logs_dir=logs_dir)
    filtered = filter_entries(entries, project="/a")
    assert len(filtered) == 1
    assert filtered[0].project == "/a"


def test_filter_by_action(tmp_path):
    """filter_entries filters by action type."""
    logs_dir = tmp_path / "logs"
    ts = datetime(2026, 3, 21, 10, 0, 0)
    write_log_entry(_make_entry(action="reconcile", timestamp=ts), logs_dir=logs_dir)
    write_log_entry(_make_entry(action="clean", timestamp=ts), logs_dir=logs_dir)

    entries = read_log_entries(logs_dir=logs_dir)
    filtered = filter_entries(entries, action="clean")
    assert len(filtered) == 1
    assert filtered[0].action == "clean"


def test_filter_by_change_type(tmp_path):
    """filter_entries filters entries containing a specific change type."""
    logs_dir = tmp_path / "logs"
    ts = datetime(2026, 3, 21, 10, 0, 0)
    create_entry = _make_entry(
        changes=(LogChange(type="create", artifact="skill", path="/s"),),
        timestamp=ts,
    )
    remove_entry = _make_entry(
        action="clean",
        changes=(LogChange(type="remove", artifact="skill", path="/s"),),
        timestamp=ts,
    )
    write_log_entry(create_entry, logs_dir=logs_dir)
    write_log_entry(remove_entry, logs_dir=logs_dir)

    entries = read_log_entries(logs_dir=logs_dir)
    filtered = filter_entries(entries, change_type="remove")
    assert len(filtered) == 1
    assert filtered[0].action == "clean"


def test_filter_combined(tmp_path):
    """filter_entries applies multiple filters with AND logic."""
    logs_dir = tmp_path / "logs"
    ts = datetime(2026, 3, 21, 10, 0, 0)
    write_log_entry(_make_entry(action="reconcile", project="/a", timestamp=ts), logs_dir=logs_dir)
    write_log_entry(_make_entry(action="reconcile", project="/b", timestamp=ts), logs_dir=logs_dir)
    write_log_entry(_make_entry(action="clean", project="/a", timestamp=ts), logs_dir=logs_dir)

    entries = read_log_entries(logs_dir=logs_dir)
    filtered = filter_entries(entries, action="reconcile", project="/a")
    assert len(filtered) == 1


def test_prune_removes_old_files(tmp_path):
    """prune_logs removes files older than retention days."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "2026-01-01.jsonl").write_text('{"test": true}\n')
    (logs_dir / "2026-03-20.jsonl").write_text('{"test": true}\n')
    (logs_dir / "2026-03-21.jsonl").write_text('{"test": true}\n')

    removed = prune_logs(logs_dir=logs_dir, retention_days=30, today=date(2026, 3, 21))
    assert len(removed) == 1
    assert removed[0].name == "2026-01-01.jsonl"
    assert not (logs_dir / "2026-01-01.jsonl").exists()
    assert (logs_dir / "2026-03-20.jsonl").exists()
    assert (logs_dir / "2026-03-21.jsonl").exists()


def test_prune_boundary_keeps_n_days(tmp_path):
    """prune_logs with retention_days=1 keeps only today, prunes yesterday."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "2026-03-20.jsonl").write_text('{"test": true}\n')
    (logs_dir / "2026-03-21.jsonl").write_text('{"test": true}\n')

    removed = prune_logs(logs_dir=logs_dir, retention_days=1, today=date(2026, 3, 21))
    assert len(removed) == 1
    assert removed[0].name == "2026-03-20.jsonl"
    assert (logs_dir / "2026-03-21.jsonl").exists()


def test_prune_empty_dir(tmp_path):
    """prune_logs on missing dir returns empty list."""
    removed = prune_logs(logs_dir=tmp_path / "nope", retention_days=90)
    assert removed == []


def test_prune_ignores_non_jsonl_files(tmp_path):
    """prune_logs leaves non-JSONL files alone."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "2020-01-01.jsonl").write_text('{"test": true}\n')
    (logs_dir / "notes.txt").write_text("keep me\n")

    removed = prune_logs(logs_dir=logs_dir, retention_days=30, today=date(2026, 3, 21))
    assert len(removed) == 1
    assert (logs_dir / "notes.txt").exists()


def test_format_entries_human_readable(tmp_path):
    """format_log_entries produces human-readable output."""
    entry = _make_entry(
        action="reconcile",
        project="/proj",
        changes=(
            LogChange(type="create", artifact="skill", path="/s1"),
            LogChange(type="update", artifact="agent", path="/a1"),
            LogChange(type="remove", artifact="prompt", path="/p1"),
        ),
        timestamp=datetime(2026, 3, 21, 14, 32, 7),
    )
    output = format_log_entries([entry])
    assert "2026-03-21 14:32:07" in output
    assert "reconcile" in output
    assert "/proj" in output
    assert "+ skill" in output
    assert "~ agent" in output
    assert "- prompt" in output


def test_format_entries_uses_change_symbols():
    """format_log_entries uses the same +/~/- symbols as format_change_report."""
    import re

    entry = LogEntry(
        timestamp=datetime(2026, 3, 21, 14, 0, 0),
        action="reconcile",
        project="/tmp/proj",
        changes=(
            LogChange(type="create", artifact="skill", path="/a"),
            LogChange(type="update", artifact="agent", path="/b"),
            LogChange(type="remove", artifact="project_file", path="/c"),
        ),
    )
    output = format_log_entries([entry])
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    assert "+" in plain
    assert "~" in plain
    assert "-" in plain


def test_format_entries_empty():
    """format_log_entries with no entries returns a no-entries message."""
    output = format_log_entries([])
    assert "No log entries" in output


def test_format_entries_json():
    """format_log_entries with json_output=True returns JSONL."""
    entry = _make_entry()
    output = format_log_entries([entry], json_output=True)
    data = json.loads(output.strip())
    assert data["action"] == "reconcile"


def test_format_entries_json_empty():
    """format_log_entries with json_output=True and no entries returns empty string."""
    output = format_log_entries([], json_output=True)
    assert output == ""


def test_build_log_entry_from_reconcile_changes():
    """build_log_entry_from_changes converts Change tuples into a LogEntry."""
    changes = (
        Change(kind="create", path=Path("/tmp/s"), resource_kind="skill"),
        Change(kind="update", path=Path("/tmp/a.toml"), resource_kind="agent"),
        Change(kind="remove", path=Path("/tmp/p.md"), resource_kind="prompt"),
    )
    entry = build_log_entry_from_changes(
        action="reconcile",
        project="/tmp/proj",
        changes=changes,
    )
    assert entry.action == "reconcile"
    assert entry.project == "/tmp/proj"
    assert len(entry.changes) == 3
    assert entry.changes[0].type == "create"
    assert entry.changes[0].artifact == "skill"
    assert entry.summary == {"created": 1, "updated": 1, "removed": 1}


def test_build_log_entry_empty_changes():
    """build_log_entry_from_changes with empty changes still creates entry."""
    entry = build_log_entry_from_changes(
        action="clean",
        project="/tmp/proj",
        changes=(),
    )
    assert len(entry.changes) == 0
