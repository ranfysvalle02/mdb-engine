"""Auto-scaffold OSI semantic models from graph config.

Generates starter OSI YAML from ``graph_config.node_types`` and
``memory_config.categories`` so developers don't have to write YAML
from scratch.

Usage::

    from mdb_engine.osi.scaffold import scaffold_osi_model, scaffold_to_yaml

    # Generate a model dict
    model = scaffold_osi_model("my_app", node_types=["actor", "movie", "genre"])

    # Generate a YAML string ready to write to a file
    yaml_str = scaffold_to_yaml("my_app", node_types=["actor", "movie", "genre"])
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Built-in synonym suggestions for common entity types.
# Unknown types get [type_name, type_name with spaces] as a fallback.
_COMMON_SYNONYMS: dict[str, list[str]] = {
    "actor": ["actor", "actress", "star", "performer", "cast member", "lead", "co-star", "movie star"],
    "movie": ["movie", "film", "picture", "flick", "feature", "motion picture", "title", "release"],
    "director": ["director", "filmmaker", "auteur", "helmer", "directed by"],
    "genre": ["genre", "type", "category", "style", "kind"],
    "person": ["person", "individual", "someone", "user", "human", "character"],
    "location": ["location", "place", "city", "country", "region", "area", "where"],
    "organization": ["organization", "company", "firm", "corporation", "institution", "org"],
    "event": ["event", "occurrence", "happening", "incident", "occasion"],
    "concept": ["concept", "idea", "notion", "topic", "theme", "subject"],
    "product": ["product", "item", "good", "merchandise", "offering"],
    "interest": ["interest", "hobby", "passion", "activity", "pursuit"],
    "food": ["food", "dish", "cuisine", "meal", "ingredient"],
    "pet": ["pet", "animal", "companion animal"],
    "skill": ["skill", "ability", "competence", "expertise", "proficiency"],
    "technology": ["technology", "tech", "tool", "framework", "platform", "software"],
}


def _synonyms_for_type(type_name: str) -> list[str]:
    """Get synonyms for a node type, with fallback for unknown types."""
    key = type_name.lower().strip()
    if key in _COMMON_SYNONYMS:
        return list(_COMMON_SYNONYMS[key])
    # Fallback: use the type name and a space-separated version
    readable = key.replace("_", " ")
    syns = [key]
    if readable != key:
        syns.append(readable)
    return syns


def scaffold_osi_model(
    app_slug: str,
    node_types: list[str],
    categories: list[str] | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Generate a starter OSI semantic model from graph config.

    Creates a dataset for each ``node_type`` with:
    - name, source, primary_key
    - A ``name`` field with basic synonyms
    - ``ai_context.synonyms`` generated from the type name

    If ``categories`` are provided, adds a ``user_preference`` dataset
    with the categories as synonyms.

    Args:
        app_slug: Application slug (used in source paths and model name).
        node_types: List of node type names from ``graph_config.node_types``.
        categories: Optional list of memory categories to include as a
            user_preference dataset.
        model_name: Optional model name override (defaults to
            ``{app_slug}_knowledge``).

    Returns:
        A dict representing a single OSI semantic model, ready to be
        loaded by the registry or serialized to YAML.
    """
    name = model_name or f"{app_slug}_knowledge"

    datasets: list[dict[str, Any]] = []
    for node_type in node_types:
        nt_lower = node_type.lower().strip()
        if not nt_lower:
            continue

        ds: dict[str, Any] = {
            "name": nt_lower,
            "source": f"mdb_engine.ai_chat.{nt_lower}",
            "primary_key": [f"{nt_lower}_id"],
            "fields": [
                {
                    "name": f"{nt_lower}_name",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "name"}],
                    },
                    "ai_context": {
                        "synonyms": ["name", f"{nt_lower} name", "full name"],
                    },
                },
            ],
            "ai_context": {
                "synonyms": _synonyms_for_type(nt_lower),
            },
        }
        datasets.append(ds)

    # Add user_preference dataset if categories are provided
    if categories:
        cat_synonyms = ["preference", "favorite", "like", "taste"]
        cat_synonyms.extend(c.lower() for c in categories if c)

        datasets.append(
            {
                "name": "user_preference",
                "source": "mdb_engine.ai_chat.user_preference",
                "primary_key": ["preference_id"],
                "fields": [
                    {
                        "name": "preference_type",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "type"}],
                        },
                        "ai_context": {"synonyms": ["type", "preference", "category"]},
                    },
                    {
                        "name": "target_name",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "target"}],
                        },
                        "ai_context": {"synonyms": ["target", "what", "subject"]},
                    },
                ],
                "ai_context": {
                    "synonyms": cat_synonyms,
                },
            }
        )

    # Build relationships between datasets (basic scaffold)
    relationships: list[dict[str, Any]] = []
    ds_names = {ds["name"] for ds in datasets}

    # Auto-generate common relationship patterns
    if "actor" in ds_names and "movie" in ds_names:
        relationships.append(
            {
                "name": "starred_in",
                "left_dataset": "actor",
                "right_dataset": "movie",
                "cardinality": "many_to_many",
                "ai_context": {"synonyms": ["starred in", "acted in", "appeared in", "was in"]},
            }
        )
    if "director" in ds_names and "movie" in ds_names:
        relationships.append(
            {
                "name": "directed",
                "left_dataset": "director",
                "right_dataset": "movie",
                "cardinality": "one_to_many",
                "ai_context": {"synonyms": ["directed", "made", "helmed", "created"]},
            }
        )
    if "movie" in ds_names and "genre" in ds_names:
        relationships.append(
            {
                "name": "genre_of",
                "left_dataset": "movie",
                "right_dataset": "genre",
                "cardinality": "many_to_many",
                "ai_context": {"synonyms": ["genre", "type of", "categorized as"]},
            }
        )

    model: dict[str, Any] = {
        "name": name,
        "description": (
            f"Auto-scaffolded OSI semantic model for '{app_slug}'. "
            f"Generated from graph_config.node_types. "
            f"Edit this model to add fields, synonyms, metrics, and relationships."
        ),
        "ai_context": {
            "synonyms": [app_slug, app_slug.replace("_", " "), f"{app_slug} knowledge"],
        },
        "datasets": datasets,
        "relationships": relationships,
        "metrics": [],
    }

    logger.info(
        f"Scaffolded OSI model '{name}' with {len(datasets)} datasets, "
        f"{len(relationships)} relationships from {len(node_types)} node types"
    )

    return model


def scaffold_to_yaml(
    app_slug: str,
    node_types: list[str],
    categories: list[str] | None = None,
    model_name: str | None = None,
) -> str:
    """Generate YAML string ready to write to a file.

    Args:
        app_slug: Application slug.
        node_types: List of node type names.
        categories: Optional memory categories.
        model_name: Optional model name override.

    Returns:
        YAML string with a ``semantic_model:`` wrapper, ready to write
        to a ``.yaml`` file in the ``semantic_models/`` directory.

    Raises:
        ImportError: If PyYAML is not installed.
    """
    model = scaffold_osi_model(
        app_slug=app_slug,
        node_types=node_types,
        categories=categories,
        model_name=model_name,
    )

    try:
        import yaml as yaml_lib
    except ImportError as e:
        raise ImportError("PyYAML is required for YAML generation: pip install pyyaml") from e

    header = (
        f"# ---------------------------------------------------------------------------\n"
        f"# {app_slug} -- Auto-Scaffolded OSI Semantic Model\n"
        f"# ---------------------------------------------------------------------------\n"
        f"# Generated from graph_config.node_types: {', '.join(node_types)}\n"
        f"#\n"
        f"# This is a starter model. Customize it by:\n"
        f"#   - Adding more fields to each dataset\n"
        f"#   - Expanding ai_context.synonyms for better entity resolution\n"
        f"#   - Adding relationships between datasets\n"
        f"#   - Defining metrics for query routing\n"
        f"# ---------------------------------------------------------------------------\n\n"
    )

    yaml_str = yaml_lib.dump(
        {"semantic_model": [model]},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    return header + yaml_str
