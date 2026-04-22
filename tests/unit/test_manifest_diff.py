"""Unit tests for the RFC-6902 manifest diff helper."""

from __future__ import annotations

from mdb_engine.core.manifest_diff import (
    filter_patch_by_prefix,
    format_patch_markdown,
    manifest_patch,
)


class TestManifestPatch:
    def test_add_remove_replace(self):
        prev = {"slug": "x", "collections": {"a": {"auto_crud": True}}}
        new = {"slug": "x", "collections": {"b": {"auto_crud": True}}}
        patch = manifest_patch(prev, new)
        ops = {o["op"] for o in patch}
        assert "add" in ops
        assert "remove" in ops
        paths = {o["path"] for o in patch}
        assert "/collections/a" in paths
        assert "/collections/b" in paths

    def test_no_changes_is_empty(self):
        m = {"slug": "x", "collections": {}}
        assert manifest_patch(m, m) == []

    def test_strips_runtime_fields_before_diffing(self):
        a = {"slug": "x", "collections": {}, "_applied_hash": "old"}
        b = {"slug": "x", "collections": {}, "_applied_hash": "new"}
        assert manifest_patch(a, b) == []

    def test_filter_by_prefix(self):
        patch = [
            {"op": "add", "path": "/collections/a"},
            {"op": "remove", "path": "/managed_indexes/z"},
            {"op": "replace", "path": "/collections/a/auth"},
        ]
        filtered = filter_patch_by_prefix(patch, "/collections/a")
        assert len(filtered) == 2
        assert all(o["path"].startswith("/collections/a") for o in filtered)

    def test_markdown_renders_empty(self):
        assert "no structural changes" in format_patch_markdown([])

    def test_markdown_orders_removes_first(self):
        patch = [
            {"op": "add", "path": "/a"},
            {"op": "remove", "path": "/b"},
        ]
        out = format_patch_markdown(patch)
        lines = out.splitlines()
        # remove should appear before add in the output
        idx_add = next(i for i, line in enumerate(lines) if "`+`" in line)
        idx_rem = next(i for i, line in enumerate(lines) if "`-`" in line)
        assert idx_rem < idx_add

    def test_escapes_pointer_components(self):
        prev = {}
        new = {"a/b": 1, "c~d": 2}
        patch = manifest_patch(prev, new)
        paths = {o["path"] for o in patch}
        assert "/a~1b" in paths
        assert "/c~0d" in paths
