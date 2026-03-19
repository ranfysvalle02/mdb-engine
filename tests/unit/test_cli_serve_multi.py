"""Tests for ``mdb-engine serve-multi`` CLI command."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mdb_engine.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestServeMultiHelp:
    def test_help_shows_options(self, runner):
        result = runner.invoke(main, ["serve-multi", "--help"])
        assert result.exit_code == 0
        assert "--apps-dir" in result.output
        assert "--manifest" in result.output
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--reload" in result.output


class TestServeMultiValidation:
    def test_neither_option_fails(self, runner):
        result = runner.invoke(main, ["serve-multi"])
        assert result.exit_code != 0
        assert "at least one is required" in result.output.lower()

    def test_both_options_fails(self, runner, tmp_path):
        apps = tmp_path / "apps"
        apps.mkdir()
        mf = tmp_path / "multi.json"
        mf.write_text("{}")
        result = runner.invoke(main, ["serve-multi", "--apps-dir", str(apps), "--manifest", str(mf)])
        assert result.exit_code != 0
        assert "not both" in result.output.lower()

    def test_apps_dir_missing_fails(self, runner, tmp_path):
        missing = tmp_path / "nope"
        result = runner.invoke(main, ["serve-multi", "--apps-dir", str(missing)])
        assert result.exit_code != 0

    def test_manifest_missing_fails(self, runner, tmp_path):
        missing = tmp_path / "nope.json"
        result = runner.invoke(main, ["serve-multi", "--manifest", str(missing)])
        assert result.exit_code != 0


class TestServeMultiAppsDir:
    def test_empty_apps_dir_fails(self, runner, tmp_path):
        apps = tmp_path / "apps"
        apps.mkdir()
        result = runner.invoke(main, ["serve-multi", "--apps-dir", str(apps)])
        assert result.exit_code != 0
        assert "No apps found" in result.output

    @patch("mdb_engine.cli.commands.serve_multi._uvicorn")
    def test_discovers_manifests(self, mock_uvicorn, runner, tmp_path):
        apps = tmp_path / "apps"

        blog_a = apps / "tech"
        blog_a.mkdir(parents=True)
        (blog_a / "manifest.json").write_text(
            json.dumps({"schema_version": "2.0", "slug": "tech_blog", "name": "Tech Blog"})
        )

        blog_b = apps / "cooking"
        blog_b.mkdir(parents=True)
        (blog_b / "manifest.json").write_text(
            json.dumps({"schema_version": "2.0", "slug": "cooking_blog", "name": "Cooking Blog"})
        )

        result = runner.invoke(main, ["serve-multi", "--apps-dir", str(apps)])

        assert result.exit_code == 0
        assert "2 discovered" in result.output
        assert "tech_blog" in result.output
        assert "cooking_blog" in result.output

    @patch("mdb_engine.cli.commands.serve_multi._uvicorn")
    def test_falls_back_to_dir_name_when_slug_missing(self, mock_uvicorn, runner, tmp_path):
        apps = tmp_path / "apps"
        child = apps / "my-app"
        child.mkdir(parents=True)
        (child / "manifest.json").write_text(json.dumps({"schema_version": "2.0", "name": "X"}))

        result = runner.invoke(main, ["serve-multi", "--apps-dir", str(apps)])

        assert result.exit_code == 0
        assert "my-app" in result.output

    @patch("mdb_engine.cli.commands.serve_multi._uvicorn")
    def test_skips_invalid_json(self, mock_uvicorn, runner, tmp_path):
        apps = tmp_path / "apps"

        good = apps / "good"
        good.mkdir(parents=True)
        (good / "manifest.json").write_text(json.dumps({"slug": "good-app"}))

        bad = apps / "bad"
        bad.mkdir(parents=True)
        (bad / "manifest.json").write_text("NOT JSON")

        result = runner.invoke(main, ["serve-multi", "--apps-dir", str(apps)])

        assert result.exit_code == 0
        assert "1 discovered" in result.output
        assert "good-app" in result.output


class TestServeMultiManifest:
    @patch("mdb_engine.cli.commands.serve_multi._uvicorn")
    def test_reads_manifest_entries(self, mock_uvicorn, runner, tmp_path):
        mf = tmp_path / "multi.json"
        mf.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "multi_app": {
                        "enabled": True,
                        "apps": [
                            {"slug": "blog-a", "manifest": "./a/manifest.json", "path_prefix": "/blog-a"},
                            {"slug": "blog-b", "manifest": "./b/manifest.json", "path_prefix": "/blog-b"},
                        ],
                    },
                }
            )
        )
        result = runner.invoke(main, ["serve-multi", "--manifest", str(mf)])

        assert result.exit_code == 0
        assert "2 configured" in result.output
        assert "/blog-a" in result.output
        assert "/blog-b" in result.output

    def test_invalid_manifest_json_fails(self, runner, tmp_path):
        mf = tmp_path / "multi.json"
        mf.write_text("NOT JSON")
        result = runner.invoke(main, ["serve-multi", "--manifest", str(mf)])
        assert result.exit_code != 0
        assert "Failed to read" in result.output


class TestServeMultiUvicornArgs:
    @patch("mdb_engine.cli.commands.serve_multi._uvicorn")
    def test_passes_host_port_reload(self, mock_uvicorn, runner, tmp_path):
        apps = tmp_path / "apps"
        child = apps / "x"
        child.mkdir(parents=True)
        (child / "manifest.json").write_text(json.dumps({"slug": "x"}))

        result = runner.invoke(
            main,
            ["serve-multi", "--apps-dir", str(apps), "--host", "127.0.0.1", "--port", "3000", "--reload"],
        )

        assert result.exit_code == 0
        mock_uvicorn.run.assert_called_once_with(
            "mdb_engine.cli._serve_multi_app:app",
            host="127.0.0.1",
            port=3000,
            reload=True,
            log_level="info",
        )

    @patch("mdb_engine.cli.commands.serve_multi._uvicorn")
    def test_sets_env_vars_for_apps_dir(self, mock_uvicorn, runner, tmp_path, monkeypatch):
        apps = tmp_path / "apps"
        child = apps / "a"
        child.mkdir(parents=True)
        (child / "manifest.json").write_text(json.dumps({"slug": "a"}))

        captured_env = {}

        def fake_run(*_args, **_kwargs):
            import os

            captured_env["mode"] = os.environ.get("_MDB_SERVE_MULTI_MODE")
            captured_env["path"] = os.environ.get("_MDB_SERVE_MULTI_PATH")
            captured_env["title"] = os.environ.get("_MDB_SERVE_MULTI_TITLE")

        mock_uvicorn.run.side_effect = fake_run

        result = runner.invoke(
            main,
            ["serve-multi", "--apps-dir", str(apps), "--title", "My Platform"],
        )

        assert result.exit_code == 0
        assert captured_env["mode"] == "apps_dir"
        assert captured_env["path"] == str(apps.resolve())
        assert captured_env["title"] == "My Platform"
