"""Tests for the manifest dry-run CLI command."""

from __future__ import annotations

from mdb_engine.cli.commands.dry_run import analyze_manifest


class TestDryRunRoutes:
    def test_basic_crud_routes(self):
        manifest = {
            "collections": {
                "posts": {"auto_crud": True},
            }
        }
        report = analyze_manifest(manifest)
        paths = [r["path"] for r in report.routes]
        assert "/api/posts" in paths
        assert "/api/posts/_count" in paths
        assert "/api/posts/{id}" in paths

    def test_read_only_collection(self):
        manifest = {
            "collections": {
                "posts": {"auto_crud": True, "read_only": True},
            }
        }
        report = analyze_manifest(manifest)
        methods = [r["method"] for r in report.routes]
        assert "POST" not in methods
        assert "PUT" not in methods
        assert "PATCH" not in methods
        assert "DELETE" not in methods

    def test_soft_delete_adds_trash_and_restore(self):
        manifest = {
            "collections": {
                "posts": {"auto_crud": True, "soft_delete": True},
            }
        }
        report = analyze_manifest(manifest)
        paths = [r["path"] for r in report.routes]
        assert "/api/posts/_trash" in paths
        assert "/api/posts/{id}/_restore" in paths

    def test_auto_crud_false_excluded(self):
        manifest = {
            "collections": {
                "posts": {"auto_crud": False},
                "comments": {"auto_crud": True},
            }
        }
        report = analyze_manifest(manifest)
        paths = [r["path"] for r in report.routes]
        assert not any("posts" in p for p in paths)
        assert "/api/comments" in paths

    def test_pipeline_routes(self):
        manifest = {
            "collections": {
                "posts": {
                    "auto_crud": True,
                    "pipelines": {
                        "by_status": [{"$group": {"_id": "$status"}}],
                        "top_authors": [{"$group": {"_id": "$author"}}],
                    },
                },
            }
        }
        report = analyze_manifest(manifest)
        paths = [r["path"] for r in report.routes]
        assert "/api/posts/_agg/by_status" in paths
        assert "/api/posts/_agg/top_authors" in paths

    def test_auth_labels(self):
        manifest = {
            "auth": {"users": {"enabled": True}},
            "collections": {
                "posts": {
                    "auto_crud": True,
                    "auth": {"write_roles": ["editor"]},
                },
            },
        }
        report = analyze_manifest(manifest)
        write_routes = [r for r in report.routes if r["method"] == "POST"]
        assert any("editor" in r["auth"] for r in write_routes)


class TestDryRunIndexes:
    def test_unique_index_from_schema(self):
        manifest = {
            "collections": {
                "users": {
                    "schema": {
                        "properties": {
                            "email": {"type": "string", "x-unique": True},
                        }
                    }
                }
            }
        }
        report = analyze_manifest(manifest)
        assert any(idx["name"] == "auto_unique_email" for idx in report.indexes)

    def test_ttl_index(self):
        manifest = {
            "collections": {
                "sessions": {
                    "ttl": {"field": "expires_at", "expire_after": "24h"},
                }
            }
        }
        report = analyze_manifest(manifest)
        assert any(idx["type"] == "ttl" for idx in report.indexes)

    def test_managed_indexes(self):
        manifest = {
            "collections": {},
            "managed_indexes": {
                "posts": [
                    {"type": "regular", "keys": {"status": 1}, "name": "idx_status"},
                ]
            },
        }
        report = analyze_manifest(manifest)
        assert any(idx["name"] == "idx_status" for idx in report.indexes)


class TestDryRunScopes:
    def test_scopes_listed(self):
        manifest = {
            "collections": {
                "posts": {
                    "scopes": {
                        "published": {"status": "published"},
                        "featured": {"featured": True},
                    }
                }
            }
        }
        report = analyze_manifest(manifest)
        assert "posts" in report.scopes
        assert "published" in report.scopes["posts"]
        assert "featured" in report.scopes["posts"]


class TestDryRunHooks:
    def test_hooks_listed(self):
        manifest = {
            "collections": {
                "posts": {
                    "hooks": {
                        "after_create": [
                            {"action": "insert", "collection": "audit_log"},
                        ]
                    }
                }
            }
        }
        report = analyze_manifest(manifest)
        assert len(report.hooks) == 1
        assert report.hooks[0]["event"] == "after_create"
        assert report.hooks[0]["action"] == "insert"

    def test_conditional_hook_flagged(self):
        manifest = {
            "collections": {
                "posts": {
                    "hooks": {
                        "after_update": [
                            {
                                "action": "insert",
                                "collection": "notifications",
                                "if": {"doc.status": "published"},
                            }
                        ]
                    }
                }
            }
        }
        report = analyze_manifest(manifest)
        assert report.hooks[0].get("condition") == "conditional"


class TestDryRunAuth:
    def test_auth_summary(self):
        manifest = {
            "auth": {
                "mode": "app",
                "users": {"enabled": True, "allow_registration": True},
                "policy": {"provider": "casbin"},
            }
        }
        report = analyze_manifest(manifest)
        assert report.auth_summary["mode"] == "app"
        assert report.auth_summary["users_enabled"] is True
        assert report.auth_summary["registration"] is True
        assert report.auth_summary["policy_provider"] == "casbin"
