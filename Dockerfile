# Multi-stage Docker build for Personal Expenses Application

# Build stage
FROM python:3.13-slim as builder

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_CACHE_DIR=/tmp/uv-cache

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Create working directory
WORKDIR /app

# Copy Python configuration files
COPY pyproject.toml README.md ./

# Install dependencies using uv
RUN uv venv && \
    uv pip install --no-cache .

# Production stage
FROM python:3.13-slim as production

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"
# Include both /app (to import package `src`) and /app/src (to support top-level imports like `models`)
ENV PYTHONPATH="/app:/app/src"

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash app

# Create working directory
WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY src/ src/
COPY templates/ templates/
COPY static/ static/
COPY main.py .
COPY init_db.py .
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Change ownership to app user
RUN chown -R app:app /app

# Switch to app user
USER app

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Default command
ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
CMD ["python", "main.py"]
