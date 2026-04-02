"""Tests for MCP server translation from Claude Code to Codex config."""

from __future__ import annotations

import pytest

from cc_codex_bridge.model import (
    DiscoveredMcpServer,
    GeneratedMcpServer,
    McpTranslationDiagnostic,
    McpTranslationResult,
)
from cc_codex_bridge.translate_mcp import (
    format_mcp_translation_diagnostics,
    translate_mcp_servers,
)


# -- stdio servers -----------------------------------------------------------

class TestStdioTranslation:
    """Translation of stdio MCP servers."""

    def test_command_args_env_map_directly(self):
        """stdio: command, args, and env map directly to output."""
        server = DiscoveredMcpServer(
            name="my-stdio",
            scope="global",
            transport="stdio",
            source="user-global",
            config={
                "command": "node",
                "args": ["server.js", "--port", "3000"],
                "env": {"NODE_ENV": "production"},
            },
        )
        result = translate_mcp_servers((server,))

        assert len(result.servers) == 1
        gen = result.servers[0]
        assert gen.name == "my-stdio"
        assert gen.scope == "global"
        assert gen.toml_table["command"] == "node"
        assert gen.toml_table["args"] == ["server.js", "--port", "3000"]
        assert gen.toml_table["env"] == {"NODE_ENV": "production"}
        assert "type" not in gen.toml_table
        assert len(result.diagnostics) == 0

    def test_empty_env_omitted(self):
        """stdio: empty env dict is omitted from output."""
        server = DiscoveredMcpServer(
            name="srv",
            scope="project",
            transport="stdio",
            source="project-local",
            config={
                "command": "python",
                "args": ["-m", "myserver"],
                "env": {},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert "env" not in gen.toml_table

    def test_missing_args_omitted(self):
        """stdio: missing args is omitted from output."""
        server = DiscoveredMcpServer(
            name="srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={"command": "my-server"},
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["command"] == "my-server"
        assert "args" not in gen.toml_table

    def test_type_field_stripped(self):
        """stdio: type field in config is stripped from output."""
        server = DiscoveredMcpServer(
            name="srv",
            scope="project",
            transport="stdio",
            source="project-shared",
            config={
                "type": "stdio",
                "command": "npx",
                "args": ["@my/server"],
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert "type" not in gen.toml_table
        assert gen.toml_table["command"] == "npx"
        assert gen.toml_table["args"] == ["@my/server"]


    def test_non_list_args_ignored(self):
        """stdio: non-list args (e.g. string) are silently ignored."""
        server = DiscoveredMcpServer(
            name="bad-args-srv",
            scope="project",
            transport="stdio",
            source="project-local",
            config={
                "command": "my-cmd",
                "args": "--flag",
            },
        )
        result = translate_mcp_servers((server,))

        assert len(result.servers) == 1
        gen = result.servers[0]
        assert gen.toml_table["command"] == "my-cmd"
        assert "args" not in gen.toml_table

    def test_non_dict_env_ignored(self):
        """stdio: non-dict env (e.g. list) is silently ignored."""
        server = DiscoveredMcpServer(
            name="bad-env-srv",
            scope="project",
            transport="stdio",
            source="project-local",
            config={
                "command": "my-cmd",
                "env": ["A=1"],
            },
        )
        result = translate_mcp_servers((server,))

        assert len(result.servers) == 1
        gen = result.servers[0]
        assert gen.toml_table["command"] == "my-cmd"
        assert "env" not in gen.toml_table

    def test_whole_value_env_var_ref_becomes_env_vars(self):
        """stdio: env value that is exactly ${VAR} moves to env_vars."""
        server = DiscoveredMcpServer(
            name="gh-srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={
                "command": "npx",
                "args": ["-y", "@mcp/server-github"],
                "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert "env" not in gen.toml_table
        assert gen.toml_table["env_vars"] == ["GITHUB_TOKEN"]
        assert not result.diagnostics

    def test_dollar_no_braces_env_var_ref_becomes_env_vars(self):
        """stdio: env value that is exactly $VAR moves to env_vars."""
        server = DiscoveredMcpServer(
            name="srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={
                "command": "node",
                "env": {"MY_TOKEN": "$MY_TOKEN"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert "env" not in gen.toml_table
        assert gen.toml_table["env_vars"] == ["MY_TOKEN"]

    def test_mixed_env_var_ref_kept_with_diagnostic(self):
        """stdio: env value with inline ${VAR} stays in env with diagnostic."""
        server = DiscoveredMcpServer(
            name="mix-srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={
                "command": "node",
                "env": {"URL": "https://${HOST}:8080/api"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["env"] == {"URL": "https://${HOST}:8080/api"}
        assert "env_vars" not in gen.toml_table
        assert len(result.diagnostics) == 1
        assert "${" in result.diagnostics[0].message
        assert "URL" in result.diagnostics[0].message

    def test_env_mixed_static_and_var_refs(self):
        """stdio: env with both static values and ${VAR} refs splits correctly."""
        server = DiscoveredMcpServer(
            name="mixed-srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={
                "command": "node",
                "env": {
                    "NODE_ENV": "production",
                    "API_KEY": "${API_KEY}",
                    "DB_URL": "${DB_URL}",
                },
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["env"] == {"NODE_ENV": "production"}
        assert sorted(gen.toml_table["env_vars"]) == ["API_KEY", "DB_URL"]
        assert not result.diagnostics

    def test_non_string_env_values_filtered(self):
        """stdio: non-string env values are silently filtered out."""
        server = DiscoveredMcpServer(
            name="bad-types-srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={
                "command": "node",
                "env": {
                    "GOOD": "value",
                    "NUMBER": 42,
                    "NESTED": {"a": "b"},
                    "BOOL": True,
                },
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["env"] == {"GOOD": "value"}


# -- HTTP servers ------------------------------------------------------------

class TestHttpTranslation:
    """Translation of HTTP MCP servers."""

    def test_url_maps_directly(self):
        """HTTP: url maps directly to output."""
        server = DiscoveredMcpServer(
            name="http-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={"url": "https://example.com/mcp"},
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["url"] == "https://example.com/mcp"
        assert "type" not in gen.toml_table

    def test_headers_become_http_headers(self):
        """HTTP: headers is renamed to http_headers."""
        server = DiscoveredMcpServer(
            name="http-srv",
            scope="project",
            transport="http",
            source="project-local",
            config={
                "url": "https://example.com/mcp",
                "headers": {"X-Custom": "value", "Accept": "application/json"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["http_headers"] == {
            "X-Custom": "value",
            "Accept": "application/json",
        }
        assert "headers" not in gen.toml_table

    def test_bearer_token_with_braces_extracted(self):
        """HTTP: Authorization 'Bearer ${TOKEN}' extracted to bearer_token_env_var."""
        server = DiscoveredMcpServer(
            name="auth-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://api.example.com/mcp",
                "headers": {
                    "Authorization": "Bearer ${MY_API_TOKEN}",
                    "X-Extra": "keep-me",
                },
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["bearer_token_env_var"] == "MY_API_TOKEN"
        assert gen.toml_table["http_headers"] == {"X-Extra": "keep-me"}
        assert "Authorization" not in gen.toml_table.get("http_headers", {})

    def test_bearer_token_without_braces_extracted(self):
        """HTTP: Authorization 'Bearer $TOKEN' (no braces) also extracted."""
        server = DiscoveredMcpServer(
            name="auth-srv",
            scope="project",
            transport="http",
            source="project-local",
            config={
                "url": "https://api.example.com/mcp",
                "headers": {"Authorization": "Bearer $API_KEY"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["bearer_token_env_var"] == "API_KEY"
        # No other headers remain, so http_headers should be omitted
        assert "http_headers" not in gen.toml_table

    def test_literal_bearer_token_omitted_with_warning(self):
        """HTTP: literal bearer token is omitted from config and emits warning."""
        server = DiscoveredMcpServer(
            name="literal-bearer-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer sk-real-secret-key"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert "bearer_token_env_var" not in gen.toml_table
        assert "http_headers" not in gen.toml_table
        assert len(result.diagnostics) == 1
        assert "literal bearer token" in result.diagnostics[0].message
        assert "omitted" in result.diagnostics[0].message

    def test_non_bearer_authorization_omitted_with_warning(self):
        """HTTP: non-Bearer literal Authorization header omitted and emits warning."""
        server = DiscoveredMcpServer(
            name="basic-auth-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Basic dXNlcjpwYXNz"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert "http_headers" not in gen.toml_table
        assert "bearer_token_env_var" not in gen.toml_table
        # Literal credential triggers a warning diagnostic
        assert len(result.diagnostics) == 1
        assert "literal credential" in result.diagnostics[0].message
        assert "omitted" in result.diagnostics[0].message

    def test_non_bearer_env_var_authorization_no_warning(self):
        """HTTP: non-Bearer Authorization header using env var emits no warning."""
        server = DiscoveredMcpServer(
            name="env-auth-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "$MY_AUTH_TOKEN"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["http_headers"] == {"Authorization": "$MY_AUTH_TOKEN"}
        assert not result.diagnostics

    def test_empty_headers_after_extraction_omitted(self):
        """HTTP: empty headers after Bearer extraction -> http_headers omitted."""
        server = DiscoveredMcpServer(
            name="auth-only-srv",
            scope="project",
            transport="http",
            source="project-shared",
            config={
                "url": "https://api.example.com/mcp",
                "headers": {"Authorization": "Bearer ${SECRET_TOKEN}"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["bearer_token_env_var"] == "SECRET_TOKEN"
        assert "http_headers" not in gen.toml_table

    def test_bearer_token_case_insensitive_header(self):
        """HTTP: lowercase 'authorization' header is also recognized."""
        server = DiscoveredMcpServer(
            name="lowercase-auth-srv",
            scope="project",
            transport="http",
            source="project-local",
            config={
                "url": "https://api.example.com/mcp",
                "headers": {"authorization": "Bearer ${MY_TOKEN}"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["bearer_token_env_var"] == "MY_TOKEN"
        assert "http_headers" not in gen.toml_table

    def test_type_field_stripped(self):
        """HTTP: type field in config is stripped from output."""
        server = DiscoveredMcpServer(
            name="http-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "type": "streamable-http",
                "url": "https://example.com/mcp",
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert "type" not in gen.toml_table
        assert gen.toml_table["url"] == "https://example.com/mcp"

    def test_non_dict_headers_ignored(self):
        """HTTP: non-dict headers (e.g. list) are silently ignored."""
        server = DiscoveredMcpServer(
            name="bad-headers-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headers": [],
            },
        )
        result = translate_mcp_servers((server,))

        assert len(result.servers) == 1
        gen = result.servers[0]
        assert gen.toml_table["url"] == "https://example.com/mcp"
        assert "http_headers" not in gen.toml_table
        assert "bearer_token_env_var" not in gen.toml_table
        assert len(result.diagnostics) == 0

    def test_non_string_header_values_skipped(self):
        """HTTP: non-string header values are silently skipped."""
        server = DiscoveredMcpServer(
            name="bad-header-val",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headers": {
                    "Authorization": 12345,
                    "X-Custom": "valid-value",
                },
            },
        )
        result = translate_mcp_servers((server,))

        assert len(result.servers) == 1
        gen = result.servers[0]
        # Non-string Authorization is skipped, valid header is kept
        assert "bearer_token_env_var" not in gen.toml_table
        assert gen.toml_table.get("http_headers", {}).get("X-Custom") == "valid-value"


# -- Diagnostics (warnings) -------------------------------------------------

class TestDiagnostics:
    """Diagnostic warnings during translation."""

    def test_headers_helper_warning(self):
        """headersHelper present produces a warning diagnostic."""
        server = DiscoveredMcpServer(
            name="helper-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headersHelper": "some-helper-command",
            },
        )
        result = translate_mcp_servers((server,))

        assert len(result.servers) == 1
        assert len(result.diagnostics) == 1
        diag = result.diagnostics[0]
        assert diag.server_name == "helper-srv"
        assert "headersHelper" in diag.message
        assert "no Codex equivalent" in diag.message

    def test_non_authorization_literal_header_warning(self):
        """HTTP: literal value in non-Authorization header emits warning but is preserved."""
        server = DiscoveredMcpServer(
            name="api-key-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headers": {"X-API-Key": "sk-real-key-123"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        # Header is preserved (not omitted — only Authorization is omitted)
        assert gen.toml_table["http_headers"] == {"X-API-Key": "sk-real-key-123"}
        assert len(result.diagnostics) == 1
        assert "X-API-Key" in result.diagnostics[0].message
        assert "literal value" in result.diagnostics[0].message

    def test_non_credential_literal_header_no_warning(self):
        """HTTP: literal value in non-credential header (Content-Type) emits no warning."""
        server = DiscoveredMcpServer(
            name="content-type-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headers": {"Content-Type": "application/json"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["http_headers"] == {"Content-Type": "application/json"}
        assert not result.diagnostics

    def test_non_authorization_env_var_header_no_warning(self):
        """HTTP: env var reference in non-Authorization header emits no warning."""
        server = DiscoveredMcpServer(
            name="api-key-env-srv",
            scope="global",
            transport="http",
            source="user-global",
            config={
                "url": "https://example.com/mcp",
                "headers": {"X-API-Key": "$MY_API_KEY"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["http_headers"] == {"X-API-Key": "$MY_API_KEY"}
        assert not result.diagnostics

    def test_stdio_literal_env_value_warning(self):
        """Stdio: literal env value emits warning but is preserved."""
        server = DiscoveredMcpServer(
            name="env-literal-srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={
                "command": "node",
                "args": ["server.js"],
                "env": {"API_KEY": "sk-secret-123"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.toml_table["env"] == {"API_KEY": "sk-secret-123"}
        assert len(result.diagnostics) == 1
        assert "API_KEY" in result.diagnostics[0].message
        assert "literal value" in result.diagnostics[0].message

    def test_stdio_env_var_ref_no_warning(self):
        """Stdio: env var reference does not emit warning."""
        server = DiscoveredMcpServer(
            name="env-ref-srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={
                "command": "node",
                "args": ["server.js"],
                "env": {"API_KEY": "$MY_SECRET"},
            },
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert "env" not in gen.toml_table
        assert gen.toml_table["env_vars"] == ["MY_SECRET"]
        assert not result.diagnostics

    def test_oauth_config_warning(self):
        """OAuth config detected produces a warning diagnostic."""
        server = DiscoveredMcpServer(
            name="oauth-srv",
            scope="project",
            transport="http",
            source="project-local",
            config={
                "url": "https://example.com/mcp",
                "oauth": {"client_id": "abc123"},
            },
        )
        result = translate_mcp_servers((server,))

        assert len(result.servers) == 1
        assert len(result.diagnostics) == 1
        diag = result.diagnostics[0]
        assert diag.server_name == "oauth-srv"
        assert "OAuth" in diag.message or "oauth" in diag.message
        assert "codex mcp login" in diag.message


# -- Multiple servers and metadata preservation ------------------------------

class TestMultipleServers:
    """Translation of multiple servers in a single call."""

    def test_multiple_servers(self):
        """Multiple servers translate independently in one call."""
        servers = (
            DiscoveredMcpServer(
                name="stdio-one",
                scope="global",
                transport="stdio",
                source="user-global",
                config={"command": "node", "args": ["a.js"]},
            ),
            DiscoveredMcpServer(
                name="http-two",
                scope="project",
                transport="http",
                source="project-local",
                config={"url": "https://two.example.com/mcp"},
            ),
            DiscoveredMcpServer(
                name="stdio-three",
                scope="project",
                transport="stdio",
                source="project-shared",
                config={"command": "python", "args": ["-m", "srv"], "env": {"KEY": "val"}},
            ),
        )
        result = translate_mcp_servers(servers)

        assert len(result.servers) == 3
        names = [s.name for s in result.servers]
        assert "stdio-one" in names
        assert "http-two" in names
        assert "stdio-three" in names


class TestScopeAndSourcePreserved:
    """Scope and source_description are preserved from input."""

    def test_scope_preserved(self):
        """Scope from DiscoveredMcpServer is preserved in GeneratedMcpServer."""
        server = DiscoveredMcpServer(
            name="test-srv",
            scope="project",
            transport="stdio",
            source="project-local",
            config={"command": "test"},
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.scope == "project"

    def test_source_description_derived(self):
        """source_description is derived from the input source field."""
        server = DiscoveredMcpServer(
            name="test-srv",
            scope="global",
            transport="stdio",
            source="user-global",
            config={"command": "test"},
        )
        result = translate_mcp_servers((server,))

        gen = result.servers[0]
        assert gen.source_description  # non-empty
        assert "user-global" in gen.source_description


# -- Formatter ---------------------------------------------------------------

class TestFormatMcpTranslationDiagnostics:
    """Tests for format_mcp_translation_diagnostics."""

    def test_format_mcp_translation_diagnostics(self):
        diags = [
            McpTranslationDiagnostic(
                server_name="auth-server",
                message="OAuth config detected; user must run 'codex mcp login' to authenticate",
            ),
            McpTranslationDiagnostic(
                server_name="api-gw",
                message="headersHelper has no Codex equivalent; dynamic headers will not be available",
            ),
        ]
        result = format_mcp_translation_diagnostics(diags)
        assert "MCP server 'auth-server'" in result
        assert "MCP server 'api-gw'" in result
        assert "OAuth config detected" in result
        assert "headersHelper" in result

    def test_empty_diagnostics(self):
        result = format_mcp_translation_diagnostics([])
        assert result == ""

    def test_single_diagnostic(self):
        diags = [
            McpTranslationDiagnostic(
                server_name="test-srv",
                message="some warning",
            ),
        ]
        result = format_mcp_translation_diagnostics(diags)
        assert result == "MCP server 'test-srv': some warning"


class TestEnvVarPatterns:
    """Tests for env var reference detection helpers."""

    def test_whole_value_braces(self):
        """${VAR_NAME} as entire value is detected as whole-value reference."""
        from cc_codex_bridge.translate_mcp import _extract_env_var_ref

        assert _extract_env_var_ref("${GITHUB_TOKEN}") == "GITHUB_TOKEN"

    def test_whole_value_dollar_no_braces(self):
        """$VAR_NAME as entire value is detected as whole-value reference."""
        from cc_codex_bridge.translate_mcp import _extract_env_var_ref

        assert _extract_env_var_ref("$MY_SECRET") == "MY_SECRET"

    def test_mixed_value_returns_none(self):
        """Literal mixed with ${VAR} is not a whole-value reference."""
        from cc_codex_bridge.translate_mcp import _extract_env_var_ref

        assert _extract_env_var_ref("prefix-${TOKEN}") is None

    def test_plain_literal_returns_none(self):
        """Plain literal string is not an env var reference."""
        from cc_codex_bridge.translate_mcp import _extract_env_var_ref

        assert _extract_env_var_ref("sk-abc123") is None

    def test_empty_returns_none(self):
        """Empty string is not an env var reference."""
        from cc_codex_bridge.translate_mcp import _extract_env_var_ref

        assert _extract_env_var_ref("") is None

    def test_contains_env_var_ref_detects_inline(self):
        """String containing ${VAR} among other text is detected."""
        from cc_codex_bridge.translate_mcp import _contains_env_var_ref

        assert _contains_env_var_ref("prefix-${TOKEN}-suffix") is True

    def test_contains_env_var_ref_plain_literal(self):
        """Plain literal string returns False."""
        from cc_codex_bridge.translate_mcp import _contains_env_var_ref

        assert _contains_env_var_ref("just-a-string") is False

    def test_contains_env_var_ref_whole_value(self):
        """Whole-value ${VAR} also returns True for contains check."""
        from cc_codex_bridge.translate_mcp import _contains_env_var_ref

        assert _contains_env_var_ref("${TOKEN}") is True
