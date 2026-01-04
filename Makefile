# Variables
PYTHON := python3
PIP := pip
PYTEST := pytest
SOURCE_DIRS := mdb_engine tests scripts
COV_FAIL_UNDER := 70
TEST_DIR := tests
UNIT_TEST_DIR := tests/unit
INTEGRATION_TEST_DIR := tests/integration
COV_ARGS := --cov=mdb_engine/core --cov=mdb_engine/database --cov-fail-under=$(COV_FAIL_UNDER)

.PHONY: help install install-dev test test-unit test-integration test-coverage test-coverage-html lint fix format clean clean-pyc clean-cache check _check-tools _lint-exceptions build build-check publish

# Default target
help:
	@echo "MDB Engine - Available commands:"
	@echo ""
	@echo "  make install          - Install package and dependencies"
	@echo "  make install-dev      - Install package with dev dependencies"
	@echo "  make test             - Run all tests (unit + integration)"
	@echo "  make test-unit        - Run unit tests only (fast, no MongoDB)"
	@echo "  make test-integration - Run integration tests only (requires Docker)"
	@echo "  make test-coverage    - Run tests with coverage report (terminal)"
	@echo "  make test-coverage-html - Run tests with HTML coverage report"
	@echo "  make fix              - Auto-fix all linting issues (format + lint fixes)"
	@echo "  make lint             - Check for linting issues (strict, fails on errors)"
	@echo "  make format           - Alias for 'make fix'"
	@echo "  make check            - Run fix + lint + unit tests (quick quality check)"
	@echo "  make clean            - Remove build artifacts and caches"
	@echo "  make clean-pyc        - Remove Python cache files"
	@echo "  make clean-cache      - Remove pytest cache"
	@echo "  make build            - Build distribution packages (wheel + sdist)"
	@echo "  make build-check      - Check built packages"
	@echo "  make publish          - Publish to PyPI (requires PYPI_API_TOKEN)"
	@echo ""

# Installation
install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[test,dev]"

# Testing
test:
	@echo "Running all tests..."
	$(PYTEST) $(TEST_DIR) -v

test-unit:
	@echo "Running unit tests..."
	$(PYTEST) $(UNIT_TEST_DIR) -v -m "not integration"

test-integration:
	@echo "Running integration tests..."
	@if ! command -v docker &> /dev/null; then \
		echo "Error: Docker is required for integration tests. Please install Docker."; \
		exit 1; \
	fi
	$(PYTEST) $(INTEGRATION_TEST_DIR) -v -m integration

test-coverage:
	@echo "Running tests with coverage..."
	$(PYTEST) $(TEST_DIR) -v $(COV_ARGS) \
		--cov-report=term-missing \
		--cov-report=term:skip-covered

test-coverage-html:
	@echo "Running tests with HTML coverage report..."
	$(PYTEST) $(TEST_DIR) -v $(COV_ARGS) \
		--cov-report=html \
		--cov-report=term-missing
	@echo ""
	@echo "Coverage report generated in htmlcov/index.html"

# Code quality helpers
_check-tools:
	@ruff --version > /dev/null 2>&1 || (echo "⚠️  ruff not installed. Install with: pip install -e '.[dev]'" && exit 1)
	@semgrep --version > /dev/null 2>&1 || (echo "⚠️  semgrep not installed. Install with: pip install -e '.[dev]'" && exit 1)

_lint-semgrep:
	@echo "Checking exception handling (Semgrep + Grinberg framework)..."
	@semgrep scan --config .semgrep.yml --error --quiet mdb_engine/ || (echo "❌ Semgrep violations found. See output above." && exit 1)
	@echo "✅ Semgrep checks passed!"

# Code quality - Auto-fix everything
fix: _check-tools
	@echo "Fixing all auto-fixable issues..."
	@ruff check --fix $(SOURCE_DIRS) || true
	@ruff format $(SOURCE_DIRS)
	@echo "✅ Auto-fix complete!"

# Alias for backwards compatibility
format: fix

# Code quality - Check only (strict)
lint: _check-tools
	@echo "Checking code quality..."
	@ruff check $(SOURCE_DIRS)
	@ruff format --check $(SOURCE_DIRS)
	@$(MAKE) _lint-semgrep
	@echo "✅ All linting checks passed!"

# Cleanup
clean: clean-pyc clean-cache
	rm -rf build/ dist/ *.egg-info htmlcov/ .coverage

clean-pyc:
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

clean-cache:
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/

# Quick quality check (fix + lint + unit tests)
check: fix lint test-unit
	@echo ""
	@echo "✅ Quality check complete: linting and unit tests passed!"

# Build and publish
build:
	@echo "Building distribution packages..."
	@$(PYTHON) -m pip install --upgrade build twine
	@$(PYTHON) -m build
	@echo "✅ Build complete! Packages are in dist/"

build-check: build
	@echo "Checking built packages..."
	@$(PYTHON) -m pip install --upgrade twine
	@$(PYTHON) -m twine check dist/*
	@echo "✅ Package check complete!"

publish: build-check
	@echo "Publishing to PyPI..."
	@if [ -z "$$PYPI_API_TOKEN" ]; then \
		echo "❌ Error: PYPI_API_TOKEN environment variable not set"; \
		echo "   Get your token from: https://pypi.org/manage/account/token/"; \
		echo "   Then run: export PYPI_API_TOKEN=your_token_here"; \
		exit 1; \
	fi
	@$(PYTHON) -m twine upload dist/* --username __token__ --password $$PYPI_API_TOKEN
	@echo "✅ Published to PyPI! Install with: pip install mdb-engine"
