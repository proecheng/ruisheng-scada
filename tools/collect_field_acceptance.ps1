[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$output = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $output) { throw 'evidence_already_exists' }
function Read-Ast([string]$Path) {
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw 'source_parse_failed' }
    return $ast
}
$controller = Read-Ast (Join-Path $PSScriptRoot 'remote_full_upgrade.ps1')
foreach ($node in $controller.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Invoke-SshScript'}, $false)) {
    . ([ScriptBlock]::Create($node.Extent.Text))
}
foreach ($node in $controller.FindAll({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$script:RemotePowerShellBootstrap'}, $false)) {
    . ([ScriptBlock]::Create($node.Extent.Text))
}
$updater = Read-Ast (Join-Path $PSScriptRoot 'remote_full_upgrade\target-updater.ps1')
$guard = @($updater.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Assert-EntitlementFeature'}, $false))
if ($guard.Count -ne 1) { throw 'guard_missing' }
$remotePath = Join-Path $PSScriptRoot 'field_acceptance.remote.ps1'
[void](Read-Ast $remotePath)
$sql = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'field_acceptance.sql')))
$remote = $guard[0].Extent.Text + "`n" + @'
$ErrorActionPreference='Stop'
if ($env:COMPUTERNAME -cne 'WIN-OAUCM8UQUGH') { throw 'target_hostname_mismatch' }
Assert-EntitlementFeature 'remote-support'
'@ + "`n" + [IO.File]::ReadAllText($remotePath).Replace('__SQL_BASE64__', $sql)
$Target = 'lenovo@100.109.90.21'
. (Join-Path $PSScriptRoot 'diagnostic_limits.ps1')
$supervised = New-BoundedDiagnosticScript -Script $remote -TimeoutSeconds 120
$envelope = (Invoke-SshScript -Script $supervised -TimeoutSeconds 150) | ConvertFrom-Json
if (-not $envelope.ok) { throw ('remote_' + [string]$envelope.error) }
$result = [string]$envelope.output
$parsed = $result | ConvertFrom-Json
if ($parsed.schema_version -ne 1 -or -not $parsed.read_only_business_data) { throw 'receipt_invalid' }
[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($output))
$stream = [IO.File]::Open($output, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($result)
    $stream.Write($bytes, 0, $bytes.Length)
} finally { $stream.Dispose() }
Write-Output $output
