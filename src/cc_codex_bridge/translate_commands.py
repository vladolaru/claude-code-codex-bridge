"""Translation from Claude Code commands to Codex skills."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import cc_codex_bridge.frontmatter as frontmatter
from cc_codex_bridge.model import (
    GeneratedSkill,
    GeneratedSkillFile,
    InstalledPlugin,
    SkillTranslationResult,
    SkillValidationDiagnostic,
    TranslationError,
)
from cc_codex_bridge.validate_skill import validate_skill_metadata

ARGUMENTS_REPLACEMENT = "<use any user-provided details; otherwise infer from context>"
PROVENANCE_MARKER = "\n<!-- translated from Claude Code command -->\n"

# Match $ARGUMENTS optionally followed by [N] index
ARGUMENTS_RE = re.compile(r"\$ARGUMENTS(?:\[\d+\])?")
# Match bare positional $0, $1 etc — but NOT inside ${...} or $ARGUMENTS
# Use word boundary and negative lookbehind for { to avoid matching ${CLAUDE_PLUGIN_ROOT}
POSITIONAL_ARG_RE = re.compile(r"(?<!\{)\$(\d+)\b")


def translate_installed_commands(
    plugins: Iterable[InstalledPlugin],
) -> SkillTranslationResult:
    """Translate installed Claude plugin commands into Codex skills."""
    generated: list[GeneratedSkill] = []
    diagnostics: list[SkillValidationDiagnostic] = []

    for plugin in plugins:
        for command_path in plugin.commands:
            skill, diagnostic = _translate_one_command(
                command_path,
                marketplace=plugin.marketplace,
                plugin_name=plugin.plugin_name,
                plugin_root=plugin.source_path,
            )
            generated.append(skill)
            if diagnostic is not None:
                diagnostics.append(diagnostic)

    return SkillTranslationResult(
        skills=tuple(sorted(generated, key=lambda s: s.install_dir_name)),
        diagnostics=tuple(diagnostics),
    )


def translate_standalone_commands(
    command_paths: Iterable[Path],
    *,
    scope: str,
) -> SkillTranslationResult:
    """Translate user-level or project-level commands into Codex skills."""
    generated: list[GeneratedSkill] = []
    diagnostics: list[SkillValidationDiagnostic] = []

    for command_path in command_paths:
        skill, diagnostic = _translate_one_command(
            command_path,
            marketplace=f"_{scope}",
            plugin_name="personal" if scope == "user" else "local",
            plugin_root=None,
        )
        generated.append(skill)
        if diagnostic is not None:
            diagnostics.append(diagnostic)

    return SkillTranslationResult(
        skills=tuple(sorted(generated, key=lambda s: s.install_dir_name)),
        diagnostics=tuple(diagnostics),
    )


def _translate_one_command(
    command_path: Path,
    *,
    marketplace: str,
    plugin_name: str,
    plugin_root: Path | None,
) -> tuple[GeneratedSkill, SkillValidationDiagnostic | None]:
    """Translate one command markdown file into a GeneratedSkill."""
    if command_path.is_symlink():
        raise TranslationError(
            f"Refusing to follow symlinked command file: {command_path}"
        )

    parsed_frontmatter, body = frontmatter.parse_markdown_with_frontmatter(
        command_path
    )

    description = str(parsed_frontmatter.get("description", "")).strip()
    if not description:
        raise TranslationError(
            f"Command missing required description frontmatter: {command_path}"
        )

    # Derive skill name from filename stem (without .md)
    skill_name = command_path.stem
    install_dir_name = skill_name

    # Apply variable replacements to the body
    transformed_body = _replace_variables(body, plugin_root)

    # Build SKILL.md content: only name + description in frontmatter
    skill_md_content = (
        f"---\n"
        f"name: {install_dir_name}\n"
        f"description: {description}\n"
        f"---\n"
        f"{transformed_body}"
    )

    # Ensure trailing newline before provenance marker
    if not skill_md_content.endswith("\n"):
        skill_md_content += "\n"
    skill_md_content += PROVENANCE_MARKER

    # Validate generated content
    parsed = frontmatter.parse_frontmatter_from_content(skill_md_content)
    errors, warnings = validate_skill_metadata(parsed, dir_name=install_dir_name)
    if errors:
        raise TranslationError(
            f"Generated command skill fails structural validation "
            f"(source: {command_path}): {'; '.join(errors)}"
        )
    diagnostic = None
    if warnings:
        diagnostic = SkillValidationDiagnostic(
            source_path=command_path,
            skill_name=skill_name,
            warnings=tuple(warnings),
        )

    files = (
        GeneratedSkillFile(
            relative_path=Path("SKILL.md"),
            content=skill_md_content.encode(),
            mode=0o644,
        ),
    )

    skill = GeneratedSkill(
        marketplace=marketplace,
        plugin_name=plugin_name,
        source_path=command_path,
        install_dir_name=install_dir_name,
        original_skill_name=skill_name,
        codex_skill_name=install_dir_name,
        files=files,
    )
    return skill, diagnostic


def _replace_variables(body: str, plugin_root: Path | None) -> str:
    """Apply deterministic variable replacements to a command body."""
    result = body

    # Replace ${CLAUDE_PLUGIN_ROOT} first (before positional args touch $ signs)
    if plugin_root is not None:
        result = result.replace(
            "${CLAUDE_PLUGIN_ROOT}", str(plugin_root.resolve())
        )

    # Replace $ARGUMENTS and $ARGUMENTS[N]
    result = ARGUMENTS_RE.sub(ARGUMENTS_REPLACEMENT, result)

    # Replace bare positional $0, $1, etc.
    result = POSITIONAL_ARG_RE.sub(ARGUMENTS_REPLACEMENT, result)

    return result
