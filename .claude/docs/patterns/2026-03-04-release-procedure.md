# Release Procedure

Date: 2026-03-04
Status: active

This project currently releases through GitHub Releases with attached `sdist` and `wheel` artifacts. PyPI publishing is not part of the automated release flow yet.

## Version Sources

Update both version declarations together:

1. `pyproject.toml` `project.version`
2. `src/cc_codex_bridge/__init__.py` `__version__`

The package tests fail if these drift.

## Release Checklist

1. Update the version in `pyproject.toml`.
2. Update `src/cc_codex_bridge/__init__.py` to the same version.
3. Run `pytest tests -q`.
4. Run `python3 -m build --sdist --wheel`.
5. Optionally verify a clean install with:

   ```bash
   python3 -m venv /tmp/cccb-release-smoke
   /tmp/cccb-release-smoke/bin/python -m pip install .
   /tmp/cccb-release-smoke/bin/cc-codex-bridge --help
   /tmp/cccb-release-smoke/bin/python -m cc_codex_bridge --help
   ```

6. Commit the version bump and any release notes.
7. Create an annotated tag in the form `vX.Y.Z`.
8. Push the commit and tag.

## Workflow Expectations

Pushing a `vX.Y.Z` tag triggers `.github/workflows/release.yml`, which:

1. installs the dev toolchain
2. runs the full test suite
3. runs packaging smoke checks with a clean `pip install .`
4. builds `sdist` and `wheel` artifacts
5. creates a GitHub Release for the tag
6. attaches the built artifacts to that release

## Release Notes

Use the tag push as the release trigger and rely on generated GitHub release notes unless a hand-written summary is needed for that release.
