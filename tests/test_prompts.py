"""Tests for command-to-prompt translation."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.model import GeneratedPrompt, PromptTranslationResult, SkillValidationDiagnostic


def test_generated_prompt_dataclass():
    """GeneratedPrompt has the expected fields."""
    prompt = GeneratedPrompt(
        filename="review.md",
        content=b"---\ndescription: Review code\n---\n\nReview the code.\n",
        source_path=Path("/tmp/commands/review.md"),
        marketplace="market",
        plugin_name="tools",
    )
    assert prompt.filename == "review.md"
    assert prompt.marketplace == "market"
    assert isinstance(prompt.content, bytes)
    assert prompt.source_path == Path("/tmp/commands/review.md")


def test_prompt_translation_result_dataclass():
    """PromptTranslationResult holds prompts and diagnostics."""
    result = PromptTranslationResult(
        prompts=(),
        diagnostics=(),
        plugin_resources=(),
    )
    assert result.prompts == ()
    assert result.diagnostics == ()
    assert result.plugin_resources == ()
