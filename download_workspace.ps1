$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot 'downloads\.runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Install the Python environment using the instructions in README.md first.'
}
& $pythonPath -B (Join-Path $PSScriptRoot 'download_workspace.py') @args
exit $LASTEXITCODE
