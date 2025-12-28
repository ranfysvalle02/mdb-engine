"""
Tests for migrate command.

Tests the manifest migration CLI command.
"""

import json
import tempfile
from pathlib import Path

from click.testing import CliRunner

from mdb_engine.cli.main import cli


class TestMigrateCommand:
    """Test the migrate command."""

    def test_migrate_to_latest_version(self):
        """Test migrating a manifest to latest version."""
        runner = CliRunner()

        # Create a temporary manifest (v1.0 style)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            manifest = {
                "slug": "test_app",
                "name": "Test App",
                "status": "active",
            }
            json.dump(manifest, f)
            manifest_path = Path(f.name)

        try:
            result = runner.invoke(cli, ["migrate", str(manifest_path)])
            assert result.exit_code == 0
            # Check output contains migrated manifest
            assert "schema_version" in result.output or "2.0" in result.output
        finally:
            manifest_path.unlink()

    def test_migrate_with_output_file(self):
        """Test migrating with output file."""
        runner = CliRunner()

        # Create a temporary manifest
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            manifest = {
                "slug": "test_app",
                "name": "Test App",
                "status": "active",
            }
            json.dump(manifest, f)
            manifest_path = Path(f.name)

        # Create output file path
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_path = Path(f.name)

        try:
            result = runner.invoke(
                cli, ["migrate", str(manifest_path), "--output", str(output_path)]
            )
            assert result.exit_code == 0
            assert output_path.exists()
            # Verify migrated manifest
            with open(output_path) as f:
                migrated = json.load(f)
                assert "schema_version" in migrated
        finally:
            manifest_path.unlink()
            if output_path.exists():
                output_path.unlink()

    def test_migrate_in_place(self):
        """Test migrating in place."""
        runner = CliRunner()

        # Create a temporary manifest
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            manifest = {
                "slug": "test_app",
                "name": "Test App",
                "status": "active",
            }
            json.dump(manifest, f)
            manifest_path = Path(f.name)

        try:
            result = runner.invoke(cli, ["migrate", str(manifest_path), "--in-place"])
            assert result.exit_code == 0
            # Verify file was updated
            with open(manifest_path) as f:
                migrated = json.load(f)
                assert "schema_version" in migrated
        finally:
            manifest_path.unlink()
