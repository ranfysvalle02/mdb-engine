# Contributing to MDB Engine

Thank you for your interest in contributing to MDB Engine! This document provides guidelines and best practices for contributing code.

## Table of Contents

- [Code Style](#code-style)
- [Makefile](#makefile)
- [Exception Handling Best Practices](#exception-handling-best-practices)
- [Pre-commit Hooks](#pre-commit-hooks)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Code Review Guidelines](#code-review-guidelines)

## Code Style

We use automated tools to maintain consistent code style:

- **Black**: Code formatting (line length: 100)
- **isort**: Import sorting (Black profile)
- **flake8**: Linting with plugins for bug detection
  - **E722**: Catches bare `except:` clauses (enforced for exception handling best practices)

### Setup

```bash
# Install development dependencies
pip install -e ".[test]"

# Run code quality checks
make lint
make format
```

### Using the Makefile (Recommended)

The Makefile provides convenient commands for common development tasks:

```bash
# Format code (black + isort)
make format

# Run linting checks (fails on errors)
make lint

# Run linting checks (non-failing, for CI)
make lint-check

# Run quick quality check (lint + unit tests)
make check
```

See the [Makefile section](#makefile) below for all available commands.

### Manual Formatting (Alternative)

If you prefer to run tools directly:

```bash
# Format code
black --line-length=100 .

# Sort imports
isort --profile black --line-length=100 .

# Run flake8 directly
flake8 mdb_engine tests scripts
```

## Makefile

The project includes a comprehensive Makefile that simplifies common development tasks. **We recommend using Makefile commands as the primary method** for running tests, formatting code, and checking code quality.

### Available Commands

```bash
# Installation
make install          # Install package and dependencies
make install-dev      # Install package with dev dependencies

# Testing
make test             # Run all tests (unit + integration)
make test-unit        # Run unit tests only (fast, no MongoDB)
make test-integration # Run integration tests only (requires Docker)
make test-coverage    # Run tests with coverage report (terminal)
make test-coverage-html # Run tests with HTML coverage report

# Code Quality
make lint             # Run linters (flake8, isort check) - fails on errors
make lint-check       # Run linters (non-failing, for CI)
make format           # Format code (black, isort)
make check            # Run lint + unit tests (quick quality check)

# Cleanup
make clean            # Remove build artifacts and caches
make clean-pyc        # Remove Python cache files
make clean-cache      # Remove pytest cache
```

### Common Workflows

**Quick quality check before committing:**
```bash
make check
```

**Run tests with coverage:**
```bash
make test-coverage-html
# Open htmlcov/index.html in your browser
```

**Format and lint before PR:**
```bash
make format
make lint
```

**Full test suite:**
```bash
make test
```

### Help

View all available commands:
```bash
make help
```

## Exception Handling Best Practices

**MDB Engine follows Miguel Grinberg's error handling framework.** All exception handling must adhere to these rules, which categorize errors into four types based on their origin and recoverability.

### The Four Error Types (Grinberg Framework)

Errors are categorized by two dimensions:
1. **Origin**: New (your code) vs. Bubbled-Up (from called function)
2. **Recoverability**: Recoverable (you can fix it) vs. Non-Recoverable (you can't fix it)

This creates four error types:

1. **Type 1: New Recoverable** - Your code found a problem and can fix it internally
   - Handle internally, don't raise
   - Example: Setting a default value when data is missing

2. **Type 2: Bubbled-Up Recoverable** - Error from called function, you can recover
   - Catch **specific exceptions only**, recover, continue
   - Example: Creating missing database record when lookup fails

3. **Type 3: New Non-Recoverable** - Your code found a problem it can't fix
   - Raise exception, let it bubble up
   - Example: Required field is missing

4. **Type 4: Bubbled-Up Non-Recoverable** - Error from called function, you can't recover
   - **Do nothing** - let it bubble up to framework handler
   - Example: Database connection failure in business logic

**Key Principle**: Most code should be Type 4 - don't catch exceptions unless you can recover. Let the framework handle errors at the top level.

### Why This Matters

Following this framework results in:
- **Cleaner code**: Less exception handling boilerplate
- **Better debugging**: Exceptions bubble up with full context
- **Framework integration**: Errors are handled consistently at the top level
- **Fewer bugs**: You don't accidentally hide errors in your own code

### The Dangers of Catching `Exception`

**NEVER catch `except Exception`. Be intentional about your exception handling.** Here's why:

1. **Hides Bugs in Your Own Code**: If you catch `Exception`, you'll catch bugs like `AttributeError`, `TypeError`, `KeyError` that indicate problems in your code. These should crash the application so you can fix them.

2. **Makes Debugging Harder**: When exceptions are caught and logged without context, you lose the stack trace and the ability to see where the error actually occurred.

3. **Prevents Framework from Handling Errors**: Frameworks like FastAPI have top-level exception handlers that:
   - Log errors with full stack traces
   - Return appropriate HTTP error responses
   - Roll back database transactions
   - Provide consistent error handling across the application

4. **Violates the Principle of Least Surprise**: Other developers expect exceptions to bubble up. Catching `Exception` breaks this expectation.

5. **Only Catches What You Can Recover From**: If you can't actually recover from an error, catching it is pointless. Let it bubble up to a level that can handle it.

### Always Be Specific

**`except Exception` is NEVER acceptable.** Even at top-level handlers, be intentional:
- **Framework-level exception handlers**: Define specific handlers for `PyMongoError`, `ValueError`, etc.
- **Top-level CLI handlers**: Catch `(ValueError, TypeError, IOError, RuntimeError)` - be explicit
- **Application entry points**: Know what exceptions can occur and catch them specifically

Being intentional means understanding what can fail and handling it appropriately.

### Decision-Making Guide

When deciding how to handle an error, ask:

1. **Is this error from my code or a function I called?**
   - My code → Type 1 or Type 3
   - Called function → Type 2 or Type 4

2. **Can I actually recover from this error?**
   - Yes → Type 1 or Type 2 (catch specific exceptions)
   - No → Type 3 or Type 4 (raise or do nothing)

3. **What specific exceptions can I recover from?**
   - Only catch exceptions you know how to handle
   - Never catch `Exception` unless at top-level

For detailed examples and patterns, see [`docs/guides/error_handling.md`](docs/guides/error_handling.md).

### Additional Guidelines

- **Never use bare `except:` clauses** - Always specify exception types
- **Preserve exception context** - Use `raise ... from e` when re-raising exceptions
- **Use `logger.exception()`** - Provides full tracebacks automatically
- **Use custom exceptions** - `MongoDBEngineError` and subclasses for domain errors
- **Don't swallow exceptions silently** - Always log or re-raise

For MongoDB operations, catch specific exceptions like `OperationFailure`, `ConnectionFailure`, `ServerSelectionTimeoutError` from `pymongo.errors`.

For authentication operations, catch specific exceptions like `jwt.ExpiredSignatureError`, `jwt.InvalidTokenError`, etc.

See [`docs/guides/error_handling.md`](docs/guides/error_handling.md) for detailed patterns and examples.

## Code Quality Checks

We use `make lint` and `make format` to ensure code quality. The checks include:

- Code formatting (Black)
- Import sorting (isort)
- Linting (flake8)
- Exception handling best practices (custom checker)

### Running Quality Checks

```bash
# Format code (auto-fixes formatting and imports)
make format

# Check code quality (does not modify files)
make lint

# Quick check (lint + unit tests)
make check
```

```bash
git commit --no-verify -m "Emergency fix (bypassing hooks)"
```

## Pre-push Hooks

We use Git pre-push hooks to automatically run code quality checks before pushing to GitHub. This ensures that all code is properly formatted and linted before it reaches the remote repository.

### Setup

**First-time setup** (or after cloning the repository):

```bash
# Install the hooks
./scripts/install-hooks.sh
```

This will copy hooks from the `githooks/` directory to `.git/hooks/`, making them active.

### What the Pre-push Hook Does

The pre-push hook automatically:

1. **Runs `make format`** - Formats your code with Black and isort
2. **Checks for formatting changes** - If formatting modified files, it blocks the push and asks you to commit the changes
3. **Runs `make lint`** - Checks for linting errors (flake8, isort, exception handling)
4. **Blocks the push** - If any check fails, the push is prevented

### Example Output

When you run `git push`, you'll see:

```bash
🔍 Running pre-push checks...
📝 Formatting code with 'make format'...
Formatting code...
✅ All linting checks passed!
🔍 Running linter with 'make lint'...
Checking flake8...
Checking import sorting (isort)...
Checking exception handling (Grinberg framework)...
✅ All linting checks passed!
✅ All pre-push checks passed!
```

If formatting changes files:

```bash
⚠️  Warning: 'make format' modified some files. Please review and commit the changes before pushing.
Modified files:
mdb_engine/core/example.py

You can review changes with: git diff
To commit the formatting changes: git add . && git commit -m 'Format code'
```

### Bypassing Hooks (Not Recommended)

If you need to bypass the hook in an emergency:

```bash
git push --no-verify
```

**Note**: Only use this for true emergencies. The hooks help maintain code quality.

### Sharing Hooks with Your Team

The hooks are stored in the `githooks/` directory, which is tracked in Git. When team members clone the repository, they should run:

```bash
./scripts/install-hooks.sh
```

This ensures everyone has the same hooks installed locally.

**Important**: Git hooks are **local-only** - they run on your machine, not on GitHub. However, by storing them in `githooks/` and providing the install script, we ensure all team members can easily set them up.

## Testing

### Running Tests

**We recommend using Makefile commands** for running tests. They provide consistent configuration and are easier to use:

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

If you need more control, you can run pytest directly:

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/unit/test_engine.py -v

# Run with specific marker
pytest -m unit -v
```

### Writing Tests

- Place unit tests in `tests/unit/`
- Place integration tests in `tests/integration/`
- Use descriptive test names: `test_function_name_scenario_expected_result`
- Mock external dependencies (MongoDB, APIs, etc.)
- Test both success and failure paths

### Test Example

```python
import pytest
from mdb_engine.database.abstraction import Collection
from pymongo.errors import OperationFailure

@pytest.mark.asyncio
async def test_insert_one_operation_failure_raises_mongodb_engine_error():
    """Test that OperationFailure is caught and re-raised as MongoDBEngineError."""
    collection = Collection(mock_scoped_collection)
    mock_scoped_collection.insert_one.side_effect = OperationFailure("Error", 1)

    with pytest.raises(MongoDBEngineError) as exc_info:
        await collection.insert_one({"test": "data"})

    assert "Failed to insert document" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None  # Context preserved
```

## Pull Request Process

1. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following the guidelines above

3. **Run code quality checks**:
   ```bash
   make format  # Auto-fix formatting
   make lint    # Check for issues
   ```

4. **Write/update tests** for your changes

5. **Update documentation** if needed

6. **Commit your changes** with descriptive messages:
   ```bash
   git commit -m "feat: add new feature X"
   git commit -m "fix: handle OperationFailure in insert_one"
   git commit -m "docs: update exception handling guidelines"
   ```

7. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

8. **Create a Pull Request** with:
   - Clear description of changes
   - Reference to related issues
   - Screenshots (if UI changes)
   - Test results

### Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style changes (formatting, etc.)
- `refactor:` Code refactoring
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

## Code Review Guidelines

### For Authors

- Keep PRs focused and small (< 500 lines when possible)
- Respond to review comments promptly
- Be open to feedback and suggestions
- Update PR based on feedback

### For Reviewers

- Be constructive and respectful
- Focus on code quality and maintainability
- Check exception handling patterns
- Verify tests are adequate
- Approve when satisfied

## Questions?

If you have questions about contributing, please:

1. Check existing issues and PRs
2. Read the documentation in `docs/`
3. Open an issue for discussion
4. Reach out to maintainers

Thank you for contributing to MDB Engine! 🚀
