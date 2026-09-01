$ErrorActionPreference = "Stop"
$provider = "C:\ProgramData\Ruisheng\bin\trust-root-freshness-provider.exe"
$config = "C:\ProgramData\Ruisheng\trust\point-profile-freshness-provider.json"
$root = "C:\ProgramData\Ruisheng\trust\point-profile-policy-root.json"
$policy = "C:\ProgramData\Ruisheng\site\b08\point-profile-trust-policy.json"
$profile = "C:\ProgramData\Ruisheng\site\b08\point-profile.json"
$publisher = "C:\ProgramData\Ruisheng\bin\verify-publisher.ps1"
$manifest = Get-Content -Raw -LiteralPath "C:\Ruisheng\candidates\deploy-20260831.1\MANIFEST.json" |
    ConvertFrom-Json
$output = "C:\ProgramData\Ruisheng\staging\freshness-cleared-env.json"
Remove-Item -LiteralPath $output -Force -ErrorAction SilentlyContinue
$bytes = [byte[]]::new(32)
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $random.GetBytes($bytes) } finally { $random.Dispose() }
$challenge = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
$now = [DateTimeOffset]::UtcNow
$microseconds = [int64][Math]::Floor([decimal]($now.Ticks % [TimeSpan]::TicksPerSecond) / 10)
$prefix = $now.ToString("yyyy-MM-dd'T'HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
$requestedAt = if ($microseconds -eq 0) { "${prefix}+00:00" } else {
    "${prefix}.$($microseconds.ToString('D6'))+00:00"
}
$arguments = @(
    "attest", "--config", $config, "--trust-root", $root, "--trust-policy", $policy,
    "--profile", $profile, "--candidate-logical-identity", [string]$manifest.logical_identity,
    "--verifier-id", "ruisheng.protected-release-publisher.windows.v1",
    "--verifier-tool-sha256", ("sha256:" + (Get-FileHash -Algorithm SHA256 -LiteralPath $publisher).Hash.ToLowerInvariant()),
    "--challenge", $challenge, "--requested-at", $requestedAt, "--output", $output
)
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = $provider
$start.UseShellExecute = $false
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
foreach ($argument in $arguments) { [void]$start.ArgumentList.Add($argument) }
$start.Environment.Clear()
$system = [Environment]::SystemDirectory
$windows = [IO.Directory]::GetParent($system).FullName
$start.Environment["COMSPEC"] = Join-Path $system "cmd.exe"
$start.Environment["PATH"] = $system
$start.Environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
$start.Environment["SYSTEMROOT"] = $windows
$start.Environment["WINDIR"] = $windows
$process = [Diagnostics.Process]::Start($start)
$stdout = $process.StandardOutput.ReadToEndAsync()
$stderr = $process.StandardError.ReadToEndAsync()
$process.WaitForExit()
[pscustomobject]@{
    exit_code = $process.ExitCode
    stdout = $stdout.GetAwaiter().GetResult()
    stderr = $stderr.GetAwaiter().GetResult()
    output_exists = Test-Path -LiteralPath $output
} | ConvertTo-Json -Compress
