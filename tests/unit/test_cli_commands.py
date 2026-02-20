"""Tests for mdb_engine CLI commands."""

import json

import pytest
from click.testing import CliRunner

from mdb_engine.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestCLIGroup:
    """Tests for the top-level CLI group."""

    def test_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "mdb-engine" in result.output

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output.lower()


class TestNewAppCommand:
    """Tests for 'mdb-engine new-app'."""

    def test_scaffold_creates_files(self, runner, tmp_path):
        result = runner.invoke(main, ["new-app", "my-app", "-o", str(tmp_path)])
        assert result.exit_code == 0

        app_dir = tmp_path / "my-app"
        assert app_dir.is_dir()
        assert (app_dir / "web.py").exists()
        assert (app_dir / "manifest.json").exists()

    def test_manifest_has_correct_slug(self, runner, tmp_path):
        runner.invoke(main, ["new-app", "test-slug", "-o", str(tmp_path)])
        manifest = json.loads((tmp_path / "test-slug" / "manifest.json").read_text())
        assert manifest["slug"] == "test-slug"
        assert manifest["schema_version"] == "2.0"

    def test_services_flag_adds_memory_config(self, runner, tmp_path):
        runner.invoke(main, ["new-app", "ai-app", "--services", "memory,graph", "-o", str(tmp_path)])
        manifest = json.loads((tmp_path / "ai-app" / "manifest.json").read_text())
        assert "memory_config" in manifest
        assert manifest["memory_config"]["enabled"] is True
        assert "graph_config" in manifest
        assert manifest["graph_config"]["enabled"] is True

    def test_existing_directory_fails(self, runner, tmp_path):
        (tmp_path / "exists").mkdir()
        result = runner.invoke(main, ["new-app", "exists", "-o", str(tmp_path)])
        assert result.exit_code != 0

    def test_mode_flag(self, runner, tmp_path):
        runner.invoke(main, ["new-app", "standalone", "--mode", "app", "-o", str(tmp_path)])
        manifest = json.loads((tmp_path / "standalone" / "manifest.json").read_text())
        assert manifest["auth"]["mode"] == "app"


class TestValidateCommand:
    """Tests for 'mdb-engine validate'."""

    def test_valid_manifest(self, runner, tmp_path):
        manifest = {"schema_version": "2.0", "slug": "ok", "name": "OK App"}
        mf = tmp_path / "manifest.json"
        mf.write_text(json.dumps(manifest))
        result = runner.invoke(main, ["validate", str(mf)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_invalid_manifest_missing_slug(self, runner, tmp_path):
        manifest = {"schema_version": "2.0"}
        mf = tmp_path / "manifest.json"
        mf.write_text(json.dumps(manifest))
        result = runner.invoke(main, ["validate", str(mf)])
        assert result.exit_code != 0
