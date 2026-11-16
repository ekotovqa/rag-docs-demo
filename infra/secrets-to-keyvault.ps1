#requires -Version 7.0
<#
.SYNOPSIS
    Puts the demo's two secrets into Key Vault without showing them.

.DESCRIPTION
    Run after the first Bicep stage (the vault's name is its output keyVaultName).
      anthropic-api-key  - the Anthropic API key
      database-url       - the Neon connection string, postgresql://user:password@host/db?sslmode=require
    Both are asked with hidden input, or taken from .env with -FromDotEnv. A rerun overwrites them.

.EXAMPLE
    pwsh infra/secrets-to-keyvault.ps1 -Vault ragdemo-kv-xxxxxxxx
    pwsh infra/secrets-to-keyvault.ps1 -Vault ragdemo-kv-xxxxxxxx -FromDotEnv
#>
param(
    [Parameter(Mandatory)][string]$Vault,
    [switch]$FromDotEnv
)

$ErrorActionPreference = 'Stop'
$az = (Get-Command az -ErrorAction SilentlyContinue)?.Source ?? 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'

function Read-Secret([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not $plain) { throw "$Prompt is empty" }
    return $plain.Trim()
}

function Read-DotEnv([string]$Name) {
    $line = Get-Content (Join-Path $PSScriptRoot '..' '.env') | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -Last 1
    if (-not $line) { throw "$Name is not in .env" }
    return ($line -replace "^\s*$Name\s*=", '').Trim().Trim('"')
}

function Set-VaultSecret([string]$Name, [string]$Value) {
    # The value goes in through a file, never on the command line: no process listing or history shows it.
    $tmp = New-TemporaryFile
    try {
        [IO.File]::WriteAllText($tmp.FullName, $Value)
        & $az keyvault secret set --vault-name $Vault --name $Name --file $tmp.FullName --output none
        if ($LASTEXITCODE -ne 0) { throw "az keyvault secret set $Name failed" }
    } finally {
        Remove-Item $tmp.FullName -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  $Name set"
}

$key = if ($FromDotEnv) { Read-DotEnv 'ANTHROPIC_API_KEY' } else { Read-Secret 'Anthropic API key' }
$db = if ($FromDotEnv) { Read-DotEnv 'DATABASE_URL' } else { Read-Secret 'Neon connection string' }
if ($db -notmatch '^postgres(ql)?://') { throw 'the connection string must start with postgresql://' }
if ($db -notmatch 'sslmode=') { $db += ($db.Contains('?') ? '&' : '?') + 'sslmode=require' }

Set-VaultSecret 'anthropic-api-key' $key
Set-VaultSecret 'database-url' $db
