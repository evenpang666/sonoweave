$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run Install.bat first.' }
& $python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$bundlePath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'dist\SonoWeave'))
if (-not $bundlePath.StartsWith($PSScriptRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid build output path.' }
& $python -m PyInstaller --noconfirm --windowed --onedir --name SonoWeave --collect-data soundcard --collect-all faster_whisper --collect-all ctranslate2 --collect-all llama_cpp --collect-data certifi main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\README.md')
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
