"""Tests for shared MCP env-template parsing and expansion."""

from __future__ import annotations

from cc_codex_bridge.mcp_env_templates import (
    collect_env_var_refs,
    expand_env_template,
)
from cc_codex_bridge.mcp_stdio_launcher import _build_child_env


class TestCollectEnvVarRefs:
    """Variable-reference discovery for MCP env templates."""

    def test_collects_braced_reference(self):
        assert collect_env_var_refs("${API_TOKEN}") == ("API_TOKEN",)

    def test_collects_bare_reference(self):
        assert collect_env_var_refs("$API_TOKEN") == ("API_TOKEN",)

    def test_collects_inline_refs_in_order_without_duplicates(self):
        assert collect_env_var_refs(
            "https://${HOST}/v1/${RESOURCE}?token=${HOST}"
        ) == ("HOST", "RESOURCE")

    def test_collects_ref_with_default(self):
        assert collect_env_var_refs("${TOKEN:-fallback}") == ("TOKEN",)


class TestExpandEnvTemplate:
    """Runtime expansion for stdio launcher env templates."""

    def test_expands_exact_alias(self):
        env = {"MY_SECRET": "top-secret"}
        assert expand_env_template("${MY_SECRET}", env) == "top-secret"

    def test_expands_inline_template(self):
        env = {"HOST": "api.example.com"}
        assert (
            expand_env_template("https://${HOST}:8080/mcp", env)
            == "https://api.example.com:8080/mcp"
        )

    def test_expands_bare_variable(self):
        env = {"TOKEN": "abc123"}
        assert expand_env_template("Bearer $TOKEN", env) == "Bearer abc123"

    def test_uses_default_for_missing_variable(self):
        assert expand_env_template("${TOKEN:-fallback}", {}) == "fallback"


class TestStdioLauncherEnvExpansion:
    """Child-env construction for stdio launcher templates."""

    def test_same_name_unset_reference_becomes_empty_string(self):
        child_env = _build_child_env(
            {"API_KEY": "${API_KEY}"},
            base_env={},
        )

        assert child_env["API_KEY"] == ""

    def test_alias_can_expand_from_forwarded_source_variable(self):
        child_env = _build_child_env(
            {"API_KEY": "${MY_SECRET}"},
            base_env={"MY_SECRET": "top-secret"},
        )

        assert child_env["API_KEY"] == "top-secret"
