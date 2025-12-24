# Contributing to MDB Engine

Thank you for your interest in contributing to MDB Engine! This document provides guidelines and best practices for contributing code.

## Table of Contents

- [Code Style](#code-style)
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

# Install pre-commit hooks (recommended)
pip install pre-commit
pre-commit install
```

### Manual Formatting

```bash
# Format code
black --line-length=100 .

# Sort imports
isort --profile black --line-length=100 .

# Run linting (recommended - uses Makefile)
make lint-check

# Or run flake8 directly
flake8 mdb_engine tests
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

**Never catch `except Exception` unless you're at the top-level framework handler.** Here's why:

1. **Hides Bugs in Your Own Code**: If you catch `Exception`, you'll catch bugs like `AttributeError`, `TypeError`, `KeyError` that indicate problems in your code. These should crash the application so you can fix them.

2. **Makes Debugging Harder**: When exceptions are caught and logged without context, you lose the stack trace and the ability to see where the error actually occurred.

3. **Prevents Framework from Handling Errors**: Frameworks like FastAPI have top-level exception handlers that:
   - Log errors with full stack traces
   - Return appropriate HTTP error responses
   - Roll back database transactions
   - Provide consistent error handling across the application

4. **Violates the Principle of Least Surprise**: Other developers expect exceptions to bubble up. Catching `Exception` breaks this expectation.

5. **Only Catches What You Can Recover From**: If you can't actually recover from an error, catching it is pointless. Let it bubble up to a level that can handle it.

### When `except Exception` is Acceptable

`except Exception` is **ONLY** allowed at:
- **Framework-level exception handlers**: FastAPI's `@app.exception_handler(Exception)`, Flask's error handlers
- **Top-level CLI handlers**: `if __name__ == '__main__': try/except Exception` blocks
- **Application entry points**: Where the application starts and needs to prevent crashes

These are the "safety nets" that prevent the application from crashing. All other code should let exceptions bubble up.

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

## Pre-commit Hooks

We use pre-commit hooks to automatically check code quality before commits. The hooks check for:

- Code formatting (Black)
- Import sorting (isort)
- Linting (flake8)
- Exception handling best practices (custom checker)

### Running Pre-commit Hooks Manually

```bash
# Run on all files
pre-commit run --all-files

# Run on staged files only
pre-commit run

# Run specific hook
pre-commit run flake8 --all-files
pre-commit run check-exception-handling --all-files
```

### Bypassing Pre-commit Hooks

**Only bypass hooks in emergencies** and document why:

```bash
git commit --no-verify -m "Emergency fix (bypassing hooks)"
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=mdb_engine --cov-report=html

# Run specific test file
pytest tests/unit/test_engine.py

# Run with verbose output
pytest -v
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

3. **Run pre-commit hooks**:
   ```bash
   pre-commit run --all-files
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

