$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
New-Item -ItemType Directory -Force build, build/windows-python, installers | Out-Null
$archive = Join-Path (Get-Location) 'build/python.zip'
$version = '3.12.10'
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$version/python-$version-embed-amd64.zip" -OutFile $archive
$expected = (Get-Content packaging/python-windows.sha256 -Raw).Trim()
if ((Get-FileHash -Algorithm SHA256 $archive).Hash -ine $expected) { throw 'Python runtime checksum mismatch' }
Expand-Archive $archive build/windows-python -Force
Add-Content build/windows-python/python312._pth '..\src'
git rev-parse HEAD | Set-Content build/SOURCE_REVISION
& build/windows-python/python.exe -m py_compile src/mosaicearth_desktop.py src/run_engine.py src/opensplat_engine.py
if ($LASTEXITCODE) { throw 'Python verification failed' }
$compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (!(Test-Path $compiler)) {
    choco install innosetup --version=6.4.3 --yes --no-progress
    if ($LASTEXITCODE) { throw 'Inno Setup installation failed' }
}
& $compiler packaging/windows.iss
if ($LASTEXITCODE) { throw 'Inno Setup build failed' }
Get-ChildItem installers/*.exe | ForEach-Object {
    "$( (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant() )  $($_.Name)"
} | Set-Content installers/SHA256SUMS.windows
