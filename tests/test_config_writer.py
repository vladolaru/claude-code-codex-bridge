"""Tests for TOML config read-modify-write helpers."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.config_writer import (
    add_to_string_list,
    read_config_data,
    remove_from_string_list,
    set_nested_value,
    write_config_data,
)


# --- read_config_data ---


def test_read_config_data_missing_file(tmp_path: Path) -> None:
    """Missing file returns empty dict."""
    result = read_config_data(tmp_path / "nonexistent.toml")
    assert result == {}


def test_read_config_data_existing_file(tmp_path: Path) -> None:
    """Existing TOML file is parsed correctly."""
    config = tmp_path / "config.toml"
    config.write_text('[section]\nkey = "value"\ncount = 42\n')
    result = read_config_data(config)
    assert result == {"section": {"key": "value", "count": 42}}


# --- write_config_data ---


def test_write_config_data_creates_file_and_parents(tmp_path: Path) -> None:
    """write_config_data creates parent directories and the file."""
    config = tmp_path / "deep" / "nested" / "config.toml"
    write_config_data(config, {"key": "value"})
    assert config.exists()
    assert config.parent.exists()


def test_write_config_data_roundtrip(tmp_path: Path) -> None:
    """Data survives a write-then-read roundtrip."""
    config = tmp_path / "config.toml"
    data = {
        "section": {"key": "value", "count": 42},
        "list_key": ["a", "b", "c"],
    }
    write_config_data(config, data)
    result = read_config_data(config)
    assert result == data


# --- add_to_string_list ---


def test_add_to_string_list_creates_new_key() -> None:
    """Adding to a non-existent key creates it."""
    data: dict = {}
    added = add_to_string_list(data, "items", "first")
    assert added is True
    assert data == {"items": ["first"]}


def test_add_to_string_list_appends_to_existing() -> None:
    """Adding to an existing list appends the value."""
    data: dict = {"items": ["a"]}
    added = add_to_string_list(data, "items", "b")
    assert added is True
    assert data == {"items": ["a", "b"]}


def test_add_to_string_list_rejects_duplicate() -> None:
    """Adding a duplicate value returns False and does not modify the list."""
    data: dict = {"items": ["a", "b"]}
    added = add_to_string_list(data, "items", "a")
    assert added is False
    assert data == {"items": ["a", "b"]}


# --- remove_from_string_list ---


def test_remove_from_string_list_removes_value() -> None:
    """Removing an existing value returns True and updates the list."""
    data: dict = {"items": ["a", "b", "c"]}
    removed = remove_from_string_list(data, "items", "b")
    assert removed is True
    assert data == {"items": ["a", "c"]}


def test_remove_from_string_list_not_found() -> None:
    """Removing a missing value returns False."""
    data: dict = {"items": ["a"]}
    removed = remove_from_string_list(data, "items", "z")
    assert removed is False
    assert data == {"items": ["a"]}


def test_remove_from_string_list_missing_key() -> None:
    """Removing from a non-existent key returns False."""
    data: dict = {}
    removed = remove_from_string_list(data, "items", "a")
    assert removed is False


# --- set_nested_value ---


def test_set_nested_value_creates_path() -> None:
    """Setting a deeply nested value creates intermediate dicts."""
    data: dict = {}
    set_nested_value(data, ["a", "b", "c"], 42)
    assert data == {"a": {"b": {"c": 42}}}


def test_set_nested_value_overwrites_existing() -> None:
    """Setting a value at an existing path overwrites it."""
    data: dict = {"a": {"b": {"c": "old"}}}
    set_nested_value(data, ["a", "b", "c"], "new")
    assert data == {"a": {"b": {"c": "new"}}}


def test_set_nested_value_single_key() -> None:
    """A single-element key path sets a top-level value."""
    data: dict = {}
    set_nested_value(data, ["top"], "value")
    assert data == {"top": "value"}
