"""Tests for skill translation and generated skill trees."""

from __future__ import annotations

from pathlib import Path

import pytest

import cc_codex_bridge.frontmatter as frontmatter_module
from cc_codex_bridge.discover import discover_latest_plugins
from cc_codex_bridge.model import GeneratedSkill, GeneratedSkillFile, TranslationError
from cc_codex_bridge.registry import hash_generated_skill
from cc_codex_bridge.translate_agents import translate_installed_agents
from cc_codex_bridge.translate_skills import (
    assign_skill_names,
    translate_installed_skills,
    translate_standalone_skills,
)


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

    skills = translate_installed_skills(discover_latest_plugins(cache_root)).skills
    codex_home = tmp_path / "codex-home"
    installed_paths = tuple(
        _write_skill_directory(codex_home / "skills" / skill.install_dir_name, skill)
        for skill in skills
    )

    assert installed_paths == (codex_home / "skills" / "prompt-engineer",)
    installed_root = installed_paths[0]
    assert (installed_root / "SKILL.md").read_text().startswith(
        "---\nname: prompt-engineer\n"
    )
    assert (installed_root / "references" / "guide.md").read_text() == "Reference material.\n"
    assert (installed_root / "scripts" / "check.sh").read_text() == "#!/bin/sh\necho ok\n"
    assert (installed_root / "scripts" / "check.sh").stat().st_mode & 0o777 == 0o755


def test_generated_skills_copy_nonstandard_directories(make_plugin_version, tmp_path: Path):
    """Non-standard directories (beyond scripts/references/assets) are copied."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "arch-plugin",
        "1.0.0",
        skill_names=("software-architecture",),
    )
    skill_dir = version_dir / "skills" / "software-architecture"
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: software-architecture\n"
        "description: Architecture patterns\n"
        "---\n\n"
        "Read `patterns/behavioral/strategy.md` for details.\n"
    )
    # Non-standard directory: patterns/ with subdirectories
    patterns_dir = skill_dir / "patterns" / "behavioral"
    patterns_dir.mkdir(parents=True)
    (patterns_dir / "strategy.md").write_text("Strategy pattern.\n")
    (skill_dir / "patterns" / "creational").mkdir()
    (skill_dir / "patterns" / "creational" / "factory.md").write_text("Factory pattern.\n")

    skills = translate_installed_skills(discover_latest_plugins(cache_root)).skills
    codex_home = tmp_path / "codex-home"
    for skill in skills:
        _write_skill_directory(codex_home / "skills" / skill.install_dir_name, skill)

    installed_root = codex_home / "skills" / "software-architecture"
    assert (installed_root / "patterns" / "behavioral" / "strategy.md").read_text() == "Strategy pattern.\n"
    assert (installed_root / "patterns" / "creational" / "factory.md").read_text() == "Factory pattern.\n"


def test_generated_skills_skip_ignored_directories(make_plugin_version, tmp_path: Path):
    """Known noise directories (.git, node_modules, etc.) are not copied."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "tools",
        "1.0.0",
        skill_names=("my-tool",),
    )
    skill_dir = version_dir / "skills" / "my-tool"
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-tool\ndescription: A tool\n---\n\nBody.\n"
    )
    # Create directories that should be ignored
    (skill_dir / "node_modules" / "dep").mkdir(parents=True)
    (skill_dir / "node_modules" / "dep" / "index.js").write_text("module.exports = {}\n")
    (skill_dir / ".git" / "objects").mkdir(parents=True)
    (skill_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    # And a valid directory that should be copied
    (skill_dir / "examples").mkdir()
    (skill_dir / "examples" / "demo.md").write_text("Demo.\n")

    skills = translate_installed_skills(discover_latest_plugins(cache_root)).skills
    codex_home = tmp_path / "codex-home"
    for skill in skills:
        _write_skill_directory(codex_home / "skills" / skill.install_dir_name, skill)

    installed_root = codex_home / "skills" / "my-tool"
    assert (installed_root / "examples" / "demo.md").read_text() == "Demo.\n"
    assert not (installed_root / "node_modules").exists()
    assert not (installed_root / ".git").exists()


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

    skills = translate_installed_skills(discover_latest_plugins(cache_root)).skills
    for skill in skills:
        _write_skill_directory(tmp_path / "codex-home" / "skills" / skill.install_dir_name, skill)

    installed_root = tmp_path / "codex-home" / "skills" / "decision-critic"
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

    skills = translate_installed_skills(discover_latest_plugins(cache_root)).skills
    for skill in skills:
        _write_skill_directory(tmp_path / "codex-home" / "skills" / skill.install_dir_name, skill)

    installed_root = tmp_path / "codex-home" / "skills" / "e2e-testing-patterns"
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
    # Create a references/ dir inside the skill
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

    skills = translate_installed_skills(discover_latest_plugins(cache_root)).skills

    # Both plugins produce the same provisional bare name.
    # Collision resolution is handled by assign_skill_names() in a later step.
    assert [skill.install_dir_name for skill in skills] == [
        "review",
        "review",
    ]
    assert [skill.codex_skill_name for skill in skills] == [
        "review",
        "review",
    ]


def test_hash_generated_skill_is_order_independent():
    """Skill hashing depends on normalized content, not caller-provided file order."""
    first = GeneratedSkill(
        marketplace="market",
        plugin_name="prompt-engineer",
        source_path=Path("/tmp/source"),
        install_dir_name="prompt-engineer",
        original_skill_name="prompt-engineer",
        codex_skill_name="prompt-engineer",
        files=(
            GeneratedSkillFile(
                relative_path=Path("scripts") / "check.sh",
                content=b"#!/bin/sh\necho ok\n",
                mode=0o755,
            ),
            GeneratedSkillFile(
                relative_path=Path("SKILL.md"),
                content=b"---\nname: prompt-engineer\n---\n",
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
        install_dir_name="prompt-engineer",
        original_skill_name="prompt-engineer",
        codex_skill_name="prompt-engineer",
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
    """User-level skills use bare directory names."""
    skill_dir = tmp_path / "skills" / "my-tool"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-tool\ndescription: A tool\n---\n\nUse this.\n"
    )

    result = translate_standalone_skills((skill_dir,), scope="user")

    assert len(result.skills) == 1
    assert result.skills[0].install_dir_name == "my-tool"
    assert result.skills[0].codex_skill_name == "my-tool"
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    assert b"name: my-tool" in skill_md.content


def test_translate_project_skill(tmp_path: Path):
    """Project-level skills use raw names (no scope prefix)."""
    skill_dir = tmp_path / "skills" / "run-tests"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: run-tests\ndescription: Run the test suite\n---\n\nRun tests.\n"
    )

    result = translate_standalone_skills((skill_dir,), scope="project")

    assert len(result.skills) == 1
    assert result.skills[0].install_dir_name == "run-tests"
    assert result.skills[0].codex_skill_name == "run-tests"
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
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

    assert len(result.skills) == 1
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    assert b"shared/guide.md" in skill_md.content
    guide = next(f for f in result.skills[0].files if f.relative_path == Path("shared") / "guide.md")
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
        commands=(),
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
        commands=(),
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
        commands=(),
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
        commands=(),
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


def test_sibling_reference_regex_ignores_triple_dot_paths(
    make_plugin_version,
    tmp_path: Path,
):
    """The sibling reference regex must not match .../name/ inside ellipsis paths.

    Skill content may contain paths like ~/.claude/plugins/cache/.../plugin-name/
    in comments or code examples.  The triple-dot pattern embeds ../ which must
    not be treated as a sibling skill reference.
    """
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("analyzing-sessions",),
    )
    skill_dir = version_dir / "skills" / "analyzing-sessions"
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: analyzing-sessions\n"
        "description: Session analysis\n"
        "---\n\n"
        "# Example path\n"
        "# ~/.claude/plugins/cache/.../pirategoat-tools/1.43.3/skills/analyzing-sessions\n"
        "# PLUGIN_ROOT is ~/.claude/plugins/cache/.../pirategoat-tools/1.43.3\n"
    )

    skills = translate_installed_skills(discover_latest_plugins(cache_root)).skills

    assert len(skills) == 1
    assert skills[0].install_dir_name == "analyzing-sessions"


def test_sibling_reference_regex_still_matches_real_siblings(
    make_plugin_version,
    tmp_path: Path,
):
    """Legitimate ../sibling/ references still resolve after the false-positive fix."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("child-skill", "shared-lib"),
    )
    child_dir = version_dir / "skills" / "child-skill"
    (child_dir / "SKILL.md").write_text(
        "---\n"
        "name: child-skill\n"
        "description: Uses sibling\n"
        "---\n\n"
        "Read `../shared-lib/data.md` for context.\n"
    )
    shared_dir = version_dir / "skills" / "shared-lib"
    (shared_dir / "SKILL.md").write_text(
        "---\nname: shared-lib\ndescription: Shared data\n---\n\nShared.\n"
    )
    (shared_dir / "data.md").write_text("Shared data.\n")

    skills = translate_installed_skills(discover_latest_plugins(cache_root)).skills

    child = next(s for s in skills if "child-skill" in s.install_dir_name)
    file_paths = [f.relative_path.as_posix() for f in child.files]
    assert "shared-lib/data.md" in file_paths
    skill_md = next(f for f in child.files if f.relative_path == Path("SKILL.md"))
    assert b"shared-lib/data.md" in skill_md.content
    assert b"../shared-lib/" not in skill_md.content


def test_translate_standalone_skill_empty_input():
    """Empty skill paths produce empty result."""
    result = translate_standalone_skills((), scope="user")
    assert result.skills == ()
    assert result.diagnostics == ()


# -- skill validation diagnostic tests --


def test_skill_translation_produces_no_warnings_for_valid_skill(make_plugin_version):
    """Valid skills produce no diagnostics."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("my-tool",),
    )
    (version_dir / "skills" / "my-tool" / "SKILL.md").write_text(
        "---\nname: my-tool\ndescription: A useful tool\n---\n\nBody.\n"
    )
    result = translate_installed_skills(discover_latest_plugins(cache_root))
    assert len(result.skills) == 1
    assert result.diagnostics == ()


def test_skill_translation_warns_on_missing_description(make_plugin_version):
    """Missing description is a source-quality warning, not a hard error."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("bad-skill",),
    )
    (version_dir / "skills" / "bad-skill" / "SKILL.md").write_text(
        "---\nname: bad-skill\n---\n\nBody.\n"
    )
    result = translate_installed_skills(discover_latest_plugins(cache_root))
    assert len(result.skills) == 1
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].skill_name == "bad-skill"
    assert any("description" in w for w in result.diagnostics[0].warnings)


def test_skill_translation_warns_on_unexpected_fields(make_plugin_version):
    """Unexpected frontmatter fields produce warnings, not errors."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("my-tool",),
    )
    (version_dir / "skills" / "my-tool" / "SKILL.md").write_text(
        "---\nname: my-tool\ndescription: A tool\ncustom-field: value\n---\n\nBody.\n"
    )
    result = translate_installed_skills(discover_latest_plugins(cache_root))
    assert len(result.skills) == 1
    assert len(result.diagnostics) == 1
    assert any("unexpected" in w.lower() for w in result.diagnostics[0].warnings)


def test_standalone_skill_translation_returns_diagnostics(tmp_path: Path):
    """Standalone skill translation also collects validation warnings."""
    skill_dir = tmp_path / "skills" / "my-tool"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-tool\n---\n\nNo description.\n"
    )
    result = translate_standalone_skills((skill_dir,), scope="user")
    assert len(result.skills) == 1
    assert len(result.diagnostics) == 1
    assert any("description" in w for w in result.diagnostics[0].warnings)


# -- assign_skill_names tests --


def _make_skill(
    bare_name: str,
    marketplace: str = "market",
    plugin_name: str = "plugin",
) -> GeneratedSkill:
    """Build a minimal GeneratedSkill for assign_skill_names tests."""
    return GeneratedSkill(
        marketplace=marketplace,
        plugin_name=plugin_name,
        source_path=Path(f"/tmp/skills/{bare_name}"),
        install_dir_name=bare_name,
        original_skill_name=bare_name,
        codex_skill_name=bare_name,
        files=(
            GeneratedSkillFile(
                relative_path=Path("SKILL.md"),
                content=f"---\nname: {bare_name}\ndescription: test\n---\n\nBody.\n".encode(),
                mode=0o644,
            ),
        ),
    )


def test_assign_skill_names_no_collisions():
    """Without collisions, skills use their bare directory name."""
    skills = (
        _make_skill("brainstorming"),
        _make_skill("debugging", marketplace="beta", plugin_name="tools"),
    )
    result = assign_skill_names(skills)

    assert [s.install_dir_name for s in result] == ["brainstorming", "debugging"]
    assert [s.codex_skill_name for s in result] == ["brainstorming", "debugging"]


def test_assign_skill_names_user_wins_collision():
    """User skills get the bare name; plugin skills get -alt suffix."""
    user_skill = _make_skill("review", marketplace="_user", plugin_name="personal")
    plugin_skill = _make_skill("review", marketplace="alpha", plugin_name="tools")
    result = assign_skill_names((plugin_skill, user_skill))

    assert [s.install_dir_name for s in result] == ["review", "review-alt"]
    assert result[0].marketplace == "_user"
    assert result[1].marketplace == "alpha"


def test_assign_skill_names_plugin_collision_sorted_by_marketplace_plugin():
    """Among plugins, first in (marketplace, plugin_name) sort wins bare name."""
    beta_skill = _make_skill("review", marketplace="beta", plugin_name="tools")
    alpha_skill = _make_skill("review", marketplace="alpha", plugin_name="tools")
    result = assign_skill_names((beta_skill, alpha_skill))

    assert [s.install_dir_name for s in result] == ["review", "review-alt"]
    assert result[0].marketplace == "alpha"
    assert result[1].marketplace == "beta"


def test_assign_skill_names_three_way_collision():
    """Third collision gets -alt-2 suffix."""
    user_skill = _make_skill("review", marketplace="_user", plugin_name="personal")
    alpha_skill = _make_skill("review", marketplace="alpha", plugin_name="tools")
    beta_skill = _make_skill("review", marketplace="beta", plugin_name="tools")
    result = assign_skill_names((beta_skill, alpha_skill, user_skill))

    assert [s.install_dir_name for s in result] == [
        "review", "review-alt", "review-alt-2",
    ]
    assert result[0].marketplace == "_user"
    assert result[1].marketplace == "alpha"
    assert result[2].marketplace == "beta"


def test_assign_skill_names_rejects_name_over_64_chars():
    """Names exceeding 64 characters after suffixing are a hard error."""
    long_name = "a" * 65
    skill = _make_skill(long_name)
    with pytest.raises(TranslationError, match="exceeds 64 characters"):
        assign_skill_names((skill,))


def test_assign_skill_names_rewrites_skill_md_frontmatter():
    """The name: field in SKILL.md is rewritten to match the assigned name."""
    user_skill = _make_skill("review", marketplace="_user", plugin_name="personal")
    plugin_skill = _make_skill("review", marketplace="alpha", plugin_name="tools")
    result = assign_skill_names((plugin_skill, user_skill))

    user_md = next(
        f for f in result[0].files if f.relative_path == Path("SKILL.md")
    )
    assert b"name: review\n" in user_md.content

    plugin_md = next(
        f for f in result[1].files if f.relative_path == Path("SKILL.md")
    )
    assert b"name: review-alt\n" in plugin_md.content


def test_assign_skill_names_allows_exactly_64_chars():
    """A name of exactly 64 characters passes validation."""
    name_64 = "a" * 64
    skill = _make_skill(name_64)
    result = assign_skill_names((skill,))

    assert len(result) == 1
    assert result[0].install_dir_name == name_64


def test_assign_skill_names_rejects_65_char_bare_name():
    """A skill directory name of 65 characters is rejected even without collision."""
    name_65 = "a" * 65
    skill = _make_skill(name_65)
    with pytest.raises(TranslationError, match="exceeds 64 characters"):
        assign_skill_names((skill,))


def test_assign_skill_names_rejects_when_alt_suffix_exceeds_64():
    """A collision suffix that pushes the name over 64 chars is rejected."""
    # 61 chars + "-alt" = 65 chars → should fail for the second skill
    name_61 = "a" * 61
    skill_a = _make_skill(name_61, marketplace="_user", plugin_name="personal")
    skill_b = _make_skill(name_61, marketplace="alpha", plugin_name="tools")
    with pytest.raises(TranslationError, match="exceeds 64 characters"):
        assign_skill_names((skill_a, skill_b))


def test_skill_plugin_root_references_are_rewritten(make_plugin_version, tmp_path: Path):
    """Skills referencing $PLUGIN_ROOT/scripts/ get paths rewritten."""
    cache_root, version_dir = make_plugin_version(
        "market", "pirategoat-tools", "1.0.0",
        skill_names=("using-figma",),
    )
    # Create plugin-level scripts
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "figma-parse-nodes.py").write_text("print('parse')\n")

    skill_dir = version_dir / "skills" / "using-figma"
    (skill_dir / "SKILL.md").write_text(
        "---\nname: using-figma\ndescription: Figma integration\n---\n\n"
        'PLUGIN_ROOT="<skill base directory>/../.."\n'
        'python3 "$PLUGIN_ROOT/scripts/figma-parse-nodes.py" input.json\n'
    )

    bridge = tmp_path / "bridge-home"
    result = translate_installed_skills(
        discover_latest_plugins(cache_root),
        bridge_home=bridge,
    )
    assert len(result.skills) == 1

    skill_md = next(
        f for f in result.skills[0].files if f.relative_path == Path("SKILL.md")
    )
    content = skill_md.content.decode()
    # The <skill base directory>/../.. pattern should be gone
    assert '<skill base directory>/../..' not in content
    # The rewritten path should reference the vendored location
    assert 'figma-parse-nodes.py' in content

    # Plugin resources should be collected
    assert len(result.plugin_resources) == 1
    resource = result.plugin_resources[0]
    assert resource.marketplace == "market"
    assert resource.plugin_name == "pirategoat-tools"
    assert resource.target_dir_name == "scripts"
    assert any(f.relative_path == Path("figma-parse-nodes.py") for f in resource.files)


def _write_skill_directory(destination: Path, skill: GeneratedSkill) -> Path:
    """Materialize one generated skill tree for test assertions."""
    destination.mkdir(parents=True, exist_ok=True)
    for generated_file in skill.files:
        file_path = destination / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(generated_file.content)
        file_path.chmod(generated_file.mode)
    return destination
