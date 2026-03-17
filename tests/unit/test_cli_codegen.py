"""Tests for the TypeScript contract generation CLI command."""

from __future__ import annotations

from mdb_engine.cli.commands.codegen import generate_typescript


class TestCodegenTypeScriptInterfaces:
    def test_generates_interface_from_schema(self):
        manifest = {
            "collections": {
                "posts": {
                    "schema": {
                        "properties": {
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "views": {"type": "integer"},
                            "published": {"type": "boolean"},
                        },
                        "required": ["title"],
                    }
                }
            }
        }
        output = generate_typescript(manifest)
        assert "export interface Posts" in output
        assert "title: string;" in output
        assert "body?: string;" in output
        assert "views?: number;" in output
        assert "published?: boolean;" in output

    def test_generates_create_type(self):
        manifest = {"collections": {"posts": {"schema": {"properties": {"title": {"type": "string"}}}}}}
        output = generate_typescript(manifest)
        assert "PostsCreate" in output
        assert "Omit<Posts" in output

    def test_enum_type(self):
        manifest = {
            "collections": {
                "posts": {
                    "schema": {
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["draft", "published", "archived"],
                            },
                        },
                    }
                }
            }
        }
        output = generate_typescript(manifest)
        assert '"draft"' in output
        assert '"published"' in output

    def test_array_type(self):
        manifest = {
            "collections": {
                "posts": {
                    "schema": {
                        "properties": {
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    }
                }
            }
        }
        output = generate_typescript(manifest)
        assert "string[]" in output


class TestCodegenTypeScriptFunctions:
    def test_generates_crud_functions(self):
        manifest = {
            "collections": {
                "posts": {
                    "schema": {"properties": {"title": {"type": "string"}}},
                }
            }
        }
        output = generate_typescript(manifest)
        assert "listPostss" in output or "listPosts" in output
        assert "getPosts" in output
        assert "createPosts" in output
        assert "updatePosts" in output
        assert "deletePosts" in output
        assert "countPosts" in output

    def test_read_only_omits_write_functions(self):
        manifest = {
            "collections": {
                "posts": {
                    "read_only": True,
                    "schema": {"properties": {"title": {"type": "string"}}},
                }
            }
        }
        output = generate_typescript(manifest)
        assert "listPosts" in output
        assert "createPosts" not in output
        assert "updatePosts" not in output
        assert "deletePosts" not in output

    def test_scope_types(self):
        manifest = {
            "collections": {
                "posts": {
                    "scopes": {"published": {}, "featured": {}},
                    "schema": {"properties": {}},
                }
            }
        }
        output = generate_typescript(manifest)
        assert '"published"' in output
        assert '"featured"' in output

    def test_auto_crud_false_excluded(self):
        manifest = {
            "collections": {
                "internal": {"auto_crud": False, "schema": {"properties": {}}},
                "posts": {"schema": {"properties": {"title": {"type": "string"}}}},
            }
        }
        output = generate_typescript(manifest)
        assert "Internal" not in output
        assert "Posts" in output

    def test_base_url_included(self):
        manifest = {"collections": {"posts": {"schema": {"properties": {}}}}}
        output = generate_typescript(manifest, base_url="/app1")
        assert 'BASE_URL = "/app1"' in output

    def test_fetch_helper_included(self):
        manifest = {"collections": {"posts": {"schema": {"properties": {}}}}}
        output = generate_typescript(manifest)
        assert "async function apiFetch" in output
