"""Translation from Claude skills to self-contained Codex skills."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

import cc_codex_bridge.frontmatter as frontmatter
from cc_codex_bridge.model import (
    GeneratedSkill,
    GeneratedSkillFile,
    InstalledPlugin,
    TranslationError,
)
from cc_codex_bridge.text import read_utf8_text


SIBLING_SKILL_REF_RE = re.compile(r"\.\./(?P<skill>[A-Za-z0-9._-]+)/")
IGNORED_NAMES = {".DS_Store", "__pycache__"}
OPTIONAL_SKILL_DIRS = {"scripts", "references", "assets", "agents"}


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
) -> tuple[GeneratedSkill, ...]:
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

    generated = [
        _build_generated_skill(
            raw_skill,
            f"{raw_skill.marketplace}-{raw_skill.plugin_name}-{raw_skill.skill_dir_name}",
        )
        for raw_skill in raw_skills
    ]

    return tuple(sorted(generated, key=lambda item: item.install_dir_name))


def translate_standalone_skills(
    skill_paths: Iterable[Path],
    *,
    scope: str,
) -> tuple[GeneratedSkill, ...]:
    """Translate user-level or project-level Claude skills into Codex skills.

    User-level skills get a ``user-`` prefix for the install directory name
    to avoid collisions in the global ``~/.codex/skills/`` registry.

    Project-level skills keep their raw directory name because they are
    installed to project-local ``.codex/skills/`` where project scope
    provides natural isolation.
    """
    generated: list[GeneratedSkill] = []

    for skill_path in skill_paths:
        original_name = _read_required_skill_name(skill_path / "SKILL.md")

        if scope == "project":
            install_dir_name = skill_path.name
        else:
            install_dir_name = f"{scope}-{skill_path.name}"

        generated_files: dict[Path, tuple[bytes, int]] = {}
        _copy_skill_tree(skill_path, Path(), generated_files)

        skill_content = read_utf8_text(
            skill_path / "SKILL.md", label="skill file", error_type=TranslationError
        )
        rewritten, referenced_sources = _resolve_relative_references(
            skill_path, skill_content,
        )
        rewritten = _rewrite_frontmatter_name(rewritten, install_dir_name)
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

    return tuple(sorted(generated, key=lambda s: s.install_dir_name))


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


def _build_generated_skill(
    raw_skill: _RawSkill,
    install_dir_name: str,
) -> GeneratedSkill:
    """Build the generated file tree for one installed Claude skill."""
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

    return GeneratedSkill(
        marketplace=raw_skill.marketplace,
        plugin_name=raw_skill.plugin_name,
        source_path=raw_skill.skill_path,
        install_dir_name=install_dir_name,
        original_skill_name=raw_skill.original_skill_name,
        codex_skill_name=install_dir_name,
        files=files,
    )


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
        if entry.is_dir() and entry.name in OPTIONAL_SKILL_DIRS
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
    """Copy only the official skill-layout files from a skill root."""
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

        if entry.is_dir() and entry.name in OPTIONAL_SKILL_DIRS:
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
