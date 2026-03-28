"""Tests for surgical TOML config editing (MCP server entries)."""

from __future__ import annotations

from pathlib import Path

import tomlkit

import pytest

from cc_codex_bridge.toml_config import (
    _dict_to_toml_table,
    apply_mcp_changes,
    hash_mcp_server_table,
    read_codex_config,
    write_codex_config,
)


# --- read_codex_config ---


def test_read_nonexistent_file(tmp_path: Path) -> None:
    """Missing file returns empty TOMLDocument."""
    doc = read_codex_config(tmp_path / "config.toml")
    assert isinstance(doc, tomlkit.TOMLDocument)
    assert len(doc) == 0


def test_read_empty_file(tmp_path: Path) -> None:
    """Empty file returns empty TOMLDocument."""
    config = tmp_path / "config.toml"
    config.write_text("")
    doc = read_codex_config(config)
    assert isinstance(doc, tomlkit.TOMLDocument)
    assert len(doc) == 0


def test_read_existing_config(tmp_path: Path) -> None:
    """Existing TOML file is parsed into a TOMLDocument."""
    config = tmp_path / "config.toml"
    config.write_text('model = "o3"\nsandbox_mode = "workspace-write"\n')
    doc = read_codex_config(config)
    assert doc["model"] == "o3"
    assert doc["sandbox_mode"] == "workspace-write"


def test_read_codex_config_raises_on_corrupt_toml(tmp_path: Path) -> None:
    """Corrupt TOML should raise a clear error, not a raw tomlkit exception."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[broken\nthis is not valid TOML", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid TOML"):
        read_codex_config(config_path)


# --- write_codex_config ---


def test_write_creates_file_and_parents(tmp_path: Path) -> None:
    """write_codex_config creates parent directories and the file."""
    config = tmp_path / "deep" / "nested" / "config.toml"
    doc = tomlkit.document()
    doc.add("key", "value")
    write_codex_config(config, doc)
    assert config.exists()
    assert config.parent.exists()


def test_write_roundtrip(tmp_path: Path) -> None:
    """Data survives a write-then-read roundtrip."""
    config = tmp_path / "config.toml"
    doc = tomlkit.document()
    doc.add("model", "o3")
    write_codex_config(config, doc)
    result = read_codex_config(config)
    assert result["model"] == "o3"


def test_write_atomic_file_exists(tmp_path: Path) -> None:
    """After write, the target file exists (temp file was renamed)."""
    config = tmp_path / "config.toml"
    doc = tomlkit.document()
    doc.add("key", "value")
    write_codex_config(config, doc)
    assert config.exists()
    # Temp file should not remain.
    tmp_file = config.with_suffix(".toml.tmp")
    assert not tmp_file.exists()


def test_write_empty_doc_removes_file(tmp_path: Path) -> None:
    """Writing an empty document removes the file if it exists."""
    config = tmp_path / "config.toml"
    config.write_text('key = "value"\n')
    assert config.exists()
    doc = tomlkit.document()
    write_codex_config(config, doc)
    assert not config.exists()


def test_write_empty_doc_no_error_if_missing(tmp_path: Path) -> None:
    """Writing an empty document when file doesn't exist is a no-op."""
    config = tmp_path / "config.toml"
    doc = tomlkit.document()
    write_codex_config(config, doc)
    assert not config.exists()


# --- apply_mcp_changes ---


def test_add_server_to_empty_doc() -> None:
    """Adding a server to an empty doc creates the mcp_servers section."""
    doc = tomlkit.document()
    desired = {"my-server": {"command": "npx", "args": ["-y", "my-server"]}}
    result = apply_mcp_changes(doc, desired=desired, owned=set())
    assert result == {"added": ["my-server"], "updated": [], "removed": []}
    assert "mcp_servers" in doc
    assert doc["mcp_servers"]["my-server"]["command"] == "npx"


def test_add_server_preserves_existing_sections() -> None:
    """Adding MCP servers preserves non-MCP config sections."""
    doc = tomlkit.document()
    doc.add("model", "o3")
    doc.add("sandbox_mode", "workspace-write")
    desired = {"test-srv": {"command": "test"}}
    apply_mcp_changes(doc, desired=desired, owned=set())
    assert doc["model"] == "o3"
    assert doc["sandbox_mode"] == "workspace-write"
    assert doc["mcp_servers"]["test-srv"]["command"] == "test"


def test_update_owned_server() -> None:
    """Updating an owned server replaces its content."""
    doc = tomlkit.document()
    mcp = tomlkit.table(is_super_table=True)
    srv = tomlkit.table()
    srv.add("command", "old-cmd")
    mcp.add("my-srv", srv)
    doc.add("mcp_servers", mcp)

    desired = {"my-srv": {"command": "new-cmd", "args": ["--flag"]}}
    result = apply_mcp_changes(doc, desired=desired, owned={"my-srv"})
    assert result == {"added": [], "updated": ["my-srv"], "removed": []}
    assert doc["mcp_servers"]["my-srv"]["command"] == "new-cmd"
    assert doc["mcp_servers"]["my-srv"]["args"] == ["--flag"]


def test_remove_owned_server() -> None:
    """Owned server not in desired is removed."""
    doc = tomlkit.document()
    mcp = tomlkit.table(is_super_table=True)
    srv = tomlkit.table()
    srv.add("command", "old-cmd")
    mcp.add("my-srv", srv)
    doc.add("mcp_servers", mcp)

    result = apply_mcp_changes(doc, desired={}, owned={"my-srv"})
    assert result == {"added": [], "updated": [], "removed": ["my-srv"]}
    # mcp_servers section should be removed since it's now empty.
    assert "mcp_servers" not in doc


def test_never_touch_non_owned_server() -> None:
    """A server not in 'owned' set is never modified or removed."""
    doc = tomlkit.document()
    mcp = tomlkit.table(is_super_table=True)
    user_srv = tomlkit.table()
    user_srv.add("command", "user-cmd")
    mcp.add("user-server", user_srv)
    doc.add("mcp_servers", mcp)

    # Try to add a server with the same name (but we don't own it).
    desired = {"user-server": {"command": "bridge-cmd"}}
    result = apply_mcp_changes(doc, desired=desired, owned=set())
    assert result == {"added": [], "updated": [], "removed": []}
    # User's original command must be preserved.
    assert doc["mcp_servers"]["user-server"]["command"] == "user-cmd"


def test_preserve_comments(tmp_path: Path) -> None:
    """Comments in the file survive MCP edits through roundtrip."""
    config = tmp_path / "config.toml"
    original = '# Main settings\nmodel = "o3"\n'
    config.write_text(original)

    doc = read_codex_config(config)
    desired = {"srv": {"command": "cmd"}}
    apply_mcp_changes(doc, desired=desired, owned=set())
    write_codex_config(config, doc)

    content = config.read_text()
    assert "# Main settings" in content
    assert 'model = "o3"' in content


def test_remove_empty_mcp_servers_section() -> None:
    """mcp_servers section is removed when last server is removed."""
    doc = tomlkit.document()
    mcp = tomlkit.table(is_super_table=True)
    srv = tomlkit.table()
    srv.add("command", "cmd")
    mcp.add("only-srv", srv)
    doc.add("mcp_servers", mcp)

    apply_mcp_changes(doc, desired={}, owned={"only-srv"})
    assert "mcp_servers" not in doc


def test_empty_desired_empty_owned_no_changes() -> None:
    """Empty desired + empty owned = no changes."""
    doc = tomlkit.document()
    doc.add("model", "o3")
    result = apply_mcp_changes(doc, desired={}, owned=set())
    assert result == {"added": [], "updated": [], "removed": []}
    assert "mcp_servers" not in doc


def test_mixed_owned_and_unowned_servers() -> None:
    """Mix of owned and unowned servers: only owned are touched."""
    doc = tomlkit.document()
    mcp = tomlkit.table(is_super_table=True)

    user_srv = tomlkit.table()
    user_srv.add("command", "user-cmd")
    mcp.add("user-server", user_srv)

    bridge_srv = tomlkit.table()
    bridge_srv.add("command", "old-bridge-cmd")
    mcp.add("bridge-server", bridge_srv)

    doc.add("mcp_servers", mcp)

    desired = {"bridge-server": {"command": "new-bridge-cmd"}, "new-srv": {"command": "new"}}
    result = apply_mcp_changes(doc, desired=desired, owned={"bridge-server"})

    assert "bridge-server" in result["updated"]
    assert "new-srv" in result["added"]
    assert result["removed"] == []
    assert doc["mcp_servers"]["user-server"]["command"] == "user-cmd"
    assert doc["mcp_servers"]["bridge-server"]["command"] == "new-bridge-cmd"
    assert doc["mcp_servers"]["new-srv"]["command"] == "new"


# --- hash_mcp_server_table ---


def test_hash_deterministic() -> None:
    """hash_mcp_server_table produces a sha256:-prefixed hash."""
    table = {"command": "npx", "args": ["-y", "server"]}
    h = hash_mcp_server_table(table)
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64  # SHA-256 hex digest is 64 chars.


def test_hash_stable_across_calls() -> None:
    """Same input produces same hash."""
    table = {"command": "npx", "args": ["-y", "server"], "env": {"KEY": "val"}}
    h1 = hash_mcp_server_table(table)
    h2 = hash_mcp_server_table(table)
    assert h1 == h2


def test_hash_independent_of_key_order() -> None:
    """Dicts with same keys in different insertion order hash identically."""
    t1 = {"command": "npx", "args": ["-y"], "env": {"A": "1"}}
    t2 = {"env": {"A": "1"}, "args": ["-y"], "command": "npx"}
    assert hash_mcp_server_table(t1) == hash_mcp_server_table(t2)


def test_hash_differs_for_different_content() -> None:
    """Different content produces different hashes."""
    t1 = {"command": "npx", "args": ["-y", "server-a"]}
    t2 = {"command": "npx", "args": ["-y", "server-b"]}
    assert hash_mcp_server_table(t1) != hash_mcp_server_table(t2)


# --- _dict_to_toml_table ---


def test_dict_to_toml_table_simple() -> None:
    """Simple dict converts to a tomlkit Table."""
    result = _dict_to_toml_table({"command": "npx", "args": ["-y"]})
    assert isinstance(result, tomlkit.items.Table)
    assert result["command"] == "npx"
    assert result["args"] == ["-y"]


def test_dict_to_toml_table_nested_dicts() -> None:
    """Nested dicts (like env) become sub-tables."""
    result = _dict_to_toml_table({"command": "cmd", "env": {"KEY": "val", "OTHER": "x"}})
    assert isinstance(result, tomlkit.items.Table)
    assert isinstance(result["env"], tomlkit.items.Table)
    assert result["env"]["KEY"] == "val"
    assert result["env"]["OTHER"] == "x"


# --- Roundtrip: non-MCP content preserved exactly ---


def test_roundtrip_non_mcp_content_exact(tmp_path: Path) -> None:
    """File with non-MCP content is preserved exactly after MCP edits."""
    config = tmp_path / "config.toml"
    original = (
        '# User config\n'
        'model = "o3"\n'
        'sandbox_mode = "workspace-write"\n'
        '\n'
        '[history]\n'
        'persistence = true\n'
    )
    config.write_text(original)

    doc = read_codex_config(config)
    desired = {"my-srv": {"command": "cmd"}}
    apply_mcp_changes(doc, desired=desired, owned=set())
    write_codex_config(config, doc)

    content = config.read_text()
    # All original lines should be present.
    assert '# User config' in content
    assert 'model = "o3"' in content
    assert 'sandbox_mode = "workspace-write"' in content
    assert 'persistence = true' in content
    # MCP server should be added.
    assert 'my-srv' in content


# --- Add server renders as super table ---


def test_add_server_renders_as_super_table(tmp_path: Path) -> None:
    """Added server renders as [mcp_servers.name] in TOML output."""
    config = tmp_path / "config.toml"
    doc = tomlkit.document()
    desired = {"my-server": {"command": "npx", "args": ["-y", "my-server"]}}
    apply_mcp_changes(doc, desired=desired, owned=set())
    write_codex_config(config, doc)

    content = config.read_text()
    assert "[mcp_servers.my-server]" in content
