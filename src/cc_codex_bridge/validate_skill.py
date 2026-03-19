"""Agent Skills Standard validation for generated skill metadata.

Implements the validation rules from the Agent Skills specification
(https://agentskills.io/specification). Rules are vendored from the
skills-ref reference library (Apache 2.0, by Keith Lazuka / Anthropic)
and adapted to use our existing PyYAML-based frontmatter parser.

Rules are split into two severity levels:

- **Errors** (structural): Issues the bridge must prevent in its own output.
  Name-directory mismatch, name too long, missing name — these indicate a
  bridge bug since we control name assignment.

- **Warnings** (source-quality): Issues in upstream skill content that the
  bridge cannot fix.  Missing description, invalid name characters,
  unexpected frontmatter fields — these are the source skill author's
  responsibility.

Source: https://github.com/agentskills/agentskills/tree/main/skills-ref
"""

from __future__ import annotations

MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500

ALLOWED_FIELDS = frozenset({
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
})


def validate_skill_metadata(
    metadata: dict[str, object],
    dir_name: str | None = None,
) -> tuple[list[str], list[str]]:
    """Validate parsed skill frontmatter against the Agent Skills Standard.

    Returns ``(errors, warnings)`` where errors are structural issues the
    bridge must prevent and warnings are source-quality issues from upstream.
    """
    errors: list[str] = []
    warnings: list[str] = []

    _validate_name(metadata, dir_name, errors, warnings)
    _validate_description(metadata, warnings)
    _validate_compatibility(metadata, warnings)
    _validate_allowed_fields(metadata, warnings)

    return errors, warnings


def _validate_name(
    metadata: dict[str, object],
    dir_name: str | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    raw = metadata.get("name")

    # Structural: bridge requires name to function
    if raw is None:
        errors.append("Missing required field: name")
        return
    if not isinstance(raw, str) or not raw.strip():
        errors.append("Field 'name' must be a non-empty string")
        return

    name = raw.strip()

    # Structural: bridge controls name length via -alt suffixing
    if len(name) > MAX_SKILL_NAME_LENGTH:
        errors.append(
            f"Skill name exceeds {MAX_SKILL_NAME_LENGTH} characters "
            f"({len(name)} chars)"
        )

    # Structural: bridge controls name-directory alignment
    if dir_name is not None and dir_name != name:
        errors.append(
            f"Directory name '{dir_name}' must match skill name '{name}'"
        )

    # Source-quality: upstream naming conventions
    if name != name.lower():
        warnings.append(f"Skill name '{name}' must be lowercase")

    if name.startswith("-") or name.endswith("-"):
        warnings.append("Skill name cannot start or end with a hyphen")

    if "--" in name:
        warnings.append("Skill name cannot contain consecutive hyphens")

    if not all(c.isalnum() or c == "-" for c in name):
        warnings.append(
            f"Skill name '{name}' contains invalid characters. "
            "Only letters, digits, and hyphens are allowed."
        )


def _validate_description(
    metadata: dict[str, object],
    warnings: list[str],
) -> None:
    raw = metadata.get("description")

    if raw is None:
        warnings.append("Missing required field: description")
        return

    if not isinstance(raw, str) or not raw.strip():
        warnings.append("Field 'description' must be a non-empty string")
        return

    if len(raw) > MAX_DESCRIPTION_LENGTH:
        warnings.append(
            f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters "
            f"({len(raw)} chars)"
        )


def _validate_compatibility(
    metadata: dict[str, object],
    warnings: list[str],
) -> None:
    raw = metadata.get("compatibility")
    if raw is None:
        return

    if not isinstance(raw, str):
        warnings.append("Field 'compatibility' must be a string")
        return

    if len(raw) > MAX_COMPATIBILITY_LENGTH:
        warnings.append(
            f"Compatibility exceeds {MAX_COMPATIBILITY_LENGTH} characters "
            f"({len(raw)} chars)"
        )


def _validate_allowed_fields(
    metadata: dict[str, object],
    warnings: list[str],
) -> None:
    extra = set(metadata.keys()) - ALLOWED_FIELDS
    if extra:
        warnings.append(
            f"Unexpected fields in frontmatter: {', '.join(sorted(extra))}. "
            f"Allowed: {sorted(ALLOWED_FIELDS)}"
        )
