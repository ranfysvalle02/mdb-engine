"""
Tests for generate command.

Tests the manifest generation CLI command.
"""

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from mdb_engine.cli.main import cli


class TestGenerateCommand:
    """Test the generate command."""

    def test_generate_basic_manifest(self):
        """Test generating a basic manifest."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "manifest.json"

            result = runner.invoke(
                cli,
                [
                    "generate",
                    "--slug",
                    "test_app",
                    "--name",
                    "Test App",
                    "--output",
                    str(output_path),
                ],
            )

            assert result.exit_code == 0
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
                    "--slug",
                    "test_app",
                    "--name",
                    "Test App",
                    "--minimal",
                    "--output",
                    str(output_path),
                ],
            )

            assert result.exit_code == 0
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

            assert result.exit_code == 0

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
