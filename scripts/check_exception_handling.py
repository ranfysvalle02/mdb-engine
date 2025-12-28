#!/usr/bin/env python3
"""
Custom pre-commit hook to enforce exception handling best practices.

This script enforces Miguel Grinberg's error handling framework:
1. Type 1: New Recoverable - Handle internally, don't raise
2. Type 2: Bubbled-Up Recoverable - Catch specific exceptions, recover, continue
3. Type 3: New Non-Recoverable - Raise exception, let it bubble up
4. Type 4: Bubbled-Up Non-Recoverable - Do nothing, let it bubble up

This script checks for:
1. Bare except clauses (except:)
2. Overly broad Exception catches outside of top-level handlers
3. Exception handlers that only log without recovery (Type 4 violations)
4. Missing exception context preservation (raise ... from e)
5. Swallowed exceptions without logging or re-raising

Usage:
    python scripts/check_exception_handling.py file1.py file2.py ...
    Or use as pre-commit hook (automatically called with filenames)
"""

import ast
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple


def _has_raise(node: ast.ExceptHandler) -> Tuple[bool, bool]:
    """
    Check if handler raises an exception.
    Returns: (has_raise, has_raise_from) - whether it raises and if it uses 'from'
    """
    has_raise = False
    has_raise_from = False

    for stmt in ast.walk(node):
        if isinstance(stmt, ast.Raise):
            has_raise = True
            if stmt.cause is not None:
                has_raise_from = True
            break

    return has_raise, has_raise_from


def _has_logger_exception(node: ast.ExceptHandler) -> bool:
    """Check if handler uses logger.exception() or logger.error() with exc_info=True."""
    for stmt in ast.walk(node):
        if isinstance(stmt, ast.Call):
            if isinstance(stmt.func, ast.Attribute):
                if stmt.func.attr == "exception":
                    return True
                elif stmt.func.attr == "error" and any(
                    kw.arg == "exc_info"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value
                    for kw in (stmt.keywords or [])
                ):
                    return True
    return False


def _has_recovery(node: ast.ExceptHandler) -> bool:
    """
    Check if handler recovers from the error (Type 2 recoverable error).
    A handler recovers if it:
    - Returns a meaningful value (not just None/False/empty without context)
    - Sets a default value and continues
    - Retries the operation
    - Transforms the error into a different return value
    """
    # Check for assignments (setting defaults)
    has_assignment = False
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            has_assignment = True
            break

    # Check for meaningful returns (not just None/False without context)
    has_meaningful_return = False
    for stmt in node.body:
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                # Returning None might be recovery if there's assignment before
                has_meaningful_return = has_assignment
            elif isinstance(stmt.value, (ast.Constant, ast.NameConstant)):
                # Returning False/empty collections might be recovery
                if stmt.value.value not in (None, False, [], {}):
                    has_meaningful_return = True
                elif has_assignment:
                    # If we set a default, returning None/False might be recovery
                    has_meaningful_return = True
            else:
                # Returning a computed value is recovery
                has_meaningful_return = True
            break

    return has_assignment or has_meaningful_return


def _is_top_level_handler(
    node: ast.FunctionDef, decorators: List[ast.expr], function_name: str
) -> bool:
    """
    Check if this is a top-level exception handler (framework-level).
    Top-level handlers are allowed to catch Exception.
    """
    # Check for FastAPI exception handler decorator
    for decorator in decorators:
        if isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Attribute):
                if decorator.func.attr == "exception_handler":
                    # Check if it's @app.exception_handler(Exception)
                    if len(decorator.args) > 0:
                        if isinstance(decorator.args[0], ast.Name):
                            if decorator.args[0].id == "Exception":
                                return True

    # Check for common exception handler function names
    handler_names = [
        "global_exception_handler",
        "handle_exception",
        "exception_handler",
        "error_handler",
    ]
    if function_name.lower() in handler_names:
        return True

    return False


def _is_in_main_block(node: ast.AST, tree: ast.Module) -> bool:
    """Check if node is inside an if __name__ == '__main__': block."""
    for stmt in tree.body:
        if isinstance(stmt, ast.If):
            if isinstance(stmt.test, ast.Compare):
                if (
                    isinstance(stmt.test.left, ast.Name)
                    and stmt.test.left.id == "__name__"
                    and len(stmt.test.comparators) == 1
                    and isinstance(stmt.test.comparators[0], ast.Constant)
                    and stmt.test.comparators[0].value == "__main__"
                ):
                    # Check if our node is in this block
                    for child in ast.walk(stmt):
                        if child == node:
                            return True
    return False


class ExceptionHandlerChecker(ast.NodeVisitor):
    """
    AST visitor to check exception handling patterns following Grinberg's framework.
    """

    def __init__(self, filename: str, tree: ast.Module):
        self.filename = filename
        self.tree = tree
        self.errors: List[Tuple[int, str]] = []
        self.current_function: Optional[ast.FunctionDef] = None
        self.current_decorators: List[ast.expr] = []
        self.top_level_handlers: Set[str] = set()

    def visit_FunctionDef(self, node):
        """Track current function name and decorators."""
        old_function = self.current_function
        old_decorators = self.current_decorators
        self.current_function = node
        self.current_decorators = node.decorator_list

        # Check if this is a top-level handler
        if _is_top_level_handler(node, node.decorator_list, node.name):
            self.top_level_handlers.add(node.name)

        self.generic_visit(node)
        self.current_function = old_function
        self.current_decorators = old_decorators

    def visit_AsyncFunctionDef(self, node):
        """Track current async function name and decorators."""
        old_function = self.current_function
        old_decorators = self.current_decorators
        self.current_function = node
        self.current_decorators = node.decorator_list

        # Check if this is a top-level handler
        if _is_top_level_handler(node, node.decorator_list, node.name):
            self.top_level_handlers.add(node.name)

        self.generic_visit(node)
        self.current_function = old_function
        self.current_decorators = old_decorators

    def visit_ExceptHandler(self, node):
        """Check exception handler patterns following Grinberg's framework."""
        line_no = node.lineno

        # Check for bare except clause (always an error)
        if node.type is None:
            self.errors.append(
                (
                    line_no,
                    "E722: Bare except clause found. Use specific exception types "
                    "(e.g., except ValueError as e:). This violates Grinberg's framework.",
                )
            )
            self.generic_visit(node)
            return

        # Check if this is catching Exception
        is_exception_catch = False
        if isinstance(node.type, ast.Name) and node.type.id == "Exception":
            is_exception_catch = True

        # Allow Exception catching in top-level handlers
        if is_exception_catch and self.current_function:
            function_name = (
                self.current_function.name
                if isinstance(
                    self.current_function, (ast.FunctionDef, ast.AsyncFunctionDef)
                )
                else None
            )
            if function_name and function_name in self.top_level_handlers:
                # This is a top-level handler, allow Exception catching
                self.generic_visit(node)
                return

            # Also allow in if __name__ == '__main__' blocks
            if _is_in_main_block(node, self.tree):
                self.generic_visit(node)
                return

        # For Exception catches outside top-level handlers, check if it's Type 4 violation
        if is_exception_catch:
            has_raise, has_raise_from = _has_raise(node)
            has_logger = _has_logger_exception(node)
            has_recovery = _has_recovery(node)

            # Type 4 violation: Catching Exception just to log it (should do nothing instead)
            if has_logger and not has_recovery and not has_raise:
                self.errors.append(
                    (
                        line_no,
                        "GRINBERG_TYPE4: Catching Exception only to log it. "
                        "This is a Type 4 error (bubbled-up non-recoverable) - "
                        "do nothing and let it bubble up to framework handler. "
                        "Remove this try/except block.",
                    )
                )

            # Type 4 violation: Catching Exception without recovery or re-raising
            elif not has_recovery and not has_raise:
                self.errors.append(
                    (
                        line_no,
                        "GRINBERG_TYPE4: Catching Exception without recovery or re-raising. "
                        "This is a Type 4 error (bubbled-up non-recoverable) - "
                        "do nothing and let it bubble up. Remove this try/except block "
                        "or catch specific exceptions if you can recover (Type 2).",
                    )
                )

            # Type 2: If recovering, should catch specific exceptions
            elif has_recovery and not has_raise:
                self.errors.append(
                    (
                        line_no,
                        "GRINBERG_TYPE2: Catching Exception for recovery. "
                        "This is a Type 2 error (bubbled-up recoverable) - "
                        "catch specific exception types instead of Exception. "
                        "Only catch exceptions you know how to recover from.",
                    )
                )

            # If re-raising, check for proper exception context
            elif has_raise and not has_raise_from:
                # Check if it's raising a new exception (should use 'from')
                for stmt in ast.walk(node):
                    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
                        # If raising a new exception (not bare raise), should use 'from'
                        if not (
                            isinstance(stmt.exc, ast.Name)
                            and stmt.exc.id in ("exc", "e", "error", "exception")
                        ):
                            self.errors.append(
                                (
                                    line_no,
                                    "GRINBERG_CONTEXT: Re-raising exception without 'from'. "
                                    "Use 'raise NewException(...) from e' to preserve chain.",
                                )
                            )
                        break

        # Check for specific exception types that should be caught for MongoDB operations
        if isinstance(node.type, ast.Name):
            exc_type = node.type.id
            function_name = (
                self.current_function.name
                if self.current_function
                and isinstance(
                    self.current_function, (ast.FunctionDef, ast.AsyncFunctionDef)
                )
                else None
            )
            # MongoDB operations should catch MongoDB-specific exceptions
            if function_name and any(
                op in function_name.lower()
                for op in [
                    "insert",
                    "update",
                    "delete",
                    "find",
                    "count",
                    "aggregate",
                    "create_index",
                ]
            ):
                if exc_type == "Exception" and "mongodb" not in self.filename.lower():
                    # This is already caught above, but add specific guidance
                    pass

        self.generic_visit(node)


def check_file(filepath: Path) -> List[Tuple[int, str]]:
    """Check a single Python file for exception handling issues."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content, filename=str(filepath))
        checker = ExceptionHandlerChecker(str(filepath), tree)
        checker.visit(tree)

        return checker.errors
    except SyntaxError as e:
        return [(e.lineno or 1, f"Syntax error: {e.msg}")]
    except Exception as e:
        # Top-level exception handler for the checker itself (Type 4 - let it bubble)
        return [(1, f"Error checking file: {e}")]


def main():
    """Main entry point for the checker."""
    if len(sys.argv) < 2:
        print("Usage: check_exception_handling.py <file1> [file2] ...")
        sys.exit(1)

    all_errors = []
    files_checked = []

    for filepath_str in sys.argv[1:]:
        filepath = Path(filepath_str)

        # Skip non-Python files
        if not filepath.suffix == ".py":
            continue

        # Skip test files (they may have different exception handling patterns)
        if "test_" in filepath.name or filepath.name.startswith("test"):
            continue

        # Skip example files (they may have simpler exception handling)
        if "examples" in str(filepath):
            continue

        files_checked.append(filepath)
        errors = check_file(filepath)

        if errors:
            all_errors.extend([(filepath, line, msg) for line, msg in errors])

    if all_errors:
        print("\n❌ Exception Handling Issues Found:\n")
        for filepath, line, msg in all_errors:
            print(f"  {filepath}:{line}: {msg}")
        print(f"\nTotal: {len(all_errors)} issue(s) in {len(files_checked)} file(s)\n")
        sys.exit(1)
    else:
        if files_checked:
            print(
                f"✅ Exception handling checks passed for {len(files_checked)} file(s)"
            )
        sys.exit(0)


if __name__ == "__main__":
    main()
