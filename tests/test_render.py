"""Tests for cc_codex_bridge.render — shared rendering primitives."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cc_codex_bridge.exclusions import ExclusionReport
from cc_codex_bridge.render import (
    CHANGE_SYMBOLS,
    KEY_WIDTH,
    padded_key,
    render_change_line,
    render_change_list,
    render_exclusion_block,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


def _noop_colors() -> dict:
    """Return a no-op color dict (identity lambdas for all keys)."""
    noop = lambda s: s  # noqa: E731
    return {k: noop for k in ("key", "good", "warn", "bad", "create", "update", "remove", "dim", "cmd")}


# ---------------------------------------------------------------------------
# Minimal change object for render_change_list tests
# ---------------------------------------------------------------------------

class _Change:
    def __init__(self, kind: str, path: str | Path, resource_kind: str = ""):
        self.kind = kind
        self.path = path
        self.resource_kind = resource_kind


# ---------------------------------------------------------------------------
# KEY_WIDTH and CHANGE_SYMBOLS
# ---------------------------------------------------------------------------


def test_key_width_is_18():
    assert KEY_WIDTH == 18


def test_change_symbols_covers_all_kinds():
    assert CHANGE_SYMBOLS["create"] == "+"
    assert CHANGE_SYMBOLS["update"] == "~"
    assert CHANGE_SYMBOLS["remove"] == "-"


# ---------------------------------------------------------------------------
# padded_key
# ---------------------------------------------------------------------------


def test_padded_key_pads_to_key_width():
    result = strip_ansi(padded_key("VERSION"))
    expected = "VERSION:".ljust(KEY_WIDTH)
    assert result == expected


def test_padded_key_accepts_explicit_colors():
    c = _noop_colors()
    result = padded_key("VERSION", c)
    # With no-op colors there should be no ANSI escapes
    assert strip_ansi(result) == result
    assert result == "VERSION:".ljust(KEY_WIDTH)


def test_padded_key_short_key_still_reaches_key_width():
    result = strip_ansi(padded_key("K"))
    assert len(result) == KEY_WIDTH


def test_padded_key_longest_key_fits_exactly():
    # EXCLUDED_COMMANDS: → 18 chars with colon
    result = strip_ansi(padded_key("EXCLUDED_COMMANDS"))
    assert len(result) == KEY_WIDTH


# ---------------------------------------------------------------------------
# render_change_line
# ---------------------------------------------------------------------------


def test_render_change_line_create_contains_plus():
    line = strip_ansi(render_change_line("create", "path/to/file"))
    assert "+" in line
    assert "path/to/file" in line


def test_render_change_line_update_contains_tilde():
    line = strip_ansi(render_change_line("update", "path/to/file"))
    assert "~" in line
    assert "path/to/file" in line


def test_render_change_line_remove_contains_minus():
    line = strip_ansi(render_change_line("remove", "path/to/file"))
    assert "-" in line
    assert "path/to/file" in line


def test_render_change_line_resource_kind_shown_in_parens():
    line = strip_ansi(render_change_line("create", "some/path", "skill"))
    assert "(skill)" in line


def test_render_change_line_no_resource_kind_no_parens():
    line = strip_ansi(render_change_line("create", "some/path"))
    assert "(" not in line
    assert ")" not in line


def test_render_change_line_unknown_kind_uses_question_mark():
    line = strip_ansi(render_change_line("unknown_kind", "some/path"))
    assert "?" in line


def test_render_change_line_path_object_accepted():
    line = strip_ansi(render_change_line("create", Path("/some/path")))
    assert "/some/path" in line


def test_render_change_line_indented_with_two_spaces():
    line = strip_ansi(render_change_line("create", "x"))
    assert line.startswith("  ")


# ---------------------------------------------------------------------------
# render_change_list
# ---------------------------------------------------------------------------


def test_render_change_list_empty_returns_no_changes_message():
    result = render_change_list([])
    assert result == "All good. No changes needed."


def test_render_change_list_uses_change_symbols():
    changes = [
        _Change("create", "a"),
        _Change("update", "b"),
        _Change("remove", "c"),
    ]
    result = strip_ansi(render_change_list(changes))
    assert "+" in result
    assert "~" in result
    assert "-" in result
    # Should not contain spelled-out kind names as section headers
    assert "CREATE:" not in result
    assert "UPDATE:" not in result
    assert "REMOVE:" not in result


def test_render_change_list_custom_no_changes_message():
    result = render_change_list([], no_changes_message="Nothing to do.")
    assert result == "Nothing to do."


def test_render_change_list_multiple_changes_newline_separated():
    changes = [_Change("create", "a"), _Change("update", "b")]
    result = strip_ansi(render_change_list(changes))
    lines = result.splitlines()
    assert len(lines) == 2


def test_render_change_list_resource_kind_appears_in_parens():
    changes = [_Change("create", "x", "agent")]
    result = strip_ansi(render_change_list(changes))
    assert "(agent)" in result


# ---------------------------------------------------------------------------
# render_exclusion_block
# ---------------------------------------------------------------------------


def test_render_exclusion_block_empty_when_all_zero():
    report = ExclusionReport()
    result = render_exclusion_block(report)
    assert result == []


def test_render_exclusion_block_shows_non_zero_sections():
    report = ExclusionReport(plugins=("a/b",), skills=("skill-x",))
    lines = [strip_ansi(ln) for ln in render_exclusion_block(report)]
    joined = "\n".join(lines)
    assert "EXCLUDED_PLUGINS" in joined
    assert "EXCLUDED_SKILLS" in joined
    assert "a/b" in joined
    assert "skill-x" in joined


def test_render_exclusion_block_omits_zero_sections():
    # Only plugins non-zero → no SKILLS/AGENTS/COMMANDS headers
    report = ExclusionReport(plugins=("a/b",))
    lines = [strip_ansi(ln) for ln in render_exclusion_block(report)]
    joined = "\n".join(lines)
    assert "EXCLUDED_PLUGINS" in joined
    assert "EXCLUDED_SKILLS" not in joined
    assert "EXCLUDED_AGENTS" not in joined
    assert "EXCLUDED_COMMANDS" not in joined


def test_render_exclusion_block_entries_are_sorted():
    report = ExclusionReport(skills=("z-skill", "a-skill", "m-skill"))
    lines = [strip_ansi(ln) for ln in render_exclusion_block(report)]
    # Skip header line, collect entry lines
    entries = [ln.strip() for ln in lines if ln.startswith("  ")]
    assert entries == sorted(entries)


def test_render_exclusion_block_returns_list_of_strings():
    report = ExclusionReport(agents=("user/agent.md",))
    result = render_exclusion_block(report)
    assert isinstance(result, list)
    assert all(isinstance(item, str) for item in result)


def test_render_exclusion_block_header_shows_count():
    report = ExclusionReport(commands=("a/b/cmd.md", "a/b/other.md"))
    lines = [strip_ansi(ln) for ln in render_exclusion_block(report)]
    header = next(ln for ln in lines if "EXCLUDED_COMMANDS" in ln)
    assert "2" in header


def test_render_exclusion_block_all_four_categories():
    report = ExclusionReport(
        plugins=("p/q",),
        skills=("s",),
        agents=("user/a.md",),
        commands=("user/c.md",),
    )
    lines = [strip_ansi(ln) for ln in render_exclusion_block(report)]
    joined = "\n".join(lines)
    assert "EXCLUDED_PLUGINS" in joined
    assert "EXCLUDED_SKILLS" in joined
    assert "EXCLUDED_AGENTS" in joined
    assert "EXCLUDED_COMMANDS" in joined
