"""
Main application entry point.
Run this file to start the Personal Health Expenses Management System.
"""

import uvicorn

from src.app import app

if __name__ == "__main__":
    # Determine if we're in development mode
    import os

    is_dev = os.getenv("ENV_MODE", "PROD").upper() == "DEV"
    port = int(os.getenv("APP_PORT", "8080"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        reload=is_dev,  # Only reload in development
        log_level="info",
    )
