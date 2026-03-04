"""Tests for package-level metadata."""

from __future__ import annotations

from cc_codex_bridge import __version__


def test_package_exposes_version():
    """The package exposes a string version for runtime introspection."""
    assert __version__ == "0.1.0"
