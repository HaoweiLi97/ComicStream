param(
    [Parameter(Mandatory)] [string]$CalibreDirectory,
    [string]$Python = 'python',
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist')
)

$ErrorActionPreference = 'Stop'
$calibre = (Resolve-Path -LiteralPath $CalibreDirectory).Path
$ebookConvert = Join-Path $calibre 'ebook-convert.exe'
foreach ($required in @($ebookConvert, (Join-Path $calibre 'LICENSE'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing runtime file: $required"
    }
}

$calibreDescription = (& $ebookConvert --version) -join "`n"
if ($LASTEXITCODE -ne 0 -or $calibreDescription -notmatch 'calibre\s+(\d+\.\d+\.\d+)') {
    throw "Could not determine the calibre version: $calibreDescription"
}
$calibreVersion = $Matches[1]

$buildPath = Join-Path $PSScriptRoot '.cache/portable'
$executables = Join-Path $buildPath 'executables'
foreach ($builder in @('build_converter.ps1', 'build_reader.ps1')) {
    & (Join-Path $PSScriptRoot $builder) -Python $Python -OutputDirectory $executables
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $Python -m PyInstaller --noconfirm --onefile --console --name CSTR `
    --distpath $executables --workpath (Join-Path $buildPath 'cli') --specpath $buildPath `
    --collect-all pypdfium2_raw (Join-Path $PSScriptRoot 'pack.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python (Join-Path $PSScriptRoot 'package_portable.py') $executables $calibre `
    $calibreVersion (Join-Path $OutputDirectory 'ComicStream-Portable-Lite-win-x64.zip')
exit $LASTEXITCODE
