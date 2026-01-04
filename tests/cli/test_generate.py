"""
Tests for generate command.

Tests the manifest generation CLI command.

NOTE: The `generate` command is now a group with subcommands:
- `generate manifest` - Generate manifest.json file
- `generate app` - Generate full app structure
"""

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from mdb_engine.cli.main import cli


class TestGenerateManifestCommand:
    """Test the generate manifest subcommand."""

    def test_generate_basic_manifest(self):
        """Test generating a basic manifest."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "manifest.json"

            result = runner.invoke(
                cli,
                [
                    "generate",
                    "manifest",  # Subcommand
                    "--slug",
                    "test_app",
                    "--name",
                    "Test App",
                    "--output",
                    str(output_path),
                ],
            )

            assert result.exit_code == 0, f"Command failed: {result.output}"
            assert output_path.exists()

            # Verify manifest structure
            with open(output_path) as f:
                manifest = json.load(f)
                assert manifest["slug"] == "test_app"
                assert manifest["name"] == "Test App"
                assert "schema_version" in manifest

    def test_generate_minimal_manifest(self):
        """Test generating a minimal manifest."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "manifest.json"

            result = runner.invoke(
                cli,
                [
                    "generate",
                    "manifest",  # Subcommand
                    "--slug",
                    "test_app",
                    "--name",
                    "Test App",
                    "--minimal",
                    "--output",
                    str(output_path),
                ],
            )

            assert result.exit_code == 0, f"Command failed: {result.output}"
            assert output_path.exists()

            # Verify minimal manifest (no auth, indexes, etc.)
            with open(output_path) as f:
                manifest = json.load(f)
                assert manifest["slug"] == "test_app"
                assert "auth" not in manifest or not manifest.get("auth")
                assert "managed_indexes" not in manifest

    def test_generate_with_description(self):
        """Test generating manifest with description."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "manifest.json"

            result = runner.invoke(
                cli,
                [
                    "generate",
                    "manifest",  # Subcommand
                    "--slug",
                    "test_app",
                    "--name",
                    "Test App",
                    "--description",
                    "A test app",
                    "--output",
                    str(output_path),
                ],
            )

            assert result.exit_code == 0, f"Command failed: {result.output}"

            with open(output_path) as f:
                manifest = json.load(f)
                assert manifest["description"] == "A test app"

    def test_generate_invalid_slug(self):
        """Test generating with invalid slug format."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "manifest.json"

            result = runner.invoke(
                cli,
                [
                    "generate",
                    "manifest",  # Subcommand
                    "--slug",
                    "Invalid Slug!",  # Invalid - contains space and exclamation
                    "--name",
                    "Test App",
                    "--output",
                    str(output_path),
                ],
            )

            assert result.exit_code != 0
            assert "invalid" in result.output.lower()


class TestGenerateAppCommand:
    """Test the generate app subcommand."""

    def test_generate_basic_app(self):
        """Test generating a basic app structure."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                cli,
                [
                    "generate",
                    "app",
                    "--slug",
                    "test_app",
                    "--name",
                    "Test App",
                    "--output",
                    tmpdir,
                ],
            )

            assert result.exit_code == 0, f"Command failed: {result.output}"

            app_path = Path(tmpdir) / "test_app"
            assert app_path.exists()
            assert (app_path / "manifest.json").exists()
            assert (app_path / "web.py").exists()
            assert (app_path / "templates" / "index.html").exists()

    def test_generate_app_with_ray(self):
        """Test generating app with Ray support."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                cli,
                [
                    "generate",
                    "app",
                    "--slug",
                    "ray_app",
                    "--name",
                    "Ray App",
                    "--output",
                    tmpdir,
                    "--ray",
                ],
            )

            assert result.exit_code == 0, f"Command failed: {result.output}"

            app_path = Path(tmpdir) / "ray_app"
            assert (app_path / "actors" / "__init__.py").exists()

    def test_generate_app_multi_site(self):
        """Test generating multi-site app."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(
                cli,
                [
                    "generate",
                    "app",
                    "--slug",
                    "multi_app",
                    "--name",
                    "Multi App",
                    "--output",
                    tmpdir,
                    "--multi-site",
                ],
            )

            assert result.exit_code == 0, f"Command failed: {result.output}"

            app_path = Path(tmpdir) / "multi_app"

            # Check manifest has multi-site config
            with open(app_path / "manifest.json") as f:
                manifest = json.load(f)
                assert manifest["data_access"]["cross_app_policy"] == "explicit"
