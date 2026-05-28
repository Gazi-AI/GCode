$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$pathParts = @()

if ($currentUserPath) {
    $pathParts = $currentUserPath -split ";" | Where-Object { $_ -and $_.Trim() }
}

$normalizedProjectRoot = $projectRoot.TrimEnd("\")

$alreadyInstalled = $pathParts | Where-Object {
    $pathEntry = [string]$_
    try {
        (Resolve-Path -LiteralPath $pathEntry -ErrorAction Stop).Path.TrimEnd("\") -ieq $normalizedProjectRoot
    } catch {
        $pathEntry.TrimEnd("\") -ieq $normalizedProjectRoot
    }
}

if (-not $alreadyInstalled) {
    $updatedPath = (@($pathParts) + $projectRoot) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $updatedPath, "User")
    Write-Host "Added GCode to your user PATH."
} else {
    Write-Host "GCode is already available in your user PATH."
}

Write-Host ""
Write-Host "Open a new Command Prompt and run:"
Write-Host "  gcode"
Write-Host ""
Write-Host "Launcher:"
Write-Host "  $projectRoot\gcode.cmd"

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $pythonExe = $venvPython
} else {
    $pythonExe = "python"
}

Write-Host ""
Write-Host "Starting the graphical setup wizard..."
& $pythonExe (Join-Path $projectRoot "setup_wizard.py") --install --force
