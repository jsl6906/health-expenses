# Docker Deployment Guide

This guide explains how to build and deploy the Personal Expenses application using Docker.

## Quick Start

### 1. Using Pre-built Images (Recommended)

If Docker images are available from the dist/ directory:

```bash
# Load the latest image
docker load -i dist/personal-expenses-latest.tar

# Copy and configure docker-compose and environment
cp docker-compose.example.yml docker-compose.yml
cp .env.docker.example .env

# Edit .env file to set your passwords and configuration
# REQUIRED: Set DB_PASSWORD and other sensitive values

# Start the application
docker-compose up -d
```

### 2. Building from Source

```bash
# Clone the repository
git clone <your-repo-url>
cd personal-expenses

# Build the Docker image manually
./scripts/build-docker.sh
# or on Windows:
.\scripts\build-docker.ps1

# Configure and run
cp docker-compose.example.yml docker-compose.yml
cp .env.docker.example .env

# Edit .env file to set your passwords and configuration
# REQUIRED: Set DB_PASSWORD and other sensitive values

docker-compose up -d
```

### 3. Automatic Building with Pre-commit Hooks

Set up automatic Docker image building on every commit:

```bash
# Install and setup pre-commit hooks
./scripts/setup-pre-commit.sh
# or on Windows:
.\scripts\setup-pre-commit.ps1

# Now Docker images will be built automatically when you commit changes
git add .
git commit -m "Your changes"  # This will trigger the Docker build
```

## Configuration

### Environment Variables

The application now uses `.env` files for configuration. Copy `.env.docker.example` to `.env` and customize the values:

```bash
cp .env.docker.example .env
# Edit .env file with your specific configuration
```

**Required Variables:**

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_HOST` | External PostgreSQL host | - | ✅ Yes |
| `DB_PASSWORD` | PostgreSQL password | - | ✅ Yes |
| `DB_NAME` | Database name | `personal_expenses` | No |
| `DB_USER` | Database user | `postgres` | No |
| `DB_PORT` | PostgreSQL port | `5432` | No |

**Optional Variables:**

| Variable | Description | Default |
|----------|-------------|---------|
| `ENV_MODE` | Environment mode (`PROD`/`TEST`) | `PROD` |
| `APP_HOST` | Application host | `0.0.0.0` |
| `APP_PORT` | Application port | `8080` |
| `APP_DEBUG` | Debug mode | `false` |
| `GEMINI_API_KEY` | Gemini API key for EOB parsing | - |
| `GEMINI_MODEL_NAME` | Gemini model | `gemini-1.5-flash` |
| `DB_POOL_SIZE` | Connection pool size | `5` |
| `DB_MAX_OVERFLOW` | Max overflow connections | `10` |
| `DB_ECHO` | SQL query logging | `false` |

### Security Considerations

1. **Never commit `.env` files** to version control
2. **Use strong passwords** for `DB_PASSWORD` (minimum 12 characters)
3. **Keep API keys secure** - don't share your `GEMINI_API_KEY`
4. **Use HTTPS** with a reverse proxy in production
5. **Enable firewall rules** to restrict database access

## Services

The docker-compose setup includes:

- **app**: Personal Expenses web application (port 8080)

The application connects to an external PostgreSQL database based on your configuration in the `.env` file.

## Build Scripts

### PowerShell (Windows)
```powershell
# Basic build
.\scripts\build-docker.ps1

# Build with custom tag
.\scripts\build-docker.ps1 -Tag "v1.0.0"

# Build and push to registry
.\scripts\build-docker.ps1 -Tag "v1.0.0" -Push -Registry "your-registry.com"
```

### Bash (Linux/macOS)
```bash
# Basic build
./scripts/build-docker.sh

# Build with custom tag
./scripts/build-docker.sh -t "v1.0.0"

# Build and push to registry
./scripts/build-docker.sh -t "v1.0.0" -p -r "your-registry.com"
```

## File Structure

```
dist/                           # Built Docker images
├── personal-expenses-latest.tar
└── personal-expenses-v1.0.0.tar

init-scripts/                   # Database initialization
└── 01-init-db.sql

scripts/                        # Build scripts
├── build-docker.ps1           # Windows build script
└── build-docker.sh            # Unix build script

docker-compose.example.yml      # Example Docker Compose configuration
Dockerfile                      # Multi-stage Docker build
.dockerignore                   # Docker build exclusions
```

## Development vs Production

### Development
```bash
# Set environment for development
export ENV_MODE=DEV

# Use docker-compose with development overrides
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### Production
- Use `ENV_MODE=PROD` (default)
- Disable reload in main.py
- Use production-grade PostgreSQL settings
- Add reverse proxy for HTTPS
- Configure proper backup strategies

## Troubleshooting

### Common Issues

1. **Port conflicts**: Change ports in docker-compose.yml
2. **Database connection**: Verify PostgreSQL is healthy
3. **Permission issues**: Check file ownership and Docker socket access

### Logs
```bash
# View application logs
docker-compose logs app

# View database logs
docker-compose logs postgres

# Follow logs in real-time
docker-compose logs -f app
```

### Database Access
```bash
# Connect to your external PostgreSQL database directly
psql -h your-database-host.com -U postgres -d personal_expenses

# Or use your preferred database administration tool
```

## Pre-commit Integration

The application is configured to automatically build Docker images when relevant files change during commits:

### Setup

```bash
# Install pre-commit hooks (one-time setup)
./scripts/setup-pre-commit.sh
# or on Windows:
.\scripts\setup-pre-commit.ps1
```

### How it Works

- When you commit changes to Docker-relevant files, the image is automatically built
- Images are saved to `dist/` with commit hash tags (e.g., `personal-expenses-pre-commit-abc1234.tar`)
- A `personal-expenses-latest.tar` symlink/copy is maintained for convenience
- Only builds when files actually change (uses hash-based change detection)
- Keeps the last 3 built images, automatically cleaning up older ones

### Docker-relevant Files

The pre-commit hook triggers on changes to:
- `Dockerfile`
- `pyproject.toml`
- `main.py`, `init_db.py`
- Any files in `src/`, `templates/`, `static/`

### Manual Build Control

```bash
# Skip pre-commit hooks for a specific commit
git commit --no-verify -m "Skip Docker build"

# Manually run all pre-commit hooks
pre-commit run --all-files

# Manually build Docker image
./scripts/build-docker.sh
```
