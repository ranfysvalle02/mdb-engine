# Publishing to PyPI - Quick Guide

This guide shows you the **least effort** way to publish `mdb-engine` to PyPI.

## Prerequisites

1. **PyPI Account**: Create an account at https://pypi.org/account/register/
2. **API Token**: Generate a token at https://pypi.org/manage/account/token/
   - **Important**: Copy the token immediately - you won't see it again!

## Option 1: Automated Publishing via GitHub (Recommended - Least Effort)

### Setup (One-time)

1. **Add GitHub Secret**:
   - Go to your GitHub repo → Settings → Secrets and variables → Actions
   - Add secret: `PYPI_API_TOKEN` with your PyPI token

2. **Update package metadata** (if not done):
   - Edit `setup.py` and `pyproject.toml`:
     - Replace `"Your Name"` with your name
     - Replace `"your.email@example.com"` with your email
     - GitHub repo URL is already set to: `https://github.com/ranfysvalle02/mdb-engine`

### Publishing

**Method A: Create a GitHub Release**
```bash
# 1. Update version in setup.py and pyproject.toml
# 2. Commit and push
git add setup.py pyproject.toml
git commit -m "Bump version to 0.1.7"
git push

# 3. Create a GitHub release (via web UI or GitHub CLI)
gh release create v0.1.7 --title "v0.1.7" --notes "Release notes here"
```

The GitHub Action will automatically build and publish to PyPI when the release is published.

**Method B: Manual Workflow Trigger**
- Go to Actions → "Publish to PyPI" → Run workflow

## Option 2: Manual Publishing (Local)

```bash
# Set your PyPI token
export PYPI_API_TOKEN=your_pypi_token_here

# Build and publish
make publish
```

## Option 3: Using Makefile Commands

```bash
# Build only (check before publishing)
make build

# Check built packages
make build-check

# Publish to PyPI
export PYPI_API_TOKEN=your_pypi_token_here
make publish
```

## Version Bumping

Before publishing, update the version in **both** files:
- `setup.py`: `version="0.1.7"`
- `pyproject.toml`: `version = "0.1.7"`

## Checklist Before Publishing

- [ ] Version updated in `setup.py` and `pyproject.toml`
- [ ] Author/email/URL updated (if not done)
- [ ] Tests pass: `make test`
- [ ] Linting passes: `make lint`
- [ ] Build succeeds: `make build`
- [ ] Package check passes: `make build-check`

## Troubleshooting

**"Package name already exists"**
- The package name `mdb-engine` might be taken on PyPI
- Check: https://pypi.org/project/mdb-engine/
- If taken, update `name` in `setup.py` and `pyproject.toml`

**"403 Forbidden" or "Invalid token"**
- **Most common cause**: You ran `twine upload` directly without credentials
- **Solution**: Use the Makefile command which handles authentication:
  ```bash
  export PYPI_API_TOKEN=your_pypi_token_here
  make publish
  ```
- Make sure you're using a valid PyPI token from https://pypi.org/manage/account/token/
- Tokens start with `pypi-`
- If using command directly, you must provide credentials:
  ```bash
  python -m twine upload dist/* \
    --username __token__ \
    --password your_pypi_token_here
  ```

**"Version already exists"**
- Bump the version number and try again

## Post-Publishing

After successful publish:
- Verify: `pip install mdb-engine`
- Check PyPI page: `https://pypi.org/project/mdb-engine/`
- Update your README if needed
