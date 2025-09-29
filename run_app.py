#!/usr/bin/env python3
"""
Entry point for running the Personal Health Expenses FastAPI application.
This script handles the import path properly and starts the FastAPI server.
"""

import sys
import uvicorn
from pathlib import Path

# Add the src directory to Python path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

def main():
    """Run the FastAPI application with uvicorn."""
    print("Starting Personal Health Expenses Management System...")
    print("Web Interface: http://localhost:8000")
    print("API Documentation: http://localhost:8000/docs")
    print("Press Ctrl+C to stop the server")
    print("-" * 50)
    
    try:
        # Import and test the app first
        print("Loading application...")
        import importlib

        module = importlib.import_module("app")
        loaded_app = getattr(module, "app", None)
        if loaded_app is None:
            raise RuntimeError("FastAPI 'app' instance not found in app module")
        print(f"✅ Application loaded successfully! Title: {loaded_app.title}")
        
        # Run the server
        uvicorn.run(
            "app:app",  # Use string import for proper reload
            host="0.0.0.0",
            port=8000,
            reload=False,  # Disable reload to avoid warnings
            log_level="info"
        )
    except Exception as e:
        print(f"❌ Error starting application: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()