# Build the image on this PC and drop it on the server's Docker share; then run update_personal_expenses.sh on the server.
# The share root comes from DEPLOY_SHARE in the local .env (e.g. \\myserver\Docker).
param([string]$Share)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
if (-not $Share) {
    $line = Get-Content "$root\.env" | Where-Object { $_ -match '^\s*DEPLOY_SHARE=(.+)$' } | Select-Object -First 1
    if (-not $line) { throw "Pass -Share or set DEPLOY_SHARE in .env" }
    $Share = ($line -replace '^\s*DEPLOY_SHARE=', '').Trim() + "\personal-expenses"
}
$tag = "personal-expenses:" + (Get-Date -Format "yyyyMMdd-HHmm")

docker build --platform linux/amd64 -t $tag $root
if ($LASTEXITCODE) { throw "docker build failed" }

New-Item -ItemType Directory -Force "$Share\container_files" | Out-Null
$name = ($tag -replace ":", "-") + ".tar"
# docker save straight to the SMB share can leave an empty file
$local = Join-Path $env:TEMP $name
docker save -o $local $tag
if ($LASTEXITCODE) { throw "docker save failed" }
$tar = "$Share\container_files\$name"
Copy-Item $local $tar -Force
if ((Get-Item $tar).Length -ne (Get-Item $local).Length) { throw "copy to $tar is incomplete" }
Remove-Item $local

# Copy with LF line endings so bash can run it
$script = (Get-Content "$PSScriptRoot\update_personal_expenses.sh" -Raw) -replace "`r`n", "`n"
[IO.File]::WriteAllText("$Share\update_personal_expenses.sh", $script)
Write-Host "Saved $tar. On the server run: bash <docker-config-dir>/personal-expenses/update_personal_expenses.sh"
