"""Shared rendering primitives for CLI output.

Single source of truth for:
- Key-value column width (KEY_WIDTH)
- Change-line symbols and colors (consistent between reconcile, status, log)
- Exclusion block rendering (suppressed when all counts are zero)

Usage pattern in formatters::

    from cc_codex_bridge.render import padded_key, render_change_list, render_exclusion_block
    from cc_codex_bridge._colors import color_fns

    c = color_fns()
    lines = []
    lines.append(f"{padded_key('VERSION', c)} v{version}")
    lines.extend(render_exclusion_block(exclusion_report, c))
    print(render_change_list(changes))
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_WIDTH: int = 18
"""Standard key column width (including colon).

Based on the longest keys: EXCLUDED_COMMANDS: and GENERATED_PROMPTS: are both
18 characters.
"""

CHANGE_SYMBOLS: dict[str, str] = {
    "create": "+",
    "update": "~",
    "remove": "-",
}
"""Canonical symbols for change kinds. Single definition used everywhere."""


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def padded_key(key: str, c: dict | None = None) -> str:
    """Return a colored, colon-terminated key padded to KEY_WIDTH.

    Padding is applied *before* coloring so ANSI escape codes do not corrupt
    terminal column width.

    Args:
        key: The key label without colon (e.g. ``"VERSION"``).
        c: Color dict from :func:`~cc_codex_bridge._colors.color_fns`.
           Loaded lazily when ``None``.

    Returns:
        Padded and colored key string ready to use in an f-string.
    """
    if c is None:
        c = _load_colors()
    return c["key"](f"{key}:".ljust(KEY_WIDTH))


def render_change_line(
    kind: str,
    path: str | Path,
    resource_kind: str = "",
    c: dict | None = None,
) -> str:
    """Return a single indented colored change line.

    Args:
        kind: Change kind — ``"create"``, ``"update"``, or ``"remove"``.
        path: File path for the changed artifact.
        resource_kind: Optional resource type label shown in parentheses.
        c: Color dict from :func:`~cc_codex_bridge._colors.color_fns`.
           Loaded lazily when ``None``.

    Returns:
        A formatted line such as ``"  + path/to/file  (skill)"``.
    """
    if c is None:
        c = _load_colors()
    symbol = CHANGE_SYMBOLS.get(kind, "?")
    color_fn = c.get(kind, c["dim"])
    colored = color_fn(f"{symbol} {path}")
    if resource_kind:
        return f"  {colored}  {c['dim'](f'({resource_kind})')}"
    return f"  {colored}"


def render_change_list(
    changes: Iterable,
    *,
    no_changes_message: str = "All good. No changes needed.",
    c: dict | None = None,
) -> str:
    """Return a colored multi-line change list, or a no-changes message.

    Each item in *changes* must expose ``.kind``, ``.path``, and
    ``.resource_kind`` attributes (duck typing).

    Args:
        changes: Iterable of change objects.
        no_changes_message: Text returned when *changes* is empty.
        c: Color dict from :func:`~cc_codex_bridge._colors.color_fns`.
           Loaded lazily when ``None``.

    Returns:
        A newline-joined string of change lines, or *no_changes_message*.
    """
    if c is None:
        c = _load_colors()
    items = list(changes)
    if not items:
        return no_changes_message
    lines = [
        render_change_line(item.kind, item.path, item.resource_kind, c)
        for item in items
    ]
    return "\n".join(lines)


def render_exclusion_block(
    exclusion_report,
    c: dict | None = None,
) -> list[str]:
    """Return lines for the exclusion block, suppressed when all counts are zero.

    If all four categories (plugins, skills, agents, commands) on
    *exclusion_report* are empty, returns an empty list so callers can
    cleanly ``extend`` onto any output list without adding blank noise.

    Args:
        exclusion_report: An :class:`~cc_codex_bridge.exclusions.ExclusionReport`
            (or duck-typed equivalent) with ``.plugins``, ``.skills``,
            ``.agents``, and ``.commands`` iterables.
        c: Color dict from :func:`~cc_codex_bridge._colors.color_fns`.
           Loaded lazily when ``None``.

    Returns:
        A ``list[str]`` of header and entry lines (one per non-empty category
        and its entries), ready to be ``extend``-ed onto any ``lines`` list.
    """
    if c is None:
        c = _load_colors()

    categories = [
        ("EXCLUDED_PLUGINS", exclusion_report.plugins),
        ("EXCLUDED_SKILLS", exclusion_report.skills),
        ("EXCLUDED_AGENTS", exclusion_report.agents),
        ("EXCLUDED_COMMANDS", exclusion_report.commands),
    ]

    # Suppress entirely when all counts are zero.
    if all(not entries for _, entries in categories):
        return []

    lines: list[str] = []
    for label, entries in categories:
        if not entries:
            continue
        lines.append(f"{padded_key(label, c)} {len(entries)}")
        for entry in sorted(entries):
            lines.append(f"  {c['dim'](entry)}")
    return lines


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_colors() -> dict:
    """Return the default color function dict from :func:`~cc_codex_bridge._colors.color_fns`.

    Called lazily by public functions when no ``c`` argument is supplied,
    avoiding import-time side effects.
    """
    from cc_codex_bridge._colors import color_fns

    return color_fns()
