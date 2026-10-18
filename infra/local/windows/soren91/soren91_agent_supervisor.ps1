# Soren91 local agent supervisor (Windows, interactive user session).
#
# Started hidden at logon by the "\soren91\Soren91LocalAgent" scheduled task
# (via soren91_agent_launch.vbs). Loads the agent environment from an
# ACL-protected env file, runs tools/soren91_local_agent.mjs and restarts it
# 5s after any exit. A named mutex keeps one supervisor per user session.
# The token is passed only through the child environment; it is never
# written to the log or to argv.
param(
  [string]$EnvFile = (Join-Path $env:LOCALAPPDATA 'soren91\agent.env')
)
$ErrorActionPreference = 'Continue'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$agentScript = Join-Path $repo 'tools\soren91_local_agent.mjs'
$logDir = Join-Path (Split-Path -Parent $EnvFile) 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir 'agent.log'
$maxLogBytes = 20MB

function Write-SupervisorLog([string]$message) {
  Add-Content -Path $log -Value ("[{0}] [supervisor] {1}" -f (Get-Date -Format o), $message) -Encoding UTF8
}

function Read-AgentEnv([string]$path) {
  $vars = @{}
  foreach ($line in Get-Content -Path $path -Encoding UTF8 -ErrorAction Stop) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $eq = $trimmed.IndexOf('=')
    if ($eq -lt 1) { continue }
    $vars[$trimmed.Substring(0, $eq).Trim()] = $trimmed.Substring($eq + 1).Trim()
  }
  return $vars
}

$mutex = New-Object System.Threading.Mutex($false, 'Local\Soren91AgentSupervisor')
if (-not $mutex.WaitOne(0)) { exit 0 }

Write-SupervisorLog "started (pid=$PID, repo=$repo)"
while ($true) {
  if ((Test-Path $log) -and (Get-Item $log).Length -gt $maxLogBytes) {
    Move-Item -Force $log "$log.1"
  }
  try {
    $vars = Read-AgentEnv $EnvFile
  } catch {
    # Distinguish "missing" from "access denied" (owner-only ACL vs the
    # identity this supervisor actually runs as); never log the contents.
    Write-SupervisorLog ("env file unreadable: {0} ({1}); retrying in 30s" -f $EnvFile, $_.Exception.GetType().Name)
    Start-Sleep -Seconds 30
    continue
  }
  $bindHost = $vars['SOREN91_LOCAL_AGENT_HOST']
  if ($bindHost -and $bindHost -ne '127.0.0.1' -and -not (Get-NetIPAddress -IPAddress $bindHost -ErrorAction SilentlyContinue)) {
    Write-SupervisorLog "bind address $bindHost is not assigned yet (Tailscale down?); retrying in 10s"
    Start-Sleep -Seconds 10
    continue
  }
  $node = $vars['SOREN91_NODE_BIN']
  if (-not $node) { $node = (Get-Command node -ErrorAction SilentlyContinue).Source }
  if (-not $node) {
    Write-SupervisorLog 'node.exe not found; retrying in 30s'
    Start-Sleep -Seconds 30
    continue
  }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = Join-Path $env:WINDIR 'System32\cmd.exe'
  $psi.Arguments = "/d /s /c `"`"$node`" `"$agentScript`" >> `"$log`" 2>&1`""
  $psi.WorkingDirectory = $repo
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  foreach ($key in $vars.Keys) { $psi.EnvironmentVariables[$key] = $vars[$key] }
  $started = Get-Date
  $proc = [System.Diagnostics.Process]::Start($psi)
  Write-SupervisorLog "agent started (cmd pid=$($proc.Id))"
  $proc.WaitForExit()
  $uptime = [int]((Get-Date) - $started).TotalSeconds
  Write-SupervisorLog "agent exited (code=$($proc.ExitCode), uptime=${uptime}s); restarting in 5s"
  Start-Sleep -Seconds 5
}
