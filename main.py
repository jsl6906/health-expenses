"""
Main application entry point.
Run this file to start the Personal Health Expenses Management System.
"""

import uvicorn
from src.app import app

if __name__ == "__main__":
    uvicorn.run(
        "src.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
