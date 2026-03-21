"""Daily JSONL activity log for state-changing CLI operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass(frozen=True)
class LogChange:
    """One artifact mutation within a log entry."""

    type: str       # "create", "update", "remove"
    artifact: str   # "skill", "agent", "prompt", "project_file", "plugin_resource"
    path: str


@dataclass(frozen=True)
class LogEntry:
    """One activity log entry."""

    timestamp: datetime
    action: str     # "reconcile", "clean", "uninstall", "install-launchagent"
    project: str
    changes: tuple[LogChange, ...]

    @property
    def summary(self) -> dict[str, int]:
        created = sum(1 for c in self.changes if c.type == "create")
        updated = sum(1 for c in self.changes if c.type == "update")
        removed = sum(1 for c in self.changes if c.type == "remove")
        return {"created": created, "updated": updated, "removed": removed}

    def to_json_line(self) -> str:
        payload = {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "project": self.project,
            "changes": [
                {"type": c.type, "artifact": c.artifact, "path": c.path}
                for c in self.changes
            ],
            "summary": self.summary,
        }
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def from_json_line(cls, line: str) -> "LogEntry":
        data = json.loads(line)
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            action=data["action"],
            project=data["project"],
            changes=tuple(
                LogChange(type=c["type"], artifact=c["artifact"], path=c["path"])
                for c in data["changes"]
            ),
        )


def build_log_entry_from_changes(
    *,
    action: str,
    project: str,
    changes: tuple,
) -> LogEntry:
    """Build a LogEntry from reconcile Change objects.

    Accepts any tuple of objects with ``.kind``, ``.resource_kind``, and
    ``.path`` attributes (duck typing).  This avoids importing the
    ``reconcile`` module and the circular-import risk that would entail.
    """
    log_changes = tuple(
        LogChange(
            type=c.kind,
            artifact=c.resource_kind or "project_file",
            path=str(c.path),
        )
        for c in changes
    )
    return LogEntry(
        timestamp=datetime.now(),
        action=action,
        project=project,
        changes=log_changes,
    )


_CHANGE_SYMBOLS = {"create": "+", "update": "~", "remove": "-"}


def format_log_entries(entries: list[LogEntry], *, json_output: bool = False) -> str:
    """Format log entries for display."""
    if json_output:
        return "\n".join(e.to_json_line() for e in entries)

    if not entries:
        return "No log entries found."

    lines: list[str] = []
    for entry in entries:
        ts = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{ts}  {entry.action:<20s} {entry.project}")
        for change in entry.changes:
            symbol = _CHANGE_SYMBOLS.get(change.type, "?")
            lines.append(f"  {symbol} {change.artifact:<16s} {change.path}")
        lines.append("")

    return "\n".join(lines).rstrip()


def write_log_entry(entry: LogEntry, *, logs_dir: Path) -> None:
    """Append a log entry to the daily JSONL file. No-op if no changes."""
    if not entry.changes:
        return
    logs_dir.mkdir(parents=True, exist_ok=True)
    filename = entry.timestamp.strftime("%Y-%m-%d") + ".jsonl"
    with open(logs_dir / filename, "a", encoding="utf-8") as f:
        f.write(entry.to_json_line() + "\n")


def read_log_entries(
    *,
    logs_dir: Path,
    since: date | None = None,
    until: date | None = None,
) -> list[LogEntry]:
    """Read log entries from JSONL files, optionally filtered by date range."""
    if not logs_dir.is_dir():
        return []

    entries: list[LogEntry] = []
    for log_file in sorted(logs_dir.glob("*.jsonl")):
        file_date = _parse_log_filename(log_file.name)
        if file_date is None:
            continue
        if since and file_date < since:
            continue
        if until and file_date > until:
            continue
        for line in log_file.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                entries.append(LogEntry.from_json_line(line))

    return entries


def filter_entries(
    entries: list[LogEntry],
    *,
    project: str | None = None,
    action: str | None = None,
    change_type: str | None = None,
) -> list[LogEntry]:
    """Filter log entries by project, action, and/or change type (AND logic)."""
    result = entries
    if project is not None:
        result = [e for e in result if e.project == project]
    if action is not None:
        result = [e for e in result if e.action == action]
    if change_type is not None:
        result = [e for e in result if any(c.type == change_type for c in e.changes)]
    return result


def prune_logs(
    *,
    logs_dir: Path,
    retention_days: int,
    today: date | None = None,
) -> list[Path]:
    """Delete log files older than retention_days. Returns list of removed paths."""
    if not logs_dir.is_dir():
        return []

    if today is None:
        today = date.today()

    removed: list[Path] = []
    for log_file in sorted(logs_dir.glob("*.jsonl")):
        file_date = _parse_log_filename(log_file.name)
        if file_date is None:
            continue
        age_days = (today - file_date).days
        if age_days > retention_days:
            log_file.unlink()
            removed.append(log_file)

    return removed


def _parse_log_filename(filename: str) -> date | None:
    """Parse YYYY-MM-DD.jsonl into a date, or None if invalid."""
    if not filename.endswith(".jsonl"):
        return None
    stem = filename[:-6]  # strip ".jsonl"
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None
