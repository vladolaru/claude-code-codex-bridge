# MCP Bridge Mapping: Claude Code → Codex

This document analyzes how Claude Code MCP configurations map to Codex MCP configurations, what can be bridged automatically, and what gaps exist. It informs bridge design decisions for MCP support.

---

## 1. Configuration Format Mapping

### 1.1 stdio Servers

| CC Field (JSON) | Codex Field (TOML) | Notes |
|-----------------|-------------------|-------|
| `command` | `command` | Direct 1:1 |
| `args` | `args` | Direct 1:1 |
| `env` | `env` | Direct 1:1 (both are string→string maps) |
| — | `env_vars` | **Codex-only**: forward parent env vars by name. CC doesn't have this — it passes `env` values directly. |
| — | `cwd` | **Codex-only**: working directory for server process. CC doesn't expose this. |

**Bridgeable**: Yes, fully. CC's stdio config maps directly.

### 1.2 HTTP Servers

| CC Field (JSON) | Codex Field (TOML) | Notes |
|-----------------|-------------------|-------|
| `url` | `url` | Direct 1:1 |
| `type: "http"` | (inferred from `url`) | Codex infers transport from field presence |
| `headers` | `http_headers` | Same semantics, different key name |
| `headersHelper` | — | **CC-only**: shell command for dynamic headers. No Codex equivalent. |
| — | `bearer_token_env_var` | **Codex-only**: dedicated bearer token field. CC uses `headers.Authorization` directly. |
| — | `env_http_headers` | **Codex-only**: header values from env vars by name. |
| `oauth.clientId` | `scopes` + OAuth login flow | Different OAuth models (see section 3) |
| `oauth.callbackPort` | `mcp_oauth_callback_port` (global) | CC: per-server. Codex: global setting. |

**Bridgeable**: Partially. URL and static headers map. OAuth and dynamic headers need special handling.

### 1.3 SSE Servers

| CC | Codex | Notes |
|----|-------|-------|
| `type: "sse"` supported | Not supported | Codex only supports stdio and streamable HTTP |

**Bridgeable**: No. SSE servers would need to be flagged as incompatible. In practice, SSE is deprecated in CC too — most servers support HTTP.

---

## 2. Shared Fields Mapping

| CC | Codex | Notes |
|----|-------|-------|
| — | `enabled` | **Codex-only**: CC doesn't have a disable toggle (you remove the server). |
| — | `required` | **Codex-only**: `codex exec` fails if required server can't init. |
| — | `startup_timeout_sec` | **Codex-only**: CC uses `MCP_TIMEOUT` env var instead. |
| — | `tool_timeout_sec` | **Codex-only**: CC has `MAX_MCP_OUTPUT_TOKENS` but no per-server timeout. |
| — | `enabled_tools` / `disabled_tools` | **Codex-only**: CC uses permission rules in `settings.json` instead. |
| — | Per-tool `approval_mode` | **Codex-only**: CC uses the standard permission system. |

---

## 3. OAuth Mapping

CC and Codex handle OAuth differently:

| Aspect | Claude Code | Codex |
|--------|-------------|-------|
| Config location | Per-server `oauth` object | Per-server `scopes` + global OAuth settings |
| Client ID | `oauth.clientId` per server | Dynamic client registration or pre-configured |
| Callback port | `oauth.callbackPort` per server | `mcp_oauth_callback_port` global |
| Metadata URL | `oauth.authServerMetadataUrl` | RFC 9728 auto-discovery |
| Credential storage | System keychain | `mcp_oauth_credentials_store` (`auto`/`file`/`keyring`) |
| Login flow | Automatic on connection | Explicit `codex mcp login <name>` |

**Bridgeable**: Partially. The bridge can translate `scopes` and flag that OAuth login is needed, but the authentication flows are fundamentally different. Users will need to run `codex mcp login` separately.

---

## 4. Tool Namespacing

Both systems use the **same convention**: `mcp__<server>__<tool>` with double underscores.

| Aspect | Claude Code | Codex |
|--------|-------------|-------|
| Format | `mcp__<server>__<tool>` | `mcp__<server>__<tool>` |
| Plugin prefix | `mcp__plugin_<plugin>_<server>__<tool>` | Same server name, no special plugin prefix |
| Max length | 64 chars | 64 chars |
| Sanitization | Spaces → underscores | `[^a-zA-Z0-9_-]` → `_` + SHA1 suffix if truncated |

**Bridgeable**: Yes. If the bridge preserves server names, tool references in skill prompts transfer directly. Plugin-prefixed tool names would need the `plugin_<name>_` prefix stripped.

---

## 5. Scope Mapping

| CC Scope | Codex Scope | Bridge Action |
|----------|-------------|---------------|
| Local (`~/.claude.json` per-project) | Project (`.codex/config.toml`) | Write to `.codex/config.toml` |
| Project (`.mcp.json`) | Project (`.codex/config.toml`) | Write to `.codex/config.toml` |
| User (`~/.claude.json` global) | User (`~/.codex/config.toml`) | Write to `~/.codex/config.toml` |
| Managed (`managed-mcp.json`) | System (`/etc/codex/config.toml`) | Out of scope — admin-managed |
| Plugin-bundled | User or project config | Extract and translate |

---

## 6. Capability Gaps

### 6.1 CC Features Without Codex Equivalent

| CC Feature | Impact | Workaround |
|------------|--------|------------|
| `headersHelper` (dynamic headers via shell) | Medium — enterprise/internal APIs | None. Users must use `env_http_headers` or `bearer_token_env_var` in Codex. |
| SSE transport | Low — deprecated in CC too | Use HTTP transport instead |
| MCP Prompts (`/mcp__server__prompt`) | Medium — CC exposes prompts as slash commands | Codex doesn't support MCP prompts at all |
| `list_changed` dynamic updates | Low — runtime behavior | Codex supports server refresh but differently |
| Tool Search (deferred loading) | Low — optimization, not functionality | Codex loads all tool schemas upfront |
| Channels (`claude/channel` push) | Low — newer CC feature | Not available in Codex |
| Per-server `MCP_TIMEOUT` | Low | Codex has per-server `startup_timeout_sec` (more granular) |

### 6.2 Codex Features Without CC Equivalent

| Codex Feature | Impact | Notes |
|---------------|--------|-------|
| `env_vars` (forward parent env vars by name) | Low | CC passes env values directly via `env` |
| `cwd` (working directory) | Low | CC servers inherit CWD from CC process |
| `enabled` toggle | Low | Useful for temporarily disabling |
| `required` flag | Low | `codex exec` CI mode |
| `enabled_tools` / `disabled_tools` | Medium | Codex-native tool filtering per-server |
| Per-tool `approval_mode` | Low | Fine-grained approval |
| `env_http_headers` | Low | Headers from env vars by name |
| Skill MCP dependency auto-install | Medium | `agents/openai.yaml` can trigger install |

---

## 7. Bridge Strategy

### 7.1 What to Bridge (Phase 1 — Core)

1. **stdio servers**: Direct field mapping (`command`, `args`, `env`). Write to appropriate Codex `config.toml` scope.
2. **HTTP servers**: Map `url`, translate `headers` → `http_headers`. Flag `headersHelper` as unsupported.
3. **Server names**: Preserve exactly — this ensures tool name references (`mcp__server__tool`) work across both systems.
4. **Scope mapping**: Local/project CC → `.codex/config.toml`; user CC → `~/.codex/config.toml`.

### 7.2 What to Bridge (Phase 2 — Enhanced)

5. **OAuth servers**: Translate `scopes`, emit `codex mcp login` instructions for user.
6. **Skill MCP dependencies**: When translating skills that reference MCP tools, generate `agents/openai.yaml` with `dependencies.tools` entries.
7. **Permission → tool filtering**: Translate CC permission rules (`allow`/`deny` with `mcp__` patterns) to Codex `enabled_tools`/`disabled_tools` per-server.

### 7.3 What NOT to Bridge

- **Managed MCP** — admin-deployed, out of bridge scope
- **`headersHelper`** — no Codex equivalent, flag as incompatible
- **SSE transport** — deprecated, suggest HTTP migration
- **MCP Prompts** — Codex doesn't support them
- **Plugin-bundled MCP servers** — need to be extracted and translated as standalone configs

### 7.4 Ownership and Reconciliation

MCP server configs in Codex's `config.toml` are shared with other Codex features. The bridge must:

- Track which `[mcp_servers.*]` entries it owns (via the existing bridge state/registry)
- Never modify user-authored MCP entries
- Handle merge conflicts when both CC and Codex define the same server name
- Clean up bridge-owned entries on uninstall

---

## 8. Translation Examples

### 8.1 stdio: CC → Codex

**CC** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"],
      "env": { "API_KEY": "sk-..." }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[mcp_servers.context7.env]
API_KEY = "sk-..."
```

### 8.2 HTTP: CC → Codex

**CC** (`.mcp.json`):
```json
{
  "mcpServers": {
    "github": {
      "type": "http",
      "url": "https://mcp.github.com/sse",
      "headers": {
        "Authorization": "Bearer ${GITHUB_TOKEN}"
      }
    }
  }
}
```

**Codex** (`.codex/config.toml`):
```toml
[mcp_servers.github]
url = "https://mcp.github.com/sse"

# Note: CC's ${GITHUB_TOKEN} expansion in headers becomes
# Codex's dedicated bearer_token_env_var field
bearer_token_env_var = "GITHUB_TOKEN"

# Alternatively, if the header isn't a simple Bearer pattern:
# [mcp_servers.github.env_http_headers]
# "Authorization" = "GITHUB_AUTH_HEADER"
```

### 8.3 Plugin MCP: CC → Codex

**CC** (plugin `.mcp.json` with tool name `mcp__plugin_context-a8c_context-a8c__context-a8c-execute-tool`):

**Codex** (`~/.codex/config.toml`):
```toml
# Plugin prefix stripped — server name is the plugin's MCP server name
[mcp_servers.context-a8c]
command = "..."
args = [...]
```

Tool name becomes `mcp__context-a8c__context-a8c-execute-tool` (plugin prefix dropped).

---

## Sources

- [docs/codex-cli-reference.md](codex-cli-reference.md) — Codex MCP section 7
- [docs/claude-code-mcp-reference.md](claude-code-mcp-reference.md) — CC MCP reference
- [.claude/docs/research/2026-03-28-codex-mcp-implementation.md](../.claude/docs/research/2026-03-28-codex-mcp-implementation.md) — Detailed Codex research with Rust types
- [.claude/docs/analysis/2026-03-28-claude-code-mcp-configuration.md](../.claude/docs/analysis/2026-03-28-claude-code-mcp-configuration.md) — Detailed CC research
