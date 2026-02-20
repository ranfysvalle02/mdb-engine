# Git Hooks - Staff Engineer Level

This directory contains optimized Git hooks that provide fast, incremental code quality checks. These hooks are designed for performance and developer experience.

## Installation

Run the install script to copy these hooks to `.git/hooks/`:

```bash
./scripts/install-hooks.sh
```

## Available Hooks

### pre-commit

**Optimized for performance:** Only checks staged files, not the entire codebase.

Runs before committing. Automatically formats and lints code:
- Auto-formats **only staged Python files** (performance optimization)
- Stages formatted files automatically
- Runs linting checks on **only staged files** (performance optimization)

**Performance Features:**
- ✅ Incremental checks (only staged files)
- ✅ Fast execution (< 5 seconds for typical changes)
- ✅ Automatic tool installation if missing
- ✅ Clear, colored error messages

**What it does:**
1. Detects staged Python files
2. Auto-formats staged files with `ruff format`
3. Stages any formatting changes automatically
4. Runs `ruff check` on staged files only

**Skip the hook (not recommended):**
```bash
SKIP_HOOKS=1 git commit
```

### pre-push

**Optimized for performance:** Only checks changed files between local and remote branches.

Runs before pushing to GitHub. Verifies code quality (read-only checks):
- Verifies formatting on **only changed files** (performance optimization)
- Verifies code quality on **only changed files** (performance optimization)
- Runs security scan (Semgrep) if available

**Performance Features:**
- ✅ Incremental checks (only changed files)
- ✅ Fast execution (< 10 seconds for typical changes)
- ✅ Skips checks if no Python files changed
- ✅ Clear, colored progress indicators

**What it does:**
1. Detects Python files changed between local and remote branches
2. Verifies formatting with `ruff format --check` (no auto-fix)
3. Verifies code quality with `ruff check` (no auto-fix)
4. Runs Semgrep security scan (if available, non-blocking)

**Note:** Formatting and linting should be handled by the pre-commit hook. This is a final verification step. Integration tests are handled by CI/CD, not the pre-push hook.

**Skip the hook (not recommended):**
```bash
SKIP_HOOKS=1 git push
```

## Performance Optimizations

### Incremental Checks
Both hooks use **incremental checking** - they only process files that have changed, not the entire codebase. This makes them:
- ⚡ **Fast**: Typically < 5-10 seconds
- 💰 **Efficient**: Minimal CPU/memory usage
- 🎯 **Focused**: Only relevant files are checked

### Smart File Detection
- **pre-commit**: Checks only staged Python files
- **pre-push**: Checks only Python files that differ between local and remote branches
- Both hooks skip entirely if no Python files are affected

### Caching
- Ruff uses built-in caching for faster subsequent runs
- Tools are auto-installed if missing (with helpful error messages)

## Troubleshooting

### Common Issues

**"ruff not found"**
- The hook will try to install dev dependencies automatically
- Or run manually: `pip install -e ".[dev]"`

**Pre-commit: "Formatting failed"**
```bash
make format          # Auto-fix formatting
git add .            # Stage fixes
git commit           # Commit again
```

**Pre-commit: "Linting failed"**
```bash
make fix             # Auto-fix linting issues
make lint-local       # See all errors
git add .
git commit
```

**Pre-push: "Formatting check failed"**
```bash
make format
git add .
git commit --amend --no-edit  # Add formatting to last commit
git push
```

**Pre-push: "Code quality check failed"**
```bash
make lint-local       # See issues
# Fix issues, then:
git add .
git commit --amend --no-edit
git push
```

### Performance Issues

If hooks are slow:
1. Check if you have many staged/changed files
2. Ensure Ruff cache is working: `ruff cache clean` then retry
3. Verify tools are installed: `which ruff`

### Debugging

To see what files are being checked:
```bash
# Pre-commit: See staged files
git diff --cached --name-only

# Pre-push: See changed files
git diff origin/main...HEAD --name-only
```

## Adding New Hooks

1. Add your hook script to this directory (`githooks/`)
2. Make it executable: `chmod +x githooks/your-hook-name`
3. Update this README with documentation
4. Run `./scripts/install-hooks.sh` to install it
5. Commit both the hook and the updated install script

## Best Practices

1. **Always run hooks**: They catch issues early and save CI time
2. **Fix issues locally**: Don't skip hooks to push broken code
3. **Use incremental checks**: The hooks are optimized - let them do their job
4. **Keep tools updated**: Run `pip install -e ".[dev]"` periodically

## Note

Git hooks are **local-only** - they run on your machine, not on GitHub. However, by storing them here and providing the install script, we ensure all team members can easily set them up with consistent, optimized behavior.
