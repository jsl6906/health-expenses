#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build Docker image for Personal Expenses Application

.DESCRIPTION
    This script builds the Docker image and saves it to the dist/ directory.
    It can be run manually or as part of a CI/CD pipeline.

.PARAMETER Tag
    Docker image tag (default: latest)

.PARAMETER Push
    Whether to push the image to a registry (default: false)

.PARAMETER Registry
    Docker registry URL (default: none)

.EXAMPLE
    .\scripts\build-docker.ps1
    .\scripts\build-docker.ps1 -Tag "v1.0.0"
    .\scripts\build-docker.ps1 -Tag "v1.0.0" -Push -Registry "your-registry.com"
#>

param(
    [string]$Tag = "latest",
    [switch]$Push = $false,
    [string]$Registry = ""
)

# Set error handling
$ErrorActionPreference = "Stop"

# Get script directory and project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$DistDir = Join-Path $ProjectRoot "dist"

# Ensure dist directory exists
if (!(Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
    Write-Host "Created dist directory: $DistDir" -ForegroundColor Green
}

# Change to project root
Set-Location $ProjectRoot

# Build image name
$ImageName = "personal-expenses"
if ($Registry) {
    $FullImageName = "$Registry/$ImageName`:$Tag"
} else {
    $FullImageName = "$ImageName`:$Tag"
}

Write-Host "Building Docker image: $FullImageName" -ForegroundColor Yellow

try {
    # Build the Docker image
    docker build -t $FullImageName .

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Docker image built successfully: $FullImageName" -ForegroundColor Green

        # Save image to dist directory
        $TarFile = Join-Path $DistDir "personal-expenses-$Tag.tar"
        Write-Host "Saving image to: $TarFile" -ForegroundColor Yellow

        docker save -o $TarFile $FullImageName

        if ($LASTEXITCODE -eq 0) {
            Write-Host "Image saved successfully to: $TarFile" -ForegroundColor Green

            # Display file size
            $FileSize = (Get-Item $TarFile).Length / 1MB
            Write-Host "Image size: $([math]::Round($FileSize, 2)) MB" -ForegroundColor Cyan
        } else {
            Write-Error "Failed to save Docker image"
        }

        # Push to registry if requested
        if ($Push -and $Registry) {
            Write-Host "Pushing image to registry: $Registry" -ForegroundColor Yellow
            docker push $FullImageName

            if ($LASTEXITCODE -eq 0) {
                Write-Host "Image pushed successfully to: $Registry" -ForegroundColor Green
            } else {
                Write-Error "Failed to push Docker image to registry"
            }
        }

    } else {
        Write-Error "Docker build failed"
    }

} catch {
    Write-Error "Error during Docker build: $($_.Exception.Message)"
    exit 1
}

Write-Host "Docker build process completed!" -ForegroundColor Green
Write-Host "To run the application:" -ForegroundColor Cyan
Write-Host "  1. Copy docker-compose.example.yml to docker-compose.yml" -ForegroundColor White
Write-Host "  2. Update environment variables in docker-compose.yml" -ForegroundColor White
Write-Host "  3. Run: docker-compose up -d" -ForegroundColor White
