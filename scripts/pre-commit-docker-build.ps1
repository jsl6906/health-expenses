#!/usr/bin/env pwsh
#
# Pre-commit hook to build Docker image when relevant files change
# This script is called by pre-commit and builds the Docker image
# when Python source files, Docker files, or dependencies change.
#

param(
    [string[]]$Files = @()
)

# Exit on any error
$ErrorActionPreference = "Stop"

Write-Host "=== Docker Build Hook ===" -ForegroundColor Cyan

# Define relevant file patterns that should trigger a Docker build
$DockerTriggerPatterns = @(
    "*.py",
    "*.pyi",
    "pyproject.toml",
    "requirements*.txt",
    "Dockerfile*",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    ".dockerignore"
)

# Also watch template and static web asset types so HTML/CSS/JS changes trigger a rebuild
$DockerTriggerPatterns += @(
    "*.html",
    "*.css",
    "*.js",
    "templates/*",
    "templates/*.*",
    "static/*",
    "static/*.*"
)

# Check if any of the staged files match our trigger patterns
$RelevantFiles = @()

if ($Files.Count -eq 0) {
    # No files passed (pass_filenames: false), so check all relevant files in the repository
    Write-Host "No specific files provided. Checking all Docker-relevant files..." -ForegroundColor Yellow
    foreach ($Pattern in $DockerTriggerPatterns) {
        $FoundFiles = Get-ChildItem -Path . -Name $Pattern -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_ -notmatch "^\.venv[/\\]" -and $_ -notmatch "^__pycache__[/\\]" -and $_ -notmatch "\.pyc$" }
        $RelevantFiles += $FoundFiles
    }
} else {
    # Files were passed, check if any match our trigger patterns
    foreach ($File in $Files) {
        # Normalize path separators to forward slashes for matching
        $NormFile = $File -replace "\\\\", "/"

        # Skip virtual environment and cache files
        if ($NormFile -match "^\.venv/" -or $NormFile -match "^__pycache__/" -or $NormFile -match "\.pyc$") {
            continue
        }

        foreach ($Pattern in $DockerTriggerPatterns) {
            # Convert glob-ish patterns to regex
            $RegexPattern = [Regex]::Escape($Pattern) -replace "\\\*", ".*"
            if ($NormFile -match "^$RegexPattern$") {
                $RelevantFiles += $File
                break
            }
            # Also check for directory prefixes like templates/ or static/
            if ($NormFile -like "$Pattern" -or $NormFile -match "^$Pattern") {
                $RelevantFiles += $File
                break
            }
        }
    }
}

if ($RelevantFiles.Count -eq 0) {
    Write-Host "No Docker-relevant files found. Skipping build." -ForegroundColor Green
    exit 0
}

Write-Host "Found $($RelevantFiles.Count) Docker-relevant file(s):" -ForegroundColor Yellow
foreach ($File in $RelevantFiles) {
    Write-Host "  - $File" -ForegroundColor Gray
}

# Create dist directory if it doesn't exist
$DistDir = Join-Path (Get-Location) "dist"
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}

# Generate hash of relevant files to avoid unnecessary rebuilds
$HashInputs = @()
foreach ($File in $RelevantFiles) {
    if (Test-Path $File) {
        $Content = Get-Content $File -Raw -ErrorAction SilentlyContinue
        $HashInputs += "$File`:$Content"
    }
}
$HashString = $HashInputs -join "|"

# Create a temporary file to hash the string content
$TempFile = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($TempFile, $HashString, [System.Text.Encoding]::UTF8)
    $CurrentHash = (Get-FileHash -Path $TempFile -Algorithm MD5).Hash
} finally {
    Remove-Item $TempFile -Force -ErrorAction SilentlyContinue
}

# Check if hash file exists and compare
$HashFile = Join-Path $DistDir "last-build.hash"
$LastHash = ""
if (Test-Path $HashFile) {
    $LastHash = Get-Content $HashFile -Raw -ErrorAction SilentlyContinue
}

# Skip build if hash hasn't changed
if ($CurrentHash -eq $LastHash.Trim()) {
    Write-Host "No changes detected in Docker-relevant files. Skipping build." -ForegroundColor Green
    exit 0
}

Write-Host "Building Docker image..." -ForegroundColor Yellow

# Generate timestamp for image tag
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ImageName = "personal-expenses"
$ImageTag = "$ImageName`:$Timestamp"

try {
    # Build the Docker image
    Write-Host "Running: docker build -t $ImageTag ." -ForegroundColor Gray

    $BuildOutput = ""
    $Process = Start-Process -FilePath "docker" -ArgumentList @("build", "-t", $ImageTag, ".") -NoNewWindow -PassThru -RedirectStandardOutput "docker-build-stdout.tmp" -RedirectStandardError "docker-build-stderr.tmp"
    $Process.WaitForExit()

    # Capture output
    if (Test-Path "docker-build-stdout.tmp") {
        $BuildOutput += Get-Content "docker-build-stdout.tmp" -Raw
        Remove-Item "docker-build-stdout.tmp" -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path "docker-build-stderr.tmp") {
        $BuildOutput += Get-Content "docker-build-stderr.tmp" -Raw
        Remove-Item "docker-build-stderr.tmp" -Force -ErrorAction SilentlyContinue
    }

    if ($Process.ExitCode -eq 0) {
        Write-Host "Build successful!" -ForegroundColor Green

        # Save the Docker image to dist directory
        $ImageFile = Join-Path $DistDir "$ImageName-$Timestamp.tar"
        Write-Host "Saving image to: $ImageFile" -ForegroundColor Gray

        $SaveProcess = Start-Process -FilePath "docker" -ArgumentList @("save", "-o", $ImageFile, $ImageTag) -NoNewWindow -PassThru -Wait

        if ($SaveProcess.ExitCode -eq 0) {
            Write-Host "Image saved successfully!" -ForegroundColor Green

            # Update hash file to avoid rebuilding
            Set-Content -Path $HashFile -Value $CurrentHash

            # Clean up intermediate image to save space
            $RemoveProcess = Start-Process -FilePath "docker" -ArgumentList @("rmi", $ImageTag) -NoNewWindow -PassThru -Wait
            if ($RemoveProcess.ExitCode -eq 0) {
                Write-Host "Cleaned up intermediate image." -ForegroundColor Gray
            }

            Write-Host ""
            Write-Host "Docker image built and saved to dist/$ImageName-$Timestamp.tar" -ForegroundColor Green
            Write-Host "Use 'docker load -i dist/$ImageName-$Timestamp.tar' to load the image" -ForegroundColor Cyan

        } else {
            Write-Host "Failed to save Docker image!" -ForegroundColor Red
            Write-Host $BuildOutput -ForegroundColor Gray
            exit 1
        }

    } else {
        # Check if the image was actually created despite non-zero exit code (warnings)
        $ImageExists = & docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -eq $ImageTag }

        if ($ImageExists) {
            Write-Host "Build completed with warnings but image was created." -ForegroundColor Yellow

            # Save the Docker image to dist directory
            $ImageFile = Join-Path $DistDir "$ImageName-$Timestamp.tar"
            Write-Host "Saving image to: $ImageFile" -ForegroundColor Gray

            $SaveProcess = Start-Process -FilePath "docker" -ArgumentList @("save", "-o", $ImageFile, $ImageTag) -NoNewWindow -PassThru -Wait

            if ($SaveProcess.ExitCode -eq 0) {
                Write-Host "Image saved successfully!" -ForegroundColor Green

                # Update hash file to avoid rebuilding
                Set-Content -Path $HashFile -Value $CurrentHash

                # Clean up intermediate image to save space
                $RemoveProcess = Start-Process -FilePath "docker" -ArgumentList @("rmi", $ImageTag) -NoNewWindow -PassThru -Wait
                if ($RemoveProcess.ExitCode -eq 0) {
                    Write-Host "Cleaned up intermediate image." -ForegroundColor Gray
                }

                Write-Host ""
                Write-Host "Docker image built and saved to dist/$ImageName-$Timestamp.tar" -ForegroundColor Green
                Write-Host "Use 'docker load -i dist/$ImageName-$Timestamp.tar' to load the image" -ForegroundColor Cyan

            } else {
                Write-Host "Failed to save Docker image!" -ForegroundColor Red
                exit 1
            }
        } else {
            Write-Host "Docker build failed!" -ForegroundColor Red
            Write-Host $BuildOutput -ForegroundColor Gray
            Write-Host ""
            Write-Host "Tip: Fix the build errors above and try committing again." -ForegroundColor Yellow
            exit 1
        }
    }

} catch {
    Write-Host "Error during Docker build: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
