"""Translation from Claude Code commands to Codex prompt files.

Unlike translate_commands.py (which produces GeneratedSkill objects with
SKILL.md format), this module produces GeneratedPrompt objects — flat
.md prompt files for ~/.codex/prompts/.

Key differences from the skill-based approach:
- No cmd- prefix on filenames
- $ARGUMENTS and positional args ($1, $2, ...) pass through unchanged
  (Codex natively supports them)
- Frontmatter only emits description and argument-hint
- No skill validation step
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import cc_codex_bridge.frontmatter as frontmatter
from cc_codex_bridge.model import (
    GeneratedPrompt,
    GeneratedSkillFile,
    InstalledPlugin,
    PromptTranslationResult,
    SkillValidationDiagnostic,
    VendoredPluginResource,
)

PROVENANCE_MARKER = "\n<!-- bridge: translated from Claude Code command -->\n"


def translate_installed_commands(
    plugins: Iterable[InstalledPlugin],
    *,
    bridge_home: Path | None = None,
) -> PromptTranslationResult:
    """Translate installed Claude plugin commands into Codex prompts."""
    generated: list[GeneratedPrompt] = []
    diagnostics: list[SkillValidationDiagnostic] = []
    all_plugin_resources: list[VendoredPluginResource] = []

    for plugin in plugins:
        for command_path in plugin.commands:
            prompt, resources = _translate_one_command(
                command_path,
                marketplace=plugin.marketplace,
                plugin_name=plugin.plugin_name,
                plugin_root=plugin.source_path,
                bridge_home=bridge_home,
            )
            generated.append(prompt)
            all_plugin_resources.extend(resources)

    return PromptTranslationResult(
        prompts=tuple(sorted(generated, key=lambda p: p.filename)),
        diagnostics=tuple(diagnostics),
        plugin_resources=tuple(all_plugin_resources),
    )


def translate_standalone_commands(
    command_paths: Iterable[Path],
    *,
    scope: str,
    project_dir_name: str | None = None,
) -> PromptTranslationResult:
    """Translate user-level or project-level commands into Codex prompts."""
    generated: list[GeneratedPrompt] = []
    diagnostics: list[SkillValidationDiagnostic] = []

    for command_path in command_paths:
        prompt, _resources = _translate_one_command(
            command_path,
            marketplace=f"_{scope}",
            plugin_name="personal" if scope == "user" else "local",
            plugin_root=None,
            project_dir_name=project_dir_name,
        )
        generated.append(prompt)

    return PromptTranslationResult(
        prompts=tuple(sorted(generated, key=lambda p: p.filename)),
        diagnostics=tuple(diagnostics),
    )


def _translate_one_command(
    command_path: Path,
    *,
    marketplace: str,
    plugin_name: str,
    plugin_root: Path | None,
    bridge_home: Path | None = None,
    project_dir_name: str | None = None,
) -> tuple[GeneratedPrompt, tuple[VendoredPluginResource, ...]]:
    """Translate one command markdown file into a GeneratedPrompt."""
    from cc_codex_bridge.bridge_home import plugin_resource_dir
    from cc_codex_bridge.vendor_plugin import (
        detect_plugin_resource_dirs,
        detect_transitive_plugin_dirs,
        read_plugin_dir_files,
        rewrite_plugin_paths,
    )

    parsed_frontmatter, body = frontmatter.parse_markdown_with_frontmatter(
        command_path
    )

    description = str(parsed_frontmatter.get("description", "")).strip()
    if not description:
        # Derive description from filename stem: "code-review" -> "code review"
        description = command_path.stem.replace("-", " ").replace("_", " ")

    argument_hint = str(parsed_frontmatter.get("argument-hint", "")).strip()

    # Compute vendored root when bridge_home is provided
    vendored_root: Path | None = None
    if bridge_home is not None and plugin_root is not None:
        vendored_root = plugin_resource_dir(
            marketplace, plugin_name, bridge_home=bridge_home,
        )

    # Apply ${CLAUDE_PLUGIN_ROOT} replacement only (not $ARGUMENTS)
    transformed_body = _replace_plugin_root(
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
            # Rewrite paths: vendored_root/file.py -> vendored_root/_root/file.py
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

    # Build prompt content with frontmatter
    lines = ["---", f"description: {description}"]
    if argument_hint:
        lines.append(f"argument-hint: {argument_hint}")
    lines.append("---")
    prompt_content = "\n".join(lines) + "\n" + transformed_body
    if not prompt_content.endswith("\n"):
        prompt_content += "\n"
    prompt_content += PROVENANCE_MARKER

    # Compute filename
    stem = command_path.stem
    if project_dir_name is not None:
        filename = f"{stem}--{project_dir_name}.md"
    else:
        filename = f"{stem}.md"

    prompt = GeneratedPrompt(
        filename=filename,
        content=prompt_content.encode(),
        source_path=command_path,
        marketplace=marketplace,
        plugin_name=plugin_name,
    )
    return prompt, tuple(plugin_resources)


def _replace_plugin_root(
    body: str,
    plugin_root: Path | None,
    *,
    vendored_root: Path | None = None,
) -> str:
    """Replace ${CLAUDE_PLUGIN_ROOT} in body. Does NOT touch $ARGUMENTS or positional args."""
    result = body
    if plugin_root is not None:
        replacement = (
            str(vendored_root)
            if vendored_root is not None
            else str(plugin_root.resolve())
        )
        result = result.replace("${CLAUDE_PLUGIN_ROOT}", replacement)
    return result
