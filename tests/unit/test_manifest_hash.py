"""Unit tests for manifest canonicalization + hashing.

Includes ``hypothesis``-driven property tests covering the invariant
"canonicalization is invariant under key-order permutation" so that the
hash we commit to ``apps_config`` is deterministic across Python
dicts with non-stable iteration order.
"""

from __future__ import annotations

import random

import pytest

from mdb_engine.core.manifest_hash import (
    HASH_SCHEMA_VERSION,
    canonicalize_manifest,
    compute_manifest_hash,
    compute_schema_hash,
    is_current_version,
)

try:  # hypothesis is a dev-only dependency; skip those tests if unavailable.
    from hypothesis import given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    HYPOTHESIS_AVAILABLE = False


def _manifest(**overrides):
    base = {
        "schema_version": "2.0",
        "slug": "test_app",
        "name": "Test",
        "collections": {
            "things": {
                "auto_crud": True,
                "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
            }
        },
        "managed_indexes": {
            "things": [{"type": "regular", "keys": {"x": 1}, "name": "idx_things_x"}],
        },
    }
    base.update(overrides)
    return base


class TestCanonicalize:
    def test_strips_runtime_keys_by_default(self):
        m = _manifest()
        m["_applied_hash"] = "sha256:deadbeef"
        m["_applied_revision"] = 3
        m["_applied_at"] = "2025-01-01"
        out = canonicalize_manifest(m)
        assert "_applied_hash" not in out
        assert "_applied_revision" not in out
        assert "_applied_at" not in out

    def test_preserves_runtime_keys_when_requested(self):
        m = _manifest()
        m["_applied_hash"] = "sha256:deadbeef"
        out = canonicalize_manifest(m, strip_runtime=False)
        assert out["_applied_hash"] == "sha256:deadbeef"

    def test_sorts_nested_keys_deterministically(self):
        a = _manifest()
        b = _manifest()
        a["collections"] = {"things": a["collections"]["things"], "z": {"auto_crud": False}}
        b["collections"] = {"z": {"auto_crud": False}, "things": b["collections"]["things"]}
        assert compute_manifest_hash(a) == compute_manifest_hash(b)

    def test_converts_sets_and_tuples(self):
        m = _manifest()
        m["tags"] = {"b", "a"}
        out = canonicalize_manifest(m)
        assert out["tags"] == ["a", "b"]


class TestHashes:
    def test_hash_is_stable_across_key_order(self):
        a = _manifest()
        b = {k: a[k] for k in reversed(list(a))}
        assert compute_manifest_hash(a) == compute_manifest_hash(b)

    def test_hash_has_sha256_prefix_and_digest(self):
        h = compute_manifest_hash(_manifest())
        assert h.startswith(f"sha256:v{HASH_SCHEMA_VERSION}:")
        # 64 hex chars + "sha256:v{N}:"
        assert len(h) == len(f"sha256:v{HASH_SCHEMA_VERSION}:") + 64

    def test_is_current_version_distinguishes_old_hashes(self):
        assert is_current_version(compute_manifest_hash(_manifest()))
        assert not is_current_version("sha256:deadbeef")
        assert not is_current_version(None)
        assert not is_current_version(f"sha256:v{HASH_SCHEMA_VERSION - 1}:deadbeef")

    def test_hash_changes_on_schema_change(self):
        a = _manifest()
        b = _manifest()
        b["collections"]["things"]["schema"]["properties"]["y"] = {"type": "string"}
        assert compute_manifest_hash(a) != compute_manifest_hash(b)
        assert compute_schema_hash(a) != compute_schema_hash(b)

    def test_schema_hash_ignores_non_schema_keys(self):
        a = _manifest()
        b = _manifest()
        b["name"] = "Completely Different Name"
        b["description"] = "new"
        assert compute_schema_hash(a) == compute_schema_hash(b)
        # But full manifest hash should differ.
        assert compute_manifest_hash(a) != compute_manifest_hash(b)

    def test_runtime_fields_do_not_affect_hash(self):
        a = _manifest()
        b = _manifest()
        b["_applied_hash"] = "sha256:old"
        b["_applied_revision"] = 7
        b["_applied_at"] = "whenever"
        assert compute_manifest_hash(a) == compute_manifest_hash(b)

    def test_ssr_route_cache_is_runtime_only(self):
        a = _manifest()
        b = _manifest()
        b["ssr"] = {
            "routes": {
                "home": {"template": "home.html", "cache": {"ttl": 30}},
            }
        }
        a["ssr"] = {
            "routes": {
                "home": {"template": "home.html", "cache": {"ttl": 999}},
            }
        }
        assert compute_manifest_hash(a) == compute_manifest_hash(b)

    def test_initial_data_excluded_from_schema_hash(self):
        a = _manifest()
        b = _manifest()
        b["initial_data"] = {"things": [{"x": "seeded"}]}
        assert compute_schema_hash(a) == compute_schema_hash(b)

    def test_observability_sampling_is_runtime_only(self):
        a = _manifest()
        b = _manifest()
        b["observability"] = {"tracing": {"sampling": 0.1, "endpoint": "http://x"}}
        a["observability"] = {"tracing": {"sampling": 0.9, "endpoint": "http://x"}}
        assert compute_manifest_hash(a) == compute_manifest_hash(b)


# -------------------------------------------------------------------------
# Hypothesis property tests: canonicalization is permutation-invariant.
# -------------------------------------------------------------------------


if HYPOTHESIS_AVAILABLE:

    def _shuffle_keys(obj):
        """Recursively rebuild dicts with keys in shuffled order."""
        if isinstance(obj, dict):
            keys = list(obj.keys())
            random.shuffle(keys)
            return {k: _shuffle_keys(obj[k]) for k in keys}
        if isinstance(obj, list):
            return [_shuffle_keys(v) for v in obj]
        return obj

    # Scalars the hash must accept without raising.
    _scalars = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-10_000, max_value=10_000),
        st.text(max_size=16),
    )
    _nested = st.recursive(
        _scalars,
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4),
        ),
        max_leaves=12,
    )

    @given(_nested)
    @settings(max_examples=50)
    def test_canonicalization_is_permutation_invariant(payload):
        manifest_a = {"slug": "prop", "collections": payload if isinstance(payload, dict) else {"x": payload}}
        manifest_b = _shuffle_keys(manifest_a)
        assert compute_manifest_hash(manifest_a) == compute_manifest_hash(manifest_b)

else:

    @pytest.mark.skip(reason="hypothesis not installed")
    def test_canonicalization_is_permutation_invariant():
        pass
