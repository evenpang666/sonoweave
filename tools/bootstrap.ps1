param(
    [ValidateSet('auto', 'cpu', 'vulkan')][string]$Backend = 'vulkan',
    [switch]$Ensure
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv\Scripts\python.exe'
try {
    if ($Ensure -and (Test-Path -LiteralPath $python)) {
        & $python tools\bootstrap.py --check --backend auto
        if ($LASTEXITCODE -eq 0) { exit 0 }
    }
    $runtime = Join-Path $root '.runtime\windows'
    $uvDirectory = Join-Path $runtime 'uv'
    New-Item -ItemType Directory -Force -Path $uvDirectory | Out-Null
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
    if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $root '.cache\uv' }
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $env:UV_PYTHON_INSTALL_REGISTRY = '0'

    function Test-CompatibleUv([string]$Path) {
        if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
        try {
            $result = & $Path --version 2>$null
            if ($LASTEXITCODE -ne 0) { return $false }
            $match = [regex]::Match(($result -join ''), '^uv (\d+\.\d+\.\d+)')
            return $match.Success -and ([version]$match.Groups[1].Value -ge [version]'0.8.22')
        } catch { return $false }
    }
    $uv = $env:SONOWEAVE_UV
    if ($uv) {
        if (-not (Test-CompatibleUv $uv)) { throw 'SONOWEAVE_UV must point to uv >= 0.8.22.' }
    } else {
        $uv = Join-Path $uvDirectory 'uv.exe'
        if (-not (Test-CompatibleUv $uv)) {
            $existing = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
            if ($existing -and (Test-CompatibleUv $existing.Source)) { $uv = $existing.Source }
            else {
                Write-Host 'Downloading uv...'
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                if ($env:HTTPS_PROXY) {
                    [Net.WebRequest]::DefaultWebProxy = New-Object Net.WebProxy($env:HTTPS_PROXY)
                }
                $download = Join-Path $runtime ('uv-download-' + [guid]::NewGuid().ToString('N'))
                New-Item -ItemType Directory -Path $download | Out-Null
                $archive = Join-Path $download 'uv.zip'
                $release = 'https://github.com/astral-sh/uv/releases/download/0.13.4/uv-x86_64-pc-windows-msvc.zip'
                Invoke-WebRequest -UseBasicParsing -Uri $release -OutFile $archive
                Invoke-WebRequest -UseBasicParsing -Uri ($release + '.sha256') -OutFile ($archive + '.sha256')
                $expected = ((Get-Content -LiteralPath ($archive + '.sha256') -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
                $stream = [IO.File]::OpenRead($archive)
                $sha = [Security.Cryptography.SHA256]::Create()
                try { $actual = ([BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant() }
                finally { $stream.Dispose(); $sha.Dispose() }
                if ($expected -notmatch '^[a-f0-9]{64}$' -or $expected -ne $actual) { throw 'uv archive SHA-256 check failed.' }
                Add-Type -AssemblyName System.IO.Compression.FileSystem
                [IO.Compression.ZipFile]::ExtractToDirectory($archive, (Join-Path $download 'unpacked'))
                $binary = Get-ChildItem -LiteralPath (Join-Path $download 'unpacked') -Recurse -Filter uv.exe | Select-Object -First 1
                if (-not $binary) { throw 'uv executable missing from archive.' }
                Copy-Item -LiteralPath $binary.FullName -Destination $uv -Force
                if (-not (Test-CompatibleUv $uv)) { throw 'uv installation failed.' }
            }
        }
    }
    & $uv --no-config python install --no-bin --no-registry 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Managed Python download failed.' }
    $managedPython = & $uv --no-config python find --managed-python --no-project 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Managed Python could not be located.' }
    & $managedPython tools\bootstrap.py --uv $uv --backend $Backend
    if ($LASTEXITCODE -ne 0) { throw 'Application dependency installation failed.' }
    exit 0
} catch {
    Write-Host ('Environment setup failed; the app was not started. ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
