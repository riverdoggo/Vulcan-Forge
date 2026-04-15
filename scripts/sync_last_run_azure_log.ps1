param(
    [string]$BaseUrl = "https://vulcan-forge.northcentralus.cloudapp.azure.com",
    [string]$ApiKey = "",
    [string]$OutputPath = "logs/last_run_azure.log"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$targetPath = Join-Path $repoRoot $OutputPath
$targetDir = Split-Path -Parent $targetPath
if (-not (Test-Path $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}

$uri = "$($BaseUrl.TrimEnd('/'))/api/logs/last_run_azure"
$headers = @{}
if ($ApiKey -and $ApiKey.Trim()) {
    $headers["X-API-Key"] = $ApiKey.Trim()
}

try {
    $response = Invoke-WebRequest -Uri $uri -Headers $headers -UseBasicParsing -TimeoutSec 30
    Set-Content -Path $targetPath -Value $response.Content -Encoding UTF8
    Write-Host "Synced Azure latest run log to: $targetPath"
}
catch {
    Write-Error "Failed to sync last_run_azure.log from $uri. $($_.Exception.Message)"
    exit 1
}
