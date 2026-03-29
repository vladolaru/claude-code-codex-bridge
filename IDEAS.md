# IDEAS.md

Rough ideas worth capturing and shaping. These are not commitments, not deferred decisions, and not on any roadmap. They're raw material — sparks that might be worth developing into proposals, or might simply be discarded after reflection.

**How this differs from FOLLOW_UPS.md:**
- `FOLLOW_UPS.md` — concrete deferred work. We know we want it; it's just not now. Clear enough to estimate.
- `IDEAS.md` — rough exploration. We don't know if we want it yet. Needs thinking, prototyping, or user signal before it can become a plan.

**Rule:** When an idea matures into something concrete and decided-but-deferred, move it to FOLLOW_UPS.md. When it's ready to build, move it to DESIGN.md (relevant section) and CHANGELOG.md.

---

## Format

Each idea should capture:
- **The spark** — what prompted this (observation, user pain, technical opportunity)
- **Rough shape** — what it might look like, even vaguely
- **Open questions** — what needs answering before this could be shaped into a plan

---

## Ideas

### Drift detection for MCP config.toml content

**Spark:** The bridge now verifies on-disk presence of MCP entries, but doesn't compare on-disk content against the expected hash. If someone hand-edits a bridge-owned entry (changes the command or args), reconcile won't notice unless the CC source also changes. Skills already have `_directory_matches_skill` for this.

**Rough shape:** After the "name exists in doc" check, compute `hash_mcp_server_table` on the on-disk TOML table and compare to the registry/state hash. Mismatch → plan an update. This would make MCP entries truly desired-state-enforced.

**Open questions:** Is this actually wanted? Some users might intentionally tweak bridge-managed entries (e.g., adding a flag to an MCP server command). Should there be an "unmanage" escape hatch? Or is the right answer "if you want to customize, exclude it from the bridge and manage it yourself"?

---

### MCP server health checks after bridging

**Spark:** The bridge translates configs but has no way to know if the resulting Codex MCP entry actually works. A `doctor` subcommand could try to start each bridged MCP server and report which ones fail.

**Rough shape:** `cc-codex-bridge doctor --mcp` attempts to spawn each stdio server (timeout 5s) or HTTP-ping each URL. Reports healthy/unhealthy/unreachable. Could also detect missing `npx` packages.

**Open questions:** Is this the bridge's job or Codex's job? Starting MCP servers has side effects (network calls, auth flows). Does this overlap with `codex mcp list` or similar Codex diagnostics?

---

### Bidirectional bridging (Codex → Claude Code)

**Spark:** The bridge is currently one-way: CC → Codex. But Codex users might configure MCP servers in `config.toml` that would also be useful in Claude Code. A reverse bridge could read Codex config and generate `.mcp.json` entries.

**Rough shape:** A `reverse` or `import` command that reads `~/.codex/config.toml` MCP entries not owned by the bridge and generates CC-compatible `.mcp.json` entries. Would need to reverse the translation (Codex TOML → CC JSON).

**Open questions:** Who is the audience? Users who start with Codex and want CC interop? Is this common enough to justify the complexity? The translation is lossy in both directions (different auth models, different env-var semantics).
