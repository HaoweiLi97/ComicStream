param(
    [string]$Python = 'python',
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist')
)

$ErrorActionPreference = 'Stop'
$buildPath = Join-Path $PSScriptRoot '.cache/reader'

& $Python -m PyInstaller --noconfirm --onefile --windowed --name CSTR-Reader `
    --distpath $OutputDirectory --workpath $buildPath --specpath $buildPath `
    (Join-Path $PSScriptRoot 'desktop_reader.py')
exit $LASTEXITCODE
