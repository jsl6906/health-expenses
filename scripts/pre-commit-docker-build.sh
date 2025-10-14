#!/bin/bash
# Pre-commit hook script for building Docker image
# This script is called by pre-commit to build the Docker image when relevant files change.

set -e

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_ROOT/dist"

# Change to project root
cd "$PROJECT_ROOT"

echo "🔍 Pre-commit: Checking if Docker image needs rebuilding..."

# Create hash of relevant files to determine if rebuild is needed
HASH_FILES=(
    "Dockerfile"
    "pyproject.toml"
    "main.py"
    "init_db.py"
)

# Add all Python source files
if [ -d "src" ]; then
    while IFS= read -r -d '' file; do
        HASH_FILES+=("${file#./}")
    done < <(find src -name "*.py" -print0)
fi

# Add template and static files
if [ -d "templates" ]; then
    while IFS= read -r -d '' file; do
        HASH_FILES+=("${file#./}")
    done < <(find templates -type f -print0)
fi

if [ -d "static" ]; then
    while IFS= read -r -d '' file; do
        HASH_FILES+=("${file#./}")
    done < <(find static -type f -print0)
fi

# Calculate hash of all relevant files
HASH_STRING=""
for file in "${HASH_FILES[@]}"; do
    if [ -f "$file" ]; then
        FILE_HASH=$(md5sum "$file" | cut -d' ' -f1)
        HASH_STRING+="$file:$FILE_HASH;"
    fi
done

CURRENT_HASH=$(echo -n "$HASH_STRING" | md5sum | cut -d' ' -f1)

# Check if hash file exists and compare
HASH_FILE="$DIST_DIR/last-build.hash"
LAST_HASH=""
if [ -f "$HASH_FILE" ]; then
    LAST_HASH=$(cat "$HASH_FILE" 2>/dev/null || echo "")
fi

# Skip build if hash hasn't changed
if [ "$CURRENT_HASH" = "$LAST_HASH" ]; then
    echo "✅ Docker image is up to date (hash: ${CURRENT_HASH:0:8}...)"
    exit 0
fi

echo "🔨 Building Docker image (changes detected)..."

# Ensure dist directory exists
mkdir -p "$DIST_DIR"

# Build image with commit hash as tag
GIT_HASH=""
if command -v git >/dev/null 2>&1; then
    GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
else
    GIT_HASH="unknown"
fi

IMAGE_TAG="pre-commit-$GIT_HASH"
IMAGE_NAME="personal-expenses:$IMAGE_TAG"

echo "Building: $IMAGE_NAME"

# Build the Docker image
if docker build -t "$IMAGE_NAME" . >/dev/null 2>&1; then
    echo "✅ Docker image built successfully!"

    # Save image to dist directory
    TAR_FILE="$DIST_DIR/personal-expenses-$IMAGE_TAG.tar"
    echo "💾 Saving image to dist/ directory..."

    if docker save -o "$TAR_FILE" "$IMAGE_NAME" 2>/dev/null; then
        # Save the current hash
        echo -n "$CURRENT_HASH" > "$HASH_FILE"

        # Create a latest link/copy
        LATEST_FILE="$DIST_DIR/personal-expenses-latest.tar"
        cp "$TAR_FILE" "$LATEST_FILE"

        # Display file size
        FILE_SIZE=$(du -h "$TAR_FILE" | cut -f1)
        echo "📦 Image saved: $FILE_SIZE"

        # Clean up old images (keep last 3)
        find "$DIST_DIR" -name "personal-expenses-pre-commit-*.tar" -type f -print0 | \
        xargs -0 ls -t | tail -n +4 | xargs -r rm -f

        echo "🎉 Pre-commit Docker build completed successfully!"
        exit 0
    else
        echo "❌ Failed to save Docker image"
        exit 1
    fi

else
    echo "❌ Docker build failed!"
    echo ""
    echo "💡 Tip: Fix the build errors above and try committing again."
    exit 1
fi
