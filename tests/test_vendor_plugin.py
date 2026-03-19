"""Tests for plugin resource detection and path rewriting."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.vendor_plugin import (
    detect_plugin_resource_dirs,
    rewrite_plugin_paths,
)


# -- detection --

def test_detect_skill_base_directory_pattern():
    content = 'PLUGIN_ROOT="<skill base directory>/../.."\npython3 "$PLUGIN_ROOT/scripts/foo.py"'
    dirs = detect_plugin_resource_dirs(content)
    assert "scripts" in dirs


def test_detect_plugin_root_scripts_pattern():
    content = 'python3 $PLUGIN_ROOT/scripts/bootstrap-reviewer.py --agent x'
    dirs = detect_plugin_resource_dirs(content)
    assert "scripts" in dirs


def test_detect_plugin_root_skills_pattern():
    content = 'Read $PLUGIN_ROOT/skills/testing-patterns/references/test-philosophy.md'
    dirs = detect_plugin_resource_dirs(content)
    assert "skills" in dirs


def test_detect_plugin_root_dir_without_trailing_slash():
    content = '[ ! -d "$PLUGIN_ROOT/scripts" ] && echo missing'
    dirs = detect_plugin_resource_dirs(content)
    assert "scripts" in dirs


def test_detect_no_references():
    content = "Just some regular text with no plugin references."
    dirs = detect_plugin_resource_dirs(content)
    assert dirs == set()


def test_detect_multiple_dirs():
    content = (
        'python3 $PLUGIN_ROOT/scripts/foo.py\n'
        'Read $PLUGIN_ROOT/skills/bar/ref.md\n'
    )
    dirs = detect_plugin_resource_dirs(content)
    assert dirs == {"scripts", "skills"}


# -- rewriting --

def test_rewrite_skill_base_directory_placeholder():
    content = (
        'PLUGIN_ROOT="<skill base directory>/../.."\n'
        'python3 "$PLUGIN_ROOT/scripts/foo.py"\n'
    )
    result = rewrite_plugin_paths(content, Path("/home/user/.cc-codex-bridge/plugins/my-plugin"))
    assert '<skill base directory>/../..' not in result
    assert '/home/user/.cc-codex-bridge/plugins/my-plugin/scripts/foo.py' in result


def test_rewrite_plugin_root_variable():
    content = 'python3 $PLUGIN_ROOT/scripts/bootstrap-reviewer.py --agent x'
    result = rewrite_plugin_paths(content, Path("/bridge/plugins/tools"))
    assert '$PLUGIN_ROOT/scripts' not in result
    assert '/bridge/plugins/tools/scripts/bootstrap-reviewer.py' in result


def test_rewrite_plugin_root_with_braces():
    content = 'python3 ${PLUGIN_ROOT}/scripts/foo.py'
    result = rewrite_plugin_paths(content, Path("/bridge/plugins/tools"))
    assert '${PLUGIN_ROOT}' not in result
    assert '/bridge/plugins/tools/scripts/foo.py' in result


def test_rewrite_preserves_non_plugin_content():
    content = 'Just regular text.\nNo plugin paths here.\n'
    result = rewrite_plugin_paths(content, Path("/bridge/plugins/tools"))
    assert result == content


def test_rewrite_claude_cache_discovery_pattern():
    content = (
        "PLUGIN_ROOT=$(cat /tmp/.pirategoat-tools-root 2>/dev/null)\n"
        '[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && '
        "PLUGIN_ROOT=$(find ~/.claude -path \"*/pirategoat-tools/*/scripts/bootstrap-reviewer.py\" "
        "-type f 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname)\n"
        'python3 $PLUGIN_ROOT/scripts/bootstrap-reviewer.py --agent security-reviewer\n'
    )
    result = rewrite_plugin_paths(content, Path("/bridge/plugins/pt"))
    # The discovery machinery should be replaced with a direct path
    assert '/bridge/plugins/pt/scripts/bootstrap-reviewer.py' in result
    # The original discovery lines should be removed or replaced
    assert 'find ~/.claude' not in result
    assert '/tmp/.pirategoat-tools-root' not in result
