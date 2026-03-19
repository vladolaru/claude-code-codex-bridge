"""Tests for plugin resource detection and path rewriting."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.model import GeneratedSkillFile
from cc_codex_bridge.vendor_plugin import (
    detect_plugin_resource_dirs,
    detect_transitive_plugin_dirs,
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


# -- transitive dependency detection --


def test_detect_transitive_finds_os_path_join_reference(tmp_path):
    """Scripts referencing os.path.join(plugin_root, "agents", ...) detect agents dir."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / "agents" / "shared").mkdir(parents=True)
    (plugin_root / "agents" / "shared" / "protocol.md").write_text("Protocol content")

    files = (
        GeneratedSkillFile(
            relative_path=Path("bootstrap.py"),
            content=b'protocol_path = os.path.join(plugin_root, "agents", "shared", "protocol.md")',
            mode=0o755,
        ),
    )
    dirs = detect_transitive_plugin_dirs(files, plugin_root)
    assert "agents" in dirs


def test_detect_transitive_finds_path_constructor_reference(tmp_path):
    """Scripts referencing Path(root, "dirname", ...) detect the directory."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / "data").mkdir(parents=True)
    (plugin_root / "data" / "config.json").write_text("{}")

    files = (
        GeneratedSkillFile(
            relative_path=Path("loader.py"),
            content=b'config = Path(plugin_root, "data", "config.json")',
            mode=0o644,
        ),
    )
    dirs = detect_transitive_plugin_dirs(files, plugin_root)
    assert "data" in dirs


def test_detect_transitive_ignores_nonexistent_dirs(tmp_path):
    """Only returns directories that actually exist at plugin root."""
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()

    files = (
        GeneratedSkillFile(
            relative_path=Path("script.py"),
            content=b'os.path.join(root, "nonexistent", "file")',
            mode=0o755,
        ),
    )
    dirs = detect_transitive_plugin_dirs(files, plugin_root)
    assert dirs == set()


def test_detect_transitive_skips_binary_files(tmp_path):
    """Binary files are skipped without error."""
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()

    files = (
        GeneratedSkillFile(
            relative_path=Path("data.bin"),
            content=b'\x00\x01\x02\xff\xfe',
            mode=0o644,
        ),
    )
    dirs = detect_transitive_plugin_dirs(files, plugin_root)
    assert dirs == set()


def test_detect_transitive_finds_multiple_dirs(tmp_path):
    """Multiple referenced directories are all detected."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / "agents").mkdir(parents=True)
    (plugin_root / "templates").mkdir(parents=True)

    files = (
        GeneratedSkillFile(
            relative_path=Path("bootstrap.py"),
            content=(
                b'agents_path = os.path.join(root, "agents", "shared")\n'
                b'tmpl_path = os.path.join(root, "templates", "base.html")\n'
            ),
            mode=0o755,
        ),
    )
    dirs = detect_transitive_plugin_dirs(files, plugin_root)
    assert dirs == {"agents", "templates"}


def test_detect_transitive_ignores_files_not_dirs(tmp_path):
    """References to names that are files (not dirs) at plugin root are ignored."""
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / "readme").write_text("Not a directory")

    files = (
        GeneratedSkillFile(
            relative_path=Path("script.py"),
            content=b'os.path.join(root, "readme", "something")',
            mode=0o755,
        ),
    )
    dirs = detect_transitive_plugin_dirs(files, plugin_root)
    assert dirs == set()


def test_detect_transitive_empty_files(tmp_path):
    """Empty file tuple returns no transitive dirs."""
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    dirs = detect_transitive_plugin_dirs((), plugin_root)
    assert dirs == set()
