param(
    [string]$Python = 'python',
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist')
)

$ErrorActionPreference = 'Stop'
$buildPath = Join-Path $PSScriptRoot '.cache/converter'

& $Python -m PyInstaller --noconfirm --onefile --windowed --name CSTR-Converter `
    --distpath $OutputDirectory --workpath $buildPath --specpath $buildPath `
    --collect-all pypdfium2_raw `
    (Join-Path $PSScriptRoot 'converter.py')
exit $LASTEXITCODE
