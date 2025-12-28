# Development Setup Guide

Quick guide to set up the development environment for MDB Engine.

## Prerequisites

- Python 3.8+
- pip
- git

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd mdb-runtime

# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install the package in development mode (recommended)
make install-dev

# Or install manually:
# pip install -e ".[dev,test]"

# Install pre-commit hooks
pre-commit install
```

## Makefile

The project includes a comprehensive Makefile that simplifies development workflows. **We recommend using Makefile commands** for consistency and ease of use.

### Quick Start

After installation, verify your setup:

```bash
# Quick quality check (lint + unit tests)
make check
```

### Common Commands

```bash
# View all available commands
make help

# Installation
make install          # Install package and dependencies
make install-dev      # Install package with dev dependencies

# Testing
make test             # Run all tests
make test-unit        # Run unit tests only
make test-integration # Run integration tests only
make test-coverage-html # Run tests with HTML coverage report

# Code Quality
make format           # Format code (black + isort)
make lint             # Run linting checks (fails on errors)
make lint-check       # Run linting checks (non-failing)
make check            # Quick quality check (lint + unit tests)

# Cleanup
make clean            # Remove build artifacts and caches
```

See [CONTRIBUTING.md](CONTRIBUTING.md#makefile) for detailed Makefile documentation.

## Verify Setup

```bash
# Install development dependencies (includes test tools)
make install-dev

# Run quick quality check (lint + unit tests)
make check

# Or verify individual components:
pre-commit run --all-files  # Pre-commit hooks
make test-unit             # Unit tests
make lint                  # Linting checks
```

## Pre-commit Hooks

Pre-commit hooks automatically run before each commit to ensure code quality:

- **Code formatting** (Black)
- **Import sorting** (isort)
- **Linting** (flake8)
- **Exception handling checks** (custom)

### Running Hooks Manually

```bash
# Run all hooks on staged files
pre-commit run

# Run all hooks on all files
pre-commit run --all-files

# Run specific hook
pre-commit run flake8 --all-files
pre-commit run check-exception-handling --all-files
```

### Bypassing Hooks (Not Recommended)

Only bypass hooks in emergencies:

```bash
git commit --no-verify -m "Emergency fix"
```

## Code Formatting

**Recommended: Use the Makefile**

```bash
# Format code (black + isort)
make format
```

**Alternative: Direct commands**

```bash
# Format code
black --line-length=100 .

# Sort imports
isort --profile black --line-length=100 .

# Check formatting without changing files
black --check --line-length=100 .
```

## Linting

**Recommended: Use the Makefile**

```bash
# Run linting checks (fails on errors)
make lint

# Run linting checks (non-failing, for CI)
make lint-check
```

The `lint` command runs:
- flake8 (code quality)
- isort (import sorting check)
- Exception handling checker (Grinberg framework compliance)

**Alternative: Direct commands**

```bash
# Run flake8
flake8 mdb_engine tests scripts

# Run with specific error codes
flake8 --select=E,W,F mdb_engine
```

## Exception Handling Checker

The custom exception handling checker enforces best practices:

```bash
# Check specific files
python scripts/check_exception_handling.py file1.py file2.py

# Check entire module
python scripts/check_exception_handling.py mdb_engine/database/

# Check all Python files (excluding tests/examples)
find mdb_engine -name "*.py" -not -path "*/test*" | xargs python scripts/check_exception_handling.py
```

## Testing

**Recommended: Use the Makefile**

```bash
# Run all tests (unit + integration)
make test

# Run unit tests only (fast, no MongoDB required)
make test-unit

# Run integration tests only (requires Docker)
make test-integration

# Run tests with coverage report (terminal)
make test-coverage

# Run tests with HTML coverage report
make test-coverage-html
# Then open htmlcov/index.html in your browser
```

**Alternative: Direct pytest commands**

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/unit/test_engine.py -v

# Run with verbose output
pytest -v -s
```

### Test Coverage

The Makefile tracks coverage for core modules (`mdb_engine/core` and `mdb_engine/database`) with a minimum threshold of 70%. Coverage reports are generated in:
- Terminal: `make test-coverage`
- HTML: `make test-coverage-html` (opens `htmlcov/index.html`)

## Common Issues

### Pre-commit hooks not running

```bash
# Reinstall hooks
pre-commit uninstall
pre-commit install
```

### Import errors in scripts

Make sure you're in the project root directory and the virtual environment is activated.

### Flake8 errors

Check `.flake8` configuration. Some errors may be acceptable (e.g., in `__init__.py` files).

## Next Steps

- Read [CONTRIBUTING.md](CONTRIBUTING.md) for detailed contribution guidelines
- Review exception handling best practices in CONTRIBUTING.md
- Check existing issues and PRs before starting work

