"""Contracts for the signed full-release remote upgrade workflow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CONTROLLER = ROOT / "tools" / "remote_full_upgrade.ps1"
UPDATER = ROOT / "tools" / "remote_full_upgrade" / "target-updater.ps1"
REMOTE_BOOTSTRAP = (
    "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
    "[Console]::InputEncoding=[Text.Encoding]::UTF8;"
    "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);"
    "$encoded=[string]($input|Select-Object -First 1);"
    "if([string]::IsNullOrWhiteSpace($encoded)){throw 'stdin_payload_missing'};"
    "if($encoded.Length -gt 2097152){throw 'stdin_payload_exceeded'};"
    "if($encoded -notmatch '^[A-Za-z0-9+/]+={0,2}$')"
    "{$invalid=[regex]::Match($encoded,'[^A-Za-z0-9+/=]');"
    'throw "stdin_payload_alphabet_invalid_$([int][char]$invalid.Value)_'
    '$($invalid.Index)_$($encoded.Length)"};'
    "try{$bytes=[Convert]::FromBase64String($encoded)}"
    "catch{throw 'stdin_payload_decode_invalid'};"
    "if([Convert]::ToBase64String($bytes) -cne $encoded)"
    "{throw 'stdin_payload_noncanonical'};"
    "$source=[Text.Encoding]::UTF8.GetString($bytes);"
    "& ([ScriptBlock]::Create($source))"
)
RESULT_KEYS = {
    "schema_version",
    "ok",
    "status",
    "action",
    "operation_id",
    "error_code",
    "active_release",
    "candidate",
    "locks",
    "backup",
}

SSH_STUB_SOURCE = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

public static class SshStub
{
    private static string RequiredEnvironment(string name)
    {
        string value = Environment.GetEnvironmentVariable(name);
        if (String.IsNullOrEmpty(value))
        {
            throw new InvalidOperationException("missing environment variable: " + name);
        }
        return value;
    }

    private static void Record(string[] args, string payload)
    {
        string arguments = Convert.ToBase64String(
            Encoding.UTF8.GetBytes(String.Join("\0", args))
        );
        string stdin = Convert.ToBase64String(Encoding.UTF8.GetBytes(payload));
        File.AppendAllText(
            RequiredEnvironment("SSH_STUB_LOG"),
            "ssh|1|" + arguments + "|" + stdin + Environment.NewLine,
            new UTF8Encoding(false)
        );
    }

    public static int Main(string[] args)
    {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        string payload = Console.In.ReadToEnd();
        Record(args, payload);
        string mode = RequiredEnvironment("SSH_STUB_MODE");
        if (mode == "empty")
        {
            return 0;
        }
        if (mode == "invalid")
        {
            Console.Out.Write("not-json");
            return 0;
        }
        if (mode == "clixml")
        {
            Console.Out.Write("#< CLIXML\r\n<Objs />");
            return 0;
        }
        if (mode == "prompt")
        {
            Console.Out.Write("PS C:\\> ");
            return 0;
        }
        if (mode == "unicode")
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            Console.Out.Write("\u4F20\u8F93\u6B63\u5E38");
            return 0;
        }
        if (mode == "failed")
        {
            Console.Error.Write("injected ssh failure");
            return 23;
        }
        if (mode == "response-observed" || mode == "response-planned")
        {
            string status = mode == "response-observed" ? "observed" : "planned";
            string action = mode == "response-observed" ? "Status" : "Plan";
            Console.Out.Write(
                "{\"schema_version\":1,\"ok\":true,\"status\":\"" + status
                + "\",\"action\":\"" + action
                + "\",\"operation_id\":\"c5a62e0e-98d9-4a9a-8150-c3c8abad719b\""
                + ",\"error_code\":\"\",\"active_release\":null,\"candidate\":null"
                + ",\"locks\":null,\"backup\":null}"
            );
            return 0;
        }

        int commandIndex = Array.IndexOf(args, "powershell.exe");
        if (commandIndex < 0)
        {
            Console.Error.Write("remote powershell command is missing");
            return 24;
        }
        string encodedPayload = payload.Trim().TrimStart('\uFEFF');
        string childPayload = encodedPayload;
        if (mode == "bad-base64")
        {
            childPayload = "not base64!";
        }
        else if (mode == "truncate")
        {
            childPayload = encodedPayload.Substring(0, encodedPayload.Length / 2);
        }
        else if (mode == "apply" || mode == "apply-extra" || mode == "apply-whitespace")
        {
            string source = Encoding.UTF8.GetString(Convert.FromBase64String(encodedPayload));
            string preamble = source.StartsWith("\uFEFF", StringComparison.Ordinal)
                ? "\uFEFF"
                : "";
            string body = preamble.Length == 0 ? source : source.Substring(1);
            string childSource = preamble + @"
function Test-Path { param([string]$LiteralPath) return $false }
function New-Item {
  param([string]$ItemType, [string]$Path)
  $script:CreatedPath = $Path
  [pscustomobject]@{ FullName = $Path }
}
function Set-Acl {
  param([string]$LiteralPath, $AclObject)
  if ($LiteralPath -cne $script:CreatedPath) { throw 'unexpected prepare path' }
}
" + body;
            childPayload = Convert.ToBase64String(
                new UTF8Encoding(false).GetBytes(childSource)
            );
        }

        Process child = new Process();
        child.StartInfo.FileName = RequiredEnvironment("SSH_STUB_REMOTE_PS");
        child.StartInfo.Arguments = String.Join(
            " ",
            args.Skip(commandIndex + 1).Select(
                value => "\"" + value.Replace("\"", "\\\"") + "\""
            ).ToArray()
        );
        child.StartInfo.UseShellExecute = false;
        child.StartInfo.CreateNoWindow = true;
        child.StartInfo.RedirectStandardInput = true;
        child.StartInfo.RedirectStandardOutput = true;
        child.StartInfo.RedirectStandardError = true;
        child.StartInfo.StandardOutputEncoding = new UTF8Encoding(false);
        child.StartInfo.StandardErrorEncoding = new UTF8Encoding(false);
        child.Start();
        Task<string> stdout = child.StandardOutput.ReadToEndAsync();
        Task<string> stderr = child.StandardError.ReadToEndAsync();
        byte[] childBytes = new UTF8Encoding(false).GetBytes(childPayload);
        child.StandardInput.BaseStream.Write(childBytes, 0, childBytes.Length);
        child.StandardInput.BaseStream.Close();
        child.WaitForExit();
        Task.WaitAll(stdout, stderr);
        Console.Out.Write(stdout.Result);
        Console.Error.Write(stderr.Result);
        if (mode == "execute-extra" || mode == "apply-extra")
        {
            Console.Out.Write("\r\nextra-output");
        }
        else if (mode == "apply-whitespace")
        {
            Console.Out.Write(" ");
        }
        return child.ExitCode;
    }
}
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _powershell() -> str:
    executable = shutil.which("powershell.exe")
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    return executable


def _windows_powershell_env() -> dict[str, str]:
    if os.name != "nt":
        pytest.skip("Windows PowerShell environment is Windows-only")
    env = os.environ.copy()
    user_profile = env.get("USERPROFILE") or str(Path.home())
    env["PSModulePath"] = os.pathsep.join(
        (
            str(Path(user_profile) / "Documents" / "WindowsPowerShell" / "Modules"),
            str(
                Path(env.get("ProgramFiles", r"C:\Program Files")) / "WindowsPowerShell" / "Modules"
            ),
            str(
                Path(env.get("SystemRoot", r"C:\Windows"))
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "Modules"
            ),
        )
    )
    return env


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _function(source: str, name: str, next_name: str) -> str:
    return (
        f"function {name}"
        + source.split(f"function {name}", 1)[1].split(f"function {next_name}", 1)[0]
    )


@pytest.fixture(scope="session")
def native_ssh_stub(tmp_path_factory: pytest.TempPathFactory) -> Path:
    stub_dir = tmp_path_factory.mktemp("native-ssh-stub")
    output = stub_dir / "ssh.exe"
    compile_command = (
        "$ErrorActionPreference='Stop';"
        "$source=[Console]::In.ReadToEnd();"
        f"Add-Type -TypeDefinition $source -OutputAssembly {_ps_literal(output)} "
        "-OutputType ConsoleApplication"
    )
    completed = subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            compile_command,
        ],
        input=SSH_STUB_SOURCE,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, (completed.stdout or "") + (completed.stderr or "")
    assert output.is_file()
    return stub_dir


def _stub_environment(
    stub_dir: Path,
    remote_powershell: str,
    log_path: Path,
    mode: str,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{stub_dir}{os.pathsep}{environment['PATH']}"
    environment["SSH_STUB_LOG"] = str(log_path)
    environment["SSH_STUB_MODE"] = mode
    environment["SSH_STUB_REMOTE_PS"] = remote_powershell
    return environment


def _read_stub_calls(log_path: Path) -> list[dict[str, object]]:
    calls = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        kind, read_count, encoded_args, encoded_stdin = line.split("|", 3)
        calls.append(
            {
                "kind": kind,
                "read_count": int(read_count),
                "args": base64.b64decode(encoded_args).decode("utf-8").split("\0"),
                "stdin": base64.b64decode(encoded_stdin).decode("utf-8"),
            }
        )
    return calls


def _decode_transport_payload(call: dict[str, object]) -> str:
    encoded = call["stdin"]
    assert isinstance(encoded, str)
    return base64.b64decode(encoded.strip().lstrip("\ufeff"), validate=True).decode("utf-8")


def _assert_transport_call(call: dict[str, object]) -> None:
    args = call["args"]
    assert isinstance(args, list)
    assert call["kind"] == "ssh"
    assert call["read_count"] == 1
    assert args == [
        "-T",
        "-F",
        "NUL",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "operator@100.64.0.1",
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-OutputFormat",
        "Text",
        "-EncodedCommand",
        base64.b64encode(REMOTE_BOOTSTRAP.encode("utf-16-le")).decode("ascii"),
    ]
    assert "-Command" not in args


def _status_harness() -> str:
    source = _read(CONTROLLER)
    bootstrap = next(
        line for line in source.splitlines() if line.startswith("$script:RemotePowerShellBootstrap")
    )
    convert = _function(source, "ConvertTo-PowerShellUtf8Expression", "Get-Sha256Text")
    exact_keys = _function(source, "Test-ExactKeys", "Get-CandidateMetadata")
    transport = _function(source, "Invoke-SshScript", "Invoke-Updater")
    updater = _function(source, "Invoke-Updater", "Set-RestrictedDirectory")
    return f"""
$ErrorActionPreference = "Stop"
$Target = "operator@100.64.0.1"
$Action = "Status"
$SiteRoot = "C:\\Ruisheng\\candidates\\missing-transport-test"
$OperationId = "c5a62e0e-98d9-4a9a-8150-c3c8abad719b"
$Reason = ""
$LeaseSeconds = 900
$Approved = $false
{bootstrap}
{convert}
{exact_keys}
{transport}
{updater}
$updaterSource = (Get-Content -LiteralPath {_ps_literal(UPDATER)} -Raw -Encoding UTF8) + "`n# stdin-编码"
$result = Invoke-Updater -UpdaterSource $updaterSource -RemoteCandidateRoot ""
$result | ConvertTo-Json -Depth 10 -Compress
"""


def _run_status(
    executable: str,
    stub_dir: Path,
    tmp_path: Path,
    mode: str = "execute",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    log_path = tmp_path / f"ssh-{mode}.log"
    completed = subprocess.run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _status_harness(),
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_stub_environment(stub_dir, executable, log_path, mode),
    )
    return completed, log_path


def _apply_prepare_harness() -> str:
    source = _read(CONTROLLER)
    bootstrap = next(
        line for line in source.splitlines() if line.startswith("$script:RemotePowerShellBootstrap")
    )
    convert = _function(source, "ConvertTo-PowerShellUtf8Expression", "Get-Sha256Text")
    transport = _function(source, "Invoke-SshScript", "Invoke-Updater")
    start = source.index('    $prepare = @"')
    end = source.index("    if ($ResumeUpload) {", start)
    prepare = source[start:end]
    return f"""
$ErrorActionPreference = "Stop"
$Target = "operator@100.64.0.1"
$incomingOperationRoot = "C:\\Ruisheng\\incoming\\c5a62e0e-98d9-4a9a-8150-c3c8abad719b"
$remoteCandidateRoot = "$incomingOperationRoot\\candidate-a"
$ResumeUpload = $false
{bootstrap}
{convert}
{transport}
{prepare}
"""


def _run_apply_prepare(
    executable: str,
    stub_dir: Path,
    tmp_path: Path,
    mode: str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    log_path = tmp_path / f"ssh-prepare-{mode}.log"
    completed = subprocess.run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _apply_prepare_harness(),
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_stub_environment(stub_dir, executable, log_path, mode),
    )
    return completed, log_path


def _unicode_transport_harness() -> str:
    source = _read(CONTROLLER)
    bootstrap = next(
        line for line in source.splitlines() if line.startswith("$script:RemotePowerShellBootstrap")
    )
    transport = _function(source, "Invoke-SshScript", "Invoke-Updater")
    return f"""
$ErrorActionPreference = "Stop"
$Target = "operator@100.64.0.1"
{bootstrap}
{transport}
$before = [Console]::OutputEncoding.CodePage
$result = Invoke-SshScript -Script '"ignored"'
$expected = [Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String("5Lyg6L6T5q2j5bi4")
)
if ($result -cne $expected) {{
  throw "unicode_transport_corrupted"
}}
if ([Console]::OutputEncoding.CodePage -ne $before) {{ throw "console_encoding_not_restored" }}
"ok"
"""


def _binding_harness() -> str:
    source = _read(CONTROLLER)
    bootstrap = next(
        line for line in source.splitlines() if line.startswith("$script:RemotePowerShellBootstrap")
    )
    convert = _function(source, "ConvertTo-PowerShellUtf8Expression", "Get-Sha256Text")
    exact_keys = _function(source, "Test-ExactKeys", "Get-CandidateMetadata")
    transport = _function(source, "Invoke-SshScript", "Invoke-Updater")
    updater = _function(source, "Invoke-Updater", "Set-RestrictedDirectory")
    return f"""
$ErrorActionPreference = "Stop"
$Target = "operator@100.64.0.1"
$Action = "Apply"
$SiteRoot = "C:\\Ruisheng\\candidates\\site-current"
$OperationId = "76fdbb4f-eaf0-4483-9300-e632bd5eb472"
$Reason = "批准升级测试"
$LeaseSeconds = 321
$Approved = $true
{bootstrap}
{convert}
{exact_keys}
{transport}
{updater}
$bindingUpdaterSource = @'
[CmdletBinding()]
param(
  [string]$Action, [string]$CandidateRoot, [string]$SiteRoot,
  [string]$OperationId, [string]$Reason, [string]$ExpectedCandidateId,
  [string]$ExpectedLogicalIdentity, [string]$ExpectedSourceCommit,
  [string]$ExpectedAlembicHead, [string]$ExpectedPlatform,
  [long]$PackageBytes, [int]$LeaseSeconds, [switch]$Approved
)
$candidate = [ordered]@{{
  candidate_root = $CandidateRoot; site_root = $SiteRoot; reason = $Reason
  candidate_id = $ExpectedCandidateId; logical_identity = $ExpectedLogicalIdentity
  source_commit = $ExpectedSourceCommit; alembic_head = $ExpectedAlembicHead
  platform = $ExpectedPlatform; package_bytes = $PackageBytes
  lease_seconds = $LeaseSeconds; approved = [bool]$Approved
}}
[ordered]@{{
  schema_version = 1; ok = $true; status = "committed"; action = $Action
  operation_id = $OperationId; error_code = ""; active_release = $null
  candidate = $candidate; locks = $null; backup = $null
}} | ConvertTo-Json -Depth 10 -Compress
'@
$metadata = [pscustomobject]@{{
  candidate_id = "candidate-a"; logical_identity = "sha256:$('a' * 64)"
  source_commit = "$('b' * 40)"; alembic_head = "0012_alarm_notification_runtime"
  platform = "linux/amd64"; package_bytes = 123456
}}
$result = Invoke-Updater -UpdaterSource $bindingUpdaterSource `
  -RemoteCandidateRoot "C:\\Ruisheng\\incoming\\candidate-a" -Metadata $metadata
$expected = @{{
  candidate_root = "C:\\Ruisheng\\incoming\\candidate-a"
  site_root = $SiteRoot; reason = $Reason; candidate_id = $metadata.candidate_id
  logical_identity = $metadata.logical_identity; source_commit = $metadata.source_commit
  alembic_head = $metadata.alembic_head; platform = $metadata.platform
  package_bytes = [long]$metadata.package_bytes; lease_seconds = $LeaseSeconds
  approved = $true
}}
foreach ($key in $expected.Keys) {{
  if ([string]$result.candidate.$key -cne [string]$expected[$key]) {{
    throw "binding_mismatch_$key"
  }}
}}
"ok"
"""


def test_controller_has_closed_approved_transport_workflow() -> None:
    script = _read(CONTROLLER)

    assert '[ValidateSet("Status", "Plan", "Initialize", "Apply", "Recover")]' in script
    assert "[Parameter(Mandatory)][string]$SiteRoot" in script
    assert 'if ($Action -in @("Initialize", "Apply", "Recover") -and -not $Approved)' in script
    assert 'if ($Action -eq "Plan" -and -not $DryRun)' in script
    assert '"-o", "BatchMode=yes"' in script
    assert '"-o", "StrictHostKeyChecking=yes"' in script
    assert '"-T", "-F", "NUL"' in script
    assert '"-r",\n        "-F", "NUL"' in script
    assert '"-b", "-",\n    "-F", "NUL"' in script
    assert "if (-not [Threading.Tasks.Task]::WaitAll" in script
    expected_bootstrap = (
        "$script:RemotePowerShellBootstrap = '" + REMOTE_BOOTSTRAP.replace("'", "''") + "'"
    )
    assert expected_bootstrap in script
    assert '"-OutputFormat", "Text", "-EncodedCommand", $encodedBootstrap' in script
    assert '"-Command", "-"' not in script
    assert "[Console]::In.ReadToEnd()" not in script
    assert "Invoke-Expression" not in script
    assert "target-updater.ps1" in script
    assert "scp.exe" in script
    assert "[switch]$ResumeUpload" in script
    assert "ResumeUpload is only valid for Apply." in script
    assert "sftp.exe" in script
    assert '"-reput $(ConvertTo-SftpPath $file.FullName)' in script
    assert '"ServerAliveInterval=15"' in script
    assert '"ServerAliveCountMax=3"' in script
    assert "$uploadState = Invoke-SshScript -Script $completionProbe" in script
    assert "$placeholderResult = Invoke-SshScript -Script $placeholderPreparation" in script
    assert "candidate_upload_relative_path_invalid" in script
    assert "candidate_upload_path_escape" in script
    assert "candidate_upload_file_invalid" in script
    assert "`$actual.Count -ne `$expectedNames.Count" in script
    assert "[long]`$property.Value -ne [long]`$file.Length" in script
    assert "incoming_operation_resume_invalid" in script
    assert 'if ($Action -eq "Plan")' in script
    plan = script.split('if ($Action -eq "Plan")', 1)[1].split('if ($Action -eq "Apply")', 1)[0]
    assert "scp.exe" not in plan
    initialize_start = script.index('if ($Action -eq "Initialize") {\n  $result = $null')
    initialize_end = script.index(
        '\n$result = $null\n$transportError = ""\ntry {', initialize_start
    )
    initialize = script[initialize_start:initialize_end]
    assert "CurrentCandidateRoot is required for Initialize" in script
    assert "scp.exe" not in initialize
    assert "Remove-Item Env:" not in script
    assert "MANAGEMENT_TOKEN" not in script


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_native_ssh_executes_the_complete_updater_with_fixed_bootstrap(
    executable: str,
    native_ssh_stub: Path,
    tmp_path: Path,
) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    completed, log_path = _run_status(resolved, native_ssh_stub, tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    output = completed.stdout.strip()
    assert "#< CLIXML" not in output
    assert "PS C:\\>" not in output
    result, end = json.JSONDecoder().raw_decode(output)
    assert output[end:].strip() == ""
    assert set(result) == RESULT_KEYS
    assert result == {
        "schema_version": 1,
        "ok": False,
        "status": "rejected",
        "action": "Status",
        "operation_id": "c5a62e0e-98d9-4a9a-8150-c3c8abad719b",
        "error_code": "restricted_directory_missing",
        "active_release": None,
        "candidate": None,
        "locks": None,
        "backup": None,
    }
    calls = _read_stub_calls(log_path)
    assert len(calls) == 1
    _assert_transport_call(calls[0])
    encoded_payload = calls[0]["stdin"]
    assert isinstance(encoded_payload, str)
    assert "\n" not in encoded_payload.strip()
    payload = _decode_transport_payload(calls[0])
    updater = _read(UPDATER)
    assert len(payload.encode("utf-8")) > 60_000
    assert payload.count(updater) == 1
    assert "# stdin-编码" in payload
    assert "CandidateRoot = [Text.Encoding]::UTF8.GetString(" in payload
    assert "& $updater @parameters" in payload


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_native_ssh_executes_apply_prepare_and_requires_exact_output(
    executable: str,
    native_ssh_stub: Path,
    tmp_path: Path,
) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    completed, log_path = _run_apply_prepare(
        resolved,
        native_ssh_stub,
        tmp_path,
        "apply",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    calls = _read_stub_calls(log_path)
    assert len(calls) == 1
    _assert_transport_call(calls[0])
    payload = _decode_transport_payload(calls[0])
    assert payload.count('$prepareState = "prepared"') == 1
    assert payload.count("[Console]::Out.Write($prepareState)") == 1
    assert "Set-Acl -LiteralPath $path -AclObject $acl" in payload
    assert 'Join-Path $candidatePath "images"' in payload
    assert _read(UPDATER) not in payload


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_native_transport_preserves_unicode_output_and_restores_encoding(
    executable: str,
    native_ssh_stub: Path,
    tmp_path: Path,
) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    log_path = tmp_path / f"ssh-unicode-{executable}.log"
    completed = subprocess.run(
        [
            resolved,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _unicode_transport_harness(),
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_stub_environment(native_ssh_stub, resolved, log_path, "unicode"),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "ok"
    assert completed.stderr == ""
    calls = _read_stub_calls(log_path)
    assert len(calls) == 1
    _assert_transport_call(calls[0])


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_native_transport_binds_nonempty_upgrade_parameters(
    executable: str,
    native_ssh_stub: Path,
    tmp_path: Path,
) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    log_path = tmp_path / f"ssh-binding-{executable}.log"
    completed = subprocess.run(
        [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _binding_harness()],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_stub_environment(native_ssh_stub, resolved, log_path, "execute"),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "ok"
    assert completed.stderr == ""
    calls = _read_stub_calls(log_path)
    assert len(calls) == 1
    _assert_transport_call(calls[0])
    args = calls[0]["args"]
    assert isinstance(args, list)
    assert not any("candidate-a" in value or "批准升级测试" in value for value in args)


@pytest.mark.parametrize("mode", ["empty", "apply-extra", "apply-whitespace", "failed"])
def test_apply_prepare_output_and_execution_fail_closed(
    mode: str,
    native_ssh_stub: Path,
    tmp_path: Path,
) -> None:
    completed, log_path = _run_apply_prepare(
        _powershell(),
        native_ssh_stub,
        tmp_path,
        mode,
    )

    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    if mode == "failed":
        assert "transport failed with exit code 23" in combined
    else:
        assert "Remote upgrade preparation returned invalid data." in combined
    calls = _read_stub_calls(log_path)
    assert len(calls) == 1
    _assert_transport_call(calls[0])


@pytest.mark.parametrize(
    "mode, expected_error",
    [
        ("empty", "returned no data"),
        ("invalid", "invalid or non-allowlisted data"),
        ("clixml", "invalid or non-allowlisted data"),
        ("prompt", "invalid or non-allowlisted data"),
        ("execute-extra", "invalid or non-allowlisted data"),
        ("bad-base64", "transport failed with exit code"),
        ("truncate", "transport failed with exit code"),
        ("failed", "transport failed with exit code 23"),
    ],
)
def test_updater_transport_anomalies_fail_closed(
    mode: str,
    expected_error: str,
    native_ssh_stub: Path,
    tmp_path: Path,
) -> None:
    completed, log_path = _run_status(
        _powershell(),
        native_ssh_stub,
        tmp_path,
        mode,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stdout + completed.stderr
    calls = _read_stub_calls(log_path)
    assert len(calls) == 1
    _assert_transport_call(calls[0])


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("action, expected_status", [("Status", "observed"), ("Plan", "planned")])
def test_read_only_dispatch_accepts_an_empty_remote_candidate_root(
    executable: str,
    action: str,
    expected_status: str,
    native_ssh_stub: Path,
    tmp_path: Path,
) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    operation_id = "c5a62e0e-98d9-4a9a-8150-c3c8abad719b"
    candidate = tmp_path / "candidate-a"
    candidate.mkdir()
    manifest = {
        "schema_version": 2,
        "candidate_id": candidate.name,
        "source_commit": "a" * 40,
        "generated_at": "2026-09-02T00:00:00+00:00",
        "target_os": "linux",
        "target_architecture": "amd64",
        "alembic_head": "0012_alarm_notification_runtime",
        "logical_identity": f"sha256:{'b' * 64}",
        "tools": {},
        "authenticity": {},
        "images": [
            {"component": component} for component in ("postgres", "redis", "api", "gw", "web")
        ],
    }
    (candidate / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    (candidate / "SHA256SUMS").write_text("", encoding="ascii")
    (candidate / "SHA256SUMS.sig").write_text(
        "-----BEGIN SSH SIGNATURE-----\n-----END SSH SIGNATURE-----\n", encoding="ascii"
    )
    response = json.dumps(
        {
            "schema_version": 1,
            "ok": True,
            "status": expected_status,
            "action": action,
            "operation_id": operation_id,
            "error_code": "",
            "active_release": None,
            "candidate": None,
            "locks": None,
            "backup": None,
        },
        separators=(",", ":"),
    )
    arguments = [
        resolved,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(CONTROLLER),
        "-Action",
        action,
        "-Target",
        "operator@100.64.0.1",
        "-SiteRoot",
        r"C:\Ruisheng\candidates\site-current",
        "-OperationId",
        operation_id,
    ]
    if action == "Plan":
        arguments.extend(["-CandidatePath", str(candidate), "-DryRun"])
    log_path = tmp_path / f"ssh-read-only-{action}-{executable}.log"
    completed = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_stub_environment(
            native_ssh_stub,
            resolved,
            log_path,
            f"response-{expected_status}",
        ),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == json.loads(response)
    calls = _read_stub_calls(log_path)
    assert len(calls) == 1
    _assert_transport_call(calls[0])
    payload = _decode_transport_payload(calls[0])
    assert (
        "CandidateRoot = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(''))"
        in payload
    )


def test_target_updater_enforces_supply_chain_schema_and_boundary_gates() -> None:
    script = _read(UPDATER)

    assert "C:\\ProgramData\\Ruisheng\\bin\\verify-publisher.ps1" in script
    assert "if ($publisherExitCode -ne 2)" in script
    assert '"[publisher] VERIFIED:"' in script
    assert '"B-04 remains BLOCKED"' in script
    assert "Assert-CandidateManifest" in script
    assert "Assert-ProtectedVerifierFile -Path $VerifierPath" in script
    assert 'throw "schema_head_changed"' in script
    assert "Assert-NetworkBoundary" in script
    assert 'pull_policy -ne "never"' in script
    assert 'host_ip -notin @("127.0.0.1", "::1")' in script
    assert 'dockerPlatform -eq "linux/x86_64"' in script
    assert '"-m", "ruisheng_gw.healthcheck"' in script
    assert "SSH_CONNECTION" in script
    assert "-T -C $connectionContext" in script


def test_publisher_verifier_keeps_the_admin_system_only_trust_boundary() -> None:
    script = _read(UPDATER)
    verifier_acl = _function(
        script,
        "Assert-ProtectedVerifierFile",
        "Set-RestrictedFileAcl",
    )

    assert '"S-1-5-18" = $false' in verifier_acl
    assert '"S-1-5-32-544" = $false' in verifier_acl
    assert "AreAccessRulesProtected" in verifier_acl
    assert "WindowsIdentity]::GetCurrent" not in verifier_acl
    assert "Get-AllowedSids" not in verifier_acl
    assert 'throw "publisher_verifier_acl_invalid"' in verifier_acl


def test_rejection_audit_accepts_empty_identity_and_preserves_primary_error() -> None:
    script = _read(UPDATER)

    assert "[Parameter(Mandatory)][AllowEmptyString()][string]$CandidateIdentity" in script
    assert "$auditCandidateIdentity = $ExpectedLogicalIdentity" in script
    assert "$auditCandidateIdentity = [string]$manifest.logical_identity" in script
    assert (
        'try { Write-Audit "upgrade_rejected" $finalStatus $auditCandidateIdentity $errorCode }'
    ) in script
    assert '$errorCode = "rejection_audit_failed"' not in script


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_rejection_audit_writes_an_empty_candidate_identity(
    executable: str, tmp_path: Path
) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    source = _read(UPDATER)
    get_hash = _function(source, "Get-Sha256Text", "Assert-AbsoluteRemotePath")
    write_audit = _function(source, "Write-Audit", "Assert-NetworkBoundary")
    audit_path = tmp_path / f"rejection-audit-{executable}.jsonl"
    lock_path = tmp_path / f"rejection-audit-{executable}.lock"
    lock_path.write_bytes(b"")
    invocation = f"""
$ErrorActionPreference = 'Stop'
$AuditPath = {_ps_literal(audit_path)}
$AuditLockPath = {_ps_literal(lock_path)}
$OperationId = '32712215-01fb-4bd1-bbfd-299ac211ef88'
$Reason = 'approved test reason'
{get_hash}
{write_audit}
Write-Audit 'upgrade_rejected' 'rejected' '' 'publisher_verification_failed'
Write-Audit 'upgrade_rejected_again' 'rejected' '' 'network_boundary_failed'
"""
    completed = subprocess.run(
        [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["candidate_identity"] == ""
    assert records[0]["error_code"] == "publisher_verification_failed"
    assert records[1]["previous_hash"] == records[0]["record_hash"]
    assert records[1]["error_code"] == "network_boundary_failed"


def test_target_updater_uses_shared_locks_journal_backup_and_recovery() -> None:
    script = _read(UPDATER)

    shared = 'Acquire-LeasedLock -Path $SharedLockPath -Name "shared-maintenance"'
    legacy = 'Acquire-LeasedLock -Path $LegacyLockPath -Name "legacy-hotfix"'
    mutation = "Set-ReleaseEnvironment"
    assert script.index(shared) < script.index(legacy) < script.rindex(mutation)
    assert "process_started_at" in script
    assert "Renew-Locks" in script
    assert "docker_command_timeout" in script
    assert "Release-Locks" in script
    assert "Write-JsonAtomic -Path $JournalPath" in script
    assert 'status = "uncertain"' in script
    assert 'status = "switching"' in script
    assert "commit_audit_incomplete" in script
    assert "pg_dump" in script
    assert "pg_dumpall" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "Restore-PreviousRelease" in script
    assert 'status = "recovery_failed"' in script
    assert 'status = "committed"' in script
    assert "candidate_transport_identity_drift" in script
    assert "docker compose" not in script.lower()
    assert '"down"' not in script
    assert "volume rm" not in script.lower()


def test_prospective_environment_changes_only_six_fields_byte_exactly(
    tmp_path: Path,
) -> None:
    source = _read(UPDATER)
    function = source.split("# BEGIN environment switch", 1)[1].split(
        "# END environment switch", 1
    )[0]
    env_path = tmp_path / ".env.prod"
    prospective_path = tmp_path / ".prospective.env"
    original = (
        b"\xef\xbb\xbfSECRET=keep-exact\r\n"
        b"TARGET_PLATFORM=linux/amd64\r\n"
        b"POSTGRES_IMAGE=old/postgres\r\n"
        b"REDIS_IMAGE=old/redis\r\n"
        b"API_IMAGE=old/api\r\n"
        b"GW_IMAGE=old/gw\r\n"
        b"WEB_IMAGE=old/web\r\n"
        b"NETWORK=keep-too\r\n"
    )
    env_path.write_bytes(original)
    replacements = {
        "TARGET_PLATFORM": "linux/amd64",
        "POSTGRES_IMAGE": "new/postgres:immutable",
        "REDIS_IMAGE": "new/redis:immutable",
        "API_IMAGE": "new/api:immutable",
        "GW_IMAGE": "new/gw:immutable",
        "WEB_IMAGE": "new/web:immutable",
    }
    invocation = f"""
$ErrorActionPreference = 'Stop'
function Set-RestrictedFileAcl {{ param([string]$Path) }}
function Assert-RestrictedFile {{ param([string]$Path) }}
{function}
$values = ConvertFrom-Json {_ps_literal(json.dumps(replacements))}
$map = @{{}}
foreach ($property in $values.PSObject.Properties) {{ $map[$property.Name] = $property.Value }}
Write-ProspectiveEnvironment -SourcePath {_ps_literal(env_path)} `
  -DestinationPath {_ps_literal(prospective_path)} -Values $map
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    expected = original
    for key, value in replacements.items():
        expected = re.sub(
            rb"(?m)^" + re.escape(key.encode()) + rb"=[^\r\n]*(?=\r?$)",
            f"{key}={value}".encode(),
            expected,
        )
    assert env_path.read_bytes() == original
    assert prospective_path.read_bytes() == expected


def test_switching_journal_contains_verified_backup_before_environment_mutation(
    tmp_path: Path,
) -> None:
    source = _read(UPDATER)
    function = source.split("# BEGIN environment switch", 1)[1].split(
        "# END environment switch", 1
    )[0]
    env_path = tmp_path / ".env.prod"
    backup_path = tmp_path / ".env.prod.before"
    journal_path = tmp_path / "journal.json"
    original = b"SECRET=keep\r\nTARGET_PLATFORM=linux/amd64\r\n"
    env_path.write_bytes(original)
    invocation = f"""
$ErrorActionPreference = 'Stop'
function Set-RestrictedFileAcl {{ param([string]$Path) }}
function Get-FileHash {{
  param([string]$Algorithm, [string]$Path)
  $bytes = [IO.File]::ReadAllBytes($Path)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  [pscustomobject]@{{ Hash = ([BitConverter]::ToString($hash).Replace('-', '')) }}
}}
function Write-JsonAtomic {{
  param([string]$Path, $Value)
  [IO.File]::WriteAllText(
    $Path, ($Value | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false))
  )
}}
{function}
$journal = [ordered]@{{ environment_backup = $null; status = 'preflighted' }}
Prepare-EnvironmentSwitch -Journal $journal -JournalFile {_ps_literal(journal_path)} `
  -SourcePath {_ps_literal(env_path)} -BackupPath {_ps_literal(backup_path)}
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["status"] == "switching"
    assert Path(journal["environment_backup"]["path"]) == backup_path
    assert journal["environment_backup"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert backup_path.read_bytes() == original
    assert env_path.read_bytes() == original

    prepare = source.index("Prepare-EnvironmentSwitch -Journal $journal")
    switch = source.index("Set-ReleaseEnvironment -Path $EnvFile", prepare)
    assert prepare < switch


def test_initialize_is_explicit_verified_and_non_disruptive() -> None:
    script = _read(UPDATER)
    start = script.index('  if ($Action -eq "Initialize") {\n    Assert-EntitlementFeature')
    end = script.index("\n  if (Test-Path -LiteralPath $JournalPath", start)
    initialize = script[start:end]
    complete_start = script.index("function Complete-ActiveReleaseInitialization")
    complete_end = script.index("\nfunction New-SafeResult", complete_start)
    complete = script[complete_start:complete_end]
    pointer_commit = initialize.index("Complete-ActiveReleaseInitialization")
    assert initialize.index('Assert-EntitlementFeature "software-updates"') < initialize.index(
        "Assert-CandidateManifest"
    )

    for gate in (
        "Assert-CandidateManifest",
        "Get-EnvironmentReleaseValues",
        "Invoke-PublisherVerification",
        "Get-DockerPlatform",
        "Get-DatabaseHead",
        "Assert-NetworkBoundary",
        "Assert-ComposeManifestImages",
        "Assert-CurrentContainerIdentity",
        "Assert-LocksOwned",
    ):
        assert initialize.index(gate) < pointer_commit
    assert "Write-JsonAtomic -Path $ActiveReleasePath" in complete
    assert complete.index("Write-JsonAtomic -Path $ActiveReleasePath") < complete.index(
        'Write-Audit "active_release_initialized"'
    )
    assert "candidate_root = $CandidateRoot" in initialize
    assert "site_root = $SiteRoot" in initialize
    assert "{{.Config.Image}}|{{.Image}}" in script
    assert ".candidate_reference" in script
    assert ".image_id" in script
    assert '"up"' not in initialize
    assert '"down"' not in initialize
    assert "scp.exe" not in initialize
    assert script.count("active_release_already_initialized") == 1

    journal_gate = script.index("if (Test-Path -LiteralPath $JournalPath", end)
    recover = script.index('if ($Action -ne "Recover")', journal_gate)
    apply_guard = script.index('Assert-EntitlementFeature "software-updates"', end)
    apply_mutation = script.index("Prepare-EnvironmentSwitch", apply_guard)
    assert recover < apply_guard < apply_mutation
    assert script.index('if ($Action -ne "Apply")', recover) < apply_guard
    assert script.count("active_release_initialization_race") == 1
    assert "active_release_already_initialized" in complete


def test_initialize_audit_failure_is_replayable_only_for_exact_pointer(
    tmp_path: Path,
) -> None:
    source = _read(UPDATER)
    function = (
        "function Complete-ActiveReleaseInitialization"
        + source.split("function Complete-ActiveReleaseInitialization", 1)[1].split(
            "function New-SafeResult", 1
        )[0]
    )
    pointer_path = tmp_path / "active-release.json"
    audit_path = tmp_path / "audit.txt"
    invocation = f"""
$ErrorActionPreference = 'Stop'
$ActiveReleasePath = {_ps_literal(pointer_path)}
$script:FailAudit = $true
function Write-JsonAtomic {{
  param([string]$Path, $Value)
  [IO.File]::WriteAllText(
    $Path, ($Value | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false))
  )
}}
function Write-Audit {{
  param([string]$Event, [string]$Result, [string]$CandidateIdentity)
  if ($script:FailAudit) {{ throw 'injected_audit_failure' }}
  [IO.File]::AppendAllText({_ps_literal(audit_path)}, "$Event|$Result|$CandidateIdentity`n")
}}
{function}
$pointer = [ordered]@{{
  schema_version = 1; candidate_id = 'candidate-a'
  logical_identity = ('sha256:' + ('a' * 64)); source_commit = ('b' * 40)
  candidate_root = 'C:\\Ruisheng\\candidates\\candidate-a'
  site_root = 'C:\\Ruisheng\\candidates\\site-a'
  committed_at = '2026-09-01T00:00:00Z'
  operation_id = '11111111-1111-4111-8111-111111111111'
}}
try {{
  Complete-ActiveReleaseInitialization -Pointer $pointer -Existing $null `
    -CandidateIdentity $pointer.logical_identity
  exit 2
}}
catch {{ if ($_.Exception.Message -cne 'injected_audit_failure') {{ throw }} }}
if (-not (Test-Path -LiteralPath $ActiveReleasePath -PathType Leaf)) {{ exit 3 }}
$existing = Get-Content -LiteralPath $ActiveReleasePath -Raw -Encoding UTF8 | ConvertFrom-Json
$script:FailAudit = $false
$result = Complete-ActiveReleaseInitialization -Pointer $pointer -Existing $existing `
  -CandidateIdentity $pointer.logical_identity
if ($result.candidate_id -cne 'candidate-a') {{ exit 4 }}
$different = [ordered]@{{}} + $pointer
$different.candidate_id = 'candidate-b'
try {{
  Complete-ActiveReleaseInitialization -Pointer $different -Existing $existing `
    -CandidateIdentity $different.logical_identity
  exit 5
}}
catch {{ if ($_.Exception.Message -cne 'active_release_already_initialized') {{ throw }} }}
exit 0
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["candidate_id"] == "candidate-a"
    assert audit_path.read_text(encoding="utf-8").count("active_release_initialized") == 1


def test_apply_commit_audit_failure_replays_to_committed(tmp_path: Path) -> None:
    source = _read(UPDATER)
    function = _function(source, "Complete-UpgradeCommit", "Complete-UpgradeRollback")
    pointer_path = tmp_path / "active.json"
    journal_path = tmp_path / "journal.json"
    invocation = f"""
$ErrorActionPreference = 'Stop'
$ActiveReleasePath = {_ps_literal(pointer_path)}
$JournalPath = {_ps_literal(journal_path)}
$script:fail = $true
function Write-JsonAtomic {{ param([string]$Path,$Value); $Value | ConvertTo-Json -Depth 10 | Set-Content $Path }}
function Write-Audit {{ if ($script:fail) {{ throw 'audit_failed' }} }}
{function}
$journal = [ordered]@{{ status='switched'; error_code=''; candidate=[ordered]@{{logical_identity='sha256:x'}} }}
$pointer = [ordered]@{{ candidate_id='a'; logical_identity='sha256:x' }}
if (Complete-UpgradeCommit $journal $pointer 'sha256:x') {{ exit 2 }}
if ($journal.status -cne 'uncertain' -or $journal.error_code -cne 'commit_audit_incomplete') {{ exit 3 }}
if (-not (Test-Path $ActiveReleasePath)) {{ exit 4 }}
$script:fail = $false
if (-not (Complete-UpgradeCommit $journal $null 'sha256:x')) {{ exit 5 }}
if ($journal.status -cne 'committed' -or $journal.error_code -cne '') {{ exit 6 }}
"""
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_candidate_journal_identity_conflict_executes() -> None:
    source = _read(UPDATER)
    function = _function(source, "Assert-JournalCandidateIdentity", "Complete-UpgradeCommit")
    invocation = f"""
$ExpectedCandidateId='a'; $ExpectedLogicalIdentity='sha256:a'; $ExpectedSourceCommit='source-a'
$ExpectedAlembicHead='head-a'; $ExpectedPlatform='linux/amd64'
{function}
$journal=[pscustomobject]@{{candidate=[pscustomobject]@{{candidate_id='b';logical_identity='sha256:a';source_commit='source-a';alembic_head='head-a';platform='linux/amd64'}}}}
try {{ Assert-JournalCandidateIdentity $journal; throw 'unexpected_success' }}
catch {{ if ($_.Exception.Message -notlike '*upgrade_candidate_identity_conflict*') {{ throw }} }}
exit 0
"""
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_process_cleanup_guards_start_failure_and_kills_started_process() -> None:
    source = _read(UPDATER)
    function = _function(source, "Stop-StartedProcess", "Invoke-DockerText")
    invocation = f"""
{function}
$notStarted = New-Object psobject
$notStarted | Add-Member ScriptProperty HasExited {{ throw 'must_not_read' }}
Stop-StartedProcess $notStarted $false
$script:killed=$false
$started = [pscustomobject]@{{HasExited=$false}}
$started | Add-Member ScriptMethod Kill {{ $script:killed=$true }}
$started | Add-Member ScriptMethod WaitForExit {{ param($ms); return $true }}
Stop-StartedProcess $started $true
if (-not $script:killed) {{ exit 2 }}
"""
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert source.count("Stop-StartedProcess -Process $process -Started $started") == 2


def test_database_backup_estimate_executes_and_rejects_invalid_size() -> None:
    source = _read(UPDATER)
    function = _function(source, "Get-DatabaseBackupEstimate", "Get-DockerPlatform")
    invocation = f"""
{function}
$script:databaseSize='10737418240'
function Invoke-DockerText {{ return $script:databaseSize }}
$estimate=Get-DatabaseBackupEstimate
if ($estimate.database_bytes -ne 10737418240 -or $estimate.required_bytes -le $estimate.database_bytes) {{ exit 2 }}
$script:databaseSize='invalid'
try {{ Get-DatabaseBackupEstimate; throw 'unexpected_success' }} catch {{ if ($_.Exception.Message -notlike '*database_size_invalid*') {{ throw }} }}
exit 0
"""
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_incoming_cleanup_and_stable_candidate_removal_are_exact(tmp_path: Path) -> None:
    source = _read(UPDATER)
    incoming = tmp_path / "incoming" / "op"
    exact = incoming / "candidate"
    nested = exact / "nested"
    stable = tmp_path / "stable"
    candidate = stable / "candidate-a"
    active_path = tmp_path / "active.json"
    candidate.mkdir(parents=True)
    functions = _function(
        source, "Remove-UncommittedCandidate", "Test-SafeIncomingCandidateCleanup"
    ) + _function(source, "Test-SafeIncomingCandidateCleanup", "Assert-JournalCandidateIdentity")
    invocation = f"""
$IncomingOperationRoot={_ps_literal(incoming)}; $StableCandidatesRoot={_ps_literal(stable)}
$ActiveReleasePath={_ps_literal(active_path)}
function Read-ActiveRelease {{ Get-Content $ActiveReleasePath -Raw | ConvertFrom-Json }}
{functions}
if (-not (Test-SafeIncomingCandidateCleanup {_ps_literal(exact)})) {{ exit 2 }}
if (Test-SafeIncomingCandidateCleanup {_ps_literal(nested)}) {{ exit 3 }}
$journal=[pscustomobject]@{{switched=$false;status='rejected';candidate=[pscustomobject]@{{candidate_root={_ps_literal(candidate)};candidate_id='candidate-a'}}}}
Remove-UncommittedCandidate $journal
if (Test-Path {_ps_literal(candidate)}) {{ exit 4 }}
New-Item -ItemType Directory {_ps_literal(candidate)} | Out-Null
@{{candidate_root={_ps_literal(candidate)}}} | ConvertTo-Json | Set-Content $ActiveReleasePath
try {{ Remove-UncommittedCandidate $journal; throw 'unexpected_success' }} catch {{ if ($_.Exception.Message -notlike '*uncommitted_candidate_is_active*') {{ throw }} }}
$journal.candidate.candidate_root={_ps_literal(stable / "other" / "candidate-a")}
try {{ Remove-UncommittedCandidate $journal; throw 'unexpected_success' }} catch {{ if ($_.Exception.Message -notlike '*uncommitted_candidate_path_invalid*') {{ throw }} }}
exit 0
"""
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (
        'elseif ($Action -eq "Apply" -and (Test-SafeIncomingCandidateCleanup $CandidateRoot))'
        in source
    )


@pytest.mark.parametrize(
    "restore_fails,expected", [(False, "rolled_back"), (True, "recovery_failed")]
)
def test_environment_switch_failure_rollback_terminal_state(
    tmp_path: Path, restore_fails: bool, expected: str
) -> None:
    source = _read(UPDATER)
    function = _function(source, "Complete-UpgradeRollback", "Complete-ActiveReleaseInitialization")
    journal_path = tmp_path / "journal.json"
    invocation = f"""
$JournalPath={_ps_literal(journal_path)}; $script:locked=$false
function Assert-LocksOwned {{ $script:locked=$true }}
function Restore-PreviousRelease {{ if ({"$true" if restore_fails else "$false"}) {{ throw 'restore_failed' }} }}
function Write-Audit {{ }}
function Write-JsonAtomic {{ param([string]$Path,$Value); $Value | ConvertTo-Json -Depth 10 | Set-Content $Path }}
{function}
$journal=[ordered]@{{status='switching';error_code=''}}
$result=Complete-UpgradeRollback $journal 'sha256:x' 'environment_switch_failed'
if (-not $script:locked -or $result.status -cne '{expected}') {{ exit 2 }}
"""
    completed = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    mutation = source.index("Set-ReleaseEnvironment -Path $EnvFile")
    rollback = source.index("Complete-UpgradeRollback -Journal $journal", mutation)
    assert mutation < rollback


def test_active_pointer_is_reread_after_both_locks_are_acquired() -> None:
    script = _read(UPDATER)
    shared = script.index('Acquire-LeasedLock -Path $SharedLockPath -Name "shared-maintenance"')
    legacy = script.index('Acquire-LeasedLock -Path $LegacyLockPath -Name "legacy-hotfix"', shared)
    reread = script.index("$lockedActive = Read-ActiveRelease", legacy)
    compare = script.index("Assert-ActiveReleaseUnchanged", reread)
    first_mutation = script.index("Set-ReleaseEnvironment -Path $EnvFile", compare)

    assert shared < legacy < reread < compare < first_mutation


@pytest.mark.parametrize(
    "service_override,expected_error",
    [
        ({"network_mode": "host"}, "network_boundary_network_mode_invalid"),
        (
            {"ports": [{"published": 0, "host_ip": "127.0.0.1"}]},
            "network_boundary_published_port_invalid",
        ),
        ({"ports": [{"published": 8080}]}, "network_boundary_non_loopback_port"),
    ],
)
def test_network_gate_executes_fail_closed_for_implicit_bindings(
    service_override: dict[str, object], expected_error: str
) -> None:
    source = _read(UPDATER)
    function = (
        "function Assert-NetworkBoundary"
        + source.split("function Assert-NetworkBoundary", 1)[1].split(
            "function Assert-ComposeManifestImages", 1
        )[0]
    )
    services = {}
    for name in ("postgres", "redis", "migrate", "gw", "api", "web"):
        services[name] = {
            "image": f"candidate/{name}:immutable",
            "pull_policy": "never",
            "ports": [],
        }
    services["web"].update(service_override)
    invocation = f"""
$ErrorActionPreference = 'Stop'
$PolicyServices = @('postgres','redis','migrate','gw','api','web')
{function}
$model = ConvertFrom-Json {_ps_literal(json.dumps({"services": services}))}
try {{ Assert-NetworkBoundary $model; exit 2 }} catch {{
  if ($_.Exception.Message -cne {_ps_literal(expected_error)}) {{ throw }}
}}
exit 0
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_network_gate_accepts_null_or_absent_ports_as_unpublished() -> None:
    source = _read(UPDATER)
    function = (
        "function Assert-NetworkBoundary"
        + source.split("function Assert-NetworkBoundary", 1)[1].split(
            "function Assert-ComposeManifestImages", 1
        )[0]
    )
    services = {}
    for name in ("postgres", "redis", "migrate", "gw", "api", "web"):
        services[name] = {
            "image": f"candidate/{name}:immutable",
            "pull_policy": "never",
            "ports": None,
        }
    del services["migrate"]["ports"]
    services["gw"]["ports"] = [{"host_ip": "127.0.0.1", "target": 5020, "published": "5020"}]
    services["web"]["ports"] = [{"host_ip": "127.0.0.1", "target": 80, "published": "80"}]
    invocation = f"""
$ErrorActionPreference = 'Stop'
$PolicyServices = @('postgres','redis','migrate','gw','api','web')
{function}
$model = ConvertFrom-Json {_ps_literal(json.dumps({"services": services}))}
Assert-NetworkBoundary $model
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_final_container_image_id_mismatch_is_executably_rejected() -> None:
    source = _read(UPDATER)
    function = (
        "function Assert-CurrentContainerIdentity"
        + source.split("function Assert-CurrentContainerIdentity", 1)[1].split(
            "function Invoke-PublisherVerification", 1
        )[0]
    )
    invocation = f"""
$ErrorActionPreference = 'Stop'
$PersistentServices = @('api')
function Invoke-DockerText {{ 'candidate/api:one|sha256:' + ('2' * 64) }}
{function}
$images = @{{ api = [pscustomobject]@{{
  candidate_reference = 'candidate/api:one'; image_id = 'sha256:' + ('1' * 64)
}} }}
try {{ Assert-CurrentContainerIdentity -Images $images; exit 2 }} catch {{
  if ($_.Exception.Message -cne 'running_container_identity_mismatch') {{ throw }}
}}
exit 0
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_acl_compatibility_functions_execute_in_both_editions(
    executable: str, tmp_path: Path
) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    source = _read(UPDATER)
    functions = (
        "function Set-DirectoryAccessControl"
        + source.split("function Set-DirectoryAccessControl", 1)[1].split(
            "function Assert-RestrictedDirectory", 1
        )[0]
    )
    directory = tmp_path / executable
    directory.mkdir()
    file_path = directory / "audit.lock"
    file_path.write_bytes(b"")
    invocation = f"""
$ErrorActionPreference = 'Stop'
{functions}
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$directoryAcl = New-Object Security.AccessControl.DirectorySecurity
$directoryAcl.SetOwner($sid)
$fileAcl = New-Object Security.AccessControl.FileSecurity
$fileAcl.SetOwner($sid)
Set-DirectoryAccessControl -Path {_ps_literal(directory)} -Acl $directoryAcl
Set-FileAccessControl -Path {_ps_literal(file_path)} -Acl $fileAcl
"""
    completed = subprocess.run(
        [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_local_audit_acl_is_idempotent_in_both_editions(executable: str, tmp_path: Path) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    source = _read(CONTROLLER)
    functions = (
        "function Test-RestrictedAccessControl"
        + source.split("function Test-RestrictedAccessControl", 1)[1].split(
            "function Write-LocalAudit", 1
        )[0]
    )
    directory = tmp_path / f"local-audit-{executable}"
    audit_file = directory / "remote-full-upgrade.jsonl"
    invocation = f"""
$ErrorActionPreference = 'Stop'
{functions}
Set-RestrictedDirectory -Path {_ps_literal(directory)} -CreateAuditMutex
Set-RestrictedFile -Path {_ps_literal(audit_file)} -Create
function Set-Acl {{ throw 'unexpected_acl_rewrite' }}
Set-RestrictedDirectory -Path {_ps_literal(directory)} -CreateAuditMutex
Set-RestrictedFile -Path {_ps_literal(audit_file)}
"""
    completed = subprocess.run(
        [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_windows_powershell_env() if executable == "powershell.exe" else os.environ.copy(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_local_audit_hash_uses_original_json_bytes(executable: str) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    source = _read(CONTROLLER)
    function = (
        "function Get-AuditLineHashMaterial"
        + source.split("function Get-AuditLineHashMaterial", 1)[1].split(
            "function Assert-RemotePath", 1
        )[0]
    )
    raw_payload = (
        '{"schema_version":1,"recorded_at":"2026-09-02T07:11:29.5362646+00:00",'
        '"operation_id":"32712215-01fb-4bd1-bbfd-299ac211ef88"}'
    )
    invocation = f"""
$ErrorActionPreference = 'Stop'
{function}
$raw = {_ps_literal(raw_payload)}
$sha = [Security.Cryptography.SHA256]::Create()
try {{
  $hash = ([BitConverter]::ToString(
    $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($raw))
  )).Replace('-', '').ToLowerInvariant()
}}
finally {{ $sha.Dispose() }}
$line = $raw.Substring(0, $raw.Length - 1) + ',"record_hash":"' + $hash + '"}}'
$material = Get-AuditLineHashMaterial -Line $line
if ($null -eq $material -or $material.payload -cne $raw -or $material.record_hash -cne $hash) {{
  throw 'audit_hash_material_changed'
}}
$altered = $line.Replace('07:11:29.5362646+00:00', '15:11:29.5362646+08:00')
$alteredMaterial = Get-AuditLineHashMaterial -Line $altered
if ($alteredMaterial.payload -ceq $raw) {{ throw 'audit_timestamp_edit_was_normalized' }}
$alteredSha = [Security.Cryptography.SHA256]::Create()
try {{
  $alteredHash = ([BitConverter]::ToString(
    $alteredSha.ComputeHash([Text.Encoding]::UTF8.GetBytes($alteredMaterial.payload))
  )).Replace('-', '').ToLowerInvariant()
}}
finally {{ $alteredSha.Dispose() }}
if ($alteredHash -ceq $hash) {{ throw 'audit_timestamp_edit_was_accepted' }}
"""
    completed = subprocess.run(
        [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_maintenance_pointer_comparison_executes_after_lock_race() -> None:
    source = _read(ROOT / "tools" / "remote_maintenance.ps1")
    function = (
        "function Assert-ActiveReleaseUnchanged"
        + source.split("function Assert-ActiveReleaseUnchanged", 1)[1].split(
            "function Assert-ManifestImageIdentity", 1
        )[0]
    )
    invocation = f"""
$ErrorActionPreference = 'Stop'
{function}
$before = [pscustomobject]@{{ schema_version=1; candidate_id='one'; logical_identity='id';
  source_commit='commit'; candidate_root='root'; site_root='site'; committed_at='time'; operation_id='op' }}
$after = $before.PSObject.Copy(); $after.source_commit = 'changed'
try {{ Assert-ActiveReleaseUnchanged -Before $before -After $after; exit 2 }} catch {{
  if ($_.Exception.Message -cne 'active_release_identity_drift') {{ throw }}
}}
exit 0
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_operations_resolve_active_pointer_without_stale_candidate_default() -> None:
    maintenance = _read(ROOT / "tools" / "remote_maintenance.ps1")
    hotfix = _read(ROOT / "tools" / "remote_hotfix_deploy.ps1")
    debug = _read(ROOT / "tools" / "remote_debug.ps1")

    for script in (maintenance, hotfix):
        assert "deploy-20260821.1" not in script
        assert "active-release.json" in script
        assert "active_release_identity_drift" in script
    assert '"-m", "ruisheng_api.healthcheck"' in maintenance
    assert '"-m", "ruisheng_api.healthcheck"' in hotfix
    assert "python -m ruisheng_api.healthcheck" in debug
    assert "urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready'" not in (
        maintenance + hotfix + debug
    )


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_full_upgrade_scripts_parse_in_both_powershell_editions(executable: str) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    for path in (CONTROLLER, UPDATER):
        escaped = str(path).replace("'", "''")
        command = (
            "$tokens=$null;$errors=$null;"
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{escaped}',"
            "[ref]$tokens,[ref]$errors);"
            "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}"
        )
        completed = subprocess.run(
            [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, f"{path.name}: {completed.stdout}{completed.stderr}"


def test_upgrade_scripts_do_not_embed_secret_transport_channels() -> None:
    combined = _read(CONTROLLER) + _read(UPDATER)
    forbidden = (
        "API_MANAGEMENT_TOKEN",
        "GW_HEALTH_TOKEN",
        "Authorization: Bearer",
        "--env-file-content",
        "Invoke-Expression",
        "iex ",
    )
    assert not any(value in combined for value in forbidden)
    assert re.search(r"Reason must contain 8-200", combined)


def test_operator_docs_use_the_controlled_upgrade_and_recovery_entrypoint() -> None:
    remote_guide = _read(ROOT / "docs" / "REMOTE_DEBUG.md")
    customer_guide = _read(ROOT / "deploy" / "setup-customer.md")
    combined = remote_guide + customer_guide

    for action in ("Plan", "Initialize", "Apply", "Status", "Recover"):
        assert f"-Action {action}" in combined
    assert combined.count("-SiteRoot $SiteRoot") >= 10
    assert "-CurrentCandidateRoot $CurrentCandidateRoot" in combined
    for guide in (remote_guide, customer_guide):
        assert guide.index("-Action Initialize") < guide.index("-Action Plan")
    assert "Remove-Item -LiteralPath `$path -Recurse" not in _read(CONTROLLER)
    assert "remote_full_upgrade.ps1" in remote_guide
    assert "active-release.json" in combined
    assert "B-04 remains BLOCKED" in combined
    assert "recovery_failed" in combined
    assert "不定时检测" in combined
