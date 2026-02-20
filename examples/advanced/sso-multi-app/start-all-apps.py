#!/usr/bin/env python3
"""
Bundled Startup Script for SSO Multi-App
==========================================
Runs all apps (master + subapps) in a single container on different ports.

Each app runs in its own thread with its own uvicorn server.

Port Mapping:
- Port 8000: auth-hub (master app)
- Port 8001: pwd-zero
- Port 8002: FLUX
"""

import logging
import os
import sys
import threading
from pathlib import Path

import uvicorn

logger = logging.getLogger(__name__)


def run_app(module_path: str, port: int, app_name: str) -> None:
    """
    Run a FastAPI app on a specific port.

    Dynamically imports the module, extracts the FastAPI app instance,
    and runs it with uvicorn.
    """
    import importlib.util

    try:
        logger.info(f"Starting {app_name} on port {port}")

        # Import the module dynamically
        spec = importlib.util.spec_from_file_location(app_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create spec for {module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Get the FastAPI app instance
        if not hasattr(module, "app"):
            raise AttributeError(f"Module {module_path} does not have an 'app' attribute")

        app = module.app

        # Run uvicorn server (blocking call)
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
            access_log=False,  # Reduce log noise with multiple apps
        )

    except (ImportError, AttributeError) as e:
        logger.error(f"Failed to load {app_name}: {e}", exc_info=True)
        sys.exit(1)
    except (OSError, RuntimeError) as e:
        logger.error(f"Failed to start {app_name} on port {port}: {e}", exc_info=True)
        sys.exit(1)


def main() -> None:
    """Start all apps concurrently in separate threads."""
    logger.info("=" * 60)
    logger.info("Starting All SSO Multi-Apps (Bundled Mode)")
    logger.info("=" * 60)

    # App configuration: (module_path, port, display_name)
    apps: list[tuple[str, int, str]] = [
        ("auth-hub/web.py", 8000, "SSO Auth Hub"),
        ("sso-app-1/web.py", 8001, "pwd-zero"),
        ("sso-app-2/web.py", 8002, "FLUX"),
    ]

    apps_dir = Path(__file__).parent / "apps"
    threads: list[threading.Thread] = []

    # Validate all modules exist before starting
    for module_rel_path, _port, _app_name in apps:
        module_path = apps_dir / module_rel_path
        if not module_path.exists():
            logger.error(f"Module not found: {module_path}")
            sys.exit(1)

    # Start all apps in separate threads
    for module_rel_path, port, app_name in apps:
        module_path = apps_dir / module_rel_path

        thread = threading.Thread(
            target=run_app,
            args=(str(module_path), port, app_name),
            daemon=False,  # Main process waits for all threads
            name=f"{app_name.replace(' ', '-').lower()}-thread",
        )
        thread.start()
        threads.append(thread)
        logger.info(f"Started {app_name} on port {port}")

    logger.info("=" * 60)
    logger.info("All apps started. Waiting for threads...")
    logger.info("=" * 60)

    # Wait for all threads (blocking until interrupted)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
