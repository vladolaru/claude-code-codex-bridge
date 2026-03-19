"""Translation from Claude skills to self-contained Codex skills."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
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
from cc_codex_bridge.text import read_utf8_text


def format_skill_validation_diagnostics(
    diagnostics: Iterable[SkillValidationDiagnostic],
) -> str:
    """Render skill validation diagnostics as stable human-readable lines."""
    return "\n".join(
        _format_skill_validation_diagnostic(d) for d in diagnostics
    )


def _format_skill_validation_diagnostic(diagnostic: SkillValidationDiagnostic) -> str:
    """Render one skill validation diagnostic."""
    warnings_text = "; ".join(diagnostic.warnings)
    return f"  skill '{diagnostic.skill_name}' ({diagnostic.source_path}): {warnings_text}"


SIBLING_SKILL_REF_RE = re.compile(r"(?<!\.)\.\./(?P<skill>[A-Za-z0-9._-]+)/")
IGNORED_NAMES = {".DS_Store", "__pycache__"}
IGNORED_SKILL_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".tox"}

MAX_SKILL_NAME_LENGTH = 64


def assign_skill_names(
    skills: tuple[GeneratedSkill, ...],
) -> tuple[GeneratedSkill, ...]:
    """Assign collision-free install names using bare skill directory names.

    Priority: user skills win bare names over plugin skills.
    Among plugins, sorted by (marketplace, plugin_name).
    Collisions get -alt, -alt-2, -alt-3 suffixes.
    """
    # Group skills by bare directory name (from source_path)
    groups: dict[str, list[GeneratedSkill]] = {}
    for skill in skills:
        bare_name = skill.source_path.name
        groups.setdefault(bare_name, []).append(skill)

    result: list[GeneratedSkill] = []

    for bare_name in sorted(groups):
        candidates = groups[bare_name]

        if len(candidates) > 1:
            # Sort by priority: user skills first (marketplace starts with _),
            # then plugin skills by (marketplace, plugin_name)
            candidates.sort(key=lambda s: (
                0 if s.marketplace.startswith("_") else 1,
                s.marketplace,
                s.plugin_name,
            ))

        for index, skill in enumerate(candidates):
            if index == 0:
                assigned_name = bare_name
            elif index == 1:
                assigned_name = f"{bare_name}-alt"
            else:
                assigned_name = f"{bare_name}-alt-{index}"

            if len(assigned_name) > MAX_SKILL_NAME_LENGTH:
                raise TranslationError(
                    f"Generated skill name exceeds {MAX_SKILL_NAME_LENGTH} characters: "
                    f"'{assigned_name}' ({len(assigned_name)} chars) "
                    f"from skill directory '{bare_name}'"
                )

            # Rewrite SKILL.md frontmatter name to match assigned name
            new_files: list[GeneratedSkillFile] = []
            for f in skill.files:
                if f.relative_path == Path("SKILL.md"):
                    rewritten = _rewrite_frontmatter_name(
                        f.content.decode(), assigned_name
                    )
                    new_files.append(GeneratedSkillFile(
                        relative_path=f.relative_path,
                        content=rewritten.encode(),
                        mode=f.mode,
                    ))
                else:
                    new_files.append(f)

            result.append(replace(
                skill,
                install_dir_name=assigned_name,
                codex_skill_name=assigned_name,
                files=tuple(new_files),
            ))

    return tuple(sorted(result, key=lambda s: s.install_dir_name))


class _RawSkill:
    """Intermediate Claude skill metadata before conflict resolution."""

    def __init__(
        self,
        *,
        marketplace: str,
        plugin_name: str,
        plugin_root: Path,
        skill_path: Path,
        original_skill_name: str,
    ) -> None:
        self.marketplace = marketplace
        self.plugin_name = plugin_name
        self.plugin_root = plugin_root
        self.skill_path = skill_path
        self.original_skill_name = original_skill_name

    @property
    def skill_dir_name(self) -> str:
        return self.skill_path.name


def translate_installed_skills(
    plugins: Iterable[InstalledPlugin],
    *,
    bridge_home: Path | None = None,
) -> SkillTranslationResult:
    """Translate installed Claude skills into self-contained Codex skills."""
    raw_skills: list[_RawSkill] = []

    for plugin in plugins:
        for skill_path in plugin.skills:
            raw_skills.append(
                _RawSkill(
                    marketplace=plugin.marketplace,
                    plugin_name=plugin.plugin_name,
                    plugin_root=plugin.source_path,
                    skill_path=skill_path,
                    original_skill_name=_read_required_skill_name(skill_path / "SKILL.md"),
                )
            )

    generated: list[GeneratedSkill] = []
    diagnostics: list[SkillValidationDiagnostic] = []
    all_plugin_resources: list[VendoredPluginResource] = []
    for raw_skill in raw_skills:
        skill, diagnostic, resources = _build_generated_skill(
            raw_skill,
            raw_skill.skill_dir_name,
            bridge_home=bridge_home,
        )
        generated.append(skill)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
        all_plugin_resources.extend(resources)

    return SkillTranslationResult(
        skills=tuple(sorted(generated, key=lambda item: item.install_dir_name)),
        diagnostics=tuple(diagnostics),
        plugin_resources=tuple(all_plugin_resources),
    )


def translate_standalone_skills(
    skill_paths: Iterable[Path],
    *,
    scope: str,
) -> SkillTranslationResult:
    """Translate user-level or project-level Claude skills into Codex skills.

    Both user-level and project-level skills use their raw directory name.
    Collision resolution across scopes is handled by ``assign_skill_names()``.
    """
    generated: list[GeneratedSkill] = []
    diagnostics: list[SkillValidationDiagnostic] = []

    for skill_path in skill_paths:
        original_name = _read_required_skill_name(skill_path / "SKILL.md")

        install_dir_name = skill_path.name

        generated_files: dict[Path, tuple[bytes, int]] = {}
        _copy_skill_tree(skill_path, Path(), generated_files)

        skill_content = read_utf8_text(
            skill_path / "SKILL.md", label="skill file", error_type=TranslationError
        )
        rewritten, referenced_sources = _resolve_relative_references(
            skill_path, skill_content,
        )
        rewritten = _rewrite_frontmatter_name(rewritten, install_dir_name)
        diagnostic = _validate_generated_skill(rewritten, install_dir_name, skill_path / "SKILL.md")
        if diagnostic is not None:
            diagnostics.append(diagnostic)
        generated_files[Path("SKILL.md")] = (
            rewritten.encode(),
            (skill_path / "SKILL.md").stat().st_mode & 0o777,
        )

        for target_name, source_path in referenced_sources.items():
            _copy_tree(source_path, Path(target_name), generated_files)

        files = tuple(
            GeneratedSkillFile(
                relative_path=path,
                content=generated_files[path][0],
                mode=generated_files[path][1],
            )
            for path in sorted(generated_files)
        )

        generated.append(GeneratedSkill(
            marketplace=f"_{scope}",
            plugin_name="personal" if scope == "user" else "local",
            source_path=skill_path,
            install_dir_name=install_dir_name,
            original_skill_name=original_name,
            codex_skill_name=install_dir_name,
            files=files,
        ))

    return SkillTranslationResult(
        skills=tuple(sorted(generated, key=lambda s: s.install_dir_name)),
        diagnostics=tuple(diagnostics),
    )


def _read_required_skill_name(skill_md_path: Path) -> str:
    """Read the canonical skill name from SKILL.md frontmatter."""
    if skill_md_path.is_symlink():
        raise TranslationError(
            f"Refusing to follow symlinked SKILL.md: {skill_md_path}"
        )
    parsed_frontmatter, _ = frontmatter.parse_markdown_with_frontmatter(skill_md_path)
    skill_name = str(parsed_frontmatter.get("name", "")).strip()
    if not skill_name:
        raise TranslationError(f"Skill missing required name frontmatter: {skill_md_path}")
    return skill_name


def _validate_generated_skill(
    content: str,
    install_dir_name: str,
    source_path: Path,
) -> SkillValidationDiagnostic | None:
    """Validate generated SKILL.md; raise on structural errors, return warning diagnostic."""
    parsed = frontmatter.parse_frontmatter_from_content(content)
    errors, warnings = validate_skill_metadata(parsed, dir_name=install_dir_name)
    if errors:
        raise TranslationError(
            f"Generated skill fails structural validation "
            f"(source: {source_path}): {'; '.join(errors)}"
        )
    if warnings:
        name = str(parsed.get("name", install_dir_name))
        return SkillValidationDiagnostic(
            source_path=source_path,
            skill_name=name,
            warnings=tuple(warnings),
        )
    return None


def _build_generated_skill(
    raw_skill: _RawSkill,
    install_dir_name: str,
    *,
    bridge_home: Path | None = None,
) -> tuple[GeneratedSkill, SkillValidationDiagnostic | None, tuple[VendoredPluginResource, ...]]:
    """Build the generated file tree for one installed Claude skill."""
    from cc_codex_bridge.bridge_home import plugin_resource_dir
    from cc_codex_bridge.vendor_plugin import detect_plugin_resource_dirs, rewrite_plugin_paths

    generated_files: dict[Path, tuple[bytes, int]] = {}

    _copy_skill_tree(raw_skill.skill_path, Path(), generated_files)

    skill_md_path = raw_skill.skill_path / "SKILL.md"
    if skill_md_path.is_symlink():
        raise TranslationError(
            f"Refusing to follow symlinked SKILL.md: {skill_md_path}"
        )
    skill_content = read_utf8_text(skill_md_path, label="skill file", error_type=TranslationError)
    rewritten, referenced_sources = _resolve_relative_references(
        raw_skill.skill_path, skill_content,
    )
    rewritten = _rewrite_frontmatter_name(rewritten, install_dir_name)

    # Detect plugin resource references and rewrite paths
    plugin_resources: list[VendoredPluginResource] = []
    if bridge_home is not None:
        detected_dirs = detect_plugin_resource_dirs(rewritten)
        if detected_dirs:
            vendored_root = plugin_resource_dir(
                raw_skill.marketplace, raw_skill.plugin_name, bridge_home=bridge_home,
            )
            rewritten = rewrite_plugin_paths(rewritten, vendored_root)

            # Build VendoredPluginResource for each detected directory
            for dir_name in sorted(detected_dirs):
                source_dir = raw_skill.plugin_root / dir_name
                if not source_dir.is_dir():
                    continue  # Referenced dir doesn't exist at plugin root
                resource_files: dict[Path, tuple[bytes, int]] = {}
                _copy_tree(source_dir, Path(), resource_files)
                files = tuple(
                    GeneratedSkillFile(
                        relative_path=path,
                        content=resource_files[path][0],
                        mode=resource_files[path][1],
                    )
                    for path in sorted(resource_files)
                )
                plugin_resources.append(VendoredPluginResource(
                    marketplace=raw_skill.marketplace,
                    plugin_name=raw_skill.plugin_name,
                    source_dir=source_dir,
                    target_dir_name=dir_name,
                    files=files,
                ))

    diagnostic = _validate_generated_skill(rewritten, install_dir_name, skill_md_path)
    generated_files[Path("SKILL.md")] = (
        rewritten.encode(),
        (skill_md_path.stat().st_mode & 0o777),
    )

    # Copy referenced sibling trees directly into the generated skill
    for target_name, source_path in referenced_sources.items():
        _copy_tree(source_path, Path(target_name), generated_files)

    files = tuple(
        GeneratedSkillFile(
            relative_path=path,
            content=generated_files[path][0],
            mode=generated_files[path][1],
        )
        for path in sorted(generated_files)
    )

    skill = GeneratedSkill(
        marketplace=raw_skill.marketplace,
        plugin_name=raw_skill.plugin_name,
        source_path=raw_skill.skill_path,
        install_dir_name=install_dir_name,
        original_skill_name=raw_skill.original_skill_name,
        codex_skill_name=install_dir_name,
        files=files,
    )
    return skill, diagnostic, tuple(plugin_resources)


def _resolve_relative_references(
    skill_dir: Path,
    content: str,
) -> tuple[str, dict[str, Path]]:
    """Resolve ../references in skill content relative to the skill's disk location.

    Returns rewritten content and a mapping of target directory names to
    source paths on disk.  Missing referenced paths are a hard error.
    """
    matches = set(SIBLING_SKILL_REF_RE.findall(content))
    if not matches:
        return content, {}

    referenced_sources: dict[str, Path] = {}
    rewritten = content
    existing_dirs = {
        entry.name for entry in skill_dir.iterdir()
        if entry.is_dir() and not _should_ignore(entry)
        and entry.name not in IGNORED_SKILL_DIRS
    }

    for ref_name in sorted(matches):
        resolved = (skill_dir / ".." / ref_name).resolve()
        if not resolved.is_dir():
            raise TranslationError(
                f"Skill references missing sibling `{ref_name}`: {skill_dir / 'SKILL.md'}"
            )
        # Guard: referenced sibling must not be a symlink
        raw_path = skill_dir / ".." / ref_name
        if raw_path.is_symlink():
            raise TranslationError(
                f"Refusing to follow symlinked sibling reference `{ref_name}`: {skill_dir / 'SKILL.md'}"
            )
        if ref_name in existing_dirs:
            raise TranslationError(
                f"Referenced sibling `{ref_name}` collides with an existing directory in skill: {skill_dir / 'SKILL.md'}"
            )
        referenced_sources[ref_name] = resolved
        rewritten = rewritten.replace(f"../{ref_name}/", f"{ref_name}/")

    return rewritten, referenced_sources


def _rewrite_frontmatter_name(content: str, codex_skill_name: str) -> str:
    """Rewrite only the `name:` entry inside the frontmatter block."""
    if not content.startswith("---\n"):
        raise TranslationError("Skill missing frontmatter block")

    lines = content.splitlines(keepends=True)
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise TranslationError("Skill frontmatter block is not closed")

    name_rewritten = False
    for index in range(1, end_index):
        line = lines[index]
        if line.startswith("name:"):
            lines[index] = f"name: {codex_skill_name}\n"
            name_rewritten = True
            break

    if not name_rewritten:
        raise TranslationError("Skill frontmatter missing required `name` field")

    return "".join(lines)


def _copy_tree(
    source_root: Path,
    target_prefix: Path,
    generated_files: dict[Path, tuple[bytes, int]],
) -> None:
    """Copy a directory tree into the generated file mapping."""
    for path in sorted(source_root.rglob("*")):
        if path.is_symlink():
            kind = "directory" if path.is_dir() else "file"
            raise TranslationError(
                f"Refusing to follow symlinked {kind}: {path}"
            )
        if path.is_dir():
            continue
        if _should_ignore(path):
            continue

        relative_path = target_prefix / path.relative_to(source_root)
        generated_files[relative_path] = (path.read_bytes(), path.stat().st_mode & 0o777)


def _copy_skill_tree(
    source_root: Path,
    target_prefix: Path,
    generated_files: dict[Path, tuple[bytes, int]],
) -> None:
    """Copy skill files and all non-ignored directories from a skill root.

    The Agent Skills Standard allows arbitrary directories alongside the
    standard ``scripts/``, ``references/``, and ``assets/`` directories.
    We copy everything except known noise (see ``IGNORED_SKILL_DIRS``).
    """
    for entry in sorted(source_root.iterdir()):
        if _should_ignore(entry):
            continue

        if entry.is_file():
            if entry.is_symlink():
                raise TranslationError(
                    f"Refusing to follow symlinked file: {entry}"
                )
            generated_files[target_prefix / entry.name] = (
                entry.read_bytes(),
                entry.stat().st_mode & 0o777,
            )
            continue

        if entry.is_dir() and entry.name not in IGNORED_SKILL_DIRS:
            if entry.is_symlink():
                raise TranslationError(
                    f"Refusing to follow symlinked resource directory: {entry}"
                )
            _copy_tree(entry, target_prefix / entry.name, generated_files)


def _should_ignore(path: Path) -> bool:
    """Ignore cache files and platform noise when copying resources."""
    if any(part in IGNORED_NAMES for part in path.parts):
        return True
    if path.suffix == ".pyc":
        return True
    return False
