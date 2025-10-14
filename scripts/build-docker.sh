#!/bin/bash
# Build Docker image for Personal Expenses Application
# This script builds the Docker image and saves it to the dist/ directory.

set -e  # Exit on any error

# Default values
TAG="latest"
PUSH=false
REGISTRY=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    -t|--tag)
      TAG="$2"
      shift 2
      ;;
    -p|--push)
      PUSH=true
      shift
      ;;
    -r|--registry)
      REGISTRY="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo "Options:"
      echo "  -t, --tag TAG        Docker image tag (default: latest)"
      echo "  -p, --push           Push image to registry"
      echo "  -r, --registry URL   Docker registry URL"
      echo "  -h, --help           Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_ROOT/dist"

# Ensure dist directory exists
mkdir -p "$DIST_DIR"
echo "✓ Created dist directory: $DIST_DIR"

# Change to project root
cd "$PROJECT_ROOT"

# Build image name
IMAGE_NAME="personal-expenses"
if [[ -n "$REGISTRY" ]]; then
    FULL_IMAGE_NAME="$REGISTRY/$IMAGE_NAME:$TAG"
else
    FULL_IMAGE_NAME="$IMAGE_NAME:$TAG"
fi

echo "🔨 Building Docker image: $FULL_IMAGE_NAME"

# Build the Docker image
if docker build -t "$FULL_IMAGE_NAME" .; then
    echo "✅ Docker image built successfully: $FULL_IMAGE_NAME"

    # Save image to dist directory
    TAR_FILE="$DIST_DIR/personal-expenses-$TAG.tar"
    echo "💾 Saving image to: $TAR_FILE"

    if docker save -o "$TAR_FILE" "$FULL_IMAGE_NAME"; then
        echo "✅ Image saved successfully to: $TAR_FILE"

        # Display file size
        FILE_SIZE=$(du -h "$TAR_FILE" | cut -f1)
        echo "📦 Image size: $FILE_SIZE"
    else
        echo "❌ Failed to save Docker image"
        exit 1
    fi

    # Push to registry if requested
    if [[ "$PUSH" == true && -n "$REGISTRY" ]]; then
        echo "🚀 Pushing image to registry: $REGISTRY"
        if docker push "$FULL_IMAGE_NAME"; then
            echo "✅ Image pushed successfully to: $REGISTRY"
        else
            echo "❌ Failed to push Docker image to registry"
            exit 1
        fi
    fi

else
    echo "❌ Docker build failed"
    exit 1
fi

echo "🎉 Docker build process completed!"
echo "To run the application:"
echo "  1. Copy docker-compose.example.yml to docker-compose.yml"
echo "  2. Update environment variables in docker-compose.yml"
echo "  3. Run: docker-compose up -d"
