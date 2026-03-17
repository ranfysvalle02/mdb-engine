"""Tests for the manifest diff CLI command."""

from __future__ import annotations

from mdb_engine.cli.commands.diff import compute_diff


class TestDiffAddedCollection:
    def test_detects_new_collection(self):
        old = {"collections": {"posts": {"auto_crud": True}}}
        new = {
            "collections": {
                "posts": {"auto_crud": True},
                "reactions": {"auto_crud": True, "auth": {"public_read": True}},
            }
        }
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "reactions" in text
        assert "added" in text

    def test_detects_removed_collection(self):
        old = {"collections": {"posts": {}, "comments": {}}}
        new = {"collections": {"posts": {}}}
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "comments" in text
        assert "removed" in text
        assert result.breaking == 1


class TestDiffScope:
    def test_detects_scope_removal(self):
        old = {"collections": {"posts": {"scopes": {"published": {}, "drafts": {}}}}}
        new = {"collections": {"posts": {"scopes": {"published": {}}}}}
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "drafts" in text

    def test_detects_scope_addition(self):
        old = {"collections": {"posts": {"scopes": {"published": {}}}}}
        new = {"collections": {"posts": {"scopes": {"published": {}, "featured": {}}}}}
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "featured" in text

    def test_detects_scope_change(self):
        old = {"collections": {"posts": {"scopes": {"published": {"status": "active"}}}}}
        new = {"collections": {"posts": {"scopes": {"published": {"status": "live"}}}}}
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "published" in text
        assert "changed" in text


class TestDiffBreakingSchema:
    def test_new_required_field_is_breaking(self):
        old = {"collections": {"posts": {"schema": {"required": ["title"]}}}}
        new = {"collections": {"posts": {"schema": {"required": ["title", "body"]}}}}
        result = compute_diff(old, new)
        assert result.breaking == 1
        text = "\n".join(result.lines)
        assert "body" in text
        assert "BREAKING" in text

    def test_removed_required_field_not_breaking(self):
        old = {"collections": {"posts": {"schema": {"required": ["title", "body"]}}}}
        new = {"collections": {"posts": {"schema": {"required": ["title"]}}}}
        result = compute_diff(old, new)
        assert result.breaking == 0

    def test_new_property_detected(self):
        old = {"collections": {"posts": {"schema": {"properties": {"title": {"type": "string"}}}}}}
        new = {
            "collections": {
                "posts": {
                    "schema": {
                        "properties": {
                            "title": {"type": "string"},
                            "subtitle": {"type": "string"},
                        }
                    }
                }
            }
        }
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "subtitle" in text


class TestDiffNoChanges:
    def test_identical_manifests_no_output(self):
        m = {"slug": "app", "collections": {"posts": {"auto_crud": True}}}
        result = compute_diff(m, m)
        assert result.lines == []
        assert result.breaking == 0


class TestDiffTopLevel:
    def test_name_change(self):
        old = {"name": "Blog v1"}
        new = {"name": "Blog v2"}
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "name" in text
        assert "Blog v1" in text
        assert "Blog v2" in text


class TestDiffHooks:
    def test_detects_hook_change(self):
        old = {
            "collections": {
                "posts": {
                    "hooks": {
                        "after_create": [{"action": "insert", "collection": "audit"}],
                    }
                }
            }
        }
        new = {
            "collections": {
                "posts": {
                    "hooks": {
                        "after_create": [{"action": "insert", "collection": "log"}],
                    }
                }
            }
        }
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "after_create" in text

    def test_detects_hook_removal(self):
        old = {
            "collections": {
                "posts": {
                    "hooks": {
                        "after_create": [{"action": "insert", "collection": "audit"}],
                        "after_delete": [{"action": "delete", "collection": "comments"}],
                    }
                }
            }
        }
        new = {
            "collections": {
                "posts": {
                    "hooks": {
                        "after_create": [{"action": "insert", "collection": "audit"}],
                    }
                }
            }
        }
        result = compute_diff(old, new)
        text = "\n".join(result.lines)
        assert "after_delete" in text
