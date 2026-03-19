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
    VendoredPluginResource,
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
    *,
    bridge_home: Path | None = None,
) -> SkillTranslationResult:
    """Translate installed Claude plugin commands into Codex skills."""
    generated: list[GeneratedSkill] = []
    diagnostics: list[SkillValidationDiagnostic] = []
    all_plugin_resources: list[VendoredPluginResource] = []

    for plugin in plugins:
        for command_path in plugin.commands:
            skill, diagnostic, resources = _translate_one_command(
                command_path,
                marketplace=plugin.marketplace,
                plugin_name=plugin.plugin_name,
                plugin_root=plugin.source_path,
                bridge_home=bridge_home,
            )
            generated.append(skill)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            all_plugin_resources.extend(resources)

    return SkillTranslationResult(
        skills=tuple(sorted(generated, key=lambda s: s.install_dir_name)),
        diagnostics=tuple(diagnostics),
        plugin_resources=tuple(all_plugin_resources),
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
        skill, diagnostic, _resources = _translate_one_command(
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
    bridge_home: Path | None = None,
) -> tuple[GeneratedSkill, SkillValidationDiagnostic | None, tuple[VendoredPluginResource, ...]]:
    """Translate one command markdown file into a GeneratedSkill."""
    from cc_codex_bridge.bridge_home import plugin_resource_dir
    from cc_codex_bridge.vendor_plugin import (
        detect_plugin_resource_dirs,
        detect_transitive_plugin_dirs,
        read_plugin_dir_files,
        rewrite_plugin_paths,
    )

    if command_path.is_symlink():
        raise TranslationError(
            f"Refusing to follow symlinked command file: {command_path}"
        )

    parsed_frontmatter, body = frontmatter.parse_markdown_with_frontmatter(
        command_path
    )

    description = str(parsed_frontmatter.get("description", "")).strip()
    if not description:
        # Derive description from filename stem: "code-review" → "code review"
        description = command_path.stem.replace("-", " ").replace("_", " ")

    # Derive skill name from filename stem (without .md), prefixed with cmd-
    skill_name = f"cmd-{command_path.stem}"
    install_dir_name = skill_name

    # Compute vendored root when bridge_home is provided
    vendored_root: Path | None = None
    if bridge_home is not None and plugin_root is not None:
        vendored_root = plugin_resource_dir(
            marketplace, plugin_name, bridge_home=bridge_home,
        )

    # Apply variable replacements to the body
    transformed_body = _replace_variables(
        body, plugin_root, vendored_root=vendored_root,
    )

    # Detect and rewrite $PLUGIN_ROOT patterns, collect vendored resources
    plugin_resources: list[VendoredPluginResource] = []
    if bridge_home is not None and plugin_root is not None:
        assert vendored_root is not None  # guaranteed by the branch above

        # Detect and rewrite $PLUGIN_ROOT patterns
        detected_plugin_root_dirs = detect_plugin_resource_dirs(transformed_body)
        if detected_plugin_root_dirs:
            transformed_body = rewrite_plugin_paths(
                transformed_body, vendored_root,
            )

        # Detect dirs referenced via ${CLAUDE_PLUGIN_ROOT} (now replaced
        # with vendored_root in the body)
        detected_claude_root_dirs: set[str] = set()
        detected_claude_root_files: list[str] = []
        vendored_root_str = str(vendored_root)
        for entry in sorted(plugin_root.iterdir(), key=lambda e: e.name):
            if entry.name.startswith("."):
                continue
            if entry.is_dir() and f"{vendored_root_str}/{entry.name}" in transformed_body:
                detected_claude_root_dirs.add(entry.name)
            elif entry.is_file() and f"{vendored_root_str}/{entry.name}" in transformed_body:
                detected_claude_root_files.append(entry.name)

        # Union of all detected dirs
        all_detected = detected_plugin_root_dirs | detected_claude_root_dirs

        # Build VendoredPluginResource for each detected directory
        for dir_name in sorted(all_detected):
            source_dir = plugin_root / dir_name
            if not source_dir.is_dir():
                continue
            files = read_plugin_dir_files(source_dir)
            plugin_resources.append(VendoredPluginResource(
                marketplace=marketplace,
                plugin_name=plugin_name,
                source_dir=source_dir,
                target_dir_name=dir_name,
                files=files,
            ))

        # Vendor root-level files into a _root resource directory and
        # rewrite their paths to point there
        if detected_claude_root_files:
            root_files: list[GeneratedSkillFile] = []
            for filename in sorted(detected_claude_root_files):
                source_file = plugin_root / filename
                root_files.append(GeneratedSkillFile(
                    relative_path=Path(filename),
                    content=source_file.read_bytes(),
                    mode=source_file.stat().st_mode & 0o777,
                ))
            plugin_resources.append(VendoredPluginResource(
                marketplace=marketplace,
                plugin_name=plugin_name,
                source_dir=plugin_root,
                target_dir_name="_root",
                files=tuple(root_files),
            ))
            # Rewrite paths: vendored_root/file.py → vendored_root/_root/file.py
            for filename in detected_claude_root_files:
                transformed_body = transformed_body.replace(
                    f"{vendored_root_str}/{filename}",
                    f"{vendored_root_str}/_root/{filename}",
                )

        # Transitive deps
        if plugin_resources:
            vendored_files = tuple(
                f for r in plugin_resources for f in r.files
            )
            transitive_dirs = detect_transitive_plugin_dirs(
                vendored_files, plugin_root,
            )
            already = {r.target_dir_name for r in plugin_resources}
            for dir_name in sorted(transitive_dirs - already):
                source_dir = plugin_root / dir_name
                files = read_plugin_dir_files(source_dir)
                plugin_resources.append(VendoredPluginResource(
                    marketplace=marketplace,
                    plugin_name=plugin_name,
                    source_dir=source_dir,
                    target_dir_name=dir_name,
                    files=files,
                ))

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
    return skill, diagnostic, tuple(plugin_resources)


def _replace_variables(
    body: str,
    plugin_root: Path | None,
    *,
    vendored_root: Path | None = None,
) -> str:
    """Apply deterministic variable replacements to a command body."""
    result = body

    # Replace ${CLAUDE_PLUGIN_ROOT} first (before positional args touch $ signs)
    if plugin_root is not None:
        replacement = (
            str(vendored_root)
            if vendored_root is not None
            else str(plugin_root.resolve())
        )
        result = result.replace("${CLAUDE_PLUGIN_ROOT}", replacement)

    # Replace $ARGUMENTS and $ARGUMENTS[N]
    result = ARGUMENTS_RE.sub(ARGUMENTS_REPLACEMENT, result)

    # Replace bare positional $0, $1, etc.
    result = POSITIONAL_ARG_RE.sub(ARGUMENTS_REPLACEMENT, result)

    return result
