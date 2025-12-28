"""
Authentication Helper Functions

Provides helper functions for initializing token management components.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging

logger = logging.getLogger(__name__)


async def initialize_token_management(app, db):
    """
    Initialize token management components (blacklist and session manager) on app startup.

    This function should be called in the app's lifespan startup event.

    Args:
        app: FastAPI application instance
        db: MongoDB database instance (Motor AsyncIOMotorDatabase)

    Example:
        from mdb_engine.auth.helpers import initialize_token_management
        from mdb_engine.auth import TokenBlacklist, SessionManager

        @app.on_event("startup")
        async def startup():
            # Get database from engine
            db = engine.get_database()

            # Initialize token management
            await initialize_token_management(app, db)
    """
    try:
        from .session_manager import SessionManager
        from .token_store import TokenBlacklist

        # Initialize token blacklist
        blacklist = TokenBlacklist(db)
        await blacklist.ensure_indexes()
        app.state.token_blacklist = blacklist
        logger.info("Token blacklist initialized")

        # Initialize session manager
        session_mgr = SessionManager(db)
        await session_mgr.ensure_indexes()
        app.state.session_manager = session_mgr
        logger.info("Session manager initialized")

    except (
        ImportError,
        AttributeError,
        TypeError,
        ValueError,
        RuntimeError,
        KeyError,
    ) as e:
        logger.error(f"Error initializing token management: {e}", exc_info=True)
        # Don't raise - allow app to start without token management (backward compatibility)
        logger.warning(
            "Token management not available - continuing without enhanced token features"
        )
