"""YAML-backed frontmatter parsing shared by agent and skill translation.

Frontmatter blocks are parsed with PyYAML's safe loader, then normalized into
the narrow runtime shapes the bridge supports:

- top-level mappings
- string keys
- string values
- nested lists and mappings composed of those same value shapes

Unsupported runtime shapes are rejected explicitly instead of being widened
silently after parsing.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from cc_codex_bridge.model import TranslationError
from cc_codex_bridge.text import read_utf8_text

# Keys whose plain-scalar values may contain YAML-confusing characters.
_QUOTABLE_KEY_RE = re.compile(r"^(tools|argument-hint):\s+")


class _FrontmatterSafeLoader(yaml.SafeLoader):
    """Safe YAML loader with block-scalar normalization for frontmatter."""


def _construct_frontmatter_scalar(
    _loader: _FrontmatterSafeLoader,
    node: yaml.nodes.ScalarNode,
) -> str:
    """Normalize YAML scalars into the bridge's historical string runtime shape."""
    value = node.value
    if node.style in {"|", ">"}:
        return value.rstrip("\n")
    return value


for _scalar_tag in (
    "tag:yaml.org,2002:str",
    "tag:yaml.org,2002:null",
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:timestamp",
    "tag:yaml.org,2002:binary",
):
    _FrontmatterSafeLoader.add_constructor(
        _scalar_tag,
        _construct_frontmatter_scalar,
    )


def parse_markdown_with_frontmatter(path: Path) -> tuple[dict[str, object], str]:
    """Parse YAML frontmatter plus markdown body."""
    content = read_utf8_text(path, label="frontmatter file", error_type=TranslationError)
    if not content.startswith("---\n"):
        return {}, content

    lines = content.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise TranslationError(f"Unclosed frontmatter in: {path}")

    frontmatter_lines = lines[1:end_index]
    body = "\n".join(lines[end_index + 1 :])
    frontmatter = parse_frontmatter_lines(frontmatter_lines)
    return frontmatter, body


def parse_frontmatter_from_content(content: str) -> dict[str, object]:
    """Parse frontmatter from in-memory content (no file I/O)."""
    if not content.startswith("---\n"):
        return {}

    lines = content.splitlines()
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise TranslationError("Unclosed frontmatter block")

    return parse_frontmatter_lines(lines[1:end_index])


def _quote_problematic_scalars(lines: list[str]) -> list[str]:
    """Quote frontmatter values that would confuse the YAML parser.

    Claude Code accepts ``tools: Read, Write, mcp__foo__bar`` and
    ``argument-hint: [person] [month]`` as plain scalars, but YAML
    may interpret colons, brackets, or other special characters in
    the value as mapping indicators or flow sequences.

    Strategy: try parsing each candidate line as YAML.  If it parses
    without error, leave it alone.  If it fails, wrap the value in
    double quotes so YAML treats it as a string.
    """
    result: list[str] = []
    for line in lines:
        m = _QUOTABLE_KEY_RE.match(line)
        if m:
            # Test whether YAML can parse this line as-is
            try:
                yaml.safe_load(line)
                result.append(line)
            except yaml.YAMLError:
                prefix = line[:m.end()]
                value = line[m.end():]
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                result.append(prefix + '"' + escaped + '"')
        else:
            result.append(line)
    return result


def parse_frontmatter_lines(lines: list[str]) -> dict[str, object]:
    """Parse frontmatter lines with safe YAML and normalize accepted shapes."""
    lines = _quote_problematic_scalars(lines)
    frontmatter_text = "\n".join(lines)
    if not frontmatter_text.strip():
        return {}

    try:
        parsed = yaml.load(frontmatter_text, Loader=_FrontmatterSafeLoader)
    except yaml.YAMLError as exc:
        raise TranslationError(_format_yaml_error(exc)) from exc

    return _normalize_frontmatter_mapping(parsed)


def _format_yaml_error(exc: yaml.YAMLError) -> str:
    """Render a stable user-facing YAML parse error."""
    problem = getattr(exc, "problem", None)
    mark = getattr(exc, "problem_mark", None)

    if problem and mark is not None:
        return (
            f"Malformed frontmatter YAML: {problem} "
            f"at line {mark.line + 1}, column {mark.column + 1}"
        )
    if problem:
        return f"Malformed frontmatter YAML: {problem}"
    return "Malformed frontmatter YAML"


def _normalize_frontmatter_mapping(value: object) -> dict[str, object]:
    """Normalize the parsed top-level mapping into bridge-supported values."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TranslationError(
            f"Frontmatter must be a YAML mapping, got: {type(value).__name__}"
        )

    normalized: dict[str, object] = {}
    for key, nested_value in value.items():
        normalized_key = _normalize_frontmatter_key(key, path="frontmatter")
        normalized[normalized_key] = _normalize_frontmatter_value(
            nested_value,
            path=f"frontmatter.{normalized_key}",
            active_nodes=set(),
        )
    return normalized


def _normalize_frontmatter_value(
    value: object,
    *,
    path: str,
    active_nodes: set[int],
) -> object:
    """Reject runtime shapes outside the bridge's supported frontmatter subset."""
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return _normalize_frontmatter_list(value, path=path, active_nodes=active_nodes)

    if isinstance(value, dict):
        return _normalize_frontmatter_nested_mapping(
            value,
            path=path,
            active_nodes=active_nodes,
        )

    raise TranslationError(
        f"Unsupported frontmatter value at {path}: {type(value).__name__}"
    )


def _normalize_frontmatter_list(
    value: list[object],
    *,
    path: str,
    active_nodes: set[int],
) -> list[object]:
    """Normalize one nested frontmatter list with recursion protection."""
    descended_nodes = _descend(active_nodes, value)
    return [
        _normalize_frontmatter_value(
            item,
            path=f"{path}[{index}]",
            active_nodes=descended_nodes,
        )
        for index, item in enumerate(value)
    ]


def _normalize_frontmatter_nested_mapping(
    value: dict[object, object],
    *,
    path: str,
    active_nodes: set[int],
) -> dict[str, object]:
    """Normalize one nested frontmatter mapping with recursion protection."""
    descended_nodes = _descend(active_nodes, value)
    normalized: dict[str, object] = {}
    for nested_key, nested_value in value.items():
        normalized_key = _normalize_frontmatter_key(nested_key, path=path)
        normalized[normalized_key] = _normalize_frontmatter_value(
            nested_value,
            path=f"{path}.{normalized_key}",
            active_nodes=descended_nodes,
        )
    return normalized


def _normalize_frontmatter_key(key: object, *, path: str) -> str:
    """Require all frontmatter mapping keys to be strings."""
    if not isinstance(key, str):
        raise TranslationError(
            f"Frontmatter keys must be strings at {path}, got: {key!r}"
        )
    return key


def _descend(active_nodes: set[int], value: object) -> set[int]:
    """Track active container nodes so recursive YAML aliases fail clearly."""
    value_id = id(value)
    if value_id in active_nodes:
        raise TranslationError("Recursive frontmatter aliases are not supported")
    return active_nodes | {value_id}
