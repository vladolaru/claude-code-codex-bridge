"""Tests for config scan add/remove/list command handlers."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.config_scan_commands import (
    ScanCommandResult,
    ScanListResult,
    handle_scan_add,
    handle_scan_list,
    handle_scan_remove,
)
from cc_codex_bridge.config_writer import read_config_data, write_config_data


# ---------------------------------------------------------------------------
# handle_scan_add
# ---------------------------------------------------------------------------


def test_scan_add_valid_glob_succeeds(tmp_path: Path) -> None:
    """Adding a glob that matches directories succeeds."""
    # Create directories that match the glob.
    (tmp_path / "projects" / "alpha").mkdir(parents=True)
    (tmp_path / "projects" / "beta").mkdir(parents=True)

    config_path = tmp_path / "config.toml"

    pattern = str(tmp_path / "projects" / "*")
    result = handle_scan_add(pattern=pattern, config_path=config_path)

    assert result.success is True
    assert "2" in result.message  # match count

    data = read_config_data(config_path)
    assert pattern in data.get("scan_paths", [])


def test_scan_add_no_match_fails(tmp_path: Path) -> None:
    """Adding a glob that matches nothing fails."""
    config_path = tmp_path / "config.toml"

    pattern = str(tmp_path / "nonexistent" / "*")
    result = handle_scan_add(pattern=pattern, config_path=config_path)

    assert result.success is False
    assert "no directories" in result.message.lower() or "no match" in result.message.lower()


def test_scan_add_duplicate_fails(tmp_path: Path) -> None:
    """Adding a pattern that already exists in scan_paths fails."""
    (tmp_path / "projects" / "alpha").mkdir(parents=True)

    config_path = tmp_path / "config.toml"
    pattern = str(tmp_path / "projects" / "*")

    # Add once.
    result1 = handle_scan_add(pattern=pattern, config_path=config_path)
    assert result1.success is True

    # Try to add again.
    result2 = handle_scan_add(pattern=pattern, config_path=config_path)
    assert result2.success is False
    assert "already" in result2.message.lower()


def test_scan_add_stores_pattern_not_expanded(tmp_path: Path) -> None:
    """The stored value is the original pattern, not each expanded path."""
    (tmp_path / "projects" / "alpha").mkdir(parents=True)

    config_path = tmp_path / "config.toml"
    pattern = str(tmp_path / "projects" / "*")
    handle_scan_add(pattern=pattern, config_path=config_path)

    data = read_config_data(config_path)
    assert data["scan_paths"] == [pattern]


def test_scan_add_file_only_match_fails(tmp_path: Path) -> None:
    """A glob matching only files (not directories) fails."""
    (tmp_path / "just-a-file.txt").write_text("hello")

    config_path = tmp_path / "config.toml"
    pattern = str(tmp_path / "just-a-file.txt")
    result = handle_scan_add(pattern=pattern, config_path=config_path)

    assert result.success is False


# ---------------------------------------------------------------------------
# handle_scan_remove
# ---------------------------------------------------------------------------


def test_scan_remove_existing_succeeds(tmp_path: Path) -> None:
    """Removing an existing scan path succeeds."""
    config_path = tmp_path / "config.toml"
    pattern = "~/Work/projects/*"
    write_config_data(config_path, {"scan_paths": [pattern]})

    result = handle_scan_remove(pattern=pattern, config_path=config_path)

    assert result.success is True

    data = read_config_data(config_path)
    assert pattern not in data.get("scan_paths", [])


def test_scan_remove_not_found_fails(tmp_path: Path) -> None:
    """Removing a pattern not in scan_paths fails."""
    config_path = tmp_path / "config.toml"
    write_config_data(config_path, {"scan_paths": ["~/Work/other/*"]})

    result = handle_scan_remove(pattern="~/nonexistent/*", config_path=config_path)

    assert result.success is False
    assert "not found" in result.message.lower()


def test_scan_remove_from_empty_config_fails(tmp_path: Path) -> None:
    """Removing from a non-existent config file fails."""
    config_path = tmp_path / "config.toml"

    result = handle_scan_remove(pattern="~/anything/*", config_path=config_path)

    assert result.success is False


# ---------------------------------------------------------------------------
# handle_scan_list
# ---------------------------------------------------------------------------


def test_scan_list_empty(tmp_path: Path) -> None:
    """Listing with no config returns empty tuples."""
    config_path = tmp_path / "config.toml"

    result = handle_scan_list(config_path=config_path)

    assert isinstance(result, ScanListResult)
    assert result.paths == ()
    assert result.exclude_paths == ()


def test_scan_list_with_paths(tmp_path: Path) -> None:
    """Listing returns configured scan_paths and exclude_paths."""
    config_path = tmp_path / "config.toml"
    write_config_data(config_path, {
        "scan_paths": ["~/Work/a/*", "~/Work/b/*"],
        "exclude_paths": ["~/Work/a/skip"],
    })

    result = handle_scan_list(config_path=config_path)

    assert result.paths == ("~/Work/a/*", "~/Work/b/*")
    assert result.exclude_paths == ("~/Work/a/skip",)


def test_scan_list_returns_tuples(tmp_path: Path) -> None:
    """ScanListResult fields are tuples (immutable)."""
    config_path = tmp_path / "config.toml"
    write_config_data(config_path, {"scan_paths": ["~/foo"]})

    result = handle_scan_list(config_path=config_path)

    assert isinstance(result.paths, tuple)
    assert isinstance(result.exclude_paths, tuple)
