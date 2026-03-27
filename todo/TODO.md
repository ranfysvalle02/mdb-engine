# Developer Experience Enhancements for mdb-engine

## 1. Wire Up the CLI Entry Point (High Impact, Low Effort)

CLI commands already exist (`validate`, `migrate`) but there is no `[project.scripts]` in `pyproject.toml` and no `mdb_engine/cli/__init__.py` tying them together. Developers cannot run `mdb validate manifest.json` from the terminal today.

- Create [`mdb_engine/cli/__init__.py`](mdb_engine/cli/__init__.py) with a Click group that registers `validate` and `migrate` as subcommands

- Add to [`pyproject.toml`](pyproject.toml):

  ```toml
  [project.scripts]
  mdb = "mdb_engine.cli:cli"
  ```

- This unlocks `mdb validate`, `mdb migrate`, and future commands like `mdb init`, `mdb scaffold`, `mdb doctor`

## 2. `mdb init` — Project Scaffolding Command (High Impact, Medium Effort)

There is no way to bootstrap a new mdb-engine project from the CLI. Developers must copy from `examples/`. A scaffolding command would dramatically lower the onboarding bar.

- `mdb init my_app` generates a minimal project structure:

  - `manifest.json` (valid v2.0 schema)

  - `web.py` (with `quickstart()` boilerplate)

  - `.env.example` (with `MONGODB_URI`, `MDB_DB_NAME`, etc.)

  - `requirements.txt` referencing `mdb-engine`

- Optionally accept flags: `--with-memory`, `--with-graph`, `--with-osi`, `--with-auth` to include config stubs for those features

- Leverage the existing [`scaffold.py`](mdb_engine/osi/scaffold.py) pattern for OSI model generation when `--with-osi` is used

## 3. `mdb doctor` — Environment Health Check Command (High Impact, Medium Effort)

Debugging environment issues (wrong MongoDB URI, missing optional deps, misconfigured manifests) is a common pain point. A single diagnostic command would save significant time.

- Check MongoDB connectivity (using the configured URI or `MONGODB_URI` env var)

- Verify all required and optional dependencies are installed (e.g., `openai`, `google-genai`, `voyageai`, `pyyaml`, `tiktoken`)

- Validate the manifest if one is found in the current directory

- Report Python version compatibility

- Check for common misconfigurations (e.g., `DEBUG=true` in production, missing `JWT_SECRET`)

## 4. Add CLI Tests (Medium Impact, Low Effort)

The `validate` and `migrate` commands in [`mdb_engine/cli/commands/`](mdb_engine/cli/commands/) have zero test coverage. Click's `CliRunner` makes this straightforward.

- Add `tests/unit/test_cli_validate.py` and `tests/unit/test_cli_migrate.py`

- Test happy paths (valid manifest, successful migration)

- Test error paths (missing file, invalid JSON, invalid schema)

- Test `--verbose`, `--in-place`, `--output`, `--target-version` flags

## 5. Better Startup Error Messages with Suggestions (Medium Impact, Low Effort)

When initialization fails (bad MongoDB URI, missing manifest fields, invalid scoping config), errors should include actionable suggestions rather than just tracebacks.

- Audit the error paths in [`mdb_engine/core/app_lifecycle.py`](mdb_engine/core/app_lifecycle.py) and [`mdb_engine/core/service_initialization.py`](mdb_engine/core/service_initialization.py)

- Add "Did you mean?" suggestions for common manifest typos (e.g., `auth_mode` vs `authentication.mode`)

- Add a "Quick fix" hint to each validation error (e.g., "Add `schema_version: \"2.0\"` to your manifest")

- Log a startup summary banner showing which services were initialized and which were skipped (and why)

## 6. Startup Summary Banner (Medium Impact, Low Effort)

When the engine boots, print a clear summary of what was configured — similar to how FastAPI prints its URL, or how Next.js prints its build output.

Example:

```
 MDB Engine v0.7.9  |  App: my_app
 MongoDB:    mongodb://localhost:27017/mdb_engine  (connected)
 Services:   memory (enabled), graph (enabled), osi (3 models loaded)
 Auth:       jwt (mode: local)
 Routes:     12 registered  |  Docs: http://localhost:8000/docs
```

This gives developers immediate confidence their config is correct.

## 7. Drop `setup.py` in Favor of `pyproject.toml`-Only (Low Impact, Low Effort)

Both [`setup.py`](setup.py) and [`pyproject.toml`](pyproject.toml) exist. Since the project targets Python >=3.10 and uses setuptools with PEP 621 metadata, `setup.py` is redundant and a maintenance burden.

- Remove `setup.py`

- Ensure all metadata and entry points live in `pyproject.toml`

- Verify `pip install -e .` still works without `setup.py`

## 8. Add `.editorconfig` (Low Impact, Low Effort)

No `.editorconfig` exists. Adding one ensures consistent formatting across editors (tab size, trailing whitespace, final newline) without requiring every contributor to configure their editor.

```ini
root = true

[*]
indent_style = space
indent_size = 4
end_of_line = lf
charset = utf-8
trim_trailing_whitespace = true
insert_final_newline = true

[*.{json,yaml,yml,toml}]
indent_size = 2

[Makefile]
indent_style = tab
```

## 9. Adopt the `pre-commit` Framework (Low Impact, Medium Effort)

Custom git hooks exist in [`githooks/`](githooks/) but they're harder to maintain and share than the standard `pre-commit` framework. Migrating would:

- Let contributors install hooks with `pre-commit install` (standard workflow)

- Allow running `pre-commit run --all-files` in CI

- Make it easy to add new checks (trailing whitespace, YAML lint, TOML lint, etc.)

- Replace `scripts/install-hooks.sh` with the standard mechanism

## 10. Improve Type Coverage Incrementally (Medium Impact, High Effort)

Mypy reports ~661 errors across 91 files. Rather than fixing them all at once:

- Add a `make typecheck-baseline` command that tracks the current error count

- Use `mypy --strict` on new/changed files only (via a CI diff check)

- Target the most-imported modules first: `core/`, `dependencies.py`, `database/`

- Add `py.typed` marker so downstream consumers get type info

## 11. Interactive Manifest Builder (Medium Impact, High Effort)

Manifests are the primary configuration surface but can be daunting. An interactive `mdb manifest` command could walk developers through building one:

- Prompt for app slug, name, description

- Ask which features to enable (memory, graph, auth, osi)

- Generate valid JSON with comments explaining each section

- Validate the result before writing

## 12. Dev Server with Watch Mode (Medium Impact, High Effort)

A `mdb dev` command that wraps uvicorn with auto-reload, similar to `next dev` or `flask run --reload`:

- Auto-detect the app entry point (`web.py`, `app.py`, `main.py`)

- Watch for `manifest.json` changes and re-validate on save

- Watch for OSI YAML changes and reload models

- Show the startup banner (item 6) on each reload

---

## Summary Matrix

| Enhancement | Impact | Effort | Priority |
|---|---|---|---|
| Wire up CLI entry point | High | Low | P0 |
| `mdb init` scaffolding | High | Medium | P0 |
| `mdb doctor` health check | High | Medium | P1 |
| CLI tests | Medium | Low | P1 |
| Better startup error messages | Medium | Low | P1 |
| Startup summary banner | Medium | Low | P1 |
| Drop `setup.py` | Low | Low | P2 |
| Add `.editorconfig` | Low | Low | P2 |
| Adopt `pre-commit` framework | Low | Medium | P2 |
| Incremental type coverage | Medium | High | P2 |
| Interactive manifest builder | Medium | High | P3 |
| Dev server with watch mode | Medium | High | P3 |

---

## OSI Developer Experience Improvements

Remaining DX improvements specific to the OSI (Open Semantic Interchange) module.

### 13. OSI CLI Commands (High Impact, Medium Effort)

No CLI tooling exists for OSI. Developers must use the Python API or hit the HTTP endpoints. Adding CLI commands would dramatically improve the edit-validate-test loop.

- `mdb osi validate <file.yaml>` -- validate a YAML file and print errors/warnings to stdout
- `mdb osi scaffold --app-slug <slug> --node-types actor,movie,genre` -- generate a starter YAML model
- `mdb osi export --app-slug <slug>` -- export graph knowledge as OSI YAML
- Support `--json` output for CI/CD integration
- Create [`mdb_engine/osi/cli.py`](mdb_engine/osi/cli.py) with Click commands, register under the main `mdb` CLI group

### 14. Pydantic Response Models on OSI API Routes (Medium Impact, Low Effort)

All OSI routes in [`mdb_engine/osi/routes.py`](mdb_engine/osi/routes.py) return `dict[str, Any]`. No OpenAPI schema docs, no client codegen, no autocomplete.

- Add Pydantic `response_model` classes for each endpoint (`ListModelsResponse`, `ListMetricsResponse`, `ValidationResponse`, etc.)
- Add request body models for POST endpoints (e.g., `ImportYamlRequest`)
- This makes `/docs` self-documenting and enables typed client generation

### 15. Expand Scaffold Relationship Patterns (Medium Impact, Low Effort)

[`mdb_engine/osi/scaffold.py`](mdb_engine/osi/scaffold.py) only generates 3 relationship patterns (actor+movie, director+movie, movie+genre). Real-world apps need more.

- Add patterns: person+organization (works_at), person+location (lives_in), product+category (belongs_to), event+location (held_at), person+skill (has_skill)
- Consider a pluggable pattern registry so apps can register domain-specific relationship templates

### 16. Fix `from`/`to` vs `left_dataset`/`right_dataset` Inconsistency (Medium Impact, Low Effort)

The validator in [`mdb_engine/osi/validator.py`](mdb_engine/osi/validator.py) checks `left_dataset`/`right_dataset` for relationship cross-references, but the actual YAML files (e.g., `family_management.yaml`) use `from`/`to`. The prompt formatter in [`mdb_engine/graph/osi_loader.py`](mdb_engine/graph/osi_loader.py) reads `from`/`to`.

- Validator should accept both forms (`from`/`to` and `left_dataset`/`right_dataset`)
- Document the canonical form in `OSI_REFERENCE.md`
- Add a deprecation warning if the non-canonical form is used

### 17. Add Pagination to `/api/osi/concepts` (Low Impact, Low Effort)

The concepts endpoint in [`mdb_engine/osi/routes.py`](mdb_engine/osi/routes.py) has a hardcoded `limit(100)` with no pagination support.

- Add `?page=1&limit=50` query parameters
- Return `total`, `page`, `limit`, `has_more` in the response
- Apply the same pattern to `/api/osi/models` and `/api/osi/metrics`

### 18. Scaffold Header Should Link to Docs (Low Impact, Low Effort)

The auto-scaffolded YAML header in [`mdb_engine/osi/scaffold.py`](mdb_engine/osi/scaffold.py) tells developers to customize the model but doesn't point them to documentation.

- Add a `# Docs: https://github.com/ranfysvalle02/mdb-engine/blob/main/docs/OSI_REFERENCE.md` line to the scaffold header
- Include a brief example of adding a metric in the header comments

### OSI DX Summary Matrix

| Enhancement | Impact | Effort | Priority |
|---|---|---|---|
| OSI CLI commands | High | Medium | P0 |
| Pydantic response models | Medium | Low | P1 |
| Expand scaffold relationships | Medium | Low | P1 |
| Fix from/to vs left/right inconsistency | Medium | Low | P1 |
| Pagination on OSI endpoints | Low | Low | P2 |
| Scaffold header links to docs | Low | Low | P2 |
