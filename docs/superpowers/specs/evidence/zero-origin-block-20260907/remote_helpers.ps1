$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ZeroEvidence = $PSScriptRoot
$ZeroTarget = 'lenovo@100.109.90.21'
$ZeroIncoming = 'C:\Ruisheng\incoming\zero-origin-block-20260907'
$ZeroSsh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$ZeroScp = 'C:\Windows\System32\OpenSSH\scp.exe'
$ZeroOptions = @('-F','NUL','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10')

function Invoke-ZeroTarget([string]$Script, [string]$LogName) {
    $Prelude = '$ErrorActionPreference="Stop"; $ProgressPreference="SilentlyContinue"; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); '
    $Encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Prelude + $Script))
    if ($Encoded.Length -gt 7600) { throw 'Remote command exceeds the bounded command envelope' }
    $Output = @(& $ZeroSsh -n -T @ZeroOptions $ZeroTarget powershell.exe -NoProfile -NonInteractive -EncodedCommand $Encoded 2>&1)
    $Exit = $LASTEXITCODE
    if ($LogName) {
        $Output | Set-Content -LiteralPath (Join-Path $ZeroEvidence $LogName) -Encoding UTF8
    }
    if ($Exit -ne 0) { throw ('Target command failed with exit ' + $Exit + '; evidence: ' + $LogName) }
    return ($Output -join [Environment]::NewLine)
}

function Copy-ZeroToTarget([string]$LocalPath) {
    $Name = [IO.Path]::GetFileName($LocalPath)
    if ($Name -cnotmatch '^[A-Za-z0-9._-]+$') { throw 'Unexpected transfer file name' }
    & $ZeroScp @ZeroOptions $LocalPath ("${ZeroTarget}:" + $ZeroIncoming.Replace('\','/') + '/' + $Name)
    if ($LASTEXITCODE -ne 0) { throw ('Upload failed: ' + $Name) }
    $Hash = (Get-FileHash -LiteralPath $LocalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [void](Invoke-ZeroTarget ("if ((Get-FileHash -LiteralPath '" + $ZeroIncoming + '\' + $Name +
        "' -Algorithm SHA256).Hash.ToLowerInvariant() -cne '" + $Hash + "') { throw 'Uploaded file hash mismatch' }") '')
}

function Invoke-ZeroTargetFile([string]$Name, [string]$Arguments, [string]$LogName) {
    $Local = Join-Path $ZeroEvidence $Name
    $Hash = (Get-FileHash -LiteralPath $Local -Algorithm SHA256).Hash.ToLowerInvariant()
    $Remote = $ZeroIncoming + '\' + $Name
    $Script = "if ((Get-FileHash -LiteralPath '$Remote' -Algorithm SHA256).Hash.ToLowerInvariant() -cne '$Hash') { throw 'Remote helper hash mismatch' }; " +
        "& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -NonInteractive -File '$Remote' $Arguments; exit " + '$LASTEXITCODE'
    return Invoke-ZeroTarget $Script $LogName
}

function Copy-ZeroFromTarget([string]$Remote, [string]$Name) {
    $Destination = Join-Path $ZeroEvidence $Name
    if (Test-Path -LiteralPath $Destination) { throw ('Evidence copy already exists: ' + $Name) }
    & $ZeroScp @ZeroOptions ("${ZeroTarget}:" + $Remote.Replace('\','/')) $Destination
    if ($LASTEXITCODE -ne 0) { throw ('Evidence download failed: ' + $Name) }
    $Expected = Invoke-ZeroTarget ("(Get-FileHash -LiteralPath '" + $Remote + "' -Algorithm SHA256).Hash.ToLowerInvariant()") ''
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Expected.Trim()) {
        throw ('Evidence copy hash mismatch: ' + $Name)
    }
    return [ordered]@{path=$Remote;local_name=$Name;sha256=$Expected.Trim()}
}
