# Error Handling Guide

This guide explains the error handling framework used in MDB Engine, based on [Miguel Grinberg's Ultimate Guide to Error Handling in Python](https://miguelgrinberg.com/post/the-ultimate-guide-to-error-handling-in-python).

## The Four Error Types

Errors are categorized by two dimensions:
1. **Origin**: New (your code) vs. Bubbled-Up (from called function)
2. **Recoverability**: Recoverable (you can fix it) vs. Non-Recoverable (you can't fix it)

This creates four error types:

| | New Error | Bubbled-Up Error |
|---|---|---|
| **Recoverable** | Type 1: Handle internally | Type 2: Catch specific, recover |
| **Non-Recoverable** | Type 3: Raise exception | Type 4: Do nothing |

## Type 1: New Recoverable Errors

**Your code found a problem and can fix it internally.**

### When to Use
- You detect an error condition in your own code
- You know how to fix it and continue
- No need to notify callers

### Approach
- Fix the problem internally without raising an exception
- Continue with normal execution
- No exception handling needed

### Key Points
- No exception handling needed
- Fix the problem and continue
- Don't raise an exception

## Type 2: Bubbled-Up Recoverable Errors

**Error from a function you called, but you can recover from it.**

### When to Use
- A function you called raised an exception
- You know how to recover from this specific error
- You can continue execution after recovery

### Approach
- Catch **specific exception types only** (never `Exception`)
- Actually recover (set default, retry, create missing resource)
- Continue with normal execution

### Key Points
- **Catch specific exceptions only** - not `Exception`
- Must actually recover (set default, retry, create missing resource)
- If you can't recover, this becomes Type 4

### Common Mistakes
- Catching `Exception` for recovery (too broad - catch specific types)
- Catching but only logging (not actually recovering - should be Type 4)

## Type 3: New Non-Recoverable Errors

**Your code found a problem it cannot fix.**

### When to Use
- You detect an error condition in your own code
- You don't know how to fix it at this level
- Caller needs to know about the error

### Approach
- Raise an appropriate exception
- Let it bubble up to caller
- Use appropriate exception type (built-in or custom)

### Key Points
- Raise an exception
- Let it bubble up to caller
- Use appropriate exception type (built-in or custom)

## Type 4: Bubbled-Up Non-Recoverable Errors

**Error from a function you called, and you cannot recover from it.**

### When to Use
- A function you called raised an exception
- You don't know how to fix it
- **Most common pattern** - most code should be Type 4

### Approach
- **Do nothing** - no try/except block
- Let exceptions bubble up to framework
- Framework will handle logging, HTTP responses, rollbacks

### Key Points
- **Do nothing** - no try/except block
- Let exceptions bubble up to framework
- Framework will handle logging, HTTP responses, rollbacks

### Common Mistakes
- Catching `Exception` just to log it (pointless - remove try/except)
- Catching `Exception` without recovery or re-raising (should do nothing)

## Decision Tree

Use this decision tree to determine which type of error handling to use:

```
Is the error from your code or a called function?
├─ Your code
│  ├─ Can you fix it? → Type 1: Fix internally
│  └─ Can't fix it? → Type 3: Raise exception
│
└─ Called function
   ├─ Can you recover? → Type 2: Catch specific, recover
   └─ Can't recover? → Type 4: Do nothing
```

## The Dangers and Risks of Catching `Exception`

**Never catch `except Exception` unless you're at the top-level framework handler.** Here's why this is dangerous:

### 1. Hides Bugs in Your Own Code

When you catch `Exception`, you'll catch bugs like:
- `AttributeError` - accessing non-existent attributes
- `TypeError` - wrong types passed to functions
- `KeyError` - missing dictionary keys
- `IndexError` - list index out of range
- `NameError` - undefined variables

These indicate **problems in your code** that should crash the application so you can fix them. Catching `Exception` hides these bugs, making them harder to find and fix.

### 2. Makes Debugging Harder

When exceptions are caught and logged without context:
- You lose the stack trace showing where the error actually occurred
- You can't see the call chain that led to the error
- Debugging becomes a guessing game

The framework's top-level handler will log errors with full stack traces, making debugging much easier.

### 3. Prevents Framework from Handling Errors Properly

Frameworks like FastAPI have top-level exception handlers that:
- Log errors with full stack traces automatically
- Return appropriate HTTP error responses (500, 503, etc.)
- Roll back database transactions (if configured)
- Provide consistent error handling across the application

When you catch `Exception` in business logic, you prevent the framework from doing its job.

### 4. Violates the Principle of Least Surprise

Other developers expect exceptions to bubble up. When you catch `Exception`:
- It's unexpected behavior
- Makes code harder to understand
- Breaks the mental model of how errors flow through the application

### 5. Only Catches What You Can Recover From

If you can't actually recover from an error, catching it is pointless. The error will either:
- Be silently ignored (bad - hides bugs)
- Be logged and re-raised (pointless - just let it bubble up)
- Cause incorrect behavior (returning wrong values, etc.)

### 6. Makes Code Less Maintainable

Code with `except Exception` scattered throughout:
- Is harder to test (exceptions are swallowed)
- Is harder to debug (errors are hidden)
- Is harder to maintain (unclear error handling logic)

## When `except Exception` is Acceptable

`except Exception` is **ONLY** allowed at:

### Framework-Level Exception Handlers

These are the "safety nets" that prevent the application from crashing:
- FastAPI's `@app.exception_handler(Exception)`
- Flask's error handlers
- Django's middleware exception handlers

These handlers are at the top level of the application and are responsible for:
- Logging all unhandled exceptions
- Returning appropriate error responses
- Ensuring the application doesn't crash

### Top-Level CLI Handlers

In CLI applications, the entry point should catch all exceptions:

```python
if __name__ == '__main__':
    try:
        my_cli()
    except Exception as error:
        print(f"Unexpected error: {error}")
        sys.exit(1)
```

This ensures the CLI application exits gracefully instead of crashing.

### Application Entry Points

Where the application starts and needs to prevent crashes. These are the outermost exception handlers.

**Key Point**: These are the only places where `except Exception` is acceptable. All other code should let exceptions bubble up.

## Top-Level Exception Handlers

Framework-level handlers catch all exceptions and handle them appropriately. These handlers:

- Log errors with full stack traces
- Return appropriate HTTP error responses
- Roll back database transactions (if configured)
- Provide consistent error handling across the application

**Key Point**: `except Exception` is ONLY allowed at these top-level handlers.

## Common Patterns (Conceptual)

### Database Operations (Type 4)

Most database operations should let errors bubble up. The framework will:
- Log the error with full stack trace
- Return appropriate HTTP error response
- Roll back transactions if configured

### Missing Resource Recovery (Type 2)

When you can recover from a missing resource:
- Catch the specific exception (e.g., `NotFound`)
- Create the missing resource
- Continue with the operation

### Validation (Type 3)

When validation fails:
- Raise an appropriate exception (e.g., `ValueError`, `ValidationError`)
- Let it bubble up to the framework
- Framework will return appropriate error response

### Default Values (Type 1)

When data is missing but you can provide defaults:
- Check for missing data
- Set default values
- Continue with the operation

## Anti-Patterns to Avoid

### ❌ Catching Exception Just to Log

**Problem**: Catching `Exception` just to log it is pointless. The framework will log it with better context.

**Fix**: Remove the try/except block (Type 4).

### ❌ Catching Exception for Recovery

**Problem**: Catching `Exception` is too broad. You don't know what exceptions you're catching.

**Fix**: Catch specific exceptions (Type 2).

### ❌ Silent Failures

**Problem**: Catching `Exception` and doing nothing hides bugs.

**Fix**: Either recover properly or let it bubble up.

### ❌ Losing Exception Context

**Problem**: Re-raising exceptions without preserving the original exception chain loses debugging information.

**Fix**: Use `raise ... from e` to preserve the exception chain.

## Summary

- **Type 1**: Fix internally, don't raise
- **Type 2**: Catch specific exceptions, recover
- **Type 3**: Raise exception, let it bubble
- **Type 4**: Do nothing, let it bubble (most common)

**Remember**: Most code should be Type 4. Only catch exceptions when you can actually recover from them. Never catch `Exception` unless you're at the top-level framework handler.

## References

- [Miguel Grinberg's Ultimate Guide to Error Handling in Python](https://miguelgrinberg.com/post/the-ultimate-guide-to-error-handling-in-python)
- [CONTRIBUTING.md](../../CONTRIBUTING.md) - Project-specific exception handling rules
