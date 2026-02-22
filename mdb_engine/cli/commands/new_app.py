"""
Scaffold a new mdb-engine app.

Creates a directory with web.py + manifest.json from templates.
"""

import json
from pathlib import Path

import click

_MANIFEST_TEMPLATE = {
    "schema_version": "2.0",
    "slug": "",
    "name": "",
    "status": "active",
    "auth": {
        "mode": "shared",
        "auth_hub_url": "/auth-hub",
        "roles": ["base_user", "viewer", "editor", "admin"],
        "require_role": "base_user",
        "public_routes": ["/health"],
    },
    "data_access": {
        "read_scopes": [],
        "write_scope": "",
    },
}

_WEB_PY_TEMPLATE = '''\
"""
{name} — web routes.

Routes are auto-imported by the engine when mounted via create_multi_app.
"""

import logging
from pathlib import Path

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates

from mdb_engine.dependencies import get_scoped_db

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/")
async def index(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        auth_hub_url = getattr(request.state, "auth_hub_url", "/auth-hub")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(auth_hub_url + "/login")
    return templates.TemplateResponse("index.html", {{"request": request, "user": user}})
'''

_WEB_PY_MEMORY_TEMPLATE = '''\
"""
{name} — web routes with memory service.

Routes are auto-imported by the engine when mounted via create_multi_app.
"""

import logging
from pathlib import Path

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates

from mdb_engine.dependencies import get_memory_service, get_scoped_db

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/")
async def index(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        auth_hub_url = getattr(request.state, "auth_hub_url", "/auth-hub")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(auth_hub_url + "/login")
    return templates.TemplateResponse("index.html", {{"request": request, "user": user}})


@app.post("/api/remember")
async def remember(request: Request, memory=Depends(get_memory_service)):
    user = getattr(request.state, "user", None)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(401, "Authentication required")
    body = await request.json()
    result = await memory.add(messages=body.get("text", ""), user_id=str(user["_id"]))
    return result


@app.get("/api/recall")
async def recall(q: str, memory=Depends(get_memory_service), request: Request = None):
    user = getattr(request.state, "user", None)
    user_id = str(user["_id"]) if user else "anonymous"
    return await memory.search(query=q, user_id=user_id, limit=5)
'''

_BASE_HTML_TEMPLATE = """\
{{% extends "mdb_base.html" %}}

{{% block title %}}{name}{{% endblock %}}

{{% block head %}}
<style>
    body {{ font-family: system-ui, sans-serif; background: #0f0f12; color: #f4f4f5; }}
    .container {{ max-width: 800px; margin: 2rem auto; padding: 0 1rem; }}
    .card {{ background: #1a1a1f; border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; }}
</style>
{{% block extra_css %}}{{% endblock %}}
{{% endblock %}}

{{% block body %}}
<main class="container">
    {{% block content %}}{{% endblock %}}
</main>
{{% endblock %}}
"""

_INDEX_HTML_TEMPLATE = """\
{{% extends "base.html" %}}

{{% block title %}}{name}{{% endblock %}}

{{% block content %}}
<div class="card">
    <h1>Welcome to {name}</h1>
    <p>Hello, {{{{ user.email }}}}!</p>
</div>
{{% endblock %}}
"""


@click.command()
@click.argument("slug")
@click.option("--mode", type=click.Choice(["shared", "app"]), default="shared", help="Auth mode")
@click.option("--path-prefix", default=None, help="URL path prefix (default: /{slug})")
@click.option("--services", default="", help="Comma-separated services: memory,graph,profile")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=Path("."), help="Parent directory")
def new_app(slug: str, mode: str, path_prefix: str | None, services: str, output_dir: Path):
    """Scaffold a new mdb-engine app.

    Creates SLUG/ with web.py and manifest.json ready to mount.

    Examples:

        mdb-engine new-app my-app

        mdb-engine new-app my-app --services memory,graph
    """
    app_dir = output_dir / slug
    if app_dir.exists():
        raise click.ClickException(f"Directory '{app_dir}' already exists")

    service_list = [s.strip() for s in services.split(",") if s.strip()]
    name = slug.replace("-", " ").replace("_", " ").title()

    # Build manifest
    manifest = dict(_MANIFEST_TEMPLATE)
    manifest["slug"] = slug
    manifest["name"] = name
    manifest["auth"]["mode"] = mode
    manifest["data_access"]["read_scopes"] = [slug]
    manifest["data_access"]["write_scope"] = slug

    if "memory" in service_list:
        manifest["memory_config"] = {
            "enabled": True,
            "collection_name": f"{slug}_memories",
            "embedding_model_dims": 1536,
            "infer": True,
        }

    if "graph" in service_list:
        manifest["graph_config"] = {
            "enabled": True,
            "collection_name": "kg",
            "auto_extract": True,
        }

    if "profile" in service_list:
        manifest["profile_config"] = {
            "enabled": True,
            "user_profiles": {"enabled": True},
        }

    # Write files
    app_dir.mkdir(parents=True)
    (app_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    template = _WEB_PY_MEMORY_TEMPLATE if "memory" in service_list else _WEB_PY_TEMPLATE
    (app_dir / "web.py").write_text(template.format(name=name))

    templates_dir = app_dir / "templates"
    templates_dir.mkdir()
    (templates_dir / "base.html").write_text(_BASE_HTML_TEMPLATE.format(name=name))
    (templates_dir / "index.html").write_text(_INDEX_HTML_TEMPLATE.format(name=name))

    click.echo(click.style(f"Created app '{slug}' in {app_dir}/", fg="green"))
    click.echo("  manifest.json        — app configuration")
    click.echo("  web.py               — route handlers")
    click.echo("  templates/base.html  — app base template (extends mdb_base.html)")
    click.echo("  templates/index.html — index page")
    if path_prefix:
        click.echo(f'\nMount with path_prefix="{path_prefix}"')
    else:
        click.echo(f'\nMount with path_prefix="/{slug}"')
