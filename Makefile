.PHONY: release-version

# Bump pyproject, ox1log.__version__ and CHANGELOG (BUMP=patch|minor|major|X.Y.Z, default patch).
# Commit and push to main afterwards: .github/workflows/publish.yml publishes the new version to PyPI.
release-version:
	python3 scripts/release_version.py $(or $(BUMP),patch)
