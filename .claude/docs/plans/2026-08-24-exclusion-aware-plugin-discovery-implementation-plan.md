# Exclusion-aware Plugin Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent excluded malformed plugin caches from breaking pipeline commands, preserve a plugin-exclusion recovery path, globally exclude superpowers, and publish patch release `v1.5.3`.

**Architecture:** Resolve effective plugin exclusions before content discovery. Query Claude once for enabled plugin IDs, subtract excluded IDs before semantic-version cache validation, and pass the remaining IDs into discovery; retain the skipped enabled IDs in the exclusion report. Explicit plugin exclusion adds validate against Claude's enabled ID list without requiring plugin-content discovery, while other entity kinds retain discovery-backed validation.

**Tech Stack:** Python 3.14, argparse, dataclasses, pytest, TOML configuration, Make, Git/GitHub Actions.

---

### Task 1: Apply plugin exclusions before cache-version validation

**Files:**
- Modify: `src/cc_codex_bridge/discover.py`
- Modify: `src/cc_codex_bridge/reconcile.py`
- Test: `tests/test_discovery.py`
- Test: `tests/test_reconcile.py`

- [ ] **Step 1: Write a failing discovery test for caller-supplied enabled IDs**

```python
def test_discover_uses_supplied_enabled_ids_without_querying_claude(make_project, make_plugin_version, monkeypatch):
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "kept", "1.0.0")
    (cache_root / "market" / "excluded-broken" / "unknown").mkdir(parents=True)
    monkeypatch.setattr(discover_module, "query_enabled_plugin_ids", lambda root: pytest.fail("unexpected query"))
    result = discover(project_root, cache_root, enabled_plugin_ids=frozenset({"market/kept"}))
    assert [plugin.plugin_name for plugin in result.plugins] == ["kept"]
```

- [ ] **Step 2: Run the discovery test and verify RED**

Run: `source .venv/bin/activate && pytest tests/test_discovery.py::test_discover_uses_supplied_enabled_ids_without_querying_claude -q`

Expected: FAIL because `discover()` does not accept `enabled_plugin_ids`.

- [ ] **Step 3: Add the minimal discovery override**

```python
def discover(..., enabled_plugin_ids: frozenset[str] | None = None) -> DiscoveryResult:
    project = resolve_project_root(project_path)
    enabled_ids = enabled_plugin_ids
    if enabled_ids is None:
        enabled_ids = query_enabled_plugin_ids(project.root)
    plugins = discover_latest_plugins(..., enabled_ids=enabled_ids)
```

- [ ] **Step 4: Run the discovery test and verify GREEN**

Run: `source .venv/bin/activate && pytest tests/test_discovery.py::test_discover_uses_supplied_enabled_ids_without_querying_claude -q`

Expected: PASS.

- [ ] **Step 5: Write a failing pipeline regression test**

```python
def test_build_project_desired_state_skips_excluded_malformed_enabled_plugin(make_project, make_plugin_version, tmp_path, monkeypatch):
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "kept", "1.0.0")
    (cache_root / "market" / "playground" / "unknown").mkdir(parents=True)
    bridge_home = tmp_path / "bridge-home"
    bridge_home.mkdir()
    (bridge_home / "config.toml").write_text('[exclude]\nplugins = ["market/playground"]\n')
    monkeypatch.setattr(discover_module, "query_enabled_plugin_ids", lambda root: frozenset({"market/kept", "market/playground"}))
    build = build_project_desired_state(project_root, cache_dir=cache_root, bridge_home=bridge_home, codex_home=tmp_path / "codex-home")
    assert [plugin.plugin_name for plugin in build.discovery.plugins] == ["kept"]
    assert build.exclusion_report.plugins == ("market/playground",)
```

- [ ] **Step 6: Run the pipeline test and verify RED**

Run: `source .venv/bin/activate && pytest tests/test_reconcile.py::test_build_project_desired_state_skips_excluded_malformed_enabled_plugin -q`

Expected: FAIL with the semantic-version discovery error.

- [ ] **Step 7: Reorder pipeline configuration and merge the prefiltered exclusion report**

```python
project = resolve_project_root(project_root)
cfg = load_config(bridge_config_path(bridge_home=bridge_home_path))
project_exclusions = load_project_exclusions(project.root)
exclusions = resolve_effective_exclusions(project_exclusions, global_config=cfg.exclude, ...)
enabled_ids = query_enabled_plugin_ids(project.root)
excluded_enabled_ids = frozenset(enabled_ids or ()) & frozenset(exclusions.plugins)
discovery_ids = None if enabled_ids is None else frozenset(enabled_ids) - excluded_enabled_ids
result = discover(project_path=project.root, ..., enabled_plugin_ids=discovery_ids)
result, exclusion_report = apply_sync_exclusions(result, exclusions)
exclusion_report = replace(exclusion_report, plugins=tuple(sorted(set(exclusion_report.plugins) | excluded_enabled_ids)))
```

- [ ] **Step 8: Run focused discovery and reconcile tests**

Run: `source .venv/bin/activate && pytest tests/test_discovery.py tests/test_reconcile.py -q`

Expected: PASS.

- [ ] **Step 9: Commit the pipeline fix**

```bash
git add src/cc_codex_bridge/discover.py src/cc_codex_bridge/reconcile.py tests/test_discovery.py tests/test_reconcile.py
git commit -m "fix(discovery): apply plugin exclusions before version checks"
```

### Task 2: Preserve plugin exclusion as a recovery command

**Files:**
- Modify: `src/cc_codex_bridge/config_exclude_commands.py`
- Modify: `src/cc_codex_bridge/cli.py`
- Test: `tests/test_config_exclude_commands.py`
- Test: `tests/test_config_commands_cli.py`

- [ ] **Step 1: Write a failing candidate-validation handler test**

```python
def test_handle_exclude_add_from_candidates_writes_matching_plugin(tmp_path):
    config_path = tmp_path / "config.toml"
    result = handle_exclude_add_from_candidates(kind="plugin", entity_id="market/superpowers", config_path=config_path, candidates=("market/superpowers",))
    assert result.success is True
    assert read_config_data(config_path)["exclude"]["plugins"] == ["market/superpowers"]
```

- [ ] **Step 2: Run the handler test and verify RED**

Run: `source .venv/bin/activate && pytest tests/test_config_exclude_commands.py::test_handle_exclude_add_from_candidates_writes_matching_plugin -q`

Expected: FAIL because the candidate-based handler does not exist.

- [ ] **Step 3: Extract candidate-backed validation and keep the discovery wrapper**

```python
def handle_exclude_add_from_candidates(*, kind: str, entity_id: str, config_path: Path, candidates: Sequence[str]) -> ExcludeCommandResult:
    normalized = normalize_entity_id(entity_id, kind=kind)
    if not _matches_any(normalized, list(candidates), kind):
        return ExcludeCommandResult(False, f"{kind} '{entity_id}' not found in discovered entities")
    return _write_exclusion(kind=kind, normalized=normalized, config_path=config_path)

def handle_exclude_add(...):
    known = list_discoverable_entities(discovery, scope=scope)
    return handle_exclude_add_from_candidates(kind=kind, entity_id=entity_id, config_path=config_path, candidates=known[kind])
```

- [ ] **Step 4: Run the handler test and verify GREEN**

Run: `source .venv/bin/activate && pytest tests/test_config_exclude_commands.py::test_handle_exclude_add_from_candidates_writes_matching_plugin -q`

Expected: PASS.

- [ ] **Step 5: Write a failing CLI recovery regression test**

```python
def test_config_exclude_add_plugin_does_not_require_content_discovery(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# test\n")
    monkeypatch.setattr(discover_module, "query_enabled_plugin_ids", lambda root: frozenset({"market/superpowers", "market/playground"}))
    monkeypatch.setattr(discover_module, "discover", lambda **kwargs: pytest.fail("content discovery should not run"))
    exit_code = cli.main(["config", "exclude", "add", "--global", "plugin", "market/superpowers", "--project", str(project_root)])
    assert exit_code == 0
```

- [ ] **Step 6: Run the CLI test and verify RED**

Run: `source .venv/bin/activate && pytest tests/test_config_commands_cli.py::test_config_exclude_add_plugin_does_not_require_content_discovery -q`

Expected: FAIL because the command invokes full discovery first.

- [ ] **Step 7: Route explicit plugin adds through enabled-ID candidates**

```python
enabled_plugin_ids = query_enabled_plugin_ids(scope.project_root)
if cli_kind == "plugin" and enabled_plugin_ids is not None:
    result = handle_exclude_add_from_candidates(kind="plugin", entity_id=entity_id, config_path=scope.config_path, candidates=tuple(sorted(enabled_plugin_ids)))
    discovery = None
else:
    discovery = discover(...)
    result = handle_exclude_add(...)
is_global_entity = kind == "plugin" or (discovery is not None and is_user_global_entity(kind, entity_id, discovery))
```

- [ ] **Step 8: Run config command tests**

Run: `source .venv/bin/activate && pytest tests/test_config_exclude_commands.py tests/test_config_commands_cli.py -q`

Expected: PASS.

- [ ] **Step 9: Commit the recovery fix**

```bash
git add src/cc_codex_bridge/config_exclude_commands.py src/cc_codex_bridge/cli.py tests/test_config_exclude_commands.py tests/test_config_commands_cli.py
git commit -m "fix(config): keep plugin exclusions available during cache failures"
```

### Task 3: Document runtime behavior and prepare `1.5.3`

**Files:**
- Modify: `DESIGN.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `src/cc_codex_bridge/__init__.py`

- [ ] **Step 1: Update canonical architecture documentation**

Document that effective plugin exclusions are resolved before semantic-version validation and that explicit plugin exclusion adds validate against Claude's enabled IDs.

- [ ] **Step 2: Add the dated `1.5.3` changelog section**

```markdown
## [1.5.3] - 2026-08-24

### Fixed

- Excluded enabled plugins with non-semantic cache versions no longer block `status` or `reconcile`; plugin exclusion adds remain available as a recovery path when content discovery cannot complete.
```

- [ ] **Step 3: Align package versions**

Set `project.version = "1.5.3"` in `pyproject.toml` and `__version__ = "1.5.3"` in `src/cc_codex_bridge/__init__.py`.

- [ ] **Step 4: Reinstall editable metadata and run metadata tests**

Run: `source .venv/bin/activate && pip install -e ".[dev]" -q && pytest tests/test_package.py -q`

Expected: PASS with installed distribution version `1.5.3`.

- [ ] **Step 5: Commit release preparation**

```bash
git add DESIGN.md CHANGELOG.md pyproject.toml src/cc_codex_bridge/__init__.py
git commit -m "chore(release): prepare v1.5.3"
```

### Task 4: Verify and apply the requested global exclusion

**Files:**
- Modify outside repository: `~/.cc-codex-bridge/config.toml`
- Reconcile generated bridge artifacts through the CLI

- [ ] **Step 1: Run the full test suite**

Run: `source .venv/bin/activate && pytest tests -q`

Expected: all tests PASS with no warnings or errors.

- [ ] **Step 2: Verify live status no longer fails on playground**

Run: `cc-codex-bridge status`

Expected: normal status output that reports `claude-plugins-official/playground` as excluded.

- [ ] **Step 3: Add and apply the global superpowers exclusion**

Run: `cc-codex-bridge config exclude add --global plugin superpowers-marketplace/superpowers && cc-codex-bridge reconcile --all`

Expected: exclusion succeeds and every registered project reconciles without the playground version failure.

- [ ] **Step 4: Verify final live config and status**

Run: `cc-codex-bridge config exclude list --global --json && cc-codex-bridge status --all`

Expected: superpowers appears in global exclusions and no project reports the playground semantic-version error.

### Task 5: Publish patch release `v1.5.3`

**Files:**
- Create and push annotated Git tag `v1.5.3`

- [ ] **Step 1: Confirm clean `main` and release preconditions**

Run: `git status --short && git branch --show-current && git log -1 --oneline`

Expected: empty status, branch `main`, and the release preparation commit at HEAD.

- [ ] **Step 2: Run the maintainer release command**

Run: `source .venv/bin/activate && make release VERSION=1.5.3`

Expected: version checks and `pytest tests -q` pass, annotated tag `v1.5.3` is created, and `main` plus the tag are pushed atomically.

- [ ] **Step 3: Verify pushed refs and report the range**

Run: `git status --short && git tag --points-at HEAD && git ls-remote --heads origin main && git ls-remote --tags origin refs/tags/v1.5.3`

Expected: clean worktree, local tag `v1.5.3`, matching remote branch/tag refs, and final reporting of range `67d3138...<v1.5.3-commit>`.
