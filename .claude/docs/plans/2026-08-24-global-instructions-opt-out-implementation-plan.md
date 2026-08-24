# Global Instructions Opt-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-enabled global configuration switch that can make the bridge completely ignore `~/.claude/CLAUDE.md` while preserving project instruction behavior and the existing Codex `~/.codex/AGENTS.md`.

**Architecture:** Parse `[sync].global_instructions` into `BridgeConfig`, carry the policy separately from optional instruction content into `DesiredState`, and make global-instruction planning a no-op when disabled. Surface the policy in config and status output so disabled management is distinguishable from an absent Claude source.

**Tech Stack:** Python 3.11+, frozen dataclasses, `tomllib`, argparse CLI formatting, pytest.

---

### Task 1: Parse and display the global synchronization policy

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_config_show.py`
- Modify: `src/cc_codex_bridge/config.py`
- Modify: `src/cc_codex_bridge/config_show.py`

- [ ] **Step 1: Write failing configuration tests**

Add tests proving the default is enabled, explicit false disables it, explicit true enables it, and malformed `[sync]` values fall back to enabled:

```python
def test_bridge_config_enables_global_instructions_by_default():
    assert BridgeConfig().sync_global_instructions is True


def test_load_config_disables_global_instructions(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[sync]\nglobal_instructions = false\n")
    assert load_config(config_path).sync_global_instructions is False


@pytest.mark.parametrize(
    "content",
    (
        "[sync]\nglobal_instructions = true\n",
        "[other]\nvalue = true\n",
        "[sync]\nglobal_instructions = \"false\"\n",
        "sync = false\n",
    ),
)
def test_load_config_keeps_global_instructions_enabled(content, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(content)
    assert load_config(config_path).sync_global_instructions is True
```

Import `pytest` in `tests/test_config.py`. Add config-show tests expecting `Global instructions sync: enabled|disabled` in human output and `sync.global_instructions` with `value` and `source` fields in JSON.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
source .venv/bin/activate && pytest tests/test_config.py tests/test_config_show.py -q
```

Expected: failures because the config field and display entries do not exist.

- [ ] **Step 3: Implement minimal parsing and display**

Add this contract in `config.py`:

```python
DEFAULT_SYNC_GLOBAL_INSTRUCTIONS = True


@dataclass(frozen=True)
class BridgeConfig:
    log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS
    sync_global_instructions: bool = DEFAULT_SYNC_GLOBAL_INSTRUCTIONS
    exclude: SyncExclusions = SyncExclusions()
```

Parse only real booleans:

```python
sync_section = data.get("sync", {})
if not isinstance(sync_section, dict):
    sync_section = {}
sync_global_instructions = sync_section.get(
    "global_instructions", DEFAULT_SYNC_GLOBAL_INSTRUCTIONS
)
if not isinstance(sync_global_instructions, bool):
    sync_global_instructions = DEFAULT_SYNC_GLOBAL_INSTRUCTIONS
```

Return the field from `load_config()`. Render it in `config_show.py` before scan paths and include:

```python
"sync": {
    "global_instructions": {
        "value": global_config.sync_global_instructions,
        "source": "default" if global_config.sync_global_instructions else "global",
    }
},
```

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
source .venv/bin/activate && pytest tests/test_config.py tests/test_config_show.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cc_codex_bridge/config.py src/cc_codex_bridge/config_show.py tests/test_config.py tests/test_config_show.py
git commit -m "feat(config): add global instructions sync policy"
```

### Task 2: Disable global-instruction planning without treating the source as absent

**Files:**
- Modify: `tests/test_reconcile.py`
- Modify: `src/cc_codex_bridge/reconcile.py`

- [ ] **Step 1: Write failing desired-state tests**

Add tests beside the existing global-instructions cases:

```python
from dataclasses import replace


def test_disabled_global_instructions_preserve_hand_authored_agents_md(
    make_project, make_plugin_version, tmp_path,
):
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "demo", "1.0.0")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    agents_md = codex_home / "AGENTS.md"
    agents_md.write_text("Codex-specific instructions.\n")
    desired = _build_desired(project_root, cache_root, codex_home)
    desired = replace(desired, manage_global_instructions=False)

    report = reconcile_desired_state(desired)

    assert agents_md.read_text() == "Codex-specific instructions.\n"
    assert not any(c.resource_kind == "global_instructions" for c in report.changes)


def test_disabled_global_instructions_preserve_bridge_owned_agents_md(
    make_project, make_plugin_version, tmp_path,
):
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "demo", "1.0.0")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    agents_md = codex_home / "AGENTS.md"
    content = "Old instructions.\n" + GLOBAL_INSTRUCTIONS_SENTINEL
    agents_md.write_text(content)
    desired = _build_desired(project_root, cache_root, codex_home)
    desired = replace(desired, manage_global_instructions=False)

    report = reconcile_desired_state(desired)

    assert agents_md.read_text() == content
    assert not any(c.resource_kind == "global_instructions" for c in report.changes)
```

Add a pipeline test that writes `[sync] global_instructions = false` under the supplied bridge home and asserts the desired state disables global instruction management while project shim behavior remains unchanged.

- [ ] **Step 2: Run the exact new tests and verify RED**

Expected: failures because `DesiredState.manage_global_instructions` does not exist.

- [ ] **Step 3: Implement the policy boundary**

Add a default-enabled field to `DesiredState` and matching keyword to `build_desired_state()`:

```python
manage_global_instructions: bool = True
```

Pass `cfg.sync_global_instructions` from `build_project_desired_state()`. Short-circuit before touching Codex instructions:

```python
def _plan_global_instructions_changes(desired: DesiredState) -> tuple[Change, ...]:
    if not desired.manage_global_instructions:
        return ()
    # existing behavior
```

- [ ] **Step 4: Run global-instruction and project-shim tests**

```bash
source .venv/bin/activate && pytest tests/test_reconcile.py -q -k 'global_instructions or user_claude_md or agents_md_from_claude_md or bootstrap'
```

Expected: disabled tests and all existing enabled/default cases pass.

- [ ] **Step 5: Commit**

```bash
git add src/cc_codex_bridge/reconcile.py tests/test_reconcile.py
git commit -m "feat(reconcile): allow global instructions opt-out"
```

### Task 3: Surface the policy in status output

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/cc_codex_bridge/reconcile.py`
- Modify: `src/cc_codex_bridge/cli.py`

- [ ] **Step 1: Write failing human and JSON status tests**

Assert disabled human status includes:

```text
GLOBAL_INSTRUCTIONS_SYNC disabled
```

Assert JSON includes:

```python
assert payload["global_instructions_sync_enabled"] is False
```

Add a default-enabled assertion as well.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
source .venv/bin/activate && pytest tests/test_cli.py -q -k 'global_instructions_sync'
```

Expected: failures because the status formatters do not expose the policy.

- [ ] **Step 3: Implement status threading**

Add `global_instructions_sync_enabled: bool` to `ProjectBuildResult`, populate it on all return paths, and thread it through `_build_status_payload()`, `format_status_json()`, and `format_status_report()`:

```python
"global_instructions_sync_enabled": global_instructions_sync_enabled,
```

```python
state = "enabled" if payload["global_instructions_sync_enabled"] else "disabled"
lines.append(f"{_k('GLOBAL_INSTRUCTIONS_SYNC')} {state}")
```

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
source .venv/bin/activate && pytest tests/test_cli.py tests/test_config_show.py -q -k 'global_instructions_sync or all_defaults'
```

Expected: all policy visibility tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cc_codex_bridge/reconcile.py src/cc_codex_bridge/cli.py tests/test_cli.py
git commit -m "feat(status): report global instructions sync policy"
```

### Task 4: Update canonical documentation and release notes

**Files:**
- Modify: `README.md`
- Modify: `DESIGN.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document the default-enabled false opt-out**

Document this global config:

```toml
[sync]
global_instructions = false
```

State that disabled mode never creates, updates, validates, or removes `~/.codex/AGENTS.md`, while project instruction behavior is unchanged. Update the discovery, desired-state, reconcile-flow, safety, configuration, status, and test-contract sections in `DESIGN.md`.

- [ ] **Step 2: Add an Unreleased changelog entry**

Add an `Added` entry describing the opt-out and preservation semantics. Do not bump package versions because no release was requested.

- [ ] **Step 3: Verify and commit**

```bash
rg -n "global_instructions|global instructions|Global instructions" README.md DESIGN.md CHANGELOG.md
git diff --check
git add README.md DESIGN.md CHANGELOG.md
git commit -m "docs: document global instructions opt-out"
```

### Task 5: Verify, configure this machine, and complete reconciliation

**Files:**
- Modify outside repository: `~/.cc-codex-bridge/config.toml`
- Reconcile generated state under `~/.codex/`, `~/.cc-codex-bridge/`, and registered project `.codex/` paths

- [ ] **Step 1: Run the full suite**

```bash
source .venv/bin/activate && pytest tests -q
```

Expected: all tests pass.

- [ ] **Step 2: Run independent code review**

Use requesting-code-review against the complete diff. Apply only verified findings, using receiving-code-review and TDD for code changes.

- [ ] **Step 3: Configure this machine**

Preserve every existing global config value and set:

```toml
[sync]
global_instructions = false
```

Run `cc-codex-bridge config show --global` and confirm the policy is disabled.

- [ ] **Step 4: Exclude the externally managed skill**

```bash
cc-codex-bridge config exclude add --global skill user/using-wpcom-local
```

Confirm both `user/using-wpcom-local` and `superpowers-marketplace/superpowers` are globally excluded.

- [ ] **Step 5: Preview and apply reconciliation**

```bash
cc-codex-bridge reconcile --all --dry-run
cc-codex-bridge reconcile --all
cc-codex-bridge status --all
```

Expected: every registered project completes with no global-instructions or wpcom-local ownership error, and final status is in sync.

- [ ] **Step 6: Audit superpowers artifacts**

Compare the 14 skill names from the installed superpowers plugin against `~/.codex/skills/` and `~/.cc-codex-bridge/registry.json`. Expected: no plugin-owned directories or registry claims remain unless an independently discovered, non-excluded source owns the same name; report any such source precisely.

- [ ] **Step 7: Final verification**

```bash
source .venv/bin/activate && pytest tests -q
git diff --check
git status --short
```

Expected: full suite passes, no whitespace errors, and only intentional committed work remains.
