<#
.SYNOPSIS
  Install Vector as a Windows service and ship logs to a Vexor instance.

.PARAMETER VexorUrl
  Base URL of the Vexor server, e.g. https://vexor.example.com

.PARAMETER Token
  Bootstrap token for the ingest endpoint (optional).

.PARAMETER Agent
  vector (default) or fluentbit.

.PARAMETER Logs
  One or more paths/log channels. Defaults to Application,System,Security event logs.

.EXAMPLE
  install-windows-agent.ps1 -VexorUrl https://vexor.example.com -Token abc -Logs Application,Security
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$VexorUrl,
  [string]$Token = "",
  [ValidateSet("vector","fluentbit")][string]$Agent = "vector",
  [string[]]$Logs = @("Application","System","Security"),
  [string]$HostName = ""
)

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Must be run from an elevated PowerShell."
}

$EffectiveHost = if ([string]::IsNullOrWhiteSpace($HostName)) { $env:COMPUTERNAME } else { $HostName }

$VectorVersion = "0.55.0"
$InstallDir = "C:\Program Files\Vexor\vector"
$DataDir    = "C:\ProgramData\Vexor\vector"
$ConfDir    = "C:\ProgramData\Vexor\vector\conf"
New-Item -Force -ItemType Directory $InstallDir,$DataDir,$ConfDir | Out-Null

function Ensure-Nssm {
  $nssm = Join-Path $InstallDir "nssm.exe"
  if (Test-Path $nssm) { return $nssm }
  $tmp = "$env:TEMP\nssm.zip"
  Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $tmp
  $exp = "$env:TEMP\nssm-extract"
  Remove-Item -Recurse -Force $exp -ErrorAction Ignore
  Expand-Archive -Path $tmp -DestinationPath $exp
  $found = Get-ChildItem -Recurse -Path $exp -Filter "nssm.exe" | Where-Object { $_.FullName -match "win64" } | Select-Object -First 1
  Copy-Item $found.FullName $nssm -Force
  return $nssm
}

function Install-Vector {
  $exe = Join-Path $InstallDir "vector.exe"
  if (Test-Path $exe) { Write-Host "vector.exe already present"; return $exe }
  $zip = "$env:TEMP\vector-$VectorVersion.zip"
  $url = "https://packages.timber.io/vector/$VectorVersion/vector-$VectorVersion-x86_64-pc-windows-msvc.zip"
  Write-Host "+ downloading $url"
  Invoke-WebRequest -Uri $url -OutFile $zip
  $ext = "$env:TEMP\vector-ext-$VectorVersion"
  Remove-Item -Recurse -Force $ext -ErrorAction Ignore
  Expand-Archive -Path $zip -DestinationPath $ext
  Copy-Item (Get-ChildItem -Recurse -Path $ext -Filter "vector.exe" | Select-Object -First 1).FullName $exe -Force
  return $exe
}

function Write-VectorConfig {
  $toml = @()
  $toml += 'data_dir = "C:/ProgramData/Vexor/vector"'
  $toml += ''
  $toml += '[sources.winlog]'
  $toml += 'type = "windows_event_log"'
  $toml += 'subscription_name = "Vexor"'
  $toml += 'channels = [' + (($Logs | ForEach-Object { '"' + $_ + '"' }) -join ", ") + ']'
  $toml += ''
  $toml += '[transforms.add_host]'
  $toml += 'type    = "remap"'
  $toml += 'inputs  = ["winlog"]'
  $vrl = ".host = `"$EffectiveHost`"`nif !exists(._msg) {`n  ._msg = to_string(.message) ?? to_string(.Message) ?? to_string(.RenderingInfo.Message) ?? encode_json(.)`n}"
  $toml += "source  = '''`n$vrl`n'''"
  $toml += ''
  $toml += '[sinks.vexor]'
  $toml += 'type    = "http"'
  $toml += 'inputs  = ["add_host"]'
  $toml += 'uri     = "' + $VexorUrl.TrimEnd('/') + '/api/v1/logs/push?_stream_fields=host,channel"'
  $toml += 'encoding.codec = "json"'
  $toml += 'framing.method = "newline_delimited"'
  $toml += 'compression = "gzip"'
  $toml += 'tls.verify_certificate = false'
  $toml += 'healthcheck.enabled = false'
  if ($Token) {
    $toml += ('request.headers.Authorization = "Bearer ' + $Token + '"')
  }
  Set-Content -Path (Join-Path $ConfDir "vector.toml") -Value ($toml -join "`r`n") -Encoding UTF8
}

if ($Agent -ne "vector") {
  throw "Only the 'vector' agent is supported on Windows in this release."
}

$exe  = Install-Vector
$nssm = Ensure-Nssm
Write-VectorConfig

# Register as Windows service via NSSM
$svc = "vexor-vector"

# NSSM writes to stderr for benign conditions (e.g. stopping/removing a service
# that does not exist yet). Under $ErrorActionPreference='Stop', PowerShell 5.1
# turns that native stderr into a terminating NativeCommandError even with
# 2>$null, so relax error handling for the service-registration section and
# guard the pre-clean with Get-Service.
$svcEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
  if (Get-Service -Name $svc -ErrorAction SilentlyContinue) {
    & $nssm stop $svc 2>&1 | Out-Null
    & $nssm remove $svc confirm 2>&1 | Out-Null
    Start-Sleep -Milliseconds 500
  }
  & $nssm install $svc $exe "--config" (Join-Path $ConfDir "vector.toml") 2>&1 | Out-Null
  & $nssm set $svc Start SERVICE_AUTO_START 2>&1 | Out-Null
  & $nssm set $svc AppStdout (Join-Path $DataDir "vector.log") 2>&1 | Out-Null
  & $nssm set $svc AppStderr (Join-Path $DataDir "vector.err") 2>&1 | Out-Null
  & $nssm start $svc 2>&1 | Out-Null
} finally {
  $ErrorActionPreference = $svcEap
}

# Verify the service was created and is running.
Start-Sleep -Seconds 2
$s = Get-Service -Name $svc -ErrorAction SilentlyContinue
if (-not $s) { throw "vexor-vector service was not created (nssm install failed)." }
if ($s.Status -ne "Running") {
  Write-Warning "vexor-vector service created but not running yet (status: $($s.Status)). Check $DataDir\vector.err"
}

Write-Host "OK: vexor-vector service installed and started."
Write-Host "Logs: $DataDir\vector.log"
