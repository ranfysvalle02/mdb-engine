# Scripts

This directory contains utility scripts for development and maintenance.

## check_exception_handling.py

Custom pre-commit hook that enforces exception handling best practices.

### What it checks:

1. **Bare except clauses** - Catches `except:` without exception types (E722)
2. **Grinberg Type 4 violations** - Catches `except Exception` that only log without recovery
3. **Grinberg Type 2 violations** - Catches `except Exception` used for recovery (should catch specific exceptions)
4. **Missing exception context** - Checks for `raise ... from e` pattern when re-raising
5. **Top-level handler detection** - Allows `except Exception` only in framework-level handlers

This script enforces Miguel Grinberg's four error types framework:
- **Type 1**: New Recoverable - Handle internally
- **Type 2**: Bubbled-Up Recoverable - Catch specific exceptions, recover
- **Type 3**: New Non-Recoverable - Raise exception
- **Type 4**: Bubbled-Up Non-Recoverable - Do nothing (most common)

See `docs/guides/error_handling.md` for full documentation.

### Usage:

```bash
# Check specific files
python scripts/check_exception_handling.py file1.py file2.py

# Run as pre-commit hook (automatic)
pre-commit run check-exception-handling --all-files
```

### Exclusions:

- Test files (`test_*.py`) - May have different exception handling patterns
- Example files (`examples/`) - May have simpler patterns for demonstration

### Integration:

This script is automatically run as part of the pre-commit hooks. See `.pre-commit-config.yaml` for configuration.
