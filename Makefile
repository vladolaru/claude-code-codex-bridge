.PHONY: test build release-check release

VERSION ?=
TAG := v$(VERSION)
PYTHON ?= python3

ifneq ($(wildcard .venv/bin/python),)
PYTHON := .venv/bin/python
endif

test:
	$(PYTHON) -m pytest tests -q

build:
	$(PYTHON) -m build --sdist --wheel

release-check:
	@test -n "$(VERSION)" || { echo "VERSION is required. Use: make release VERSION=X.Y.Z" >&2; exit 1; }
	@test -f pyproject.toml || { echo "Run this target from the repository root." >&2; exit 1; }
	@$(PYTHON) packaging/release_check.py "$(VERSION)"
	$(PYTHON) -m pytest tests -q

release: release-check
	@if git rev-parse "refs/tags/$(TAG)" >/dev/null 2>&1; then \
		echo "Tag $(TAG) already exists." >&2; \
		exit 1; \
	fi
	@git tag -a "$(TAG)" -m "Release $(TAG)"
	@git push origin "$$(git rev-parse --abbrev-ref HEAD)"
	@git push origin "$(TAG)"
	@echo "Pushed $(TAG). GitHub Actions will build artifacts and publish the release."
