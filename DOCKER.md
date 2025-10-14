# Docker Deployment Guide

This guide explains how to build and deploy the Personal Expenses application using Docker.

## Quick Start

### 1. Using Pre-built Images (Recommended)

If Docker images are available from the dist/ directory:

```bash
# Load the latest image
docker load -i dist/personal-expenses-latest.tar

# Copy and configure docker-compose
cp docker-compose.example.yml docker-compose.yml
# Edit docker-compose.yml to set your passwords and configuration

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
# Edit docker-compose.yml with your configuration
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

Edit your `docker-compose.yml` file to configure:

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_HOST` | PostgreSQL host | `postgres` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `DB_NAME` | Database name | `personal_expenses` |
| `DB_USER` | Database user | `postgres` |
| `DB_PASSWORD` | Database password | **CHANGE THIS** |
| `ENV_MODE` | Environment mode | `PROD` |
| `GOOGLE_API_KEY` | Gemini API key (optional) | - |

### Security Considerations

1. **Change all default passwords** in `docker-compose.yml`
2. **Generate secure random passwords** for PostgreSQL and pgAdmin
3. **Use environment files** for sensitive data in production
4. **Enable HTTPS** with a reverse proxy (nginx/traefik) in production

## Services

The docker-compose setup includes:

- **app**: Personal Expenses web application (port 8080)
- **postgres**: PostgreSQL database (port 5432)
- **pgadmin**: Database administration interface (port 5050, optional)

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
# Connect to PostgreSQL directly
docker-compose exec postgres psql -U postgres -d personal_expenses

# Or use pgAdmin at http://localhost:5050
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
