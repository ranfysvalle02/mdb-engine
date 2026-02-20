"""
Canonical environment variable reader for mdb-engine.

All env vars should be read through this module so that:
  - Deprecated names emit a one-time warning.
  - There is a single source of truth for variable names.

Canonical naming: ``MDB_<COMPONENT>_<NAME>`` (e.g. ``MDB_MONGO_URI``).
"""

import os
import warnings

_warned: set[str] = set()


def get_env(
    canonical: str,
    *,
    deprecated: list[str] | None = None,
    default: str | None = None,
) -> str | None:
    """Read an env var with fallback to deprecated names.

    The first match wins.  A :class:`DeprecationWarning` is emitted (once
    per deprecated name) when a legacy name is used.
    """
    val = os.environ.get(canonical)
    if val is not None:
        return val

    for old in deprecated or []:
        val = os.environ.get(old)
        if val is not None:
            if old not in _warned:
                _warned.add(old)
                warnings.warn(
                    f"Environment variable '{old}' is deprecated; " f"use '{canonical}' instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            return val

    return default


# ── Convenience accessors ────────────────────────────────────────────────


def get_mongo_uri(fallback: str = "mongodb://localhost:27017") -> str:
    return (
        get_env(
            "MDB_MONGO_URI",
            deprecated=["MONGODB_URI", "MONGO_URI"],
            default=fallback,
        )
        or fallback
    )


def get_db_name(fallback: str = "mdb_engine") -> str:
    return (
        get_env(
            "MDB_DB_NAME",
            deprecated=["MONGODB_DB", "MONGO_DB_NAME", "DB_NAME"],
            default=fallback,
        )
        or fallback
    )


def get_jwt_secret() -> str | None:
    return get_env(
        "MDB_JWT_SECRET",
        deprecated=[
            "MDB_ENGINE_JWT_SECRET",
            "FLASK_SECRET_KEY",
            "SECRET_KEY",
            "APP_SECRET_KEY",
        ],
    )
