"""Translation from Claude Code MCP server config to Codex config.toml format."""

from __future__ import annotations

import re
from collections.abc import Iterable

from cc_codex_bridge.model import (
    DiscoveredMcpServer,
    GeneratedMcpServer,
    McpTranslationDiagnostic,
    McpTranslationResult,
)


# Matches Bearer token patterns:
#   Bearer ${VAR_NAME}  (with braces)
#   Bearer $VAR_NAME    (without braces)
_BEARER_TOKEN_RE = re.compile(
    r"^Bearer\s+\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$",
    re.IGNORECASE,
)


def translate_mcp_servers(
    servers: tuple[DiscoveredMcpServer, ...],
) -> McpTranslationResult:
    """Translate discovered Claude Code MCP servers to Codex config.toml format.

    Stdio servers map command/args/env directly.  HTTP servers map url directly,
    rename headers to http_headers, and extract Bearer token env var references.
    Unsupported features (headersHelper, oauth) produce warning diagnostics.
    """
    generated: list[GeneratedMcpServer] = []
    diagnostics: list[McpTranslationDiagnostic] = []

    for server in servers:
        # Validate server name — must be a valid TOML key compatible with
        # the registry's character-set requirements (alphanumeric + hyphens
        # + underscores).  Invalid names would reconcile once then crash on
        # the next load when the registry rejects its own stored entry.
        if not _is_valid_mcp_name(server.name):
            diagnostics.append(McpTranslationDiagnostic(
                server_name=server.name,
                message=(
                    "server name contains invalid characters "
                    "(only alphanumeric, hyphens, and underscores are allowed); skipped"
                ),
            ))
            continue

        server_diagnostics: list[McpTranslationDiagnostic] = []

        if server.transport == "stdio":
            toml_table = _translate_stdio(server, server_diagnostics)
        else:
            toml_table = _translate_http(server, server_diagnostics)

        generated.append(GeneratedMcpServer(
            name=server.name,
            scope=server.scope,
            toml_table=toml_table,
            source_description=f"{server.source} ({server.transport})",
        ))
        diagnostics.extend(server_diagnostics)

    return McpTranslationResult(
        servers=tuple(generated),
        diagnostics=tuple(diagnostics),
    )


def format_mcp_translation_diagnostics(
    diagnostics: Iterable[McpTranslationDiagnostic],
) -> str:
    """Render MCP translation diagnostics as stable human-readable lines."""
    return "\n".join(
        f"MCP server '{d.server_name}': {d.message}" for d in diagnostics
    )


# Key name fragments that suggest the value might be a credential.
_CREDENTIAL_KEY_FRAGMENTS = (
    "key", "token", "secret", "password", "pass", "credential", "auth",
    "bearer", "cookie", "api_key", "apikey",
)


def _looks_like_credential_key(key: str) -> bool:
    """Return True if *key* contains a fragment suggesting a credential."""
    lower = key.lower()
    return any(frag in lower for frag in _CREDENTIAL_KEY_FRAGMENTS)


def _is_valid_mcp_name(name: str) -> bool:
    """Return True if *name* is a valid MCP server identifier.

    Valid names contain only alphanumeric characters, hyphens, and underscores.
    This matches the character-set check in ``registry._require_mcp_server_key_name``.
    """
    return bool(name) and all(c.isalnum() or c in "-_" for c in name)


def _translate_stdio(
    server: DiscoveredMcpServer,
    diagnostics: list[McpTranslationDiagnostic],
) -> dict:
    """Build Codex TOML table for a stdio MCP server."""
    config = server.config
    table: dict = {}

    table["command"] = config["command"]

    args = config.get("args")
    if isinstance(args, list):
        table["args"] = list(args)

    env = config.get("env")
    if isinstance(env, dict) and env:
        table["env"] = dict(env)
        # Warn about env values that look like literal credentials
        for env_key, env_value in env.items():
            if (isinstance(env_value, str)
                    and not env_value.startswith("$")
                    and _looks_like_credential_key(env_key)):
                diagnostics.append(McpTranslationDiagnostic(
                    server_name=server.name,
                    message=(
                        f"env var '{env_key}' contains a literal value; "
                        "if this is a credential, consider using $ENV_VAR syntax instead. "
                        "The value was included in the generated config"
                    ),
                ))

    return table


def _translate_http(
    server: DiscoveredMcpServer,
    diagnostics: list[McpTranslationDiagnostic],
) -> dict:
    """Build Codex TOML table for an HTTP MCP server."""
    config = server.config
    table: dict = {}

    table["url"] = config["url"]

    # Warn about unsupported features
    if "headersHelper" in config:
        diagnostics.append(McpTranslationDiagnostic(
            server_name=server.name,
            message="headersHelper has no Codex equivalent; dynamic headers will not be available",
        ))

    if "oauth" in config:
        diagnostics.append(McpTranslationDiagnostic(
            server_name=server.name,
            message="OAuth config detected; user must run 'codex mcp login' to authenticate",
        ))

    # Process headers
    headers = config.get("headers")
    if isinstance(headers, dict):
        remaining_headers: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(value, str):
                continue
            if key.lower() == "authorization":
                match = _BEARER_TOKEN_RE.match(value)
                if match:
                    # Extract env var name from either group (braces or no braces)
                    var_name = match.group(1) or match.group(2)
                    table["bearer_token_env_var"] = var_name
                    continue
                # Literal bearer token — do NOT persist the secret into
                # generated config.  Warn the user to use an env var ref.
                if value.lstrip().lower().startswith("bearer "):
                    diagnostics.append(McpTranslationDiagnostic(
                        server_name=server.name,
                        message=(
                            "Authorization header contains a literal bearer token; "
                            "use $ENV_VAR or ${ENV_VAR} syntax instead. "
                            "The header was omitted from the generated config"
                        ),
                    ))
                    continue
                # Other auth schemes (Basic, etc.) — omit literal credential
                # (not an env var reference) from generated config.toml.
                if not value.strip().startswith("$"):
                    diagnostics.append(McpTranslationDiagnostic(
                        server_name=server.name,
                        message=(
                            "Authorization header contains a literal credential; "
                            "use $ENV_VAR or ${ENV_VAR} syntax instead. "
                            "The header was omitted from the generated config"
                        ),
                    ))
                    continue
            # Scan non-Authorization headers for literal credentials.
            # Values starting with $ are env var references and are safe.
            if (not value.strip().startswith("$")
                    and _looks_like_credential_key(key)):
                diagnostics.append(McpTranslationDiagnostic(
                    server_name=server.name,
                    message=(
                        f"header '{key}' contains a literal value; "
                        "if this is a credential, consider using $ENV_VAR syntax instead. "
                        "The header was included in the generated config"
                    ),
                ))
            remaining_headers[key] = value

        if remaining_headers:
            table["http_headers"] = remaining_headers

    return table
