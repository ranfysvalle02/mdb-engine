"""
Thin app-factory module loaded by uvicorn when ``mdb-engine serve`` runs.

The ``serve`` CLI command sets ``_MDB_SERVE_MANIFEST`` (and optionally
``MONGODB_URI`` / ``MDB_DB_NAME``) before starting uvicorn, which imports
this module to obtain the ``app`` object.

Everything here is generic — auth, CRUD, scopes, policies, defaults, and
the ``public/`` static-file convention are all driven by the manifest and
wired by ``create_app()``.  No application-specific logic belongs here.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

manifest_env = os.environ.get("_MDB_SERVE_MANIFEST")
if not manifest_env:
    print("Error: _MDB_SERVE_MANIFEST environment variable is not set.", file=sys.stderr)
    print("Use `mdb-engine serve <manifest>` to start the server.", file=sys.stderr)
    sys.exit(1)

manifest_path = Path(manifest_env).resolve()
if not manifest_path.exists():
    print(f"Error: manifest file not found: {manifest_path}", file=sys.stderr)
    sys.exit(1)

with open(manifest_path) as f:
    _manifest_data = json.load(f)

_slug = _manifest_data.get("slug")
if not _slug:
    print("Error: manifest is missing required 'slug' field.", file=sys.stderr)
    sys.exit(1)

from mdb_engine import MongoDBEngine  # noqa: E402

_mongo_uri = os.environ.get("MONGODB_URI")
_db_name = os.environ.get("MDB_DB_NAME")

engine = MongoDBEngine(mongo_uri=_mongo_uri, db_name=_db_name)
app = engine.create_app(slug=_slug, manifest=manifest_path)

# --- public/ convention -------------------------------------------------
# If a ``public/`` directory exists next to the manifest, auto-serve it.
# ``public/index.html`` is served at ``/``; all files available under
# ``/public/``.  Inspired by Rails' ``public/`` directory.
_public_dir = manifest_path.parent / "public"
if _public_dir.is_dir():
    _index = _public_dir / "index.html"
    if _index.exists():
        from fastapi.responses import HTMLResponse  # noqa: E402

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def _serve_index():
            return _index.read_text()

    from starlette.staticfiles import StaticFiles  # noqa: E402

    app.mount("/public", StaticFiles(directory=str(_public_dir)), name="public")
