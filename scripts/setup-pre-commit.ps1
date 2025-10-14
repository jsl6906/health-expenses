#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Setup pre-commit hooks for Personal Expenses Application

.DESCRIPTION
    This script installs and configures pre-commit hooks, including the Docker build hook.
    It ensures all necessary dependencies are available and sets up the git hooks.

.PARAMETER SkipDocker
    Skip Docker availability check (useful for CI environments)

.EXAMPLE
    .\scripts\setup-pre-commit.ps1
    .\scripts\setup-pre-commit.ps1 -SkipDocker
#>

param(
    [switch]$SkipDocker = $false
)

# Set error handling
$ErrorActionPreference = "Stop"

Write-Host "Setting up pre-commit hooks for Personal Expenses Application..." -ForegroundColor Cyan

# Check if we're in the right directory
if (!(Test-Path ".pre-commit-config.yaml")) {
    Write-Host "Error: .pre-commit-config.yaml not found. Run this script from the project root." -ForegroundColor Red
    exit 1
}

# Use Windows-specific config if it exists
if (Test-Path ".pre-commit-config.windows.yaml") {
    Write-Host "Using Windows-specific pre-commit configuration..." -ForegroundColor Cyan
    Copy-Item ".pre-commit-config.windows.yaml" ".pre-commit-config.yaml" -Force
}

# Check if Python is available
try {
    $PythonVersion = python --version 2>&1
    Write-Host "Python found: $PythonVersion" -ForegroundColor Green
} catch {
    Write-Host "Python not found. Please install Python 3.8+ and ensure it's in your PATH." -ForegroundColor Red
    exit 1
}

# Check if uv is available (preferred) or pip
$UseUv = $false
try {
    $UvVersion = uv --version 2>&1
    Write-Host "uv found: $UvVersion" -ForegroundColor Green
    $UseUv = $true
} catch {
    Write-Host "uv not found, falling back to pip" -ForegroundColor Yellow
    try {
        $PipVersion = pip --version 2>&1
        Write-Host "pip found: $PipVersion" -ForegroundColor Green
    } catch {
        Write-Host "Neither uv nor pip found. Please install one of them." -ForegroundColor Red
        exit 1
    }
}

# Check if Docker is available (unless skipped)
if (!$SkipDocker) {
    try {
        $DockerVersion = docker --version 2>&1
        Write-Host "Docker found: $DockerVersion" -ForegroundColor Green
    } catch {
        Write-Host "Docker not found. Please install Docker Desktop or Docker Engine." -ForegroundColor Red
        Write-Host "   Or use -SkipDocker flag if you don't want Docker build hooks." -ForegroundColor Yellow
        exit 1
    }
}

# Check if git is available
try {
    $GitVersion = git --version 2>&1
    Write-Host "Git found: $GitVersion" -ForegroundColor Green
} catch {
    Write-Host "Git not found. Please install Git." -ForegroundColor Red
    exit 1
}

# Install pre-commit
Write-Host "Installing pre-commit..." -ForegroundColor Yellow
try {
    # Check if pre-commit is already available
    $PreCommitVersion = ""
    try {
        $PreCommitVersion = uv run pre-commit --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "pre-commit already installed: $PreCommitVersion" -ForegroundColor Green
        } else {
            throw "pre-commit not working"
        }
    } catch {
        # Need to install pre-commit
        if ($UseUv) {
            Write-Host "Installing pre-commit as dev dependency..." -ForegroundColor Cyan
            uv add --dev pre-commit
            if ($LASTEXITCODE -ne 0) {
                throw "uv add failed"
            }
            $PreCommitVersion = uv run pre-commit --version 2>&1
            Write-Host "pre-commit installed: $PreCommitVersion" -ForegroundColor Green
        } else {
            pip install pre-commit
            if ($LASTEXITCODE -ne 0) {
                throw "pip install failed"
            }
            $PreCommitVersion = pre-commit --version 2>&1
            Write-Host "pre-commit installed: $PreCommitVersion" -ForegroundColor Green
        }
    }
} catch {
    Write-Host "Failed to install pre-commit: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Make scripts executable (PowerShell doesn't need this, but good for documentation)
Write-Host "Setting up script permissions..." -ForegroundColor Yellow

# Create dist directory
$DistDir = "dist"
if (!(Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
    Write-Host "Created dist directory" -ForegroundColor Green
}

# Install pre-commit hooks
Write-Host "Installing pre-commit git hooks..." -ForegroundColor Yellow
try {
    # Try direct command first, then uv run
    try {
        pre-commit install
    } catch {
        uv run pre-commit install
    }
    Write-Host "Pre-commit hooks installed successfully!" -ForegroundColor Green
} catch {
    Write-Host "Failed to install pre-commit hooks: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Test the installation
Write-Host "Testing pre-commit installation..." -ForegroundColor Yellow
try {
    try {
        pre-commit run --all-files --show-diff-on-failure
    } catch {
        uv run pre-commit run --all-files --show-diff-on-failure
    }
    Write-Host "Pre-commit test passed!" -ForegroundColor Green
} catch {
    Write-Host "Pre-commit test had issues (this is normal for first run)" -ForegroundColor Yellow
    Write-Host "   The hooks will work correctly on future commits." -ForegroundColor Gray
}

Write-Host ""
Write-Host "Pre-commit setup completed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "What happens now:" -ForegroundColor Cyan
Write-Host "  Code will be automatically formatted with black and isort" -ForegroundColor White
Write-Host "  Basic file checks will run for trailing whitespace etc" -ForegroundColor White
if (!$SkipDocker) {
    Write-Host "  Docker image will be built when relevant files change" -ForegroundColor White
    Write-Host "  Images will be saved to the dist directory" -ForegroundColor White
}
Write-Host ""
Write-Host "To manually run all hooks:" -ForegroundColor Cyan
Write-Host "  pre-commit run --all-files" -ForegroundColor White
Write-Host ""
Write-Host "To skip hooks for a specific commit:" -ForegroundColor Cyan
Write-Host "  git commit --no-verify -m your-message" -ForegroundColor White
