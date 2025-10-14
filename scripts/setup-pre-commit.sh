#!/bin/bash
# Setup pre-commit hooks for Personal Expenses Application

set -e

# Default values
SKIP_DOCKER=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --skip-docker)
      SKIP_DOCKER=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo "Options:"
      echo "  --skip-docker    Skip Docker availability check"
      echo "  -h, --help       Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

echo "🔧 Setting up pre-commit hooks for Personal Expenses Application..."

# Check if we're in the right directory
if [ ! -f ".pre-commit-config.yaml" ]; then
    echo "❌ Error: .pre-commit-config.yaml not found. Run this script from the project root."
    exit 1
fi

# Check if Python is available
if command -v python3 >/dev/null 2>&1; then
    PYTHON_VERSION=$(python3 --version)
    echo "✅ Python found: $PYTHON_VERSION"
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_VERSION=$(python --version)
    echo "✅ Python found: $PYTHON_VERSION"
    PYTHON_CMD="python"
else
    echo "❌ Python not found. Please install Python 3.8+ and ensure it's in your PATH."
    exit 1
fi

# Check if pip is available
if command -v pip3 >/dev/null 2>&1; then
    PIP_CMD="pip3"
elif command -v pip >/dev/null 2>&1; then
    PIP_CMD="pip"
else
    echo "❌ pip not found. Please install pip."
    exit 1
fi

echo "✅ pip found: $($PIP_CMD --version)"

# Check if Docker is available (unless skipped)
if [ "$SKIP_DOCKER" = false ]; then
    if command -v docker >/dev/null 2>&1; then
        DOCKER_VERSION=$(docker --version)
        echo "✅ Docker found: $DOCKER_VERSION"
    else
        echo "❌ Docker not found. Please install Docker."
        echo "   Or use --skip-docker flag if you don't want Docker build hooks."
        exit 1
    fi
fi

# Check if git is available
if command -v git >/dev/null 2>&1; then
    GIT_VERSION=$(git --version)
    echo "✅ Git found: $GIT_VERSION"
else
    echo "❌ Git not found. Please install Git."
    exit 1
fi

# Install pre-commit
echo "📦 Installing pre-commit..."
if $PIP_CMD install pre-commit; then
    PRE_COMMIT_VERSION=$(pre-commit --version)
    echo "✅ pre-commit installed: $PRE_COMMIT_VERSION"
else
    echo "❌ Failed to install pre-commit"
    exit 1
fi

# Make scripts executable
echo "🔧 Setting up script permissions..."
chmod +x scripts/*.sh

# Create dist directory
DIST_DIR="dist"
if [ ! -d "$DIST_DIR" ]; then
    mkdir -p "$DIST_DIR"
    echo "✅ Created dist directory"
fi

# Install pre-commit hooks
echo "🔗 Installing pre-commit git hooks..."
if pre-commit install; then
    echo "✅ Pre-commit hooks installed successfully!"
else
    echo "❌ Failed to install pre-commit hooks"
    exit 1
fi

# Test the installation
echo "🧪 Testing pre-commit installation..."
if pre-commit run --all-files --show-diff-on-failure; then
    echo "✅ Pre-commit test passed!"
else
    echo "⚠️  Pre-commit test had issues (this is normal for first run)"
    echo "   The hooks will work correctly on future commits."
fi

echo ""
echo "🎉 Pre-commit setup completed successfully!"
echo ""
echo "What happens now:"
echo "  ✅ Code will be automatically formatted (black, isort)"
echo "  ✅ Basic file checks will run (trailing whitespace, etc.)"
if [ "$SKIP_DOCKER" = false ]; then
    echo "  ✅ Docker image will be built when relevant files change"
    echo "  📦 Images will be saved to the dist/ directory"
fi
echo ""
echo "To manually run all hooks:"
echo "  pre-commit run --all-files"
echo ""
echo "To skip hooks for a specific commit:"
echo "  git commit --no-verify -m 'your message'"
