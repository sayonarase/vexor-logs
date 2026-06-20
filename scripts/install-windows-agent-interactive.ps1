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

Write-Host ""
Write-Host "Log paths / event channels to ship."
Write-Host "  - Files: full glob, e.g. C:\inetpub\logs\LogFiles\**\*.log"
Write-Host "  - Event channels: Application | System | Security | Setup | ForwardedEvents"
Write-Host "  Press Enter on an empty line to finish."
Write-Host "  Default if you skip: Application,System,Security"
Write-Host ""

$Logs = @()
while ($true) {
  $p = Read-Host "  path/channel"
  if ([string]::IsNullOrWhiteSpace($p)) { break }
  $Logs += $p
}
if ($Logs.Count -eq 0) { $Logs = @("Application","System","Security") }

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
& powershell -ExecutionPolicy Bypass -File $tmp -VexorUrl $VexorUrl -Token $Token -Agent $Agent -Logs $Logs -HostName $HostName
