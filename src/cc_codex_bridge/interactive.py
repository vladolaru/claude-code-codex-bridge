"""Interactive CLI helpers using plain ``input()``."""

from __future__ import annotations

import sys


def is_interactive() -> bool:
    """Return True if stdin is a terminal."""
    return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


def select_from_list(
    items: list[str],
    *,
    prompt: str = "Select:",
    max_attempts: int = 3,
) -> str | None:
    """Display a numbered list and return the selected item, or None on failure.

    Prints numbered items like::

        1. apple
        2. banana
        3. cherry

    Then prompts for selection.  Returns ``None`` on:

    * Empty list (no prompting)
    * Max attempts exceeded
    * ``EOFError`` (Ctrl-D)
    * ``KeyboardInterrupt`` (Ctrl-C)
    """
    if not items:
        return None

    print()
    for idx, item in enumerate(items, start=1):
        print(f"  {idx}. {item}")
    print()

    try:
        for _ in range(max_attempts):
            raw = input(f"{prompt} [1-{len(items)}]: ").strip()
            if not raw:
                continue
            try:
                choice = int(raw)
            except ValueError:
                print(f"  Enter a number between 1 and {len(items)}.")
                continue
            if 1 <= choice <= len(items):
                return items[choice - 1]
            print(f"  Enter a number between 1 and {len(items)}.")
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    return None


def prompt_for_value(
    prompt: str,
    *,
    max_attempts: int = 3,
) -> str | None:
    """Prompt for a non-empty string value.  Returns ``None`` on failure.

    Rejects empty/whitespace-only input with retry.
    Returns ``None`` on max attempts, ``EOFError``, ``KeyboardInterrupt``.
    """
    try:
        for _ in range(max_attempts):
            raw = input(prompt)
            stripped = raw.strip()
            if stripped:
                return stripped
    except (EOFError, KeyboardInterrupt):
        return None

    return None
