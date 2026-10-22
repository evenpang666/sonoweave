$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'tools\bootstrap.ps1') -Ensure -Backend auto
if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed.' }
# uv-created application environments do not include pip by default.
& $python -m ensurepip
if ($LASTEXITCODE -ne 0) { throw 'pip bootstrap failed.' }
# Runtime dependencies (including the selected CPU/Vulkan backend) are ready.
# Install only the build tool; requirements-dev.txt selects Vulkan again.
& $python -m pip --disable-pip-version-check install 'pyinstaller>=6.10,<7'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$bundlePath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'dist\SonoWeave'))
if (-not $bundlePath.StartsWith($PSScriptRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid build output path.' }
& $python tools\build_bundle.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\README.md')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'LICENSE') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\LICENSE')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY.md') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\THIRD_PARTY.md')
New-Item -ItemType Directory -Force 'dist\SonoWeave\docs' | Out-Null
Copy-Item -Path 'docs\*.md' -Destination 'dist\SonoWeave\docs' -Force
New-Item -ItemType Directory -Force 'dist\SonoWeave\docs\assets' | Out-Null
Copy-Item -Path 'docs\assets\*' -Destination 'dist\SonoWeave\docs\assets' -Recurse -Force
& $python -m pip freeze | Set-Content -Encoding utf8 (Join-Path $PSScriptRoot 'dist\SonoWeave\requirements-installed.txt')
& $python tools\collect_licenses.py dist\SonoWeave\licenses
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'licenses')) {
    Copy-Item -Path (Join-Path $PSScriptRoot 'licenses\*') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\licenses') -Recurse -Force
}
Write-Host 'Ready: dist\SonoWeave\SonoWeave.exe (models download automatically on first use)'
