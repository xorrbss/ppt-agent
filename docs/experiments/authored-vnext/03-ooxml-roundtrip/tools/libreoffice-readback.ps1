[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [string]$OutputJson,
    [string]$LibreOfficeExecutable = "C:\Program Files\LibreOffice\program\soffice.exe",
    [ValidateRange(10, 3600)][int]$TimeoutSeconds = 120,
    [string]$WorkingRoot = [System.IO.Path]::GetTempPath(),
    [switch]$KeepWorkingDirectory
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$windowsReopen = Join-Path $PSScriptRoot "windows-reopen.ps1"
$arguments = @{
    InputPath = $InputPath
    Engine = "LibreOffice"
    LibreOfficeExecutable = $LibreOfficeExecutable
    TimeoutSeconds = $TimeoutSeconds
    WorkingRoot = $WorkingRoot
}
if ($OutputJson) { $arguments.OutputJson = $OutputJson }
if ($KeepWorkingDirectory) { $arguments.KeepWorkingDirectory = $true }

& $windowsReopen @arguments
exit $LASTEXITCODE
