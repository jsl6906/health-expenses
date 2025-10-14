#!/usr/bin/env python3
"""
Entry point for running the Personal Health Expenses FastAPI application.
This script handles the import path properly and starts the FastAPI server.
"""

import sys
from pathlib import Path

import uvicorn

# Add the src directory to Python path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))


def main():
    """Run the FastAPI application with uvicorn."""
    import os

    port = int(os.getenv("APP_PORT", "8080"))

    print("Starting Personal Health Expenses Management System...")
    print(f"Web Interface: http://localhost:{port}")
    print(f"API Documentation: http://localhost:{port}/docs")
    print("Press Ctrl+C to stop the server")
    print("-" * 50)

    try:
        # Import and test the app first
        print("Loading application...")
        import importlib

        module = importlib.import_module("src.app")
        loaded_app = getattr(module, "app", None)
        if loaded_app is None:
            raise RuntimeError("FastAPI 'app' instance not found in src.app module")
        print(f"✅ Application loaded successfully! Title: {loaded_app.title}")

        # Run the server
        uvicorn.run(
            "src.app:app",  # Use full module path for proper reload and relative imports
            host="0.0.0.0",
            port=port,
            reload=False,  # Disable reload to avoid warnings
            log_level="info",
        )
    except Exception as e:
        print(f"❌ Error starting application: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
