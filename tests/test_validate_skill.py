"""Tests for Agent Skills Standard validation."""

from __future__ import annotations

from cc_codex_bridge.validate_skill import validate_skill_metadata


# -- structural rules (hard errors when bridge produces bad output) --

def test_missing_name():
    errors, warnings = validate_skill_metadata({"description": "A skill"})
    assert any("name" in e for e in errors)


def test_empty_name():
    errors, warnings = validate_skill_metadata({"name": "", "description": "A skill"})
    assert any("name" in e for e in errors)


def test_name_too_long():
    errors, warnings = validate_skill_metadata({"name": "a" * 65, "description": "A skill"})
    assert any("64" in e for e in errors)


def test_name_exactly_64_chars():
    errors, warnings = validate_skill_metadata(
        {"name": "a" * 64, "description": "A skill"}, dir_name="a" * 64,
    )
    assert errors == []


def test_name_directory_mismatch():
    errors, warnings = validate_skill_metadata(
        {"name": "correct-name", "description": "A skill"},
        dir_name="wrong-name",
    )
    assert any("match" in e for e in errors)


def test_name_directory_match():
    errors, warnings = validate_skill_metadata(
        {"name": "my-skill", "description": "A skill"},
        dir_name="my-skill",
    )
    assert errors == []
    assert warnings == []


# -- source-quality rules (warnings for upstream issues) --

def test_name_must_be_lowercase():
    errors, warnings = validate_skill_metadata(
        {"name": "MySkill", "description": "A skill"},
    )
    assert any("lowercase" in w for w in warnings)
    assert errors == []


def test_name_no_leading_hyphen():
    errors, warnings = validate_skill_metadata(
        {"name": "-my-skill", "description": "A skill"},
    )
    assert any("hyphen" in w for w in warnings)
    assert errors == []


def test_name_no_trailing_hyphen():
    errors, warnings = validate_skill_metadata(
        {"name": "my-skill-", "description": "A skill"},
    )
    assert any("hyphen" in w for w in warnings)
    assert errors == []


def test_name_no_consecutive_hyphens():
    errors, warnings = validate_skill_metadata(
        {"name": "my--skill", "description": "A skill"},
    )
    assert any("consecutive" in w for w in warnings)
    assert errors == []


def test_name_only_alphanumeric_and_hyphens():
    errors, warnings = validate_skill_metadata(
        {"name": "my_skill", "description": "A skill"},
    )
    assert any("invalid" in w.lower() for w in warnings)
    assert errors == []


def test_missing_description():
    errors, warnings = validate_skill_metadata({"name": "my-skill"})
    assert any("description" in w for w in warnings)
    assert errors == []


def test_empty_description():
    errors, warnings = validate_skill_metadata({"name": "my-skill", "description": ""})
    assert any("description" in w for w in warnings)
    assert errors == []


def test_description_too_long():
    errors, warnings = validate_skill_metadata(
        {"name": "my-skill", "description": "x" * 1025},
        dir_name="my-skill",
    )
    assert any("1024" in w for w in warnings)
    assert errors == []


def test_compatibility_too_long():
    errors, warnings = validate_skill_metadata(
        {"name": "my-skill", "description": "A skill", "compatibility": "x" * 501},
        dir_name="my-skill",
    )
    assert any("500" in w for w in warnings)
    assert errors == []


def test_valid_compatibility():
    errors, warnings = validate_skill_metadata(
        {"name": "my-skill", "description": "A skill", "compatibility": "Python 3.11+"},
        dir_name="my-skill",
    )
    assert errors == []
    assert warnings == []


def test_unexpected_frontmatter_fields():
    errors, warnings = validate_skill_metadata(
        {"name": "my-skill", "description": "A skill", "unknown_field": "value"},
        dir_name="my-skill",
    )
    assert any("unexpected" in w.lower() for w in warnings)
    assert errors == []


def test_all_allowed_fields_accepted():
    errors, warnings = validate_skill_metadata(
        {
            "name": "my-skill",
            "description": "A skill",
            "license": "MIT",
            "allowed-tools": "Bash(git:*)",
            "metadata": {"author": "Test"},
            "compatibility": "Python 3.11+",
        },
        dir_name="my-skill",
    )
    assert errors == []
    assert warnings == []


# -- valid skill --

def test_minimal_valid_skill():
    errors, warnings = validate_skill_metadata(
        {"name": "my-skill", "description": "A skill"},
        dir_name="my-skill",
    )
    assert errors == []
    assert warnings == []
