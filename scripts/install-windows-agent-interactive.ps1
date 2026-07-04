<#
.SYNOPSIS
  Interactive Vexor Logs agent installer for Windows.
  Prompts for URL, token, agent, and log paths/channels,
  then delegates to install-windows-agent.ps1.
#>
[CmdletBinding()] param()

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Must be run from an elevated PowerShell."
}

$VexorUrl = Read-Host "Vexor server URL (e.g. https://vexor.example.com)"
if ([string]::IsNullOrWhiteSpace($VexorUrl)) { throw "URL required" }

$Token = Read-Host "Bootstrap token (from GUI: Logs > Shippers; blank = none)"
$HostName = Read-Host "Host name as Vexor knows it (blank = this computer name)"
$Agent = Read-Host "Agent [vector / fluentbit] (default: vector)"
if ([string]::IsNullOrWhiteSpace($Agent)) { $Agent = "vector" }

$FileEncoding = Read-Host "Charset for file logs (UTF-16LE for SQL Server ERRORLOG, windows-1252 for legacy ANSI; blank = UTF-8)"

$Logs = @()

Write-Host ""
$stdAns = Read-Host "Ship the standard Windows event logs (Application, System, Security)? [Y/n]"
if ($stdAns -notmatch '^\s*(n|no)\s*$') {
  $Logs += @("Application","System","Security")
  Write-Host "  + Application, System, Security"
}

Write-Host ""
Write-Host "Add more log sources? For each entry you can point to:"
Write-Host "  - a log file:       C:\MyApp\logs\app.log"
Write-Host "  - a folder:         C:\inetpub\logs\LogFiles      (all files under it, recursively)"
Write-Host "  - a glob:           C:\logs\**\*.log"
Write-Host "  - an event channel: Setup | ForwardedEvents | ..."
Write-Host "  Press Enter on an empty line to finish."
Write-Host ""

while ($true) {
  $p = Read-Host "  add log (blank = done)"
  if ([string]::IsNullOrWhiteSpace($p)) { break }
  $p = $p.Trim()
  # If the entry is an existing directory, expand it to a recursive file glob.
  if (Test-Path -LiteralPath $p -PathType Container) {
    $p = (Join-Path $p "**\*")
    Write-Host "  (folder -> $p)"
  }
  $Logs += $p
}

if ($Logs.Count -eq 0) {
  Write-Host "No sources selected; defaulting to Application, System, Security."
  $Logs = @("Application","System","Security")
}

# Download default installer from this Vexor server and re-invoke
$tmp = Join-Path $env:TEMP "install-windows-agent.ps1"
$base = $VexorUrl.TrimEnd('/')
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# Trust self-signed certs for the download:
add-type @"
using System.Net; using System.Security.Cryptography.X509Certificates;
public class TrustAllCerts : ICertificatePolicy {
  public bool CheckValidationResult(ServicePoint sp, X509Certificate c, WebRequest r, int p) { return true; }
}
"@
[Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCerts
Invoke-WebRequest -Uri "$base/api/v1/logs/install-scripts/install-windows-agent.ps1" -OutFile $tmp -UseBasicParsing

Write-Host ""
Write-Host "=> install-windows-agent.ps1 -VexorUrl $VexorUrl -Agent $Agent -Logs $($Logs -join ',')"
$installArgs = @{
  VexorUrl     = $VexorUrl
  Token        = $Token
  Agent        = $Agent
  Logs         = $Logs
  FileEncoding = $FileEncoding
  HostName     = $HostName
}
& $tmp @installArgs
