"""Interactive CLI helpers using plain ``input()``."""

from __future__ import annotations

import sys
import tty
import termios


def is_interactive() -> bool:
    """Return True if stdin is a terminal."""
    return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


def select_from_list(
    items: list[str],
    *,
    prompt: str = "Select:",
    max_attempts: int = 3,
    clear_on_select: bool = False,
) -> str | None:
    """Display a numbered list and return the selected item, or None on failure.

    Prints numbered items like::

        Select:
          1. apple
          2. banana
          3. cherry
        Enter choice [1-3]:

    When *clear_on_select* is True the list is erased after a valid
    choice and replaced with a compact ``prompt → selected`` line.
    This keeps multi-step interactive flows tidy.

    Returns ``None`` on empty list, max attempts, ``EOFError``, or
    ``KeyboardInterrupt``.
    """
    if not items:
        return None

    print(f"\n{prompt}")
    for idx, item in enumerate(items, start=1):
        print(f"  {idx}. {item}")

    # Track lines printed so we can erase them on success.
    # heading (with leading blank line) + N items = N + 2 lines
    lines_printed = len(items) + 2
    extra_lines = 0  # error messages printed during retries

    try:
        for _ in range(max_attempts):
            raw = _input_with_escape(f"Enter choice [1-{len(items)}]: ")
            if raw is _ESCAPE:
                return None
            raw = raw.strip()
            extra_lines += 1  # the input line itself
            if not raw:
                continue
            try:
                choice = int(raw)
            except ValueError:
                print(f"  Enter a number between 1 and {len(items)}.")
                extra_lines += 1
                continue
            if 1 <= choice <= len(items):
                selected = items[choice - 1]
                if clear_on_select:
                    _clear_lines(lines_printed + extra_lines)
                return selected
            print(f"  Enter a number between 1 and {len(items)}.")
            extra_lines += 1
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    return None


def _clear_lines(n: int) -> None:
    """Move cursor up *n* lines and clear each one (ANSI escape)."""
    for _ in range(n):
        sys.stdout.write("\033[A\033[2K")
    sys.stdout.flush()


# Sentinel for ESC key detection.
_ESCAPE = object()


def _input_with_escape(prompt: str) -> str | object:
    """Read a line of input, returning :data:`_ESCAPE` if ESC is pressed.

    Uses raw terminal mode to detect the ESC key (``\\x1b``) immediately.
    Supports backspace for basic editing.  Falls back to plain ``input()``
    when stdin is not a real terminal.
    """
    if not is_interactive():
        return input(prompt)

    sys.stdout.write(prompt)
    sys.stdout.flush()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    buf: list[str] = []
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x1b":  # ESC
                sys.stdout.write("\r\033[2K")  # clear the prompt line
                sys.stdout.flush()
                return _ESCAPE
            if ch in ("\r", "\n"):  # Enter
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(buf)
            if ch in ("\x7f", "\x08"):  # Backspace
                if buf:
                    buf.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if ch == "\x03":  # Ctrl-C
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise KeyboardInterrupt
            if ch == "\x04":  # Ctrl-D
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise EOFError
            # Regular character
            buf.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def prompt_for_value(
    prompt: str,
    *,
    max_attempts: int = 3,
) -> str | None:
    """Prompt for a non-empty string value.  Returns ``None`` on failure.

    Rejects empty/whitespace-only input with retry.  ESC cancels immediately.
    Returns ``None`` on max attempts, ESC, ``EOFError``, ``KeyboardInterrupt``.
    """
    try:
        for _ in range(max_attempts):
            raw = _input_with_escape(prompt)
            if raw is _ESCAPE:
                return None
            stripped = raw.strip()
            if stripped:
                return stripped
    except (EOFError, KeyboardInterrupt):
        return None

    return None
