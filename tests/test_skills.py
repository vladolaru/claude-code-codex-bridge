"""Tests for skill translation and generated skill trees."""

from __future__ import annotations

from pathlib import Path

import pytest

import cc_codex_bridge.frontmatter as frontmatter_module
from cc_codex_bridge.discover import discover_latest_plugins
from cc_codex_bridge.model import GeneratedSkill, GeneratedSkillFile, TranslationError
from cc_codex_bridge.registry import hash_generated_skill
from cc_codex_bridge.translate_agents import translate_installed_agents
from cc_codex_bridge.translate_skills import translate_installed_skills, translate_standalone_skills


def test_generated_skills_copy_bundled_resources(make_plugin_version, tmp_path: Path):
    """A translated skill stays self-contained after materialization."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    skill_dir = version_dir / "skills" / "prompt-engineer"
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: prompt-engineer\n"
        "description: Prompt help\n"
        "---\n\n"
        "Read `references/guide.md` before using `scripts/check.sh`.\n"
    )
    references_dir = skill_dir / "references"
    references_dir.mkdir()
    (references_dir / "guide.md").write_text("Reference material.\n")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    check_script = scripts_dir / "check.sh"
    check_script.write_text("#!/bin/sh\necho ok\n")
    check_script.chmod(0o755)

    skills = translate_installed_skills(discover_latest_plugins(cache_root))
    codex_home = tmp_path / "codex-home"
    installed_paths = tuple(
        _write_skill_directory(codex_home / "skills" / skill.install_dir_name, skill)
        for skill in skills
    )

    assert installed_paths == (codex_home / "skills" / "market-prompt-engineer-prompt-engineer",)
    installed_root = installed_paths[0]
    assert (installed_root / "SKILL.md").read_text().startswith(
        "---\nname: market-prompt-engineer-prompt-engineer\n"
    )
    assert (installed_root / "references" / "guide.md").read_text() == "Reference material.\n"
    assert (installed_root / "scripts" / "check.sh").read_text() == "#!/bin/sh\necho ok\n"
    assert (installed_root / "scripts" / "check.sh").stat().st_mode & 0o777 == 0o755


def test_translate_installed_skills_resolves_sibling_directory_references(
    make_plugin_version,
    tmp_path: Path,
):
    """Sibling directory references are resolved and copied directly into the skill."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("decision-critic",),
    )
    # Create a shared scripts directory as a sibling of the skills directory
    # At skill level, ../shared-scripts/ resolves to skills/shared-scripts/
    shared_scripts = version_dir / "skills" / "shared-scripts"
    shared_scripts.mkdir(parents=True)
    (shared_scripts / "decision-critic.py").write_text("print('ok')\n")

    skill_dir = version_dir / "skills" / "decision-critic"
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: decision-critic\n"
        "description: Criticize decisions\n"
        "---\n\n"
        'python3 "../shared-scripts/decision-critic.py"\n'
    )

    skills = translate_installed_skills(discover_latest_plugins(cache_root))
    for skill in skills:
        _write_skill_directory(tmp_path / "codex-home" / "skills" / skill.install_dir_name, skill)

    installed_root = tmp_path / "codex-home" / "skills" / "market-pirategoat-tools-decision-critic"
    skill_md = (installed_root / "SKILL.md").read_text()
    # Reference is rewritten from ../shared-scripts/ to shared-scripts/
    assert 'python3 "shared-scripts/decision-critic.py"' in skill_md
    assert (installed_root / "shared-scripts" / "decision-critic.py").read_text() == "print('ok')\n"


def test_translate_installed_skills_vendors_referenced_sibling_skills(
    make_plugin_version,
    tmp_path: Path,
):
    """Cross-skill relative references are rewritten and copied directly."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("e2e-testing-patterns", "testing-patterns"),
    )
    e2e_skill_dir = version_dir / "skills" / "e2e-testing-patterns"
    (e2e_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: e2e-testing-patterns\n"
        "description: E2E guidance\n"
        "---\n\n"
        "Read `../testing-patterns/references/test-philosophy.md` first.\n"
    )
    testing_skill_dir = version_dir / "skills" / "testing-patterns"
    (testing_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: testing-patterns\n"
        "description: Shared testing guidance\n"
        "---\n\n"
        "Sibling skill.\n"
    )
    sibling_references = testing_skill_dir / "references"
    sibling_references.mkdir()
    (sibling_references / "test-philosophy.md").write_text("Behavior over implementation.\n")

    skills = translate_installed_skills(discover_latest_plugins(cache_root))
    for skill in skills:
        _write_skill_directory(tmp_path / "codex-home" / "skills" / skill.install_dir_name, skill)

    installed_root = tmp_path / "codex-home" / "skills" / "market-pirategoat-tools-e2e-testing-patterns"
    skill_md = (installed_root / "SKILL.md").read_text()
    assert "testing-patterns/references/test-philosophy.md" in skill_md
    assert (
        installed_root / "testing-patterns" / "references" / "test-philosophy.md"
    ).read_text() == "Behavior over implementation.\n"


def test_translate_installed_skills_rejects_missing_sibling_skill_references(make_plugin_version):
    """Relocated sibling-skill references must resolve to a real sibling."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("e2e-testing-patterns",),
    )
    e2e_skill_dir = version_dir / "skills" / "e2e-testing-patterns"
    (e2e_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: e2e-testing-patterns\n"
        "description: E2E guidance\n"
        "---\n\n"
        "Read `../testing-patterns/references/test-philosophy.md` first.\n"
    )

    with pytest.raises(TranslationError, match="missing sibling"):
        translate_installed_skills(discover_latest_plugins(cache_root))


def test_translate_installed_skills_rejects_colliding_sibling_reference(
    make_plugin_version,
):
    """Sibling references that collide with existing skill directories are rejected."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("my-skill",),
    )
    skill_dir = version_dir / "skills" / "my-skill"
    # Create a references/ dir inside the skill (one of OPTIONAL_SKILL_DIRS)
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "guide.md").write_text("Guide.\n")

    # Also create a references/ sibling directory and reference it
    sibling_refs = version_dir / "skills" / "references"
    sibling_refs.mkdir(parents=True)
    (sibling_refs / "other.md").write_text("Other.\n")

    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: my-skill\n"
        "description: test\n"
        "---\n\n"
        "Read `../references/other.md`.\n"
    )

    with pytest.raises(TranslationError, match="collides with an existing directory"):
        translate_installed_skills(discover_latest_plugins(cache_root))


def test_translate_installed_skills_handles_name_and_directory_collisions(make_plugin_version):
    """Marketplace prefixes resolve deterministic collisions."""
    cache_root, alpha_dir = make_plugin_version(
        "alpha",
        "shared-plugin",
        "1.0.0",
        skill_names=("review",),
    )
    _, beta_dir = make_plugin_version(
        "beta",
        "shared-plugin",
        "1.0.0",
        skill_names=("review",),
    )
    for skill_dir in (alpha_dir / "skills" / "review", beta_dir / "skills" / "review"):
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: reviewer\n"
            "description: Review things\n"
            "---\n\n"
            "Review instructions.\n"
        )

    skills = translate_installed_skills(discover_latest_plugins(cache_root))

    assert [skill.install_dir_name for skill in skills] == [
        "alpha-shared-plugin-review",
        "beta-shared-plugin-review",
    ]
    assert [skill.codex_skill_name for skill in skills] == [
        "alpha-shared-plugin-review",
        "beta-shared-plugin-review",
    ]


def test_hash_generated_skill_is_order_independent():
    """Skill hashing depends on normalized content, not caller-provided file order."""
    first = GeneratedSkill(
        marketplace="market",
        plugin_name="prompt-engineer",
        source_path=Path("/tmp/source"),
        install_dir_name="market-prompt-engineer-prompt-engineer",
        original_skill_name="prompt-engineer",
        codex_skill_name="market-prompt-engineer-prompt-engineer",
        files=(
            GeneratedSkillFile(
                relative_path=Path("scripts") / "check.sh",
                content=b"#!/bin/sh\necho ok\n",
                mode=0o755,
            ),
            GeneratedSkillFile(
                relative_path=Path("SKILL.md"),
                content=b"---\nname: market-prompt-engineer-prompt-engineer\n---\n",
                mode=0o644,
            ),
        ),
    )
    second = GeneratedSkill(
        marketplace=first.marketplace,
        plugin_name=first.plugin_name,
        source_path=first.source_path,
        install_dir_name=first.install_dir_name,
        original_skill_name=first.original_skill_name,
        codex_skill_name=first.codex_skill_name,
        files=tuple(reversed(first.files)),
    )

    assert hash_generated_skill(first) == hash_generated_skill(second)


def test_hash_generated_skill_tracks_bytes_and_mode():
    """Skill hashing changes when either file content or executable mode changes."""
    base = GeneratedSkill(
        marketplace="market",
        plugin_name="prompt-engineer",
        source_path=Path("/tmp/source"),
        install_dir_name="market-prompt-engineer-prompt-engineer",
        original_skill_name="prompt-engineer",
        codex_skill_name="market-prompt-engineer-prompt-engineer",
        files=(
            GeneratedSkillFile(relative_path=Path("SKILL.md"), content=b"alpha\n", mode=0o644),
        ),
    )
    changed_bytes = GeneratedSkill(
        marketplace=base.marketplace,
        plugin_name=base.plugin_name,
        source_path=base.source_path,
        install_dir_name=base.install_dir_name,
        original_skill_name=base.original_skill_name,
        codex_skill_name=base.codex_skill_name,
        files=(
            GeneratedSkillFile(relative_path=Path("SKILL.md"), content=b"beta\n", mode=0o644),
        ),
    )
    changed_mode = GeneratedSkill(
        marketplace=base.marketplace,
        plugin_name=base.plugin_name,
        source_path=base.source_path,
        install_dir_name=base.install_dir_name,
        original_skill_name=base.original_skill_name,
        codex_skill_name=base.codex_skill_name,
        files=(
            GeneratedSkillFile(relative_path=Path("SKILL.md"), content=b"alpha\n", mode=0o755),
        ),
    )

    assert hash_generated_skill(base) != hash_generated_skill(changed_bytes)
    assert hash_generated_skill(base) != hash_generated_skill(changed_mode)


def test_agent_and_skill_translation_share_the_same_frontmatter_entrypoint(
    make_plugin_version,
    monkeypatch: pytest.MonkeyPatch,
):
    """Both translation modules call through the shared frontmatter parser module."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    parsed_paths: list[Path] = []
    original_parse = frontmatter_module.parse_markdown_with_frontmatter

    def record_parse(path: Path):
        parsed_paths.append(path)
        return original_parse(path)

    monkeypatch.setattr(frontmatter_module, "parse_markdown_with_frontmatter", record_parse)
    plugins = discover_latest_plugins(cache_root)

    translate_installed_agents(plugins)
    translate_installed_skills(plugins)

    assert parsed_paths == [
        version_dir / "agents" / "reviewer.md",
        version_dir / "skills" / "prompt-engineer" / "SKILL.md",
    ]


def test_translate_user_skill(tmp_path: Path):
    """User-level skills are translated with a user- prefix."""
    skill_dir = tmp_path / "skills" / "my-tool"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-tool\ndescription: A tool\n---\n\nUse this.\n"
    )

    result = translate_standalone_skills((skill_dir,), scope="user")

    assert len(result) == 1
    assert result[0].install_dir_name == "user-my-tool"
    assert result[0].codex_skill_name == "user-my-tool"
    skill_md = next(f for f in result[0].files if f.relative_path == Path("SKILL.md"))
    assert b"name: user-my-tool" in skill_md.content


def test_translate_project_skill(tmp_path: Path):
    """Project-level skills use raw names (no scope prefix)."""
    skill_dir = tmp_path / "skills" / "run-tests"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: run-tests\ndescription: Run the test suite\n---\n\nRun tests.\n"
    )

    result = translate_standalone_skills((skill_dir,), scope="project")

    assert len(result) == 1
    assert result[0].install_dir_name == "run-tests"
    assert result[0].codex_skill_name == "run-tests"
    skill_md = next(f for f in result[0].files if f.relative_path == Path("SKILL.md"))
    assert b"name: run-tests" in skill_md.content


def test_translate_standalone_skill_with_sibling_reference(tmp_path: Path):
    """Standalone skills resolve ../references to filesystem siblings."""
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: test\n---\n\nRead `../shared/guide.md`.\n"
    )
    shared = skills_dir / "shared"
    shared.mkdir()
    (shared / "guide.md").write_text("Guide content.\n")

    result = translate_standalone_skills((skill_dir,), scope="user")

    assert len(result) == 1
    skill_md = next(f for f in result[0].files if f.relative_path == Path("SKILL.md"))
    assert b"shared/guide.md" in skill_md.content
    guide = next(f for f in result[0].files if f.relative_path == Path("shared") / "guide.md")
    assert guide.content == b"Guide content.\n"


def test_skill_translation_rejects_symlinked_resource_directory(tmp_path):
    """Skill translation must not follow symlinked resource directories."""
    from cc_codex_bridge.translate_skills import translate_installed_skills
    from cc_codex_bridge.model import InstalledPlugin, SemVer, TranslationError

    # Create a skill with a symlinked scripts/ directory
    cache_root = tmp_path / "cache"
    version_dir = cache_root / "market" / "tools" / "1.0.0"
    skill_dir = version_dir / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: review\ndescription: test\n---\nBody\n")

    outside = tmp_path / "outside_scripts"
    outside.mkdir()
    (outside / "run.sh").write_text("#!/bin/bash\necho hi\n")
    (skill_dir / "scripts").symlink_to(outside)

    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="tools",
        version_text="1.0.0",
        version=SemVer(1, 0, 0),
        installed_path=version_dir,
        source_path=version_dir,
        skills=(skill_dir,),
        agents=(),
    )

    with pytest.raises(TranslationError, match="symlink"):
        translate_installed_skills((plugin,))


def test_skill_translation_rejects_symlinked_file_in_resource_dir(tmp_path):
    """Skill translation must not follow symlinked files inside resource directories."""
    from cc_codex_bridge.translate_skills import translate_installed_skills
    from cc_codex_bridge.model import InstalledPlugin, SemVer, TranslationError

    cache_root = tmp_path / "cache"
    version_dir = cache_root / "market" / "tools" / "1.0.0"
    skill_dir = version_dir / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: review\ndescription: test\n---\nBody\n")

    references_dir = skill_dir / "references"
    references_dir.mkdir()
    (references_dir / "legit.md").write_text("Real content.\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("Leaked content.\n")
    (references_dir / "sneaky.md").symlink_to(outside / "secret.md")

    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="tools",
        version_text="1.0.0",
        version=SemVer(1, 0, 0),
        installed_path=version_dir,
        source_path=version_dir,
        skills=(skill_dir,),
        agents=(),
    )

    with pytest.raises(TranslationError, match="symlinked file"):
        translate_installed_skills((plugin,))


def test_skill_translation_rejects_symlinked_subdir_in_resource_dir(tmp_path):
    """Skill translation must not follow symlinked subdirectories inside resource directories."""
    from cc_codex_bridge.translate_skills import translate_installed_skills
    from cc_codex_bridge.model import InstalledPlugin, SemVer, TranslationError

    cache_root = tmp_path / "cache"
    version_dir = cache_root / "market" / "tools" / "1.0.0"
    skill_dir = version_dir / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: review\ndescription: test\n---\nBody\n")

    references_dir = skill_dir / "references"
    references_dir.mkdir()
    (references_dir / "legit.md").write_text("Real content.\n")

    outside = tmp_path / "outside_subdir"
    outside.mkdir()
    (outside / "leaked.md").write_text("Leaked content.\n")
    (references_dir / "sneaky-dir").symlink_to(outside)

    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="tools",
        version_text="1.0.0",
        version=SemVer(1, 0, 0),
        installed_path=version_dir,
        source_path=version_dir,
        skills=(skill_dir,),
        agents=(),
    )

    with pytest.raises(TranslationError, match="symlinked"):
        translate_installed_skills((plugin,))


def test_skill_translation_rejects_symlinked_top_level_file(tmp_path):
    """Skill translation must not follow symlinked top-level files in the skill root."""
    from cc_codex_bridge.translate_skills import translate_installed_skills
    from cc_codex_bridge.model import InstalledPlugin, SemVer, TranslationError

    cache_root = tmp_path / "cache"
    version_dir = cache_root / "market" / "tools" / "1.0.0"
    skill_dir = version_dir / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: review\ndescription: test\n---\nBody\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "extra.txt").write_text("Leaked content.\n")
    (skill_dir / "extra.txt").symlink_to(outside / "extra.txt")

    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="tools",
        version_text="1.0.0",
        version=SemVer(1, 0, 0),
        installed_path=version_dir,
        source_path=version_dir,
        skills=(skill_dir,),
        agents=(),
    )

    with pytest.raises(TranslationError, match="symlinked file"):
        translate_installed_skills((plugin,))


def test_skill_translation_rejects_symlinked_skill_md(tmp_path):
    """Skill translation must not follow a symlinked SKILL.md."""
    from cc_codex_bridge.translate_skills import translate_standalone_skills
    from cc_codex_bridge.model import TranslationError

    skill_dir = tmp_path / "skills" / "my-tool"
    skill_dir.mkdir(parents=True)

    real_skill = tmp_path / "elsewhere" / "SKILL.md"
    real_skill.parent.mkdir(parents=True)
    real_skill.write_text("---\nname: my-tool\ndescription: A tool\n---\n\nLeaked.\n")
    (skill_dir / "SKILL.md").symlink_to(real_skill)

    with pytest.raises(TranslationError, match="symlinked"):
        translate_standalone_skills((skill_dir,), scope="user")


def test_translate_standalone_skill_empty_input():
    """Empty skill paths produce empty result."""
    result = translate_standalone_skills((), scope="user")
    assert result == ()


def _write_skill_directory(destination: Path, skill: GeneratedSkill) -> Path:
    """Materialize one generated skill tree for test assertions."""
    destination.mkdir(parents=True, exist_ok=True)
    for generated_file in skill.files:
        file_path = destination / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(generated_file.content)
        file_path.chmod(generated_file.mode)
    return destination
