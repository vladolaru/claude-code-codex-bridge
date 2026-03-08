"""Translation from Claude agent markdown to Codex roles."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from cc_codex_bridge.model import GeneratedAgentRole, InstalledPlugin, TranslationError


TOOL_TRANSLATIONS = {
    "Read": "read",
    "Glob": "glob",
    "Grep": "grep",
    "Write": "write",
    "Bash": "bash",
    "WebSearch": "web_search",
}

ROLE_NAMESPACE_RE = re.compile(r"[^A-Za-z0-9_-]+")
ROLE_AGENT_RE = re.compile(r"[^A-Za-z0-9_]+")
PROMPT_COMPONENT_RE = re.compile(r"[^A-Za-z0-9-]+")


@dataclass(frozen=True)
class _RawAgentRole:
    """Intermediate translated agent before collision resolution."""

    marketplace: str
    plugin_name: str
    source_path: Path
    description: str
    original_model_hint: str | None
    tools: tuple[str, ...]
    prompt_body: str
    role_name_base: str
    role_name_prefix: str
    prompt_stem_base: str
    prompt_stem_prefix: str


def translate_installed_agents(
    plugins: Iterable[InstalledPlugin],
    *,
    default_model: str = "gpt-5.3-codex",
) -> tuple[GeneratedAgentRole, ...]:
    """Translate installed Claude agent files into Codex role definitions."""
    raw_roles: list[_RawAgentRole] = []

    for plugin in plugins:
        for agent_path in plugin.agents:
            frontmatter, body = parse_markdown_with_frontmatter(agent_path)
            agent_name = str(frontmatter.get("name", "")).strip()
            if not agent_name:
                raise TranslationError(f"Agent missing required name frontmatter: {agent_path}")

            description = str(frontmatter.get("description", "")).strip()
            if not description:
                raise TranslationError(
                    f"Agent missing required description frontmatter: {agent_path}"
                )

            raw_roles.append(
                _RawAgentRole(
                    marketplace=plugin.marketplace,
                    plugin_name=plugin.plugin_name,
                    source_path=agent_path,
                    description=description,
                    original_model_hint=_optional_str(frontmatter.get("model")),
                    tools=translate_tools(frontmatter.get("tools")),
                    prompt_body=body.strip() + ("\n" if body.strip() else ""),
                    role_name_base=(
                        f"{_normalize_role_namespace(plugin.plugin_name, kind='plugin name')}_"
                        f"{_normalize_name(agent_name)}"
                    ),
                    role_name_prefix=_normalize_role_namespace(
                        plugin.marketplace,
                        kind="marketplace",
                    ),
                    prompt_stem_base=(
                        f"{_normalize_prompt_component(plugin.plugin_name, kind='plugin name')}-"
                        f"{_normalize_prompt_component(agent_name, kind='agent name')}"
                    ),
                    prompt_stem_prefix=_normalize_prompt_component(
                        plugin.marketplace,
                        kind="marketplace",
                    ),
                )
            )

    role_names = _resolve_role_names(raw_roles)
    prompt_stems = _resolve_prompt_stems(raw_roles)
    roles = [
        GeneratedAgentRole(
            plugin_name=raw_role.plugin_name,
            source_path=raw_role.source_path,
            role_name=role_names[id(raw_role)],
            description=raw_role.description,
            original_model_hint=raw_role.original_model_hint,
            model=default_model,
            tools=raw_role.tools,
            prompt_relpath=Path("prompts") / "agents" / f"{prompt_stems[id(raw_role)]}.md",
            prompt_body=raw_role.prompt_body,
        )
        for raw_role in raw_roles
    ]

    return tuple(sorted(roles, key=lambda role: role.role_name))


def parse_markdown_with_frontmatter(path: Path) -> tuple[dict[str, object], str]:
    """Parse simple YAML-like frontmatter plus markdown body."""
    content = path.read_text()
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
    frontmatter = _parse_frontmatter_lines(frontmatter_lines)
    return frontmatter, body


def translate_tools(raw_tools: object) -> tuple[str, ...]:
    """Translate Claude tool names into Codex tool identifiers."""
    if raw_tools is None:
        return ()
    if not isinstance(raw_tools, list):
        raise TranslationError(f"Agent tools must be a list, got: {type(raw_tools).__name__}")

    translated: list[str] = []
    for tool in raw_tools:
        if not isinstance(tool, str):
            raise TranslationError(f"Agent tool entry must be a string, got: {tool!r}")
        translated_tool = TOOL_TRANSLATIONS.get(tool)
        if translated_tool and translated_tool not in translated:
            translated.append(translated_tool)

    return tuple(sorted(translated))


def _resolve_role_names(raw_roles: list[_RawAgentRole]) -> dict[int, str]:
    """Resolve deterministic, collision-safe role names."""
    return _resolve_name_map(
        raw_roles,
        lambda raw_role: raw_role.role_name_base,
        lambda raw_role: f"{raw_role.role_name_prefix}_{raw_role.role_name_base}",
        error_label="role name",
    )


def _resolve_prompt_stems(raw_roles: list[_RawAgentRole]) -> dict[int, str]:
    """Resolve deterministic, collision-safe prompt file stems."""
    return _resolve_name_map(
        raw_roles,
        lambda raw_role: raw_role.prompt_stem_base,
        lambda raw_role: f"{raw_role.prompt_stem_prefix}-{raw_role.prompt_stem_base}",
        error_label="prompt file name",
    )


def _resolve_name_map(
    raw_roles: list[_RawAgentRole],
    base_name_getter,
    disambiguated_name_getter,
    *,
    error_label: str,
) -> dict[int, str]:
    """Resolve unique names, prefixing collisions by marketplace when needed."""
    grouped: dict[str, list[_RawAgentRole]] = defaultdict(list)
    for raw_role in raw_roles:
        grouped[base_name_getter(raw_role)].append(raw_role)

    resolved: dict[int, str] = {}
    seen_names: set[str] = set()
    for group_name in sorted(grouped):
        group = sorted(grouped[group_name], key=_raw_role_sort_key)
        if len(group) == 1:
            _assign_unique_name(
                resolved,
                seen_names,
                group[0],
                base_name_getter(group[0]),
                error_label=error_label,
            )
            continue

        for raw_role in group:
            _assign_unique_name(
                resolved,
                seen_names,
                raw_role,
                disambiguated_name_getter(raw_role),
                error_label=error_label,
            )

    return resolved


def _assign_unique_name(
    resolved: dict[int, str],
    seen_names: set[str],
    raw_role: _RawAgentRole,
    candidate: str,
    *,
    error_label: str,
) -> None:
    """Store one resolved name and fail when normalization still collides."""
    if candidate in seen_names:
        raise TranslationError(f"Generated duplicate {error_label}: {candidate}")
    seen_names.add(candidate)
    resolved[id(raw_role)] = candidate


def _raw_role_sort_key(raw_role: _RawAgentRole) -> tuple[str, str, str]:
    """Stable ordering for marketplace-based collision resolution."""
    return (raw_role.marketplace, raw_role.plugin_name, raw_role.source_path.name)


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, object]:
    """Parse the YAML frontmatter shapes used by current Claude and Codex assets."""
    result: dict[str, object] = {}
    current_key: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip():
            index += 1
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current_key is None:
                raise TranslationError("List item found before a frontmatter key")
            current_value = result.setdefault(current_key, [])
            if not isinstance(current_value, list):
                raise TranslationError(f"Mixed scalar and list values for key: {current_key}")
            current_value.append(stripped[2:].strip())
            index += 1
            continue

        if indent:
            if current_key is None:
                raise TranslationError(f"Unexpected indented frontmatter line: {line}")
            current_value = result.get(current_key)
            if isinstance(current_value, str):
                block_lines, next_index = _consume_block_scalar(lines, index, indent)
                separator = "\n" if "\n" in current_value else " "
                joined = separator.join(part.strip() for part in block_lines if part.strip())
                result[current_key] = f"{current_value}{separator}{joined}".strip()
                index = next_index
                continue
            if isinstance(current_value, dict):
                nested_key, nested_value = _parse_key_value(stripped)
                current_value[nested_key] = nested_value
                index += 1
                continue
            raise TranslationError(f"Unexpected indented frontmatter line: {line}")

        if ":" not in line:
            raise TranslationError(f"Invalid frontmatter line: {line}")

        key, value = _parse_key_value(line)
        current_key = key
        if value in {">", "|"}:
            block_lines, next_index = _consume_block_scalar(lines, index + 1, min_indent=1)
            result[current_key] = _join_block_scalar(block_lines, folded=(value == ">"))
            index = next_index
            continue
        if value:
            result[current_key] = value
        else:
            next_line = _next_nonempty_line(lines, index + 1)
            if next_line is None:
                result[current_key] = []
            else:
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                next_stripped = next_line.lstrip()
                if next_indent == 0:
                    result[current_key] = []
                elif next_stripped.startswith("- "):
                    result[current_key] = []
                else:
                    result[current_key] = {}
        index += 1

    return result


def _parse_key_value(line: str) -> tuple[str, str]:
    """Parse one `key: value` line."""
    if ":" not in line:
        raise TranslationError(f"Invalid frontmatter line: {line}")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _next_nonempty_line(lines: list[str], start_index: int) -> str | None:
    """Return the next non-empty line after `start_index`."""
    for index in range(start_index, len(lines)):
        if lines[index].strip():
            return lines[index]
    return None


def _consume_block_scalar(
    lines: list[str],
    start_index: int,
    min_indent: int,
) -> tuple[list[str], int]:
    """Consume consecutive indented lines for a block scalar."""
    collected: list[str] = []
    index = start_index
    base_indent: int | None = None

    while index < len(lines):
        raw_line = lines[index]
        if not raw_line.strip():
            collected.append("")
            index += 1
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent < min_indent:
            break
        if base_indent is None:
            base_indent = indent
        collected.append(raw_line[base_indent:].rstrip())
        index += 1

    return collected, index


def _join_block_scalar(lines: list[str], *, folded: bool) -> str:
    """Join YAML-like block scalar lines."""
    if folded:
        joined = " ".join(part.strip() for part in lines if part.strip())
        return joined.strip()
    return "\n".join(lines).rstrip()


def _optional_str(value: object) -> str | None:
    """Return a stripped string or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normalize_role_namespace(value: str, *, kind: str) -> str:
    """Normalize marketplace/plugin names for TOML bare-key compatibility."""
    normalized = value.strip().replace(" ", "_")
    normalized = ROLE_NAMESPACE_RE.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_-")
    if not normalized:
        raise TranslationError(f"{kind.capitalize()} normalizes to an empty role namespace: {value!r}")
    return normalized


def _normalize_name(value: str) -> str:
    """Normalize agent names for Codex role identifiers."""
    normalized = value.strip().replace("-", "_").replace(" ", "_")
    normalized = ROLE_AGENT_RE.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        raise TranslationError(f"Agent name normalizes to an empty role identifier: {value!r}")
    return normalized


def _normalize_prompt_component(value: str, *, kind: str) -> str:
    """Normalize marketplace/plugin/agent names for safe prompt file paths."""
    normalized = value.strip().replace("_", "-").replace(" ", "-")
    normalized = PROMPT_COMPONENT_RE.sub("-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        raise TranslationError(f"{kind.capitalize()} normalizes to an empty prompt path segment: {value!r}")
    return normalized
