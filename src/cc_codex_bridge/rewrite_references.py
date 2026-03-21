"""Rewrite plugin-qualified references in generated Codex content.

Claude Code content references skills/commands using ``plugin:name`` syntax
(e.g., ``superpowers:brainstorming``).  Codex uses ``$skill-name`` syntax.
This module builds a lookup from generated artifacts and rewrites all known
references in arbitrary byte content.
"""

from __future__ import annotations

from cc_codex_bridge.model import GeneratedPrompt, GeneratedSkill


def build_reference_map(
    *,
    skills: tuple[GeneratedSkill, ...],
    prompts: tuple[GeneratedPrompt, ...],
) -> dict[str, str]:
    """Build a lookup of plugin:name -> $codex-name from generated artifacts.

    Only plugin-scoped artifacts are included (marketplace not starting
    with ``_``).  User and project artifacts are excluded because their
    names are not plugin-qualified in source content.
    """
    ref_map: dict[str, str] = {}

    for skill in skills:
        if skill.marketplace.startswith("_"):
            continue
        key = f"{skill.plugin_name}:{skill.original_skill_name}"
        value = f"${skill.codex_skill_name}"
        ref_map[key] = value

    for prompt in prompts:
        if prompt.marketplace.startswith("_"):
            continue
        source_stem = prompt.source_path.stem
        prompt_stem = prompt.filename.removesuffix(".md")
        key = f"{prompt.plugin_name}:{source_stem}"
        value = f"${prompt_stem}"
        ref_map[key] = value

    return ref_map


def rewrite_content(content: bytes, reference_map: dict[str, str]) -> bytes:
    """Replace all known plugin-qualified references in content.

    Keys are sorted longest-first to prevent shorter keys from
    partially matching longer ones.
    """
    if not reference_map:
        return content

    # Sort keys longest-first so e.g. "tools:review-pr" is matched
    # before "tools:review".
    sorted_keys = sorted(reference_map, key=len, reverse=True)

    result = content
    for key in sorted_keys:
        result = result.replace(key.encode(), reference_map[key].encode())

    return result
