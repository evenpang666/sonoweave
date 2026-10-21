$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run Install.bat first.' }
& $python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath 'models\base\model.bin') -or -not (Test-Path -LiteralPath 'models\translation\Qwen3-1.7B-Q4_K_M.gguf')) {
    throw 'Run PrepareModels.bat first. Offline releases must include both models.'
}
& $python -m PyInstaller --noconfirm --windowed --onedir --name SonoWeave --collect-data soundcard --collect-all faster_whisper --collect-all ctranslate2 --collect-all llama_cpp --collect-data certifi main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\README.md')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY.md') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\THIRD_PARTY.md')
& $python -m pip freeze | Set-Content -Encoding utf8 (Join-Path $PSScriptRoot 'dist\SonoWeave\requirements-installed.txt')
& $python tools\collect_licenses.py dist\SonoWeave\licenses
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'licenses')) {
    Copy-Item -Path (Join-Path $PSScriptRoot 'licenses\*') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\licenses') -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'artifacts') -Destination (Join-Path $PSScriptRoot 'dist\SonoWeave\artifacts') -Recurse -Force
New-Item -ItemType Directory -Force 'dist\SonoWeave\models\base','dist\SonoWeave\models\translation' | Out-Null
Copy-Item -Path 'models\base\*' -Destination 'dist\SonoWeave\models\base' -Force
Copy-Item -LiteralPath 'models\translation\Qwen3-1.7B-Q4_K_M.gguf' -Destination 'dist\SonoWeave\models\translation' -Force
Write-Host 'Ready: dist\SonoWeave\SonoWeave.exe (keep the entire folder together)'
