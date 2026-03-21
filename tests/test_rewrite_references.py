"""Tests for plugin-qualified reference rewriting."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.model import GeneratedPrompt, GeneratedSkill, GeneratedSkillFile
from cc_codex_bridge.rewrite_references import build_reference_map, rewrite_content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(
    *,
    marketplace: str,
    plugin_name: str,
    original_skill_name: str,
    codex_skill_name: str,
) -> GeneratedSkill:
    """Create a minimal GeneratedSkill for reference-map tests."""
    return GeneratedSkill(
        marketplace=marketplace,
        plugin_name=plugin_name,
        source_path=Path("/fake/skills") / original_skill_name,
        install_dir_name=codex_skill_name,
        original_skill_name=original_skill_name,
        codex_skill_name=codex_skill_name,
        files=(
            GeneratedSkillFile(
                relative_path=Path("SKILL.md"),
                content=b"---\nname: test\n---\n",
                mode=0o644,
            ),
        ),
    )


def _make_prompt(
    *,
    marketplace: str,
    plugin_name: str,
    filename: str,
    source_stem: str,
) -> GeneratedPrompt:
    """Create a minimal GeneratedPrompt for reference-map tests."""
    return GeneratedPrompt(
        filename=filename,
        content=b"---\ndescription: test\n---\n",
        source_path=Path("/fake/commands") / f"{source_stem}.md",
        marketplace=marketplace,
        plugin_name=plugin_name,
    )


# ---------------------------------------------------------------------------
# build_reference_map tests
# ---------------------------------------------------------------------------


class TestBuildReferenceMap:
    """Tests for build_reference_map."""

    def test_build_reference_map_from_skills(self):
        """Skills produce plugin:original_name -> $codex_name entries."""
        skill = _make_skill(
            marketplace="market",
            plugin_name="superpowers",
            original_skill_name="brainstorming",
            codex_skill_name="brainstorming",
        )

        result = build_reference_map(skills=(skill,), prompts=())

        assert result == {"superpowers:brainstorming": "$brainstorming"}

    def test_build_reference_map_from_prompts(self):
        """Prompts produce plugin:command_stem -> $prompt_stem entries."""
        prompt = _make_prompt(
            marketplace="market",
            plugin_name="pirategoat-tools",
            filename="code-review.md",
            source_stem="code-review",
        )

        result = build_reference_map(skills=(), prompts=(prompt,))

        assert result == {"pirategoat-tools:code-review": "$code-review"}

    def test_build_reference_map_combined(self):
        """Skills + prompts from different plugins produce combined map."""
        skill = _make_skill(
            marketplace="market-a",
            plugin_name="superpowers",
            original_skill_name="brainstorming",
            codex_skill_name="brainstorming",
        )
        prompt = _make_prompt(
            marketplace="market-b",
            plugin_name="pirategoat-tools",
            filename="code-review.md",
            source_stem="code-review",
        )

        result = build_reference_map(skills=(skill,), prompts=(prompt,))

        assert result == {
            "superpowers:brainstorming": "$brainstorming",
            "pirategoat-tools:code-review": "$code-review",
        }

    def test_build_reference_map_with_collision_suffix(self):
        """Collision-resolved skills use codex_skill_name, not original_skill_name."""
        skill = _make_skill(
            marketplace="market",
            plugin_name="superpowers",
            original_skill_name="brainstorming",
            codex_skill_name="brainstorming--superpowers",
        )

        result = build_reference_map(skills=(skill,), prompts=())

        assert result == {
            "superpowers:brainstorming": "$brainstorming--superpowers",
        }

    def test_build_reference_map_skips_user_and_project_scopes(self):
        """Marketplace starting with '_' is excluded (user/project scope)."""
        user_skill = _make_skill(
            marketplace="_user",
            plugin_name="my-skills",
            original_skill_name="quick-fix",
            codex_skill_name="quick-fix",
        )
        project_skill = _make_skill(
            marketplace="_project",
            plugin_name="local",
            original_skill_name="lint",
            codex_skill_name="lint",
        )
        user_prompt = _make_prompt(
            marketplace="_user",
            plugin_name="my-commands",
            filename="check.md",
            source_stem="check",
        )
        plugin_skill = _make_skill(
            marketplace="market",
            plugin_name="superpowers",
            original_skill_name="brainstorming",
            codex_skill_name="brainstorming",
        )

        result = build_reference_map(
            skills=(user_skill, project_skill, plugin_skill),
            prompts=(user_prompt,),
        )

        assert result == {"superpowers:brainstorming": "$brainstorming"}


# ---------------------------------------------------------------------------
# rewrite_content tests
# ---------------------------------------------------------------------------


class TestRewriteContent:
    """Tests for rewrite_content."""

    def test_rewrite_content_replaces_known_references(self):
        """A known plugin:skill reference is replaced with $codex_name."""
        content = b"Use superpowers:brainstorming to generate ideas."
        ref_map = {"superpowers:brainstorming": "$brainstorming"}

        result = rewrite_content(content, ref_map)

        assert result == b"Use $brainstorming to generate ideas."

    def test_rewrite_content_replaces_multiple_references(self):
        """Multiple different references are all replaced."""
        content = (
            b"First run pirategoat-tools:code-review, "
            b"then use superpowers:brainstorming."
        )
        ref_map = {
            "pirategoat-tools:code-review": "$code-review",
            "superpowers:brainstorming": "$brainstorming",
        }

        result = rewrite_content(content, ref_map)

        assert result == (
            b"First run $code-review, "
            b"then use $brainstorming."
        )

    def test_rewrite_content_leaves_unknown_references(self):
        """URLs, YAML keys, and unknown references are not touched."""
        content = (
            b"Visit https://example.com:8080/path\n"
            b"key: value\n"
            b"unknown-plugin:unknown-skill stays unchanged\n"
        )
        ref_map = {"superpowers:brainstorming": "$brainstorming"}

        result = rewrite_content(content, ref_map)

        assert result == content

    def test_rewrite_content_empty_map_is_noop(self):
        """An empty reference map returns content unchanged."""
        content = b"Use superpowers:brainstorming to generate ideas."

        result = rewrite_content(content, {})

        assert result is content  # identity check — no copy

    def test_rewrite_content_longest_match_first(self):
        """Longer keys are matched before shorter prefixes."""
        content = b"Use tools:review-pr not tools:review here."
        ref_map = {
            "tools:review": "$review",
            "tools:review-pr": "$review-pr",
        }

        result = rewrite_content(content, ref_map)

        assert result == b"Use $review-pr not $review here."

    def test_rewrite_content_handles_backtick_wrapped_references(self):
        """References inside backticks are still replaced."""
        content = b"Run `superpowers:brainstorming` for ideas."
        ref_map = {"superpowers:brainstorming": "$brainstorming"}

        result = rewrite_content(content, ref_map)

        assert result == b"Run `$brainstorming` for ideas."
