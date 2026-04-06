"""
Thin app-factory module loaded by uvicorn when ``mdb-engine serve`` runs.

The ``serve`` CLI command sets ``_MDB_SERVE_MANIFEST`` (and optionally
``MONGODB_URI`` / ``MDB_DB_NAME``) before starting uvicorn, which imports
this module to obtain the ``app`` object.

Everything here is generic — auth, CRUD, scopes, policies, defaults, and
the ``public/`` / ``templates/`` conventions are all driven by the
manifest and wired by ``create_app()``.  No application-specific logic
belongs here.
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

# --- templates/ SSR convention ------------------------------------------
# If a ``templates/`` directory exists next to the manifest and the
# manifest has ``ssr.enabled: true``, register server-side rendered
# routes.  SSR routes are mounted BEFORE static files so they take
# priority over ``public/index.html`` for overlapping paths like ``/``.
_templates_dir = manifest_path.parent / "templates"
_ssr_config = _manifest_data.get("ssr", {})

# --- public/ convention -------------------------------------------------
# If a ``public/`` directory exists next to the manifest, auto-serve it.
# ``public/index.html`` is served at ``/``; all files available under
# ``/public/``.  Inspired by Rails' ``public/`` directory.
#
# Build AssetRegistry early so SSR templates can use {{ asset_url() }}.
_public_dir = manifest_path.parent / "public"
_static_cache_cfg = _manifest_data.get("static_cache", {})
_asset_registry = None

if _public_dir.is_dir():
    from mdb_engine.routing.static import AssetRegistry  # noqa: E402

    _asset_registry = AssetRegistry(directory=_public_dir)

if _templates_dir.is_dir() and _ssr_config.get("enabled"):
    from mdb_engine.routing._ssr import mount_ssr_routes  # noqa: E402

    mount_ssr_routes(
        app,
        _templates_dir,
        _ssr_config,
        collections_config=_manifest_data.get("collections", {}),
        asset_registry=_asset_registry,
    )

# Mount static files with Cache-Control headers
if _public_dir.is_dir():
    _has_ssr_root = _ssr_config.get("enabled") and "/" in _ssr_config.get("routes", {})

    _index = _public_dir / "index.html"
    if _index.exists() and not _has_ssr_root:
        from fastapi.responses import HTMLResponse  # noqa: E402

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def _serve_index():
            return _index.read_text()

    from mdb_engine.routing.static import CachedStaticFiles  # noqa: E402

    app.mount(
        "/public",
        CachedStaticFiles(
            directory=str(_public_dir),
            cache_config=_static_cache_cfg,
            minify=_static_cache_cfg.get("minify", False),
        ),
        name="public",
    )
