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

.PHONY: help install install-dev test test-unit test-integration test-coverage test-coverage-html lint lint-check format clean clean-pyc clean-cache check _check-tools _lint-flake8 _lint-isort _lint-exceptions build build-check publish-test publish

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
	@echo "  make lint             - Run linters (flake8, isort check) - fails on errors"
	@echo "  make lint-check       - Run linters (non-failing, for CI)"
	@echo "  make format           - Format code (black, isort)"
	@echo "  make check            - Run lint + unit tests (quick quality check)"
	@echo "  make clean            - Remove build artifacts and caches"
	@echo "  make clean-pyc        - Remove Python cache files"
	@echo "  make clean-cache      - Remove pytest cache"
	@echo "  make build            - Build distribution packages (wheel + sdist)"
	@echo "  make build-check      - Check built packages"
	@echo "  make publish-test     - Publish to TestPyPI (requires PYPI_API_TOKEN)"
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
	@python -m flake8 --version > /dev/null 2>&1 || (echo "⚠️  flake8 not installed. Install with: pip install -e '.[dev]'" && exit 1)
	@python -m isort --version > /dev/null 2>&1 || (echo "⚠️  isort not installed. Install with: pip install -e '.[dev]'" && exit 1)

_lint-flake8:
	@echo "Checking flake8..."
	@flake8 $(SOURCE_DIRS) || (echo "❌ flake8 found issues. Run 'make format' to auto-fix some issues." && exit 1)

_lint-isort:
	@echo "Checking import sorting (isort)..."
	@isort --check-only $(SOURCE_DIRS) || (echo "❌ isort found import ordering issues. Run 'make format' to auto-fix." && exit 1)

_lint-exceptions:
	@echo "Checking exception handling (Grinberg framework)..."
	@$(PYTHON) scripts/check_exception_handling.py $$(find mdb_engine -name "*.py" -not -path "*/test*" -not -path "*/__pycache__/*") || (echo "❌ Exception handling violations found. See output above." && exit 1)

# Code quality
lint: _check-tools _lint-flake8 _lint-isort _lint-exceptions
	@echo "✅ All linting checks passed!"

lint-check:
	@echo "Running lint check (non-failing)..."
	@if command -v flake8 &> /dev/null; then \
		flake8 $(SOURCE_DIRS) 2>&1 | tee /tmp/flake8_output.txt || true; \
		if grep -qE "^mdb_engine|^tests|^scripts" /tmp/flake8_output.txt 2>/dev/null; then \
			echo "⚠️  flake8 found issues"; \
		fi; \
	else \
		echo "⚠️  flake8 not installed"; \
	fi
	@if command -v isort &> /dev/null; then \
		isort --check-only $(SOURCE_DIRS) 2>&1 | tee /tmp/isort_output.txt || true; \
		if grep -q "ERROR:" /tmp/isort_output.txt 2>/dev/null; then \
			echo "⚠️  isort found issues"; \
		fi; \
	else \
		echo "⚠️  isort not installed"; \
	fi
	@$(PYTHON) scripts/check_exception_handling.py $$(find mdb_engine -name "*.py" -not -path "*/test*" -not -path "*/__pycache__/*") 2>&1 | tee /tmp/exception_check_output.txt || true
	@$(PYTHON) scripts/count_lint_errors.py || true

format:
	@echo "Formatting code..."
	@if command -v black &> /dev/null; then \
		black $(SOURCE_DIRS); \
	else \
		echo "⚠️  black not installed. Install with: pip install -e '.[dev]'"; \
	fi
	@if command -v isort &> /dev/null; then \
		isort $(SOURCE_DIRS); \
	else \
		echo "⚠️  isort not installed. Install with: pip install -e '.[dev]'"; \
	fi

# Cleanup
clean: clean-pyc clean-cache
	rm -rf build/ dist/ *.egg-info htmlcov/ .coverage

clean-pyc:
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

clean-cache:
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/

# Quick quality check (lint + unit tests)
check: lint test-unit
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

publish-test: build-check
	@echo "Publishing to TestPyPI..."
	@if [ -z "$$PYPI_API_TOKEN" ]; then \
		echo "❌ Error: PYPI_API_TOKEN environment variable not set"; \
		echo "   Get your token from: https://test.pypi.org/manage/account/token/"; \
		echo "   Then run: export PYPI_API_TOKEN=your_token_here"; \
		exit 1; \
	fi
	@$(PYTHON) -m twine upload --repository testpypi dist/* --username __token__ --password $$PYPI_API_TOKEN
	@echo "✅ Published to TestPyPI! Test install with: pip install -i https://test.pypi.org/simple/ mdb-engine"

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
