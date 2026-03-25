"""Tests for interactive CLI helpers."""

from __future__ import annotations

import io

import cc_codex_bridge.interactive as interactive_mod
from cc_codex_bridge.interactive import (
    _ESCAPE,
    is_interactive,
    prompt_for_value,
    select_from_list,
)


# -- is_interactive -----------------------------------------------------------


def test_is_interactive_non_tty(monkeypatch):
    """Non-TTY stdin returns False."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert is_interactive() is False


# -- select_from_list ---------------------------------------------------------


def test_select_from_list_valid_input(monkeypatch, capsys):
    """Valid numeric input returns the correct item."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "2")
    result = select_from_list(["apple", "banana", "cherry"], prompt="Pick:")
    assert result == "banana"
    captured = capsys.readouterr()
    assert "1. apple" in captured.out
    assert "2. banana" in captured.out
    assert "3. cherry" in captured.out


def test_select_from_list_first_item(monkeypatch):
    """Selecting first item works."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "1")
    result = select_from_list(["only"])
    assert result == "only"


def test_select_from_list_last_item(monkeypatch):
    """Selecting last item works."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "3")
    result = select_from_list(["a", "b", "c"])
    assert result == "c"


def test_select_from_list_out_of_range(monkeypatch):
    """Out-of-range input returns None after max_attempts."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "99")
    result = select_from_list(["apple", "banana"], max_attempts=3)
    assert result is None


def test_select_from_list_zero_input(monkeypatch):
    """Zero is out of range (1-based indexing)."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "0")
    result = select_from_list(["apple"], max_attempts=2)
    assert result is None


def test_select_from_list_negative_input(monkeypatch):
    """Negative numbers are rejected."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "-1")
    result = select_from_list(["apple"], max_attempts=2)
    assert result is None


def test_select_from_list_non_numeric_input(monkeypatch):
    """Non-numeric input is rejected and retried."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "banana")
    result = select_from_list(["apple", "banana"], max_attempts=2)
    assert result is None


def test_select_from_list_empty_list():
    """Empty list returns None without prompting."""
    result = select_from_list([])
    assert result is None


def test_select_from_list_eof(monkeypatch):
    """EOFError (Ctrl-D) returns None."""

    def raise_eof(_):
        raise EOFError

    monkeypatch.setattr(interactive_mod, "_input_with_escape", raise_eof)
    result = select_from_list(["apple", "banana"])
    assert result is None


def test_select_from_list_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt (Ctrl-C) returns None."""

    def raise_ki(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(interactive_mod, "_input_with_escape", raise_ki)
    result = select_from_list(["apple", "banana"])
    assert result is None


def test_select_from_list_escape_returns_none(monkeypatch):
    """ESC key returns None."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: _ESCAPE)
    result = select_from_list(["apple", "banana"])
    assert result is None


def test_select_from_list_retry_then_valid(monkeypatch):
    """Invalid input followed by valid input succeeds."""
    responses = iter(["bad", "2"])
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: next(responses))
    result = select_from_list(["apple", "banana"], max_attempts=3)
    assert result == "banana"


def test_select_from_list_clear_on_select(monkeypatch, capsys):
    """clear_on_select replaces the list with a compact summary."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "1")
    result = select_from_list(["alpha", "beta"], prompt="Pick:", clear_on_select=True)
    assert result == "alpha"
    captured = capsys.readouterr()
    # The summary line should appear in output
    assert "Pick: alpha" in captured.out


# -- prompt_for_value ---------------------------------------------------------


def test_prompt_for_value_returns_stripped(monkeypatch):
    """Returns stripped input."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "  hello  ")
    result = prompt_for_value("Enter:")
    assert result == "hello"


def test_prompt_for_value_rejects_empty_then_accepts(monkeypatch):
    """Rejects empty/whitespace input, retries, accepts valid."""
    responses = iter(["", "   ", "valid"])
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: next(responses))
    result = prompt_for_value("Enter:", max_attempts=3)
    assert result == "valid"


def test_prompt_for_value_all_empty(monkeypatch):
    """All empty inputs exhaust max_attempts and return None."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: "")
    result = prompt_for_value("Enter:", max_attempts=2)
    assert result is None


def test_prompt_for_value_eof(monkeypatch):
    """EOFError returns None."""

    def raise_eof(_):
        raise EOFError

    monkeypatch.setattr(interactive_mod, "_input_with_escape", raise_eof)
    result = prompt_for_value("Enter:")
    assert result is None


def test_prompt_for_value_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt returns None."""

    def raise_ki(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(interactive_mod, "_input_with_escape", raise_ki)
    result = prompt_for_value("Enter:")
    assert result is None


def test_prompt_for_value_escape_returns_none(monkeypatch):
    """ESC key returns None."""
    monkeypatch.setattr(interactive_mod, "_input_with_escape", lambda _: _ESCAPE)
    result = prompt_for_value("Enter:")
    assert result is None
