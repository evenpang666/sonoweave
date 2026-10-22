$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$fixtureDirectory = Join-Path $projectRoot '.cache'
New-Item -ItemType Directory -Force $fixtureDirectory | Out-Null
$referenceText = 'Deng Xiaoping was a pragmatist. Local officials wanted a promotion. They were rewarded for economic growth, not revolutionary credentials. The temperature is thirty seven point five degrees, not thirty five. A private developer builds houses and factories when there is demand.'
$speechVoice = New-Object -ComObject SAPI.SpVoice
$speechStream = New-Object -ComObject SAPI.SpFileStream
try {
    $speechStream.Open((Join-Path $fixtureDirectory 'asr-synthetic.wav'), 3, $false)
    $speechVoice.AudioOutputStream = $speechStream
    $speechVoice.Rate = 1
    $null = $speechVoice.Speak($referenceText)
} finally {
    $speechStream.Close()
    $null = [System.Runtime.InteropServices.Marshal]::ReleaseComObject($speechVoice)
    $null = [System.Runtime.InteropServices.Marshal]::ReleaseComObject($speechStream)
}
$referenceText | Set-Content -Encoding utf8 (Join-Path $fixtureDirectory 'asr-synthetic-reference.txt')
Write-Output 'Synthetic local speech fixture ready; nothing was played through the speakers.'
