# Release helpers for raptor-opencode.
#
#   make release-notes           regenerate CHANGELOG.md with git-cliff
#   make release VERSION=0.1.0   tag the current HEAD and push the tag
#
# `release-notes` is idempotent: the changelog only depends on the commit
# history and the existing tags.

SHELL := /bin/bash
.DEFAULT_GOAL := help

TAG = v$(VERSION)

.PHONY: help release-notes release

help: ## Show this help
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

release-notes: ## Regenerate CHANGELOG.md from the full history
	git-cliff -o CHANGELOG.md

release: ## Tag the current HEAD and push it (VERSION=x.y.z)
	@if [ -z '$(VERSION)' ]; then echo 'usage: make release VERSION=x.y.z' >&2; exit 2; fi
	@if ! printf '%s' '$(VERSION)' | grep -qxE '[0-9]+\.[0-9]+\.[0-9]+'; then \
		echo "invalid VERSION '$(VERSION)': expected x.y.z" >&2; exit 2; \
	fi
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo 'working tree is not clean: commit or stash first' >&2; exit 1; \
	fi
	@if git rev-parse -q --verify 'refs/tags/$(TAG)' >/dev/null; then \
		echo "tag $(TAG) already exists" >&2; exit 1; \
	fi
	git tag -a '$(TAG)' -m '🚀 release: $(TAG)'
	git push origin '$(TAG)'
