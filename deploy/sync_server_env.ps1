# Copy the Gemini settings from the local .env into the server's shared compose .env without printing secrets.
param([string]$ServerEnv = "\\myserver\Docker\.env", [string[]]$Keys = @("GEMINI_API_KEY", "GEMINI_MODEL_NAME"))
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

$values = @{}
foreach ($line in Get-Content "$root\.env") {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$' -and $Keys -contains $Matches[1]) { $values[$Matches[1]] = $Matches[2] }
}
$missing = $Keys | Where-Object { -not $values[$_] }
if ($missing) { throw "Local .env is missing: $($missing -join ', ')" }

$text = [IO.File]::ReadAllText($ServerEnv)
$nl = if ($text -match "`r`n") { "`r`n" } else { "`n" }
$lines = [Collections.Generic.List[string]]($text -split "\r?\n")
if ($lines.Count -and $lines[$lines.Count - 1] -eq "") { $lines.RemoveAt($lines.Count - 1) }

$changed = @()
foreach ($k in $Keys) {
    $new = "$k=$($values[$k])"
    $i = $lines.FindIndex([Predicate[string]] { param($l) $l -match "^\s*$k=" })
    if ($i -lt 0) { $lines.Add($new); $changed += $k }
    elseif ($lines[$i] -ne $new) { $lines[$i] = $new; $changed += $k }
}
if (-not $changed) { Write-Host "Server .env already up to date"; return }

$backupDir = Join-Path (Split-Path $ServerEnv -Parent) "_backups"
New-Item -ItemType Directory -Force $backupDir | Out-Null
Copy-Item $ServerEnv (Join-Path $backupDir (".env." + (Get-Date -Format "yyyyMMdd-HHmmss")))
[IO.File]::WriteAllText($ServerEnv, ($lines -join $nl) + $nl)
Write-Host "Updated $($changed -join ', ') in $ServerEnv. Recreate the container (update_personal_expenses.sh) to apply."
