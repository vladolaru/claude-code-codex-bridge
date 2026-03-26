"""ANSI color helpers matching the argparse help theme (Python 3.14+)."""

from __future__ import annotations


def color_fns() -> dict[str, object]:
    """Return color callables matching the argparse help theme, or no-ops if unavailable."""
    try:
        from _colorize import can_colorize, get_theme
        if can_colorize():
            t = get_theme(force_color=True).argparse
            R = t.reset
            return {
                "key":    lambda s: f"{t.heading}{s}{R}",
                "good":   lambda s: f"{t.action}{s}{R}",
                "warn":   lambda s: f"{t.label}{s}{R}",
                "bad":    lambda s: f"\x1b[1;31m{s}{R}",
                "create": lambda s: f"{t.summary_short_option}{s}{R}",
                "update": lambda s: f"{t.summary_label}{s}{R}",
                "remove": lambda s: f"\x1b[31m{s}{R}",
                "dim":    lambda s: f"\x1b[2m{s}{R}",
            }
    except ImportError:
        pass
    noop = lambda s: s
    return {k: noop for k in ("key", "good", "warn", "bad", "create", "update", "remove", "dim")}
