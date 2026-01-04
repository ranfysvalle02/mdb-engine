"""
Tests for show command.

Tests the manifest display CLI command.
"""

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from mdb_engine.cli.main import cli


class TestShowCommand:
    """Test the show command."""

    def test_show_manifest_json(self):
        """Test showing manifest in JSON format."""
        runner = CliRunner()

        # Create a temporary manifest
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
            result = runner.invoke(cli, ["show", str(manifest_path)])
            assert result.exit_code == 0
            assert "test_app" in result.output
            assert "Test App" in result.output
        finally:
            manifest_path.unlink()

    def test_show_manifest_pretty(self):
        """Test showing manifest in pretty format."""
        runner = CliRunner()

        # Create a temporary manifest
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
            result = runner.invoke(cli, ["show", str(manifest_path), "--format", "pretty"])
            assert result.exit_code == 0
            assert "test_app" in result.output
            assert "Test App" in result.output
        finally:
            manifest_path.unlink()

    def test_show_with_validation(self):
        """Test showing manifest with validation."""
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
            runner_no_mix = CliRunner(mix_stderr=False)
            result = runner_no_mix.invoke(cli, ["show", str(manifest_path), "--validate"])
            assert result.exit_code == 0
            # Should not show warnings for valid manifest
            output_lower = (result.output + result.stderr).lower()
            assert "warning" not in output_lower and "invalid" not in output_lower
        finally:
            manifest_path.unlink()

    def test_show_invalid_manifest_with_validation(self):
        """Test showing invalid manifest with validation."""
        # Create a temporary invalid manifest
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            manifest = {
                "schema_version": "2.0",
                # Missing slug and name
            }
            json.dump(manifest, f)
            manifest_path = Path(f.name)

        try:
            runner_no_mix = CliRunner(mix_stderr=False)
            result = runner_no_mix.invoke(cli, ["show", str(manifest_path), "--validate"])
            assert result.exit_code == 0  # Show still works, just warns
            # Check both stdout and stderr for warning/invalid message
            output_lower = (result.output + result.stderr).lower()
            assert "warning" in output_lower or "invalid" in output_lower
        finally:
            manifest_path.unlink()
