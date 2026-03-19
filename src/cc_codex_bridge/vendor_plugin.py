"""Plugin resource detection, vendoring, and path rewriting.

Detects references to plugin-level resources ($PLUGIN_ROOT/scripts/,
<skill base directory>/../.., etc.) in skill and agent content, and
rewrites them to absolute paths pointing at vendored copies under
the bridge home directory.

Also provides ``read_plugin_dir_files()``, the canonical file-walking
helper for reading plugin resource directories into ``GeneratedSkillFile``
tuples.  Both skill and agent translation paths use this helper.
"""

from __future__ import annotations

import re
from pathlib import Path

from cc_codex_bridge.model import GeneratedSkillFile, TranslationError

_IGNORED_NAMES = frozenset({".DS_Store", "__pycache__"})


def read_plugin_dir_files(source_dir: Path) -> tuple[GeneratedSkillFile, ...]:
    """Read all files from a plugin directory into GeneratedSkillFile objects.

    Filters out noise files (.DS_Store, __pycache__, .pyc) and rejects
    symlinked files and directories.  This is the canonical file-walking
    helper for plugin resource vendoring — both skill and agent translation
    paths use it.
    """
    entries: list[GeneratedSkillFile] = []
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            kind = "directory" if path.is_dir() else "file"
            raise TranslationError(
                f"Refusing to follow symlinked {kind}: {path}"
            )
        if path.is_dir():
            continue
        if any(part in _IGNORED_NAMES for part in path.parts):
            continue
        if path.suffix == ".pyc":
            continue
        entries.append(GeneratedSkillFile(
            relative_path=path.relative_to(source_dir),
            content=path.read_bytes(),
            mode=path.stat().st_mode & 0o777,
        ))
    return tuple(entries)


# Patterns that reference plugin-level directories.
# Matches $PLUGIN_ROOT/dir/, ${PLUGIN_ROOT}/dir/, or
# "<skill base directory>/../../dir/" forms.
_PLUGIN_ROOT_DIR_RE = re.compile(
    r'(?:\$PLUGIN_ROOT|\$\{PLUGIN_ROOT\}|"<skill base directory>/\.\./\.\.")'
    r"/(?P<dir>[A-Za-z0-9._-]+)(?=/|\s|$|\")"
)

# The multi-line discovery block that agents use to find PLUGIN_ROOT
# at runtime by probing a cache file then falling back to find(1).
_DISCOVERY_BLOCK_RE = re.compile(
    r'PLUGIN_ROOT=\$\(cat /tmp/\.[\w-]+-root[^\n]*\)\n'
    r'[^\n]*\[ -z[^\n]*PLUGIN_ROOT=\$\(find ~/\.claude[^\n]*\n',
    re.MULTILINE,
)

# The simple assignment pattern: PLUGIN_ROOT="<skill base directory>/../.."
_PLUGIN_ROOT_ASSIGN_RE = re.compile(
    r'PLUGIN_ROOT="<skill base directory>/\.\./\.\."[^\n]*\n'
)

# All forms of $PLUGIN_ROOT / ${PLUGIN_ROOT} / "<skill base directory>/../.."
# path references (followed by /), used for the final substitution pass.
_PLUGIN_ROOT_REF_RE = re.compile(
    r'(?:\$PLUGIN_ROOT|\$\{PLUGIN_ROOT\}|"<skill base directory>/\.\./\.\.")'
    r"(?=/)"
)


def detect_plugin_resource_dirs(content: str) -> set[str]:
    """Detect which plugin-level directories are referenced in *content*.

    Returns a set of directory names (e.g., ``{"scripts", "skills"}``).
    """
    return set(_PLUGIN_ROOT_DIR_RE.findall(content))


def rewrite_plugin_paths(content: str, vendored_root: Path) -> str:
    """Rewrite plugin resource references to point at *vendored_root*.

    Replaces ``$PLUGIN_ROOT``, ``${PLUGIN_ROOT}``, and
    ``<skill base directory>/../..`` with the absolute *vendored_root*
    path.  Also removes the multi-line discovery block that agents use
    to locate the plugin root at runtime.
    """
    result = content
    root_str = str(vendored_root)

    # Remove the discovery block (cat /tmp/... + find ~/.claude ...)
    result = _DISCOVERY_BLOCK_RE.sub("", result)

    # Remove the simple assignment line
    result = _PLUGIN_ROOT_ASSIGN_RE.sub("", result)

    # Replace all remaining $PLUGIN_ROOT references with the absolute path
    result = _PLUGIN_ROOT_REF_RE.sub(root_str, result)

    return result


# Patterns for transitive dependency detection in vendored Python scripts.
# Matches os.path.join(..., "dirname", ...) and Path(..., "dirname", ...) calls
# where dirname looks like a directory name (lowercase letters, digits, hyphens,
# underscores).
_PYTHON_PATH_JOIN_DIR_RE = re.compile(
    r'(?:os\.path\.join|Path)\s*\([^)]*?["\'](?P<dir>[a-z][a-z0-9_-]*)["\']'
)


def detect_transitive_plugin_dirs(
    files: tuple[GeneratedSkillFile, ...],
    plugin_source: Path,
) -> set[str]:
    """Scan vendored file content for references to other plugin directories.

    Detects patterns like ``os.path.join(plugin_root, "agents", ...)`` in
    Python scripts that reference sibling directories at the plugin root.
    Only returns directory names that actually exist at the plugin source root.
    """
    referenced: set[str] = set()
    for f in files:
        try:
            content = f.content.decode()
        except UnicodeDecodeError:
            continue
        for match in _PYTHON_PATH_JOIN_DIR_RE.finditer(content):
            dir_name = match.group("dir")
            if (plugin_source / dir_name).is_dir():
                referenced.add(dir_name)
    return referenced
