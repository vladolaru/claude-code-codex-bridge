# Global Codex Skill Ownership Tracking Plan

Date: 2026-03-08
Status: draft

## Problem

Generated Codex skills are written into a user-global location under `~/.codex/skills/`, but ownership is currently tracked only in each project's local `.codex/claude-code-interop-state.json`.

That creates an unsafe multi-project case:

1. project A reconciles and creates `~/.codex/skills/<skill>/`
2. project B reconciles and reuses the same generated skill directory
3. project A later stops desiring that skill
4. project A removes the directory as stale, even though project B still relies on it

Project-local outputs do not have this problem because they are already scoped to one project root. The gap is specifically the user-global skill surface.

## Goal

Make generated Codex skill ownership safe across multiple projects that reconcile into the same `codex_home`.

Safety target:

- a shared generated skill directory is removed only when no project still claims it
- identical desired skill content can be shared safely across projects
- conflicting desired content for the same install directory fails clearly instead of being silently overwritten

## Non-Goals

- no change to project-local ownership semantics for `CLAUDE.md` or `.codex/*`
- no filesystem-wide project discovery
- no background daemon or watcher requirement
- no attempt to infer ownership from arbitrary pre-existing `~/.codex/skills/*` directories

## Proposed Model

Introduce a user-global ownership registry under the resolved Codex home.

Suggested path:

- `<codex_home>/claude-code-interop-global-state.json`

Suggested lock path:

- `<codex_home>/claude-code-interop-global-state.lock`

Keep the existing per-project state file. The new global registry complements it rather than replacing it.

### Per-project state remains responsible for

- project-local generated files
- the current project's selected plugin set
- the current project's claimed generated skill directory names

### Global registry becomes responsible for

- which project roots currently claim each generated skill directory
- the expected content fingerprint for each claimed generated skill
- preventing one project from deleting a shared skill still claimed elsewhere

## Registry Shape

Minimal schema:

```json
{
  "version": 1,
  "skills": {
    "prompt-engineer-prompt-engineer": {
      "content_hash": "sha256:...",
      "owners": [
        "/abs/project-a",
        "/abs/project-b"
      ]
    }
  }
}
```

Possible future extension:

- owner records can later expand from a plain project-root string to an object with `state_path`, `last_seen_at`, or `selected_plugins`

Start simple unless cleanup requirements force richer metadata immediately.

## Reconcile Flow Changes

1. Resolve the desired project state exactly as today.
2. Acquire an exclusive lock for the global registry.
3. Load the current project's local state.
4. Load the global registry.
5. Compute this project's desired skill claims:
   - skill install dir name
   - deterministic content hash from the generated skill tree
6. For each desired skill:
   - if no registry entry exists, create one with this project as the first owner
   - if an entry exists with the same content hash, add this project to the owner set
   - if an entry exists with a different content hash, fail with a clear ownership conflict error
7. For each skill previously claimed by this project but no longer desired:
   - remove this project from the owner set
   - remove the skill directory only if the owner set becomes empty
8. Reconcile the actual skill directory tree to match the registry-backed desired union.
9. Persist both:
   - project-local state
   - global ownership registry
10. Release the lock.

## Content Fingerprint

Use a deterministic digest over the generated skill tree:

- relative path
- file bytes
- executable mode

This must match the same equality rules already used by `_directory_matches_skill()`.

The hash is what makes safe sharing possible:

- same install dir + same hash => shared ownership allowed
- same install dir + different hash => hard conflict

## Migration Strategy

Phase 1 should migrate conservatively:

1. if the global registry does not exist, create it from the current project's desired skills only
2. do not try to backfill claims for unknown other projects
3. when a skill directory already exists and matches this project's desired content, register the current project as an owner without rewriting the directory
4. when a skill directory already exists but conflicts with the desired content and the registry has no compatible claim, fail rather than guessing

This avoids destructive inference during rollout.

## Handling Deleted or Moved Projects

A global owner set introduces stale-claim cleanup needs.

Recommended first step:

- keep cleanup explicit, not automatic

Add a future maintenance command such as:

- `cc-codex-bridge prune-global-state`

Behavior:

- remove owner entries whose local project state file no longer exists
- optionally remove owners whose state file no longer claims the skill
- garbage-collect unowned skill directories

Do not auto-prune missing projects during normal reconcile in v1. That is too risky.

## Failure Semantics

Hard errors:

- conflicting content hashes for the same install dir
- malformed global registry
- inability to acquire the registry lock
- registry/project state disagreement that cannot be resolved deterministically

Safe no-op cases:

- a project reconciles and reaffirms existing claims with unchanged content
- multiple projects share the same generated skill content

## Test Plan

Add tests for:

1. two projects claiming the same skill with identical content
2. one project dropping a shared claim while another still owns it
3. removal only after the last owner is gone
4. conflicting content for the same skill dir across projects
5. migration from a repo with only per-project state and no global registry
6. malformed or lock-blocked global registry behavior

## Suggested Implementation Order

1. add global registry model and serializer
2. add deterministic generated-skill hashing
3. add lock acquisition helpers
4. integrate registry-aware skill diff/apply logic
5. add migration logic
6. add multi-project tests
7. add explicit prune/garbage-collect command

## Notes

The 2026-03-08 hardening changes already fixed the immediate safety issues around foreign state files and invalid managed skill entries. This plan addresses the next layer: coordinated ownership of valid shared skill directories across projects.
