"""FastAPI routes for OSI integration.

Provides API endpoints for:
- Listing loaded OSI models and metrics
- Importing user-provided YAML
- Listing and managing discovered concepts
- Exporting the knowledge graph as OSI YAML
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .dependencies import get_osi_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/osi", tags=["osi"])


# ---------------------------------------------------------------------------
# Models & Metrics
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_models(
    registry: Any = Depends(get_osi_registry),
) -> dict[str, Any]:
    """List all loaded OSI semantic models."""
    if not registry:
        return {"models": [], "message": "OSI integration not enabled"}

    models = []
    for model in registry.models:
        models.append(
            {
                "name": model.get("name", "unknown"),
                "description": model.get("description", ""),
                "datasets": len(model.get("datasets", [])),
                "metrics": len(model.get("metrics", [])),
                "relationships": len(model.get("relationships", [])),
                "status": model.get("_status", "approved"),
            }
        )

    return {"models": models, "total": len(models)}


@router.get("/metrics")
async def list_metrics(
    registry: Any = Depends(get_osi_registry),
) -> dict[str, Any]:
    """List all governed metric definitions."""
    if not registry:
        return {"metrics": [], "message": "OSI integration not enabled"}

    metrics = []
    for metric in registry.list_metrics():
        expression = ""
        dialects = metric.get("expression", {})
        if isinstance(dialects, dict):
            dialect_list = dialects.get("dialects", [])
            if dialect_list:
                expression = dialect_list[0].get("expression", "")

        synonyms_ctx = metric.get("ai_context", {})
        synonyms = synonyms_ctx.get("synonyms", []) if isinstance(synonyms_ctx, dict) else []

        metrics.append(
            {
                "name": metric.get("name", ""),
                "description": metric.get("description", ""),
                "expression": expression,
                "synonyms": synonyms,
                "model": metric.get("_model_name", "unknown"),
            }
        )

    return {"metrics": metrics, "total": len(metrics)}


# ---------------------------------------------------------------------------
# Import (Dual-Path: User-Provided YAML)
# ---------------------------------------------------------------------------


@router.post("/validate")
async def validate_yaml(
    request: Request,
) -> dict[str, Any]:
    """Validate OSI YAML without loading it.

    Accepts raw YAML in the request body. Parses and validates the
    structure, returning detailed errors and warnings. No side effects --
    nothing is loaded into the registry.

    Returns:
        Structured validation result with ``valid``, ``errors``, ``warnings``.
    """
    from .validator import validate_osi_yaml

    body = await request.body()
    yaml_content = body.decode("utf-8")

    result = validate_osi_yaml(yaml_content)
    return result.to_dict()


@router.post("/models/import")
async def import_yaml(
    request: Request,
    registry: Any = Depends(get_osi_registry),
) -> dict[str, Any]:
    """Import user-provided OSI YAML.

    Accepts raw YAML in the request body. Validates schema structure first,
    then persists to the MongoDB-backed store (if available) and loads into
    the live registry. Takes effect immediately -- no restart needed.
    """
    if not registry:
        raise HTTPException(status_code=400, detail="OSI integration not enabled")

    body = await request.body()
    yaml_content = body.decode("utf-8")

    if not yaml_content.strip():
        raise HTTPException(status_code=400, detail="Empty YAML content")

    try:
        import yaml as yaml_lib
    except ImportError:
        raise HTTPException(status_code=500, detail="PyYAML not installed") from None

    try:
        data = yaml_lib.safe_load(yaml_content)
        if not data:
            raise HTTPException(status_code=400, detail="Empty YAML content")

        # Extract models
        models = []
        if "semantic_model" in data:
            sm = data["semantic_model"]
            if isinstance(sm, list):
                models.extend(sm)
            elif isinstance(sm, dict):
                models.append(sm)
        else:
            models.append(data)

        # Validate before loading
        from .validator import validate_osi_models

        validation = validate_osi_models(models)
        if not validation.valid:
            return {
                "success": False,
                "message": f"Validation failed with {len(validation.errors)} error(s)",
                "errors": [e.to_dict() for e in validation.errors],
                "warnings": [w.to_dict() for w in validation.warnings],
            }

        # Write-through: add each model to registry (persists to MongoDB if store exists)
        for model in models:
            await registry.add_model(model, origin="api_import", status="approved")

        response: dict[str, Any] = {
            "success": True,
            "models_loaded": len(models),
            "message": f"Imported {len(models)} model(s) -- live immediately",
        }
        if validation.warnings:
            response["warnings"] = [w.to_dict() for w in validation.warnings]
        return response

    except yaml_lib.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}") from e


# ---------------------------------------------------------------------------
# Concepts (Discovered Definitions)
# ---------------------------------------------------------------------------


@router.get("/concepts")
async def list_concepts(
    request: Request,
    status: str = "provisional",
) -> dict[str, Any]:
    """List discovered concept definitions.

    Args:
        status: Filter by status ('provisional', 'approved', 'rejected', or 'all').
    """
    # Get graph service from app state
    graph_service = None
    app = request.app
    if hasattr(app.state, "engine") and app.state.engine is not None:
        slug = getattr(app.state, "app_slug", None) or getattr(app.state, "slug", None)
        if slug:
            graph_service = app.state.engine._service_initializer.get_graph_service(slug)  # noqa: SLF001

    if not graph_service:
        return {"concepts": [], "message": "Graph service not available"}

    try:
        # Query concept nodes with is_definition=true
        query: dict[str, Any] = {
            "type": "concept",
            "properties.is_definition": True,
        }
        if status != "all":
            query["properties.status"] = status

        cursor = graph_service.collection.find(query).sort("_id", -1).limit(100)
        concepts = await cursor.to_list(length=100)

        result = []
        for concept in concepts:
            props = concept.get("properties", {})
            result.append(
                {
                    "id": concept.get("_id"),
                    "name": concept.get("name"),
                    "definition": props.get("definition"),
                    "status": props.get("status", "provisional"),
                    "confidence": props.get("confidence"),
                    "confidence_label": props.get("confidence_label"),
                    "created_by": props.get("created_by"),
                    "has_yaml": bool(props.get("osi_yaml")),
                    "osi_yaml": props.get("osi_yaml"),
                }
            )

        return {"concepts": result, "total": len(result)}

    except (AttributeError, TypeError, RuntimeError) as e:
        logger.warning(f"Failed to list concepts: {e}")
        return {"concepts": [], "error": str(e)}


@router.post("/concepts/{concept_id}/approve")
async def approve_concept(
    concept_id: str,
    request: Request,
    registry: Any = Depends(get_osi_registry),
) -> dict[str, Any]:
    """Approve a discovered concept, promoting it to governed status.

    This loads the generated YAML into the OSI registry as an approved model.
    """
    # Get graph service
    graph_service = None
    app = request.app
    if hasattr(app.state, "engine") and app.state.engine is not None:
        slug = getattr(app.state, "app_slug", None) or getattr(app.state, "slug", None)
        if slug:
            graph_service = app.state.engine._service_initializer.get_graph_service(slug)  # noqa: SLF001

    if not graph_service:
        raise HTTPException(status_code=400, detail="Graph service not available")

    # Get the concept node
    node = await graph_service.get_node(concept_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Concept '{concept_id}' not found")

    props = node.get("properties", {})
    if not props.get("is_definition"):
        raise HTTPException(status_code=400, detail="Node is not a concept definition")

    # Update status to approved
    updated_props = dict(props)
    updated_props["status"] = "approved"

    await graph_service.upsert_node(
        node_id=concept_id,
        node_type="concept",
        name=node.get("name", ""),
        properties=updated_props,
        user_id=node.get("user_id"),
    )

    # If YAML was generated, add the model to the registry (persists to MongoDB)
    osi_yaml = props.get("osi_yaml")
    if osi_yaml and registry:
        try:
            import yaml as yaml_lib

            parsed = yaml_lib.safe_load(osi_yaml)
            if parsed:
                models = []
                if "semantic_model" in parsed:
                    sm = parsed["semantic_model"]
                    models = sm if isinstance(sm, list) else [sm]
                else:
                    models = [parsed]
                for model in models:
                    await registry.add_model(model, origin="discovered", status="approved")
        except (ImportError, Exception) as e:
            logger.warning(f"Could not load approved concept YAML into registry: {e}")

    return {
        "success": True,
        "concept_id": concept_id,
        "status": "approved",
        "message": f"Concept '{node.get('name')}' promoted to governed status",
    }


@router.post("/concepts/{concept_id}/reject")
async def reject_concept(
    concept_id: str,
    request: Request,
) -> dict[str, Any]:
    """Reject a discovered concept."""
    # Get graph service
    graph_service = None
    app = request.app
    if hasattr(app.state, "engine") and app.state.engine is not None:
        slug = getattr(app.state, "app_slug", None) or getattr(app.state, "slug", None)
        if slug:
            graph_service = app.state.engine._service_initializer.get_graph_service(slug)  # noqa: SLF001

    if not graph_service:
        raise HTTPException(status_code=400, detail="Graph service not available")

    node = await graph_service.get_node(concept_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Concept '{concept_id}' not found")

    props = node.get("properties", {})
    updated_props = dict(props)
    updated_props["status"] = "rejected"

    await graph_service.upsert_node(
        node_id=concept_id,
        node_type="concept",
        name=node.get("name", ""),
        properties=updated_props,
        user_id=node.get("user_id"),
    )

    return {
        "success": True,
        "concept_id": concept_id,
        "status": "rejected",
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.post("/export")
async def export_graph(
    request: Request,
    model_name: str | None = None,
    user_id: str | None = None,
) -> PlainTextResponse:
    """Export the knowledge graph as OSI-compatible YAML."""
    # Get graph service
    graph_service = None
    app = request.app
    if hasattr(app.state, "engine") and app.state.engine is not None:
        slug = getattr(app.state, "app_slug", None) or getattr(app.state, "slug", None)
        if slug:
            graph_service = app.state.engine._service_initializer.get_graph_service(slug)  # noqa: SLF001

    if not graph_service:
        raise HTTPException(status_code=400, detail="Graph service not available")

    try:
        from .exporter import OsiExporter

        exporter = OsiExporter(
            app_slug=getattr(graph_service, "app_slug", "unknown"),
            graph_service=graph_service,
        )
        result = await exporter.export(user_id=user_id, model_name=model_name)

        try:
            import yaml

            yaml_str = yaml.dump(result, default_flow_style=False, sort_keys=False)
        except ImportError:
            import json

            yaml_str = json.dumps(result, indent=2)

        return PlainTextResponse(content=yaml_str, media_type="application/x-yaml")

    except (ImportError, RuntimeError, TypeError) as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e}") from e


# ---------------------------------------------------------------------------
# Discovery Report
# ---------------------------------------------------------------------------


@router.get("/discovery-report")
async def discovery_report(
    request: Request,
    registry: Any = Depends(get_osi_registry),
) -> dict[str, Any]:
    """Generate a report of graph entities not yet in OSI models.

    Surfaces unmatched entity types, relationship patterns, and
    provisional concepts for analyst review.
    """
    if not registry:
        raise HTTPException(status_code=400, detail="OSI integration not enabled")

    # Get graph service
    graph_service = None
    app = request.app
    if hasattr(app.state, "engine") and app.state.engine is not None:
        slug = getattr(app.state, "app_slug", None) or getattr(app.state, "slug", None)
        if slug:
            graph_service = app.state.engine._service_initializer.get_graph_service(slug)  # noqa: SLF001

    if not graph_service:
        raise HTTPException(status_code=400, detail="Graph service not available")

    from .reporter import OsiDiscoveryReporter

    reporter = OsiDiscoveryReporter(graph_service=graph_service, registry=registry)
    return await reporter.generate_report()


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------


@router.post("/reload")
async def reload_models(
    registry: Any = Depends(get_osi_registry),
) -> dict[str, Any]:
    """Force reload of OSI models from the backing store."""
    if not registry:
        raise HTTPException(status_code=400, detail="OSI integration not enabled")

    await registry.reload()
    return {
        "success": True,
        "models_loaded": len(registry.models),
        "message": "OSI models reloaded from store",
    }
