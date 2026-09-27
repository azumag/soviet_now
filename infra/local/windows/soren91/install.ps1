# Installs the Soren91 Windows local agent (cdp-host mode) for the CURRENT
# user: builds the helpers, writes an ACL-protected env file (the token is
# generated once, kept across re-installs and never printed), and registers
# a scheduled task that starts the hidden supervisor at logon and re-checks
# every 5 minutes. No admin rights needed; it does NOT create a service
# (Session 0 has no GPU/audio session) and does NOT touch the firewall.
#
#
# Game audio never plays on this PC's speakers: the renderer runs a dedicated
# Chrome for Testing (downloaded here from Google's CfT bucket) whose per-app output is
# routed to -AudioSink, a render endpoint nobody listens to (an unused
# virtual cable). The process loopback still captures it for the stream.
#
#   powershell -ExecutionPolicy Bypass -File infra\local\windows\soren91\install.ps1 [-FfmpegBin C:\path\to\ffmpeg.exe] [-AudioSink "CABLE-A Input (VB-Audio Cable A)"]
#   powershell -ExecutionPolicy Bypass -File infra\local\windows\soren91\install.ps1 -Uninstall
param(
  [string]$FfmpegBin = '',
  [int]$Port = 19191,
  [string]$AudioSink = 'CABLE-A Input (VB-Audio Cable A)',
  [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$taskName = 'Soren91LocalAgent'
$taskPath = '\soren91\'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$stateDir = Join-Path $env:LOCALAPPDATA 'soren91'
$envFile = Join-Path $stateDir 'agent.env'

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -like '*soren91_agent_supervisor.ps1*' } |
    ForEach-Object { & taskkill /PID $_.ProcessId /T /F | Out-Null }
  Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -like '*soren91_local_agent.mjs*' } |
    ForEach-Object { & taskkill /PID $_.ProcessId /T /F | Out-Null }
  Write-Output "uninstalled task $taskPath$taskName (env file kept: $envFile)"
  exit 0
}

$tailscaleIp = (& tailscale ip -4 | Select-Object -First 1).Trim()
$octets = $tailscaleIp.Split('.')
if ($octets.Count -ne 4 -or [int]$octets[0] -ne 100 -or [int]$octets[1] -lt 64 -or [int]$octets[1] -gt 127) {
  throw "no Tailscale IPv4 detected (got '$tailscaleIp')"
}
if (-not $FfmpegBin) { $FfmpegBin = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }
# winget adds its Links dir to PATH only for shells started after the install.
$wingetFfmpeg = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\ffmpeg.exe'
if (-not $FfmpegBin -and (Test-Path $wingetFfmpeg)) { $FfmpegBin = $wingetFfmpeg }
if (-not $FfmpegBin -or -not (Test-Path $FfmpegBin)) { throw 'ffmpeg with libsrt is required: pass -FfmpegBin' }
$protocols = & $FfmpegBin -hide_banner -protocols 2>$null
if (-not ($protocols -match '^\s*srt\s*$')) { throw "$FfmpegBin lacks SRT protocol support" }
$node = (Get-Command node -ErrorAction Stop).Source

$sinkEndpoint = Get-PnpDevice -Class AudioEndpoint -PresentOnly -ErrorAction SilentlyContinue |
  Where-Object { $_.FriendlyName -ceq $AudioSink -and $_.Status -eq 'OK' -and $_.InstanceId -like 'SWD\MMDEVAPI\{0.0.0.*' }
if (@($sinkEndpoint).Count -ne 1) { throw "audio sink '$AudioSink' is not exactly one active playback device (pass -AudioSink)" }

# Dedicated Chrome for Testing (same channel as the operator's Stable). The
# per-app audio routing sticks to this executable path only.
$cft = (Invoke-RestMethod 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json').channels.Stable
$cftUrl = ($cft.downloads.chrome | Where-Object platform -eq 'win64').url
if ($cft.version -notmatch '^\d+(\.\d+){3}$' -or $cftUrl -notlike 'https://storage.googleapis.com/chrome-for-testing-public/*') {
  throw 'unexpected Chrome for Testing manifest'
}
$chromeRoot = Join-Path $stateDir 'chrome'
$chromeDir = Join-Path $chromeRoot "$($cft.version)\chrome-win64"
$chromeBin = Join-Path $chromeDir 'chrome.exe'
if (-not (Test-Path $chromeBin)) {
  New-Item -ItemType Directory -Force (Split-Path -Parent $chromeDir) | Out-Null
  $zip = Join-Path $chromeRoot "chrome-win64-$($cft.version).zip"
  $ProgressPreference = 'SilentlyContinue'
  Invoke-WebRequest -UseBasicParsing $cftUrl -OutFile $zip
  Expand-Archive -Force $zip (Split-Path -Parent $chromeDir)
  Remove-Item -Force $zip
}
# Chrome for Testing builds are not Authenticode-signed and Google publishes no
# hashes; integrity rests on HTTPS from the pinned storage.googleapis.com path.
if (-not (Test-Path $chromeBin)) { throw "Chrome for Testing missing at $chromeBin" }

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'tools\soren91_windows_helpers_build.ps1')
if ($LASTEXITCODE -ne 0) { throw 'helper build failed' }

New-Item -ItemType Directory -Force $stateDir | Out-Null
$token = $null
if (Test-Path $envFile) {
  $existing = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -like 'SOREN91_LOCAL_AGENT_TOKEN=*' } | Select-Object -First 1
  if ($existing) { $token = $existing.Substring('SOREN91_LOCAL_AGENT_TOKEN='.Length).Trim() }
}
if (-not $token -or $token.Length -lt 24) {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
# Create the file empty, lock its ACL down to the current user, then write.
Set-Content -Path $envFile -Value '' -Encoding ASCII
& icacls $envFile /inheritance:r /grant:r "${env:USERNAME}:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'failed to restrict env file ACL' }
@(
  '# Soren91 Windows local agent (written by infra/local/windows/soren91/install.ps1).',
  '# Owner-only ACL. Never commit or paste the token.',
  "SOREN91_LOCAL_AGENT_HOST=$tailscaleIp",
  "SOREN91_LOCAL_AGENT_PORT=$Port",
  "SOREN91_LOCAL_AGENT_TOKEN=$token",
  'SOREN91_LOCAL_SESSION_MODE=cdp-host',
  "SOREN91_LOCAL_FFMPEG_BIN=$FfmpegBin",
  "SOREN91_CDP_CHROME_BIN=$chromeBin",
  "SOREN91_LOCAL_AUDIO_SINK=$AudioSink",
  "SOREN91_LOCAL_AUDIO_SINK_DIR=$chromeDir",
  "SOREN91_NODE_BIN=$node"
) | Set-Content -Path $envFile -Encoding ASCII

$launcher = Join-Path $PSScriptRoot 'soren91_agent_launch.vbs'
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\wscript.exe') -Argument "//B //Nologo `"$launcher`""
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Action $action -Trigger @($atLogon, $watchdog) `
  -Settings $settings -Principal $principal -Description 'Soren91 (Meriken AI) Windows renderer agent: hidden supervisor, restarts the agent on exit.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath
Write-Output "installed $taskPath$taskName"
Write-Output "agent: http://${tailscaleIp}:$Port (token in $envFile, owner-only ACL)"
Write-Output "cdp proxy (per session): http://${tailscaleIp}:19093"
