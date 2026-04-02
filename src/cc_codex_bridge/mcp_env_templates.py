"""Shared parsing and expansion helpers for MCP env-template values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re


_ENV_TEMPLATE_RE = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))"
)


@dataclass(frozen=True)
class EnvTemplateLiteral:
    """One literal fragment in an env-template string."""

    text: str


@dataclass(frozen=True)
class EnvTemplateReference:
    """One environment-variable reference in an env-template string."""

    name: str
    default: str | None = None


EnvTemplateSegment = EnvTemplateLiteral | EnvTemplateReference


def parse_env_template(value: str) -> tuple[EnvTemplateSegment, ...]:
    """Parse *value* into literal and env-reference segments."""
    if not value:
        return ()

    segments: list[EnvTemplateSegment] = []
    position = 0
    for match in _ENV_TEMPLATE_RE.finditer(value):
        if match.start() > position:
            segments.append(EnvTemplateLiteral(value[position:match.start()]))

        name = match.group(1) or match.group(3)
        default = match.group(2)
        segments.append(EnvTemplateReference(name=name, default=default))
        position = match.end()

    if position < len(value):
        segments.append(EnvTemplateLiteral(value[position:]))

    return tuple(segments)


def collect_env_var_refs(value: str) -> tuple[str, ...]:
    """Return unique env var names referenced by *value*, preserving order."""
    refs: list[str] = []
    seen: set[str] = set()
    for segment in parse_env_template(value):
        if isinstance(segment, EnvTemplateReference) and segment.name not in seen:
            refs.append(segment.name)
            seen.add(segment.name)
    return tuple(refs)


def extract_whole_env_var_ref(value: str) -> str | None:
    """If *value* is exactly ``${VAR}`` or ``$VAR``, return the var name."""
    segments = parse_env_template(value)
    if len(segments) != 1:
        return None
    segment = segments[0]
    if isinstance(segment, EnvTemplateReference) and segment.default is None:
        return segment.name
    return None


def contains_env_var_ref(value: str) -> bool:
    """Return True when *value* contains any env-variable reference."""
    return any(
        isinstance(segment, EnvTemplateReference)
        for segment in parse_env_template(value)
    )


def expand_env_template(value: str, env: Mapping[str, str]) -> str:
    """Expand *value* against *env* using Claude-style `${VAR}` semantics."""
    pieces: list[str] = []
    for segment in parse_env_template(value):
        if isinstance(segment, EnvTemplateLiteral):
            pieces.append(segment.text)
            continue

        resolved = env.get(segment.name)
        if (resolved is None or resolved == "") and segment.default is not None:
            resolved = segment.default
        if resolved is None:
            resolved = ""
        pieces.append(resolved)

    return "".join(pieces)
