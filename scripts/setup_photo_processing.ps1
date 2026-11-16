$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$photoEnvironment = Join-Path $projectRoot 'data/photo-tools/.venv'
$photoPython = Join-Path $photoEnvironment 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $photoPython)) {
    python -m venv $photoEnvironment
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the photo-processing environment.' }
}
& $photoPython -m pip install -r (Join-Path $PSScriptRoot 'photo-processing-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the photo-processing dependencies.' }
Write-Output "Photo-processing Python: $photoPython"
