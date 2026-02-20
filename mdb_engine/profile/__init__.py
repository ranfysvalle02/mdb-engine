"""Profile system for MDB-Engine.

Materializes user profiles and community profiles from the memory
and graph services.  Profiles act as the brain's **prefrontal
self-model** — a continuously-updated synthesis of identity,
preferences, relationships and context that can be retrieved in
<1 ms for prompt injection.

Usage::

    from mdb_engine.profile import ProfileService

    profile_svc = ProfileService(
        app_slug="my_app",
        collection=scoped_db.user_profiles,
        memory_service=memory_service,
        graph_service=graph_service,
        llm_service=llm_service,
    )

    # Retrieve (instant — single MongoDB read)
    profile = await profile_svc.get_user_profile(user_id)

    # Incremental update after new memories
    await profile_svc.incremental_update(user_id, new_memories)

    # Full rebuild (periodic / manual)
    await profile_svc.build_user_profile(user_id)
"""

from .service import ProfileService

__all__ = ["ProfileService"]
