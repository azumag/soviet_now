# Builds the Windows cdp-host helpers into tools/windows/bin/ with the csc.exe
# that ships with .NET Framework 4.x on every Windows 10/11 install (no SDK or
# download needed). Output is gitignored build output, like tools/macos/bin.
#   soren91_process_loopback.exe  Chrome-process-tree audio (ApplicationLoopback)
#   soren91_window_audit.exe      visible-window audit of a process tree (no pixels)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$outDir = Join-Path $here 'windows\bin'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "csc.exe not found at $csc" }
New-Item -ItemType Directory -Force $outDir | Out-Null
foreach ($name in @('soren91_process_loopback', 'soren91_window_audit')) {
  $src = Join-Path $here "windows\$name.cs"
  $out = Join-Path $outDir "$name.exe"
  & $csc /nologo /optimize+ /platform:x64 /target:exe "/out:$out" $src
  if ($LASTEXITCODE -ne 0) { throw "csc failed for $name with exit code $LASTEXITCODE" }
  Write-Output "built $out"
}
