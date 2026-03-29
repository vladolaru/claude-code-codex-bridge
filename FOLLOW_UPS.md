# FOLLOW_UPS.md

Items deferred during development and review — intentional scope cuts, planned improvements, and discoveries made while building. Pull from here when planning next iterations rather than improvising scope.

**Rule:** Follow-ups belong here, not in DESIGN.md, code comments, or commit messages. When you defer something during development, add an entry here instead of annotating the design or code with "planned for v2" or "add this later."

---

## MCP Robustness

### Non-table `mcp_servers` key in Codex config.toml
**Area:** Reconcile, TOML editing
**Source:** Iterative review r3_f4
The TOML editing path assumes `doc["mcp_servers"]` is always a table. A hand-crafted scalar value (`mcp_servers = "oops"`) would pass TOML validation but crash `apply_mcp_changes()` with `AttributeError`, leaving the registry inconsistent. Fix by checking `isinstance(mcp_section, tomlkit.items.Table)` before iterating, and treating non-table values as corrupt config (skip with warning or raise cleanly).

### Non-string env values in stdio MCP translation
**Area:** Translation
**Source:** Iterative review r6_f2
`_translate_stdio()` copies `env` dicts verbatim once the top-level `isinstance(env, dict)` check passes. Non-string values like `{"A": 1}` or `{"B": {"nested": "x"}}` serialize as TOML numbers or subtables rather than string values, producing a broken Codex MCP entry. Fix by filtering env to string-valued entries only: `{k: v for k, v in env.items() if isinstance(v, str)}`.

---

## MCP Translation Fidelity

### `${VAR}` expansion semantics across runtimes
**Area:** Translation, Design
**Source:** Iterative review r7_f1
Claude Code expands `${VAR}` and `${CLAUDE_PROJECT_DIR}` in MCP configs at runtime. The bridge copies these references verbatim into Codex config. Codex may or may not expand them depending on the field — `bearer_token_env_var` is handled correctly, but `env` values, `http_headers`, and `args` are passed through as literal strings. A proper fix would detect `${VAR}` patterns in all fields and remap them to Codex's equivalent mechanism (`env_vars`, `env_http_headers`), but this requires understanding which Codex fields support env-var substitution.

### Relaxed MCP server name validation
**Area:** Discovery, Translation
**Source:** Iterative review r8_f1
Server names are restricted to `[A-Za-z0-9_-]`. TOML can represent any name via quoted keys (`[mcp_servers."my.server"]`), and Codex may handle them fine. The restriction exists for registry key safety and `mcp__<server>__<tool>` naming compatibility. Investigate whether Codex actually rejects dots/spaces in server names — if not, the validation could be relaxed to only reject characters that break TOML bare keys or Codex tool references.

---

## Reconcile Safety

### Symlink containment in `write_codex_config`
**Area:** TOML editing, Reconcile
**Source:** Iterative review r9_f2
`write_codex_config` has no built-in containment check. The callers in `reconcile.py` now add `_assert_path_contained` for project-scoped paths, but the function itself is unguarded. Adding an optional `container` parameter (matching `_atomic_write_file`'s pattern) would make the safety guarantee structural rather than caller-dependent.
