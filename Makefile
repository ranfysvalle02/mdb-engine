.PHONY: help install install-dev test test-unit test-integration test-coverage test-coverage-html lint lint-check format clean clean-pyc clean-cache check

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
	@echo "  make format           - Format code (black, isort)"
	@echo "  make check            - Run lint + unit tests (quick quality check)"
	@echo "  make clean            - Remove build artifacts and caches"
	@echo "  make clean-pyc        - Remove Python cache files"
	@echo "  make clean-cache       - Remove pytest cache"
	@echo ""

# Installation
install:
	pip install -e .

install-dev:
	pip install -e ".[test,dev]"

# Testing
test:
	@echo "Running all tests..."
	pytest tests/ -v

test-unit:
	@echo "Running unit tests..."
	pytest tests/unit/ -v -m "not integration"

test-integration:
	@echo "Running integration tests..."
	@if ! command -v docker &> /dev/null; then \
		echo "Error: Docker is required for integration tests. Please install Docker."; \
		exit 1; \
	fi
	pytest tests/integration/ -v -m integration

test-coverage:
	@echo "Running tests with coverage..."
	pytest tests/ -v \
		--cov=mdb_engine/core \
		--cov=mdb_engine/database \
		--cov-report=term-missing \
		--cov-report=term:skip-covered \
		--cov-fail-under=70 \
		--cov-exclude=*/tests/* \
		--cov-exclude=*/__pycache__/* \
		--cov-exclude=*/__init__.py

test-coverage-html:
	@echo "Running tests with HTML coverage report..."
	pytest tests/ -v \
		--cov=mdb_engine/core \
		--cov=mdb_engine/database \
		--cov-report=html \
		--cov-report=term-missing \
		--cov-fail-under=70 \
		--cov-exclude=*/tests/* \
		--cov-exclude=*/__pycache__/* \
		--cov-exclude=*/__init__.py
	@echo ""
	@echo "Coverage report generated in htmlcov/index.html"

# Code quality
lint:
	@echo "Running linters..."
	@echo "Checking flake8..."
	@if command -v flake8 &> /dev/null; then \
		flake8 mdb_engine tests scripts || (echo "❌ flake8 found issues. Run 'make format' to auto-fix some issues." && exit 1); \
	else \
		echo "⚠️  flake8 not installed. Install with: pip install -e '.[dev]'"; \
		exit 1; \
	fi
	@echo "Checking import sorting (isort)..."
	@if command -v isort &> /dev/null; then \
		isort --check-only mdb_engine tests scripts || (echo "❌ isort found import ordering issues. Run 'make format' to auto-fix." && exit 1); \
	else \
		echo "⚠️  isort not installed. Install with: pip install -e '.[dev]'"; \
		exit 1; \
	fi
	@echo "Checking exception handling (Grinberg framework)..."
	@python3 scripts/check_exception_handling.py $$(find mdb_engine -name "*.py" -not -path "*/test*" -not -path "*/__pycache__/*") || (echo "❌ Exception handling violations found. See output above." && exit 1)
	@echo "✅ All linting checks passed!"

lint-check:
	@echo "Running lint check (non-failing)..."
	@if command -v flake8 &> /dev/null; then \
		flake8 mdb_engine tests scripts 2>&1 | tee /tmp/flake8_output.txt || true; \
		if grep -qE "^mdb_engine|^tests|^scripts" /tmp/flake8_output.txt 2>/dev/null; then \
			echo "⚠️  flake8 found issues"; \
		fi; \
	else \
		echo "⚠️  flake8 not installed"; \
		touch /tmp/flake8_output.txt; \
	fi
	@if command -v isort &> /dev/null; then \
		isort --check-only mdb_engine tests scripts 2>&1 | tee /tmp/isort_output.txt || true; \
		if grep -q "ERROR:" /tmp/isort_output.txt 2>/dev/null; then \
			echo "⚠️  isort found issues"; \
		fi; \
	else \
		echo "⚠️  isort not installed"; \
		touch /tmp/isort_output.txt; \
	fi
	@echo "Checking exception handling (Grinberg framework)..."
	@python3 scripts/check_exception_handling.py $$(find mdb_engine -name "*.py" -not -path "*/test*" -not -path "*/__pycache__/*") 2>&1 | tee /tmp/exception_check_output.txt || true
	@python3 scripts/count_lint_errors.py || true

format:
	@echo "Formatting code..."
	@if command -v black &> /dev/null; then \
		black mdb_engine tests scripts; \
	else \
		echo "black not installed. Install with: pip install -e '.[dev]'"; \
	fi
	@if command -v isort &> /dev/null; then \
		isort mdb_engine tests scripts; \
	else \
		echo "isort not installed. Install with: pip install -e '.[dev]'"; \
	fi

# Cleanup
clean: clean-pyc clean-cache
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache/

clean-pyc:
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete

clean-cache:
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/

# Quick quality check (lint + unit tests)
check: lint test-unit
	@echo ""
	@echo "✅ Quality check complete: linting and unit tests passed!"

