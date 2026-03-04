# Packaging and Release Readiness Plan

Date: 2026-03-04
Status: proposed
Depends on:
- `DESIGN.md`
- `AGENTS.md`

## Goal

Make `cc-codex-bridge` easy to install from source, easy to validate as a distributable package, and easy to release reproducibly.

This plan covers:

1. packaging smoke coverage
2. version consistency enforcement
3. user-facing install documentation
4. release automation
5. release procedure documentation

## Current State

What already exists:

1. package metadata in `pyproject.toml`
2. runtime version in `src/cc_codex_bridge/__init__.py`
3. console script entrypoint `cc-codex-bridge`
4. module entrypoint `python3 -m cc_codex_bridge`
5. test workflow in `.github/workflows/test.yml`
6. contributor-oriented install docs in `README.md`

What is missing or weak:

1. no packaging smoke checks in CI for clean non-editable installs
2. no build artifact verification for `sdist` and `wheel`
3. no automated guard that `pyproject.toml` version matches `cc_codex_bridge.__version__`
4. no release workflow
5. no documented release checklist
6. install docs are contributor-first, not user-first
7. `build` is not part of the dev toolchain

## Locked Decisions

1. Keep the package layout under `src/cc_codex_bridge/`.
2. Keep the console script name `cc-codex-bridge`.
3. Keep `pyproject.toml` as the canonical packaging metadata file.
4. Keep `src/cc_codex_bridge/__init__.py` exporting `__version__`, but enforce consistency with `pyproject.toml`.
5. Treat clean `pip install .` as a supported local install path.
6. Treat editable install as a contributor workflow, not the only documented install path.
7. Release automation must validate packaging before publishing artifacts.
8. Documentation updates are part of the implementation, not follow-up work.

## Open Decision To Confirm Early

The implementation session should confirm the intended release channel before wiring the publish step:

1. GitHub Releases only, with built artifacts attached
2. GitHub Releases plus PyPI publish

If PyPI is not ready, implement artifact build plus GitHub Release upload first and leave the publish step behind a documented follow-up.

## Deliverables

### Deliverable 1: Packaging smoke coverage

Add CI coverage for:

1. clean non-editable `pip install .`
2. console script invocation with `cc-codex-bridge --help`
3. module invocation with `python -m cc_codex_bridge --help` or a similarly cheap smoke command
4. `python -m build --sdist --wheel`

Expected outcome:

- package installability is verified independently of the editable dev workflow
- release automation can rely on the same packaging checks

## Deliverable 2: Version consistency enforcement

Add tests that ensure:

1. `project.version` in `pyproject.toml`
2. `cc_codex_bridge.__version__`

stay identical.

Expected outcome:

- version drift fails in tests and CI before release

## Deliverable 3: Packaging toolchain readiness

Update the project so release/build tooling is present for contributors and CI.

Likely changes:

1. add `build` to `[project.optional-dependencies].dev`
2. add other minimal release-time dependencies only if strictly needed

Expected outcome:

- building artifacts is a first-class supported workflow

## Deliverable 4: User-facing install docs

Update `README.md` to distinguish:

1. user install from a local checkout
2. contributor setup
3. raw-checkout execution without install

Expected outcome:

- installation guidance is clear for both users and contributors

## Deliverable 5: Release workflow

Add a GitHub Actions workflow that:

1. triggers from version tags
2. sets up Python
3. installs release/build dependencies
4. runs tests
5. runs packaging smoke checks
6. builds `sdist` and `wheel`
7. uploads artifacts
8. optionally publishes to PyPI if the release channel decision is confirmed

Expected outcome:

- tagged releases are reproducible and validated

## Deliverable 6: Release procedure doc

Add a short release procedure covering:

1. version bump
2. tests
3. tag creation
4. release workflow expectations
5. artifact verification
6. release notes or changelog expectations

The procedure can live in one of:

1. `README.md`
2. `DESIGN.md` only if architectural
3. `.claude/docs/plans/` or `.claude/docs/patterns/` if it is primarily operator guidance

Prefer keeping architecture out of operational release instructions unless the release model becomes part of the architecture.

## Implementation Sequence

### Phase 1: Packaging contract and metadata checks

Implement:

1. version consistency test
2. any small metadata cleanup needed in `pyproject.toml`
3. add `build` to dev extras

Verification:

```bash
pytest tests/test_package.py -q
python3 -m pip install -e ".[dev]"
python3 -m build --sdist --wheel
```

### Phase 2: Packaging smoke coverage

Implement:

1. a CI job or CI steps for clean `pip install .`
2. console script smoke check
3. module entrypoint smoke check
4. artifact build smoke check

Verification:

```bash
python3 -m venv /tmp/cccb-install-venv
/tmp/cccb-install-venv/bin/python -m pip install .
/tmp/cccb-install-venv/bin/cc-codex-bridge --help
/tmp/cccb-install-venv/bin/python -m cc_codex_bridge --help
python3 -m build --sdist --wheel
```

### Phase 3: Docs split between users and contributors

Implement:

1. update `README.md` install section
2. separate user install from contributor setup
3. document the packaged CLI and module entrypoints clearly

Verification:

1. README commands match actual package behavior
2. local clean-venv install steps work as documented

### Phase 4: Release automation

Implement:

1. release workflow file under `.github/workflows/`
2. tag-based trigger
3. build/test/package gates
4. artifact upload
5. publish step if release channel is confirmed

Verification:

1. workflow YAML validates
2. dry-run logic is reasonable from inspection
3. if possible, test against a throwaway tag or disabled publish path

### Phase 5: Release procedure documentation

Implement:

1. short checklist doc or README section
2. explicit version bump location(s)
3. tag format
4. expected workflow outputs

Verification:

1. procedure matches workflow triggers
2. procedure matches version consistency enforcement

## File-Level Change Targets

Likely files to touch:

1. `pyproject.toml`
2. `README.md`
3. `.github/workflows/test.yml`
4. new `.github/workflows/release.yml` or similar
5. `tests/test_package.py`
6. optional new packaging-focused test file if `tests/test_package.py` becomes too cramped
7. optional release procedure doc under `.claude/docs/patterns/` or `README.md`

## Suggested Test Additions

Minimum:

1. test that `cc_codex_bridge.__version__` equals `pyproject.toml` version

Nice to have:

1. test that the declared console script entrypoint remains `cc_codex_bridge.cli:main`
2. test that required package metadata fields stay present if that becomes valuable

Prefer keeping package tests small and structural. The full installation smoke should live in CI, not in pytest.

## Risks

1. Publishing automation can be added before the package contract is actually validated.
2. Editable-install assumptions can hide failures that only happen under `pip install .`.
3. Version drift can silently ship the wrong metadata if not explicitly tested.
4. Release docs can drift from automation if both are added without shared assumptions.

## Success Criteria

This effort is done when:

1. `pip install .` works in a clean environment
2. `cc-codex-bridge --help` works after install
3. `python -m build --sdist --wheel` works
4. CI verifies packaging smoke checks
5. tests fail if package version metadata drifts
6. a tag-triggered release workflow exists
7. release steps are documented clearly enough for a fresh session to execute them without guesswork

## Recommended Order For Another Session

1. implement version consistency test and `build` dev dependency
2. add packaging smoke coverage to CI
3. update README install docs
4. add release workflow
5. add release checklist doc
6. run full tests plus local packaging checks
