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
$Random = Get-Random -Minimum 1000 -Maximum 9999
$ImageName = "personal-expenses"
$ImageTag = "$ImageName`:$Timestamp-$Random"

# Helper: save docker image with a retry to handle transient IO errors (e.g., 'Stream was not readable')
function Save-Image-WithRetry([string]$Tag, [string]$PathToFile, [int]$Attempts=2) {
    $lastOutput = ''
    for ($i = 1; $i -le $Attempts; $i++) {
        $SaveStdOutFile = [System.IO.Path]::GetTempFileName()
        $SaveStdErrFile = [System.IO.Path]::GetTempFileName()
        try {
            $sp = Start-Process -FilePath "docker" -ArgumentList @("save", "-o", $PathToFile, $Tag) -NoNewWindow -PassThru -RedirectStandardOutput $SaveStdOutFile -RedirectStandardError $SaveStdErrFile -Wait
            $out = ''
            if (Test-Path $SaveStdOutFile) { $out += (Get-Content $SaveStdOutFile -Raw -ErrorAction SilentlyContinue) }
            if (Test-Path $SaveStdErrFile) { $out += (Get-Content $SaveStdErrFile -Raw -ErrorAction SilentlyContinue) }
            $lastOutput = $out
            if ($sp.ExitCode -eq 0) {
                return @{ Success = $true; Output = $out }
            } else {
                Write-Host "docker save attempt $i failed (exit code $($sp.ExitCode)). Retrying..." -ForegroundColor Yellow
            }
        } catch {
            $lastOutput = $_.Exception.Message
            Write-Host "docker save attempt $i error: $lastOutput" -ForegroundColor Yellow
        } finally {
            if (Test-Path $SaveStdOutFile) { Remove-Item $SaveStdOutFile -Force -ErrorAction SilentlyContinue }
            if (Test-Path $SaveStdErrFile) { Remove-Item $SaveStdErrFile -Force -ErrorAction SilentlyContinue }
        }
        Start-Sleep -Milliseconds 200
    }
    return @{ Success = $false; Output = $lastOutput }
}

try {
    # Build the Docker image
    Write-Host "Running: docker build -t $ImageTag ." -ForegroundColor Gray

    $BuildStdOutFile = [System.IO.Path]::GetTempFileName()
    $BuildStdErrFile = [System.IO.Path]::GetTempFileName()
    $BuildOutput = ""
    $Process = Start-Process -FilePath "docker" -ArgumentList @("build", "-t", $ImageTag, ".") -NoNewWindow -PassThru -RedirectStandardOutput $BuildStdOutFile -RedirectStandardError $BuildStdErrFile
    $Process.WaitForExit()

    # Capture output
    if (Test-Path $BuildStdOutFile) {
        $BuildOutput += Get-Content $BuildStdOutFile -Raw -ErrorAction SilentlyContinue
        Remove-Item $BuildStdOutFile -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $BuildStdErrFile) {
        $BuildOutput += Get-Content $BuildStdErrFile -Raw -ErrorAction SilentlyContinue
        Remove-Item $BuildStdErrFile -Force -ErrorAction SilentlyContinue
    }

    $ImageSaved = $false

    if ($Process.ExitCode -eq 0) {
        Write-Host "Build successful!" -ForegroundColor Green

        # Save the Docker image to dist directory
        $ImageFile = Join-Path $DistDir "$ImageName-$Timestamp.tar"
        Write-Host "Saving image to: $ImageFile" -ForegroundColor Gray

        # Helper: save docker image with a retry to handle transient IO errors (e.g., 'Stream was not readable')
        function Save-Image-WithRetry([string]$Tag, [string]$PathToFile, [int]$Attempts=2) {
            $lastOutput = ''
            for ($i = 1; $i -le $Attempts; $i++) {
                $SaveStdOutFile = [System.IO.Path]::GetTempFileName()
                $SaveStdErrFile = [System.IO.Path]::GetTempFileName()
                try {
                    $sp = Start-Process -FilePath "docker" -ArgumentList @("save", "-o", $PathToFile, $Tag) -NoNewWindow -PassThru -RedirectStandardOutput $SaveStdOutFile -RedirectStandardError $SaveStdErrFile -Wait
                    $out = ''
                    if (Test-Path $SaveStdOutFile) { $out += (Get-Content $SaveStdOutFile -Raw -ErrorAction SilentlyContinue) }
                    if (Test-Path $SaveStdErrFile) { $out += (Get-Content $SaveStdErrFile -Raw -ErrorAction SilentlyContinue) }
                    $lastOutput = $out
                    if ($sp.ExitCode -eq 0) {
                        return @{ Success = $true; Output = $out }
                    } else {
                        Write-Host "docker save attempt $i failed (exit code $($sp.ExitCode)). Retrying..." -ForegroundColor Yellow
                    }
                } catch {
                    $lastOutput = $_.Exception.Message
                    Write-Host "docker save attempt $i error: $lastOutput" -ForegroundColor Yellow
                } finally {
                    if (Test-Path $SaveStdOutFile) { Remove-Item $SaveStdOutFile -Force -ErrorAction SilentlyContinue }
                    if (Test-Path $SaveStdErrFile) { Remove-Item $SaveStdErrFile -Force -ErrorAction SilentlyContinue }
                }
                Start-Sleep -Milliseconds 200
            }
            return @{ Success = $false; Output = $lastOutput }
        }

        $saveResult = Save-Image-WithRetry -Tag $ImageTag -PathToFile $ImageFile -Attempts 3
        if ($saveResult.Success) {
            Write-Host "Image saved successfully!" -ForegroundColor Green
            $ImageSaved = $true

            # Update hash file to avoid rebuilding
            Set-Content -Path $HashFile -Value $CurrentHash

            # Clean up intermediate image to save space
            try {
                $RmiStdOutFile = [System.IO.Path]::GetTempFileName()
                $RmiStdErrFile = [System.IO.Path]::GetTempFileName()
                $RemoveProcess = Start-Process -FilePath "docker" -ArgumentList @("rmi", $ImageTag) -NoNewWindow -PassThru -RedirectStandardOutput $RmiStdOutFile -RedirectStandardError $RmiStdErrFile -Wait
                if (Test-Path $RmiStdOutFile) { Remove-Item $RmiStdOutFile -Force -ErrorAction SilentlyContinue }
                if (Test-Path $RmiStdErrFile) { Remove-Item $RmiStdErrFile -Force -ErrorAction SilentlyContinue }
                if ($RemoveProcess.ExitCode -eq 0) {
                    Write-Host "Cleaned up intermediate image." -ForegroundColor Gray
                }
            } catch {
                Write-Host "Warning: Failed to clean up intermediate image: $($_.Exception.Message)" -ForegroundColor Yellow
            }

            Write-Host ""
            Write-Host "Docker image built and saved to dist/$ImageName-$Timestamp.tar" -ForegroundColor Green
            Write-Host "Use 'docker load -i dist/$ImageName-$Timestamp.tar' to load the image" -ForegroundColor Cyan
        } else {
            Write-Host "Failed to save Docker image after retries!" -ForegroundColor Red
            Write-Host $saveResult.Output -ForegroundColor Gray
            exit 1
        }

    } else {
        # Check if the image was actually created despite non-zero exit code (warnings)
        $ImageExists = & docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -eq $ImageTag }

        if ($ImageExists) {
            Write-Host "Build completed with warnings but image was created." -ForegroundColor Yellow

            # Save the Docker image to dist directory (with retry)
            $ImageFile = Join-Path $DistDir "$ImageName-$Timestamp.tar"
            Write-Host "Saving image to: $ImageFile" -ForegroundColor Gray
            $saveResult2 = Save-Image-WithRetry -Tag $ImageTag -PathToFile $ImageFile -Attempts 3
            if ($saveResult2.Success) {
                Write-Host "Image saved successfully!" -ForegroundColor Green
                $ImageSaved = $true
                Set-Content -Path $HashFile -Value $CurrentHash
                try {
                    $RmiStdOutFile = [System.IO.Path]::GetTempFileName()
                    $RmiStdErrFile = [System.IO.Path]::GetTempFileName()
                    $RemoveProcess = Start-Process -FilePath "docker" -ArgumentList @("rmi", $ImageTag) -NoNewWindow -PassThru -RedirectStandardOutput $RmiStdOutFile -RedirectStandardError $RmiStdErrFile -Wait
                    if (Test-Path $RmiStdOutFile) { Remove-Item $RmiStdOutFile -Force -ErrorAction SilentlyContinue }
                    if (Test-Path $RmiStdErrFile) { Remove-Item $RmiStdErrFile -Force -ErrorAction SilentlyContinue }
                    if ($RemoveProcess.ExitCode -eq 0) { Write-Host "Cleaned up intermediate image." -ForegroundColor Gray }
                } catch {
                    Write-Host "Warning: Failed to clean up intermediate image: $($_.Exception.Message)" -ForegroundColor Yellow
                }
                Write-Host ""; Write-Host "Docker image built and saved to dist/$ImageName-$Timestamp.tar" -ForegroundColor Green; Write-Host "Use 'docker load -i dist/$ImageName-$Timestamp.tar' to load the image" -ForegroundColor Cyan
            } else {
                Write-Host "Failed to save Docker image after retries!" -ForegroundColor Red
                Write-Host $saveResult2.Output -ForegroundColor Gray
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
    if (-not $ImageSaved) {
        exit 1
    }
}
