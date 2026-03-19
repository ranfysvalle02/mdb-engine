"""
Thin app-factory module loaded by uvicorn when ``mdb-engine serve-multi`` runs.

The ``serve-multi`` CLI command sets ``_MDB_SERVE_MULTI_MODE`` and
``_MDB_SERVE_MULTI_PATH`` (and optionally ``MONGODB_URI`` /
``MDB_DB_NAME`` / ``_MDB_SERVE_MULTI_TITLE``) before starting uvicorn,
which imports this module to obtain the ``app`` object.

This mirrors ``_serve_app.py`` but delegates to ``create_multi_app()``
for multi-app/multi-tenant deployments.  After the parent app is created,
it post-processes each mounted child app to wire SSR routes and static
file serving — the same convention ``_serve_app.py`` uses for single apps.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_mode = os.environ.get("_MDB_SERVE_MULTI_MODE")
_path = os.environ.get("_MDB_SERVE_MULTI_PATH")

if not _mode or not _path:
    print(
        "Error: _MDB_SERVE_MULTI_MODE and _MDB_SERVE_MULTI_PATH must be set.",
        file=sys.stderr,
    )
    print("Use `mdb-engine serve-multi` to start the server.", file=sys.stderr)
    sys.exit(1)

_title = os.environ.get("_MDB_SERVE_MULTI_TITLE", "Multi-App API")
_resolved = Path(_path).resolve()

if not _resolved.exists():
    print(f"Error: path does not exist: {_resolved}", file=sys.stderr)
    sys.exit(1)

from mdb_engine import MongoDBEngine  # noqa: E402

_mongo_uri = os.environ.get("MONGODB_URI")
_db_name = os.environ.get("MDB_DB_NAME")

engine = MongoDBEngine(mongo_uri=_mongo_uri, db_name=_db_name)

if _mode == "apps_dir":
    app = engine.create_multi_app(apps_dir=_resolved, title=_title)
elif _mode == "manifest":
    app = engine.create_multi_app(multi_app_manifest=_resolved, title=_title)
else:
    print(f"Error: unknown _MDB_SERVE_MULTI_MODE: {_mode!r}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Post-process: mount SSR routes and static files on each child app.
#
# create_multi_app() handles CRUD, auth, middleware, and WebSocket wiring
# but SSR route registration and public/ static serving are conventions
# of the CLI layer (same as _serve_app.py).  We iterate the mounted apps
# and apply the same logic for each one that has templates/ or public/.
# ---------------------------------------------------------------------------


def _mount_ssr_and_static() -> None:  # noqa: C901
    mounted = getattr(app.state, "mounted_apps", [])
    if not mounted:
        return

    for entry in mounted:
        slug = entry.get("slug", "")
        child_app = entry.get("app")
        manifest_data = entry.get("manifest", {})
        path_prefix = entry.get("path_prefix", f"/{slug}")

        if not child_app or not manifest_data:
            continue

        manifest_dir = _find_manifest_dir(slug)
        if not manifest_dir:
            continue

        ssr_config = manifest_data.get("ssr", {})
        templates_dir = manifest_dir / "templates"

        if templates_dir.is_dir() and ssr_config.get("enabled"):
            try:
                from mdb_engine.routing._ssr import mount_ssr_routes  # noqa: E402

                mount_ssr_routes(
                    child_app,
                    templates_dir,
                    ssr_config,
                    collections_config=manifest_data.get("collections", {}),
                    base_path=path_prefix,
                )
                logger.info("Mounted SSR routes for '%s'", slug)
            except (ImportError, OSError, ValueError) as exc:
                logger.warning("SSR mounting failed for '%s': %s", slug, exc)

        public_dir = manifest_dir / "public"
        if public_dir.is_dir():
            _has_ssr_root = ssr_config.get("enabled") and "/" in ssr_config.get("routes", {})

            index_html = public_dir / "index.html"
            if index_html.exists() and not _has_ssr_root:
                from fastapi.responses import HTMLResponse  # noqa: E402

                _idx = index_html

                @child_app.get("/", response_class=HTMLResponse, include_in_schema=False)
                async def _serve_child_index(_idx=_idx):
                    return _idx.read_text()

            from starlette.staticfiles import StaticFiles  # noqa: E402

            child_app.mount("/public", StaticFiles(directory=str(public_dir)), name=f"public_{slug}")
            logger.info("Mounted static files for '%s' at %s/public/", slug, path_prefix)

    # Also mount a shared public/ directory at the apps_dir root (if it exists)
    if _mode == "apps_dir":
        shared_public = _resolved / "public"
        if shared_public.is_dir():
            from starlette.staticfiles import StaticFiles  # noqa: E402

            app.mount("/public", StaticFiles(directory=str(shared_public)), name="shared_public")
            logger.info("Mounted shared static files at /public/")


def _find_manifest_dir(slug: str) -> Path | None:
    """Locate the directory containing manifest.json for a given slug."""
    if _mode == "apps_dir":
        for child in _resolved.iterdir():
            if not child.is_dir():
                continue
            mf = child / "manifest.json"
            if mf.exists():
                try:
                    with open(mf) as f:
                        data = json.load(f)
                    if data.get("slug", child.name) == slug:
                        return child
                except (json.JSONDecodeError, OSError):
                    continue
    elif _mode == "manifest":
        try:
            with open(_resolved) as f:
                data = json.load(f)
            manifest_dir = _resolved.parent
            for app_entry in data.get("multi_app", {}).get("apps", []):
                if app_entry.get("slug") == slug:
                    rel_path = app_entry.get("manifest", "")
                    return (manifest_dir / rel_path).resolve().parent
        except (json.JSONDecodeError, OSError):
            pass
    return None


_mount_ssr_and_static()
