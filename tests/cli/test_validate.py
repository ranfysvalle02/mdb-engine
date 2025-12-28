"""
Tests for validate command.

Tests the manifest validation CLI command.
"""

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from mdb_engine.cli.main import cli


class TestValidateCommand:
    """Test the validate command."""

    def test_validate_valid_manifest(self):
        """Test validating a valid manifest."""
        runner = CliRunner()

        # Create a temporary valid manifest
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            manifest = {
                "schema_version": "2.0",
                "slug": "test_app",
                "name": "Test App",
                "status": "active",
            }
            json.dump(manifest, f)
            manifest_path = Path(f.name)

        try:
            result = runner.invoke(cli, ["validate", str(manifest_path)])
            assert result.exit_code == 0
            assert "valid" in result.output.lower()
        finally:
            manifest_path.unlink()

    def test_validate_invalid_manifest(self):
        """Test validating an invalid manifest."""
        runner = CliRunner()

        # Create a temporary invalid manifest (missing required fields)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            manifest = {
                "schema_version": "2.0",
                # Missing slug and name
            }
            json.dump(manifest, f)
            manifest_path = Path(f.name)

        try:
            result = runner.invoke(cli, ["validate", str(manifest_path)])
            assert result.exit_code == 1
            assert "invalid" in result.output.lower()
        finally:
            manifest_path.unlink()

    def test_validate_nonexistent_file(self):
        """Test validating a non-existent file."""
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "nonexistent.json"])
        assert result.exit_code != 0

    def test_validate_with_verbose(self):
        """Test validating with verbose output."""
        runner = CliRunner()

        # Create a temporary invalid manifest
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            manifest = {"schema_version": "2.0"}  # Missing required fields
            json.dump(manifest, f)
            manifest_path = Path(f.name)

        try:
            result = runner.invoke(cli, ["validate", str(manifest_path), "--verbose"])
            assert result.exit_code == 1
            assert "invalid" in result.output.lower()
        finally:
            manifest_path.unlink()
