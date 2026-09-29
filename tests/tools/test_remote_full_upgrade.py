"""Contracts for the signed full-release remote upgrade workflow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
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


def _bounded_function(name: str) -> str:
    source = _read(UPDATER)
    start = source.index(f"function {name} {{")
    end = re.search(r"\n(?:function |\$active = \$null)", source[start + 1 :])
    assert end is not None
    return source[start : start + 1 + end.start()]


def _run_bounded_powershell(executable: str, source: str) -> subprocess.CompletedProcess[str]:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    encoded = base64.b64encode(source.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_windows_powershell_env(),
        check=False,
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
    {_bounded_function("Convert-UpgradeJson")}
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
$fixtureAcl = Get-Acl -LiteralPath {_ps_literal(directory)}
$fixtureRule = New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
$fixtureAcl.AddAccessRule($fixtureRule)
Set-DirectoryAccessControl -Path {_ps_literal(directory)} -Acl $fixtureAcl
$directoryAcl = New-Object Security.AccessControl.DirectorySecurity
$directoryAcl.SetOwner($sid)
$directoryAcl.AddAccessRule($fixtureRule)
$fileAcl = New-Object Security.AccessControl.FileSecurity
$fileAcl.SetOwner($sid)
$fileAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow')))
Set-DirectoryAccessControl -Path {_ps_literal(directory)} -Acl $directoryAcl
Set-FileAccessControl -Path {_ps_literal(file_path)} -Acl $fileAcl
foreach ($path in @({_ps_literal(directory)},{_ps_literal(file_path)})) {{
  $actual=Get-Acl -LiteralPath $path
  if ($actual.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne $sid.Value) {{ throw 'fixture_owner_mismatch' }}
  $allowed=@($actual.Access | Where-Object {{
    $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -ceq $sid.Value -and
    $_.AccessControlType -eq 'Allow' -and
    ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl
  }})
  if (-not $allowed.Count) {{ throw 'fixture_acl_mismatch' }}
}}
"""
    completed = subprocess.run(
        [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_windows_powershell_env(),
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
            f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{escaped}',"
            "[ref]$tokens,[ref]$errors);"
            "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1};"
            "$unsafe=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.CommandAst] "
            "-and $n.GetCommandName() -iin @('Invoke-Expression','iex')},$true));"
            "if($unsafe.Count){throw 'unsafe_expression_evaluation'}"
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
        upgrade_commands = re.findall(r"remote_full_upgrade\.ps1\s+-Action\s+(\w+)", guide)
        assert upgrade_commands.index("Initialize") < upgrade_commands.index("Plan")
    assert "Remove-Item -LiteralPath `$path -Recurse" not in _read(CONTROLLER)
    assert "remote_full_upgrade.ps1" in remote_guide
    assert "active-release.json" in combined
    assert "B-04 remains BLOCKED" in combined
    assert "recovery_failed" in combined
    assert "不定时检测" in combined


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_schema_gate_executes_exact_allowlist(executable: str) -> None:
    source = f"""
$ErrorActionPreference = 'Stop'
$BoundedSourceHead = '0012_alarm_notification_runtime'
$BoundedTargetHead = '0013_serial_polling_profile'
{_bounded_function("Get-SchemaUpgradeKind")}
$result = @()
foreach ($case in @(
  @($BoundedSourceHead, $BoundedSourceHead), @($BoundedSourceHead, $BoundedTargetHead),
  @($BoundedTargetHead, $BoundedSourceHead), @('0011', $BoundedTargetHead),
  @("$BoundedSourceHead`nother", $BoundedTargetHead), @('', ''),
  @($BoundedSourceHead.ToUpperInvariant(), $BoundedTargetHead)
)) {{
  try {{ $result += Get-SchemaUpgradeKind $case[0] $case[1] }}
  catch {{ $result += $_.Exception.Message }}
}}
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "same_head",
        "bounded_0012_0013",
        *(["schema_head_changed"] * 5),
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_recovery_never_chooses_old_after_new_application_start(executable: str) -> None:
    source = f"""
$ErrorActionPreference = 'Stop'
$BoundedSourceHead = '0012_alarm_notification_runtime'
$BoundedTargetHead = '0013_serial_polling_profile'
{_bounded_function("Get-BoundedRestoreDecision")}
$result = @()
foreach ($started in @($false, $true)) {{
  foreach ($head in @($BoundedSourceHead, $BoundedTargetHead, '0014', "$BoundedTargetHead`nother")) {{
    $journal = @{{ migration = @{{ application_start_attempted = $started }} }}
    try {{ $result += Get-BoundedRestoreDecision $journal $head }}
    catch {{ $result += $_.Exception.Message }}
  }}
}}
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "previous",
        "forward",
        "recovery_database_head_unknown",
        "recovery_database_head_unknown",
        "recovery_database_head_unknown",
        "forward",
        "recovery_database_head_unknown",
        "recovery_database_head_unknown",
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_roles_restore_original_login_flags_only(executable: str) -> None:
    source = f"""
$ErrorActionPreference = 'Stop'
$script:sql = @()
function Assert-LocksOwned {{ }}
function Invoke-DatabaseSql {{ param([string]$Sql) $script:sql += $Sql }}
{_bounded_function("Restore-ApplicationRoles")}
$journal = @{{ migration = @{{ roles = @(
  @{{ name='ruisheng_api'; login=$false }}, @{{ name='ruisheng_gw'; login=$true }}
) }} }}
Restore-ApplicationRoles $journal
ConvertTo-Json -InputObject $script:sql -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "ALTER ROLE ruisheng_api NOLOGIN;",
        "ALTER ROLE ruisheng_gw LOGIN;",
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_roles_reject_invalid_receipt_and_lost_lock(executable: str) -> None:
    source = f"""
$ErrorActionPreference = 'Stop'
$script:lost = $false
function Assert-LocksOwned {{ if ($script:lost) {{ throw 'upgrade_lock_lost' }} }}
function Invoke-DatabaseSql {{ throw 'unexpected_database_mutation' }}
{_bounded_function("Restore-ApplicationRoles")}
$journal = @{{ migration = @{{ roles = @(@{{ name='ruisheng_api'; login='true' }}) }} }}
$result = @()
try {{ Restore-ApplicationRoles $journal }} catch {{ $result += $_.Exception.Message }}
$script:lost = $true
try {{ Restore-ApplicationRoles $journal }} catch {{ $result += $_.Exception.Message }}
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["recovery_role_receipt_invalid", "upgrade_lock_lost"]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_marker_has_no_lease_expiry_bypass(executable: str, tmp_path: Path) -> None:
    marker = tmp_path / "maintenance.json"
    operation = "dba53f23-5736-4a0c-a59e-c8485a60bcde"
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "site_root": "test-site",
                "status": "active",
                "operation_id": operation,
                "updated_at": "2000-01-01T00:00:00.0000000+00:00",
                "source_identity": "sha256:" + "a" * 64,
                "candidate_identity": "sha256:" + "b" * 64,
                "source_head": "0012_alarm_notification_runtime",
                "target_head": "0013_serial_polling_profile",
                "journal_path": str(tmp_path / f"full-upgrade-{operation}.json"),
            }
        ),
        encoding="utf-8",
    )
    source = f"""
$ErrorActionPreference = 'Stop'
$MaintenanceStatePath = {_ps_literal(marker)}
$SiteRoot = 'test-site'; $OperationId = '{operation}'; $StateDirectory = {_ps_literal(tmp_path)}
$BoundedSourceHead = '0012_alarm_notification_runtime'; $BoundedTargetHead = '0013_serial_polling_profile'
function Assert-RestrictedFile {{ param([string]$Path) }}
{_function(_read(UPDATER), "Test-ExactKeys", "Get-AllowedSids")}
{_bounded_function("Convert-UpgradeJson")}
{_bounded_function("Assert-MaintenanceState")}
$result = @()
try {{ Assert-MaintenanceState; $result += 'incorrectly_allowed' }} catch {{ $result += $_.Exception.Message }}
Assert-MaintenanceState -Recovering
$result += 'matching_recovery_allowed'
$OperationId = 'different-operation'
try {{ Assert-MaintenanceState -Recovering; $result += 'incorrectly_allowed' }} catch {{ $result += $_.Exception.Message }}
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "full_upgrade_maintenance_active",
        "matching_recovery_allowed",
        "full_upgrade_maintenance_active",
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_migration_must_stop_before_observing_database(executable: str) -> None:
    source = f"""
$ErrorActionPreference = 'Stop'
$OperationId = 'test-operation'
function Invoke-DockerText {{
  param([string[]]$Arguments)
  if ($Arguments[0] -eq 'ps') {{ return 'known-id' }}
  return '{{"Id":"known-id","Name":"/ruisheng-migrate-test-operation","Image":"target-image","Config":{{"Labels":{{"com.ruisheng.upgrade.operation":"test-operation"}}}},"State":{{"Running":true,"Restarting":false}}}}'
}}
function Assert-NoUnknownWriters {{ throw 'database_observed_before_migration_stopped' }}
{_bounded_function("Convert-UpgradeJson")}
{_bounded_function("Assert-BoundedMigrationStopped")}
$journal = @{{ migration = @{{ container_name='ruisheng-migrate-test-operation'; container_id='known-id'; target_images=@{{api='target-image'}} }} }}
try {{ Assert-BoundedMigrationStopped $journal; throw 'incorrectly_allowed' }} catch {{ $_.Exception.Message }}
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "migration_execution_uncertain"


def test_bounded_migration_digest_is_bound_to_reviewed_source() -> None:
    migration = ROOT / "alembic" / "versions" / "20260907_0013_serial_polling_profile.py"
    digest = hashlib.sha256(migration.read_bytes()).hexdigest()
    assert digest == "df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1"
    assert digest in _bounded_function("Assert-BoundedMigrationImage")


def test_bounded_recovery_does_not_restore_or_downgrade_production_database() -> None:
    recovery = _bounded_function("Invoke-BoundedRecovery")
    assert "pg_restore" not in recovery
    assert "downgrade" not in recovery
    assert "Restore-PreviousRelease" not in recovery
    assert recovery.index("Stop-BoundedApplications") < recovery.index(
        "Assert-BoundedMigrationStopped"
    )
    assert '-RequireVerified:($decision -eq "forward")' in recovery
    assert "application_start_attempted" in _bounded_function("Start-BoundedApplications")
    assert "--restart" not in _bounded_function("Start-BoundedApplications")


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_strict_health_rejects_live_but_unready_services(executable: str) -> None:
    source = f"""
$ErrorActionPreference = 'Stop'
function Assert-LocksOwned {{ }}
function Renew-Locks {{ }}
function Start-Sleep {{ param([int]$Seconds) }}
function Invoke-DockerText {{
  param([string[]]$Arguments)
  if ($Arguments[0] -eq 'inspect') {{ return '{{"Running":true,"Health":{{"Status":"healthy"}}}}' }}
  if ($Arguments[1] -eq 'ruisheng-web') {{ return '' }}
  return '{{"status":"not_ready","database":"failed","redis":"ready","service":"ready","batch":"ready","outbox":"ready"}}'
}}
{_bounded_function("Wait-BoundedHealthy")}
try {{ Wait-BoundedHealthy @('compose') -TimeoutSeconds 0; throw 'incorrectly_allowed' }} catch {{ $_.Exception.Message }}
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "service_health_failed"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_marker_roundtrips_and_rejects_unknown_fields(
    executable: str, tmp_path: Path
) -> None:
    source = f"""
$ErrorActionPreference = 'Stop'
$SiteRoot = 'test-site'; $OperationId = 'dba53f23-5736-4a0c-a59e-c8485a60bcde'
$StateDirectory = {_ps_literal(tmp_path)}
$MaintenanceStatePath = Join-Path $StateDirectory 'maintenance.json'
$JournalPath = Join-Path $StateDirectory "full-upgrade-$OperationId.json"
$BoundedSourceHead = '0012_alarm_notification_runtime'; $BoundedTargetHead = '0013_serial_polling_profile'
function Assert-LocksOwned {{ }}
function Assert-RestrictedFile {{ param([string]$Path) }}
{_function(_read(UPDATER), "Write-JsonAtomic", "Get-SshPosture")}
{_function(_read(UPDATER), "Test-ExactKeys", "Get-AllowedSids")}
{_bounded_function("Convert-UpgradeJson")}
{_bounded_function("Assert-MaintenanceState")}
{_bounded_function("Write-MaintenanceState")}
$journal = @{{ previous_release=@{{logical_identity=('sha256:'+('a'*64))}}; candidate=@{{logical_identity=('sha256:'+('b'*64))}} }}
Write-MaintenanceState $journal
Assert-MaintenanceState -Recovering
$result = @('roundtrip_ok')
$value = Convert-UpgradeJson ([IO.File]::ReadAllText($MaintenanceStatePath))
$value = Add-Member -InputObject $value -NotePropertyName expires_at -NotePropertyValue '2000-01-01T00:00:00Z' -PassThru
Write-JsonAtomic $MaintenanceStatePath $value
try {{ Assert-MaintenanceState -Recovering; $result += 'incorrectly_allowed' }} catch {{ $result += $_.Exception.Message }}
Write-MaintenanceState $journal 'committed'
$value = Convert-UpgradeJson ([IO.File]::ReadAllText($MaintenanceStatePath))
$value.PSObject.Properties.Remove('source_identity')
Write-JsonAtomic $MaintenanceStatePath $value
try {{ Assert-MaintenanceState; $result += 'incorrectly_allowed' }} catch {{ $result += $_.Exception.Message }}
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "roundtrip_ok",
        "full_upgrade_maintenance_invalid",
        "full_upgrade_maintenance_invalid",
    ]


def _bounded_backup_journal(directory: Path) -> dict:
    operation = "06fe9214-ad53-4e65-b72c-a21e67b61e85"
    image = "sha256:" + "c" * 64
    receipt = {
        "schema_version": 4,
        "operation_id": operation,
        "snapshot_id": "00000003-00000002-1",
        "source_identity": "sha256:" + "a" * 64,
        "candidate_identity": "sha256:" + "b" * 64,
        "database_head": "0012_alarm_notification_runtime",
        "fingerprint_sha256": "d" * 64,
        "postgres_version": "150007",
        "timescale_version": "2.16.1",
        "timescale_owner": "ruisheng_admin",
        "postgres_image": image,
        "restore_verified": False,
        "restore_container": f"ruisheng-restore-{operation}",
        "restore_container_id": "",
        "restore_volume": f"ruisheng-restore-{operation}",
        "restore_volume_created_at": "",
        "restore_asset_token": operation,
        "created_at": "2026-09-08T00:00:00Z",
    }
    for kind, name in {
        "database": "ruisheng.dump",
        "roles": "roles.sql",
        "expressions": "expressions.sql",
        "database_properties": "database-properties.sql",
    }.items():
        path = directory / name
        path.write_bytes(b"test-only-backup-material")
        receipt[kind] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {
        "backup": receipt,
        "previous_release": {"logical_identity": receipt["source_identity"]},
        "candidate": {"logical_identity": receipt["candidate_identity"]},
        "migration": {"source_images": {"postgres": image}, "phase": "writers_fenced"},
    }


def _bounded_ast_loader(*names: str) -> str:
    if "Test-UnprotectedStartupAction" in names:
        names = (
            *names,
            "Expand-StartupEnvironment",
            "Assert-StartupScriptIdentity",
            "Test-ApprovedStartupScript",
            "Test-ApprovedWindowsStartupHost",
            "Test-StartupPowerShellCommand",
        )
    selected = ",".join(_ps_literal(name) for name in names)
    return f"""
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile({_ps_literal(UPDATER)},[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'updater_parse_failed' }}
foreach ($node in $ast.EndBlock.Statements) {{
  if ($node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -cin @({selected})) {{
    . ([ScriptBlock]::Create($node.Extent.Text))
  }}
}}
"""


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_actual_leased_lock_roundtrip_expiry_and_foreign_owner(executable: str, tmp_path: Path):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Convert-UpgradeJson", "New-LockRecord", "Acquire-LeasedLock", "Assert-LocksOwned", "Renew-Locks", "Release-Locks", "Write-JsonAtomic", "Test-MatchingLockProcess")}
$OperationId='807c6cae-d862-44da-9342-8d20ce226b52'; $LeaseSeconds=120
$ProcessStartedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
$AcquiredLocks=New-Object Collections.ArrayList
$lockPath={_ps_literal(tmp_path / "owned.lock")}
Acquire-LeasedLock $lockPath 'shared-maintenance'
Assert-LocksOwned
Renew-Locks
Assert-LocksOwned
Release-Locks
if (Test-Path $lockPath) {{ throw 'lock_not_released' }}
Acquire-LeasedLock $lockPath 'shared-maintenance'
$record=Convert-UpgradeJson (Get-Content $lockPath -Raw)
$record.expires_at=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')
Write-JsonAtomic $lockPath $record
try {{ Assert-LocksOwned; throw 'expiry_ignored' }} catch {{ $_.Exception.Message }}
Release-Locks
Acquire-LeasedLock $lockPath 'shared-maintenance'
$record=Convert-UpgradeJson (Get-Content $lockPath -Raw)
$record.operation_id='be3fcaab-793c-4a90-ab6b-30ae6c7584e2'
Write-JsonAtomic $lockPath $record
try {{ Renew-Locks; throw 'owner_ignored' }} catch {{ $_.Exception.Message }}
Release-Locks
if (-not (Test-Path $lockPath)) {{ throw 'foreign_lock_removed' }}
'roundtrip_ok'
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["upgrade_lock_lost", "upgrade_lock_lost", "roundtrip_ok"]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_short_docker_calls_renew_real_operation_locks(executable: str, tmp_path: Path):
    # Real disk locks and elapsed time; only the external Docker process is replaced.
    # The extracted functions use a six-second fixture lease; the CLI minimum stays 120.
    source = rf"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Convert-UpgradeJson", "New-LockRecord", "Acquire-LeasedLock", "Assert-LocksOwned", "Renew-Locks", "Write-JsonAtomic", "Invoke-DockerText")}
$OperationId='a3f345c3-7c35-4a40-a98a-4cdeaf64bcba'; $LeaseSeconds=6
$ProcessStartedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
$AcquiredLocks=New-Object Collections.ArrayList
$script:commandCalls=0
function Get-Command {{ param($Name); if ($Name -ceq 'docker.exe') {{ @{{Source='owned-no-docker-probe'}} }} else {{ Microsoft.PowerShell.Core\Get-Command $Name }} }}
function Invoke-ContainedUpgradeProcess {{
  $script:commandCalls++
  [Threading.Thread]::Sleep(100)
  return 'short_command_completed'
}}
foreach ($name in @('shared-maintenance','legacy-hotfix')) {{
  Acquire-LeasedLock (Join-Path {_ps_literal(tmp_path)} ($name+'.lock')) $name
}}
$initial=Convert-UpgradeJson (Get-Content -LiteralPath $AcquiredLocks[0].path -Raw)
$initialExpiry=[DateTimeOffset]::Parse([string]$initial.expires_at)
$expiries=@()
for ($iteration=0; $iteration -lt 14; $iteration++) {{
  if ((Invoke-DockerText @('inspect','owned-test-target')) -cne 'short_command_completed') {{ throw 'command_failed' }}
  $observed=Convert-UpgradeJson (Get-Content -LiteralPath $AcquiredLocks[0].path -Raw)
  $expiries+=[string]$observed.expires_at
  [Threading.Thread]::Sleep(400)
}}
Assert-LocksOwned
$preserved=@(foreach ($held in $AcquiredLocks) {{
  $record=Convert-UpgradeJson (Get-Content -LiteralPath $held.path -Raw)
  [string]$record.operation_id -ceq $OperationId -and [int]$record.pid -eq $PID -and
    [string]$record.process_started_at -ceq $ProcessStartedAt -and
    [DateTimeOffset]::Parse([string]$record.expires_at) -gt $initialExpiry
}})
@{{calls=$script:commandCalls;past_original_expiry=([DateTimeOffset]::UtcNow -gt $initialExpiry);
  renewed_multiple_times=(@($expiries | Select-Object -Unique).Count -ge 3);
  both_owners_preserved=($preserved.Count -eq 2 -and $preserved -notcontains $false)}} | ConvertTo-Json -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "calls": 14,
        "past_original_expiry": True,
        "renewed_multiple_times": True,
        "both_owners_preserved": True,
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_due_renewal_rejects_lost_lock_before_command_or_any_write(executable: str, tmp_path: Path):
    source = rf"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Convert-UpgradeJson", "New-LockRecord", "Acquire-LeasedLock", "Assert-LocksOwned", "Renew-Locks", "Write-JsonAtomic", "Invoke-DockerText")}
$OperationId='833f8d06-9a0c-4c55-9b88-cda986a4592b'; $LeaseSeconds=120
$ProcessStartedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
$script:commandCalls=0
function Get-Command {{ param($Name); if ($Name -ceq 'docker.exe') {{ @{{Source='owned-no-docker-probe'}} }} else {{ Microsoft.PowerShell.Core\Get-Command $Name }} }}
function Invoke-ContainedUpgradeProcess {{ $script:commandCalls++; throw 'must_not_launch' }}
$results=@()
foreach ($fault in @('expired','foreign','pid','process_start')) {{
  $AcquiredLocks=New-Object Collections.ArrayList
  foreach ($name in @('shared-maintenance','legacy-hotfix')) {{
    Acquire-LeasedLock (Join-Path {_ps_literal(tmp_path)} ($fault+'-'+$name+'.lock')) $name
  }}
  $first=Convert-UpgradeJson (Get-Content -LiteralPath $AcquiredLocks[0].path -Raw)
  $first.expires_at=[DateTimeOffset]::UtcNow.AddSeconds(60).ToString('o')
  Write-JsonAtomic $AcquiredLocks[0].path $first
  $second=Convert-UpgradeJson (Get-Content -LiteralPath $AcquiredLocks[1].path -Raw)
  if ($fault -ceq 'expired') {{ $second.expires_at=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o') }}
  elseif ($fault -ceq 'foreign') {{ $second.operation_id='d51ad56e-2e3a-480f-a1ca-82e4b3e17d89' }}
  elseif ($fault -ceq 'pid') {{ $second.pid=$PID+1 }}
  else {{ $second.process_started_at=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o') }}
  Write-JsonAtomic $AcquiredLocks[1].path $second
  $before=@($AcquiredLocks | ForEach-Object {{ (Get-FileHash -LiteralPath $_.path).Hash }})
  $errorCode=''
  try {{ Invoke-DockerText @('inspect','owned-test-target') | Out-Null }} catch {{ $errorCode=[string]$_.Exception.Message }}
  $after=@($AcquiredLocks | ForEach-Object {{ (Get-FileHash -LiteralPath $_.path).Hash }})
  $results+=@{{fault=$fault;error=$errorCode;unchanged=(($before -join '|') -ceq ($after -join '|'))}}
}}
@{{calls=$script:commandCalls;results=$results}} | ConvertTo-Json -Depth 4 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "calls": 0,
        "results": [
            {"fault": fault, "error": "upgrade_lock_lost", "unchanged": True}
            for fault in ("expired", "foreign", "pid", "process_start")
        ],
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_lock_lost_during_command_preserves_pending_daemon_intent(executable: str, tmp_path: Path):
    source = rf"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Convert-UpgradeJson", "New-LockRecord", "Acquire-LeasedLock", "Assert-LocksOwned", "Renew-Locks", "Write-JsonAtomic", "Invoke-DockerText")}
$OperationId='0590b59c-a72e-463c-9764-b360e15bdf43'; $LeaseSeconds=120
$ProcessStartedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
$AcquiredLocks=New-Object Collections.ArrayList
$JournalPath={_ps_literal(tmp_path / "pending-journal.json")}
$journal=@{{migration=@{{phase='application_prepare_intent'}}}}
foreach ($name in @('shared-maintenance','legacy-hotfix')) {{
  Acquire-LeasedLock (Join-Path {_ps_literal(tmp_path)} ($name+'.lock')) $name
}}
$script:commandCalls=0
function Get-Command {{ param($Name); if ($Name -ceq 'docker.exe') {{ @{{Source='owned-no-docker-probe'}} }} else {{ Microsoft.PowerShell.Core\Get-Command $Name }} }}
function Invoke-ContainedUpgradeProcess {{
  $script:commandCalls++
  $record=Convert-UpgradeJson (Get-Content -LiteralPath $AcquiredLocks[1].path -Raw)
  $record.operation_id='5c8279d4-cf14-4b9e-93c9-358227522f70'
  Write-JsonAtomic $AcquiredLocks[1].path $record
  return 'external_command_completed'
}}
$errorCode=''
try {{ Invoke-DockerText @('create','owned-test-target') | Out-Null }} catch {{ $errorCode=[string]$_.Exception.Message }}
$saved=Convert-UpgradeJson (Get-Content -LiteralPath $JournalPath -Raw)
@{{calls=$script:commandCalls;error=$errorCode;intent=[string]$saved.migration.docker_intent.command}} | ConvertTo-Json -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "calls": 1,
        "error": "upgrade_lock_lost",
        "intent": "create",
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_marker_directory_is_rejected(executable: str, tmp_path: Path):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Assert-MaintenanceState")}
$MaintenanceStatePath={_ps_literal(tmp_path)}
try {{ Assert-MaintenanceState -Recovering; throw 'directory_allowed' }} catch {{ $_.Exception.Message }}
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "full_upgrade_maintenance_invalid"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("kind", ["source", "target", "nonrelease_drift", "prospective_drift"])
def test_recovery_environment_derivation_checks_all_bytes(
    executable: str, kind: str, tmp_path: Path
):
    operation = "807c6cae-d862-44da-9342-8d20ce226b52"
    original = b"\xef\xbb\xbfSECRET=keep-exact\r\n" + b"".join(
        f"{key}=old\r\n".encode()
        for key in (
            "TARGET_PLATFORM",
            "POSTGRES_IMAGE",
            "REDIS_IMAGE",
            "API_IMAGE",
            "GW_IMAGE",
            "WEB_IMAGE",
        )
    )
    backup = tmp_path / f"full-upgrade-{operation}.env.before"
    current = tmp_path / ".env.prod"
    backup.write_bytes(original)
    current.write_bytes(
        original if kind != "nonrelease_drift" else original.replace(b"keep-exact", b"changed")
    )
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Get-ProspectiveEnvironmentBytes", "Write-ProspectiveEnvironment", "Assert-BoundedEnvironment")}
function Assert-LocksOwned {{ }}
function Assert-RestrictedFile {{ param($Path) }}
function Set-RestrictedFileAcl {{ param($Path) }}
$StateDirectory={_ps_literal(tmp_path)}; $OperationId='{operation}'
$EnvFile={_ps_literal(current)}; $ProspectiveEnvPath=Join-Path $StateDirectory 'prospective.env'
$hash=(Get-FileHash {_ps_literal(backup)}).Hash.ToLowerInvariant()
$journal=@{{environment_backup=@{{path={_ps_literal(backup)};sha256=$hash}};source_environment_sha256=$hash}}
$values=@{{TARGET_PLATFORM='new';POSTGRES_IMAGE='new';REDIS_IMAGE='new';API_IMAGE='new';GW_IMAGE='new';WEB_IMAGE='new'}}
Write-ProspectiveEnvironment {_ps_literal(backup)} $ProspectiveEnvPath $values
if ('{kind}' -eq 'target') {{ Copy-Item $ProspectiveEnvPath $EnvFile -Force }}
if ('{kind}' -eq 'prospective_drift') {{ [IO.File]::AppendAllText($ProspectiveEnvPath,'INJECTED=yes') }}
try {{ [void](Assert-BoundedEnvironment $journal $values); 'verified' }} catch {{ $_.Exception.Message }}
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    expected = {
        "nonrelease_drift": "site_environment_identity_drift",
        "prospective_drift": "prospective_environment_identity_drift",
    }
    assert result.stdout.strip() == expected.get(kind, "verified")
    assert backup.read_bytes() == original


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_resource_estimates_and_failure_do_not_mutate(executable: str):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Get-BoundedResources", "Convert-UpgradeJson")}
$script:disk='10485760';$script:memory='4194304'
function Invoke-DockerText {{
  param([string[]]$Arguments)
  if ($Arguments[0] -eq 'info') {{ return '{{"MemTotal":8589934592}}' }}
  if ($Arguments[0] -cne 'exec' -or $Arguments[2] -cne 'sh') {{ throw 'unexpected_mutation' }}
  if ($Arguments[4] -match 'MemAvailable') {{ return $script:memory }}
  return $script:disk
}}
$resources=Get-BoundedResources @{{required_bytes=[long](5GB)}}
if (-not $resources.docker_memory.sufficient -or -not $resources.docker_data_volume.sufficient) {{ throw 'capacity_rejected' }}
$script:memory='1024';$script:disk='1024'
$resources=Get-BoundedResources @{{required_bytes=[long](5GB)}}
if ($resources.docker_memory.sufficient -or $resources.docker_data_volume.sufficient) {{ throw 'capacity_ignored' }}
$script:disk='unknown'
try {{ Get-BoundedResources @{{required_bytes=[long](5GB)}}; throw 'unknown_ignored' }} catch {{ $_.Exception.Message }}
'resources_checked'
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["bounded_restore_resources_unknown", "resources_checked"]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_lost_lock_retains_journal_phase_and_never_refences_or_cleans_up(
    executable: str, tmp_path: Path
):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Record-BoundedFailure", "Test-BoundedCleanupAuthority")}
$script:BoundedRecoveryMutationStarted=$true;$script:BoundedRecoveryDatabaseVerified=$true
function Assert-LocksOwned {{ throw 'upgrade_lock_lost' }}
function Write-JsonAtomic {{ throw 'unsafe_write' }}
function Stop-BoundedApplications {{ throw 'unsafe_stop' }}
function Set-ApplicationRoleFence {{ throw 'unsafe_sql' }}
function Write-Audit {{ throw 'unsafe_audit' }}
$journal=@{{status='switched';error_code='';migration=@{{phase='migration_running'}}}}
Record-BoundedFailure $journal 'upgrade_lock_lost'
if ($journal.migration.phase -cne 'migration_running') {{ throw 'phase_lost' }}
'lock_loss_retained'
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lock_loss_retained"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_backup_receipt_rejects_malformed_or_tampered_proof(
    executable: str, tmp_path: Path
):
    journal = _bounded_backup_journal(tmp_path)
    source = f"""
$ErrorActionPreference='Stop'
$OperationId={_ps_literal(journal["backup"]["operation_id"])}
$BackupDirectory={_ps_literal(tmp_path)}; $BoundedSourceHead='0012_alarm_notification_runtime'
function Assert-RestrictedFile {{ param([string]$Path) if (-not (Test-Path -LiteralPath $Path)) {{ throw 'file_missing' }} }}
{_bounded_function("Test-ExactKeys")}
{_bounded_function("Convert-UpgradeJson")}
{_bounded_function("Assert-BoundedBackupReceipt")}
$original={_ps_literal(json.dumps(journal))}
$journal=ConvertFrom-Json $original
Assert-BoundedBackupReceipt $journal
$result=@('valid')
foreach ($change in @(
  {{ $journal.backup.schema_version='4' }},
  {{ $journal.backup.restore_verified='true' }},
  {{ $journal.backup.restore_verified=$true }},
  {{ $journal.backup.fingerprint_sha256='invalid' }},
  {{ $journal.backup.snapshot_id='not-a-snapshot' }},
  {{ $journal.backup.timescale_owner='wrong-owner' }},
  {{ $journal.backup.postgres_image='sha256:'+('e'*64) }},
  {{ $journal.backup.expressions.path='untrusted-path' }},
  {{ $journal.backup.expressions.sha256='e'*64 }},
  {{ $journal.backup.roles.sha256='e'*64 }},
  {{ $journal.backup.database.sha256='e'*64 }},
  {{ $journal.backup.PSObject.Properties.Remove('expressions') }},
  {{ $journal.backup.PSObject.Properties.Remove('database_properties') }},
  {{ $journal.backup.database_properties.path='untrusted-path' }},
  {{ $journal.backup.database_properties.sha256='invalid' }},
  {{ $journal.backup.database_properties.sha256='e'*64 }},
  {{ Add-Member -InputObject $journal.backup -NotePropertyName extra -NotePropertyValue 1 }}
)) {{
  $journal=ConvertFrom-Json $original
  & $change
  try {{ Assert-BoundedBackupReceipt $journal; $result+='incorrectly_allowed' }}
  catch {{ $result+=$_.Exception.Message }}
}}
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "valid",
        *(["recovery_backup_receipt_invalid"] * 8),
        *(["recovery_backup_hash_invalid"] * 3),
        *(["recovery_backup_receipt_invalid"] * 4),
        "recovery_backup_hash_invalid",
        "recovery_backup_receipt_invalid",
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize(
    "failure,expected",
    [
        ("container", "restore_asset_conflict"),
        ("volume", "restore_asset_conflict"),
        ("memory", "restore_memory_insufficient"),
        ("disk", "restore_docker_disk_insufficient"),
        ("image", "restore_source_image_mismatch"),
    ],
)
def test_bounded_restore_preflight_rejects_before_creating_assets(
    executable: str,
    failure: str,
    expected: str,
    tmp_path: Path,
):
    journal = _bounded_backup_journal(tmp_path)
    source = f"""
$ErrorActionPreference='Stop'
$OperationId={_ps_literal(journal["backup"]["operation_id"])}
$BackupDirectory={_ps_literal(tmp_path)}; $BoundedSourceHead='0012_alarm_notification_runtime'
$failure={_ps_literal(failure)}; $script:calls=@()
function Assert-LocksOwned {{ }}
function Assert-RestrictedFile {{ param([string]$Path) }}
function Invoke-DatabaseSql {{ param([string]$Sql) if ($Sql -notmatch '^SELECT count') {{ throw 'unexpected_sql' }}; '0' }}
function Get-DatabaseBackupEstimate {{ @{{required_bytes=5GB}} }}
function Save-BoundedPhase {{ throw 'unexpected_side_effect' }}
function Invoke-DockerText {{
  param([string[]]$Arguments)
  $script:calls+=$Arguments[0]
  if ($Arguments[0] -ceq 'ps') {{ if ($failure -ceq 'container') {{ return 'conflicting-container' }}; return '' }}
  if ($Arguments[0] -ceq 'volume' -and $Arguments[1] -ceq 'ls') {{ if ($failure -ceq 'volume') {{ return 'conflicting-volume' }}; return '' }}
  if ($Arguments[0] -ceq 'inspect') {{ if ($failure -ceq 'image') {{ return 'changed-image' }}; return '{journal["backup"]["postgres_image"]}' }}
  if ($Arguments[0] -ceq 'info') {{ if ($failure -ceq 'memory') {{ return '{{"MemTotal":1}}' }}; return '{{"MemTotal":8589934592}}' }}
  if ($Arguments[0] -ceq 'exec') {{ if ($failure -ceq 'disk') {{ return '1' }}; return '999999999' }}
  throw 'unexpected_side_effect'
}}
{_bounded_ast_loader("Test-ExactKeys", "Convert-UpgradeJson", "Assert-BoundedBackupReceipt", "Test-BoundedBackupRestore")}
$journal=ConvertFrom-Json {_ps_literal(json.dumps(journal))}
try {{ Test-BoundedBackupRestore $journal; throw 'incorrectly_allowed' }} catch {{
  [ordered]@{{error=$_.Exception.Message;phase=$journal.migration.phase;verified=$journal.backup.restore_verified;calls=$script:calls}} | ConvertTo-Json -Compress
}}
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["error"] == expected
    assert observed["phase"] == "writers_fenced"
    assert observed["verified"] is False
    assert not set(observed["calls"]) & {"create", "run", "start", "stop"}


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_restore_start_timeout_retains_owned_assets_and_refuses_proof(
    executable: str, tmp_path: Path
):
    journal = _bounded_backup_journal(tmp_path)
    receipt = journal["backup"]
    volume = {
        "Name": receipt["restore_volume"],
        "Driver": "local",
        "Options": None,
        "Mountpoint": "/owned-test-volume",
        "CreatedAt": "2026-09-08T00:00:00Z",
        "Labels": {
            "com.ruisheng.upgrade.operation": receipt["operation_id"],
            "com.ruisheng.upgrade.restore-token": receipt["restore_asset_token"],
        },
    }
    container = {
        "Name": "/" + receipt["restore_container"],
        "Id": "a" * 64,
        "Image": receipt["postgres_image"],
        "Config": {"Labels": volume["Labels"]},
        "NetworkSettings": {"Networks": {"none": {}}},
        "HostConfig": {"NetworkMode": "none", "RestartPolicy": {"Name": "no"}, "PortBindings": {}},
        "Mounts": [
            {
                "Type": "volume",
                "Name": receipt["restore_volume"],
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
                "Source": "/owned-test-volume",
            }
        ],
    }
    source = f"""
$ErrorActionPreference='Stop'
$OperationId={_ps_literal(receipt["operation_id"])}; $BackupDirectory={_ps_literal(tmp_path)}
$BoundedSourceHead='0012_alarm_notification_runtime'; $JournalPath=Join-Path $BackupDirectory 'journal.json'
$script:created=$false; $script:stops=0; $script:calls=@()
function Assert-LocksOwned {{ }}
function Assert-RestrictedFile {{ param([string]$Path) }}
function Invoke-DatabaseSql {{ param([string]$Sql) if ($Sql -notmatch '^SELECT count') {{ throw 'unexpected_sql' }}; '0' }}
function Get-DatabaseBackupEstimate {{ @{{required_bytes=5GB}} }}
function Save-BoundedPhase {{ param($Journal,[string]$Phase) $Journal.migration.phase=$Phase }}
function Invoke-DockerText {{
  param([string[]]$Arguments)
  $script:calls+=$Arguments[0]
  if ($Arguments[0] -ceq 'ps') {{ if ($script:created) {{ return 'owned-container' }}; return '' }}
  if ($Arguments[0] -ceq 'volume') {{
    if ($Arguments[1] -ceq 'inspect') {{ return {_ps_literal(json.dumps(volume))} }}
    return ''
  }}
  if ($Arguments[0] -ceq 'inspect') {{ return {_ps_literal(receipt["postgres_image"])} }}
  if ($Arguments[0] -ceq 'info') {{ return '{{"MemTotal":8589934592}}' }}
  if ($Arguments[0] -ceq 'exec') {{ return '999999999' }}
  if ($Arguments[0] -ceq 'create') {{ $script:created=$true; return '' }}
  if ($Arguments[0] -ceq 'container') {{ return {_ps_literal(json.dumps(container))} }}
  if ($Arguments[0] -ceq 'start') {{ throw 'docker_command_timeout' }}
  if ($Arguments[0] -ceq 'stop') {{ $script:stops++; return '' }}
  throw 'unexpected_side_effect'
}}
{_bounded_ast_loader("Test-ExactKeys", "Convert-UpgradeJson", "Write-JsonAtomic", "Assert-BoundedBackupReceipt", "Test-BoundedBackupRestore", "Assert-BoundedRestoreAssets", "Stop-BoundedRestoreAssets")}
$journal=ConvertFrom-Json {_ps_literal(json.dumps(journal))}
try {{ Test-BoundedBackupRestore $journal; throw 'incorrectly_allowed' }} catch {{
  [ordered]@{{error=$_.Exception.Message;phase=$journal.migration.phase;verified=$journal.backup.restore_verified;stops=$script:stops;calls=$script:calls}} | ConvertTo-Json -Compress
}}
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["error"] == "docker_command_timeout"
    assert observed["phase"] == "restore_container_created"
    assert observed["verified"] is False
    assert observed["stops"] == 1
    assert not set(observed["calls"]) & {"rm", "prune", "compose", "cp"}


@pytest.fixture(scope="session")
def upgrade_process_stub(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("upgrade-process-tree")
    output = directory / "docker.exe"
    source = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
public static class UpgradeProcessStub {
  public static int Main(string[] args) {
    Console.OutputEncoding=new UTF8Encoding(false);
    if(args[0]=="args") { foreach(string a in args) Console.WriteLine(Convert.ToBase64String(Encoding.UTF8.GetBytes(a))); return 0; }
    if(args[0]=="child") { Thread.Sleep(60000); return 0; }
    ProcessStartInfo start=new ProcessStartInfo();
    start.FileName=Process.GetCurrentProcess().MainModule.FileName;
    start.Arguments="child"; start.UseShellExecute=false;
    Process child=Process.Start(start);
    File.WriteAllText(args[1], Process.GetCurrentProcess().Id+","+child.Id);
    if(args[0]=="exit" || (args.Length>2 && args[2]=="exit")) return 0;
    Thread.Sleep(60000); return 0;
  }
}
"""
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop';Add-Type -TypeDefinition ([Console]::In.ReadToEnd()) "
            f"-OutputAssembly {_ps_literal(output)} -OutputType ConsoleApplication",
        ],
        input=source,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def _assert_owned_processes_stopped(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    for pid in map(int, path.read_text().split(",")):
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 5000) == 0, (
                    f"owned process {pid} survived"
                )
            finally:
                kernel.CloseHandle(handle)


def _contained_process_loader() -> str:
    return _bounded_ast_loader(
        "Initialize-UpgradeProcessJob", "Stop-StartedProcess", "Invoke-ContainedUpgradeProcess"
    )


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("failure", ["exit", "timeout", "lock"])
def test_contained_process_stops_real_parent_and_child(
    executable, failure, upgrade_process_stub, tmp_path
):
    pids = tmp_path / "owned-pids.txt"
    source = f"""
$ErrorActionPreference='Stop';$LeaseSeconds=120;$AcquiredLocks=@(1)
{_contained_process_loader()}
function Assert-LocksOwned {{
  if ({_ps_literal(failure)} -ceq 'lock' -and (Test-Path -LiteralPath {_ps_literal(pids)})) {{ throw 'upgrade_lock_lost' }}
}}
function Renew-Locks {{ Assert-LocksOwned }}
try {{ Invoke-ContainedUpgradeProcess {_ps_literal(upgrade_process_stub)} @({_ps_literal(failure)}, {_ps_literal(pids)}) 8; 'complete' }}
catch {{ $_.Exception.Message }}
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == {
            "exit": "docker_process_tree_incomplete",
            "timeout": "docker_command_timeout",
            "lock": "upgrade_lock_lost",
        }[failure]
    )
    assert pids.is_file()
    _assert_owned_processes_stopped(pids)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_contained_process_parent_death_closes_entire_job(
    executable, upgrade_process_stub, tmp_path
):
    pids = tmp_path / "owned-pids.txt"
    source = f"""
$ErrorActionPreference='Stop';$LeaseSeconds=120;$AcquiredLocks=@()
{_contained_process_loader()}
Invoke-ContainedUpgradeProcess {_ps_literal(upgrade_process_stub)} @('wait',{_ps_literal(pids)}) 30
"""
    process = subprocess.Popen(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(source.encode("utf-16le")).decode(),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_windows_powershell_env(),
    )
    try:
        deadline = time.monotonic() + 15
        while not pids.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.1)
        assert pids.exists(), process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    _assert_owned_processes_stopped(pids)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("utf8_stdin", [False, True])
def test_contained_process_preserves_long_unicode_empty_and_quoted_arguments(
    executable, utf8_stdin, upgrade_process_stub
):
    # The real SSH bootstrap sets UTF-8 input; on Windows PowerShell this makes
    # Process.StandardInput use a writer that prefixes its stream with a BOM.
    input_setup = "[Console]::InputEncoding=[Text.Encoding]::UTF8" if utf8_stdin else ""
    source = rf"""
$ErrorActionPreference='Stop';$LeaseSeconds=120;$AcquiredLocks=@()
{input_setup}
{_contained_process_loader()}
$long=([string][char]0x6c5f)*14000
Invoke-ContainedUpgradeProcess {_ps_literal(upgrade_process_stub)} @('args',$long,'','a "quoted" value','C:\path with space\') 10
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert [base64.b64decode(line).decode() for line in result.stdout.splitlines()] == [
        "args",
        "江" * 14000,
        "",
        'a "quoted" value',
        "C:\\path with space\\",
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_contained_process_stalled_stdin_write_is_bounded(executable, upgrade_process_stub):
    source = f"""
$ErrorActionPreference='Stop';$LeaseSeconds=120;$AcquiredLocks=@()
{_contained_process_loader()}
$body=${{function:Invoke-ContainedUpgradeProcess}}.ToString().Replace('$text=[Console]::In.ReadToEnd()', 'Start-Sleep -Seconds 60; $text=[Console]::In.ReadToEnd()')
Set-Item Function:Invoke-ContainedUpgradeProcess ([ScriptBlock]::Create($body))
try {{ Invoke-ContainedUpgradeProcess {_ps_literal(upgrade_process_stub)} @('args',('x'*14000)) 2 }} catch {{ $_.Exception.Message }}
"""
    start = time.monotonic()
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "docker_command_timeout"
    assert time.monotonic() - start < 15


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_startup_hosts_require_disabled_startup_code_and_explicit_input(executable):
    launcher = r"C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1"
    rejected = [
        ("powershell.exe", '-Command "Write-Output ready"'),
        ("pwsh.exe", '-Command "Write-Output ready"'),
        ("powershell.exe", f'-File "{launcher}"'),
        ("cmd.exe", "/c echo ready"),
        ("powershell.exe", '-Command "Write-Output -NoProfile"'),
        ("powershell.exe", f'-File "{launcher}" -NoProfile'),
        ("cmd.exe", "/c echo /d"),
        ("cmd.exe", "/d /k echo ready"),
        ("powershell.exe", '-NoProfile -NoExit -Command "Write-Output ready"'),
        ("pwsh.exe", f'-NoProfile -NoExit -File "{launcher}"'),
        ("powershell.exe", "-NoProfile"),
        ("pwsh.exe", "-NoProfile -NonInteractive"),
        ("powershell.exe", "-NoProfile -Command -"),
        ("pwsh.exe", "-NoProfile -File -"),
        (
            "powershell.exe",
            "-NoProfile -Command \"Write-Output 'docker start ruisheng-api' | powershell.exe -NoProfile\"",
        ),
        (
            "pwsh.exe",
            "-NoProfile -Command \"Write-Output 'docker start ruisheng-api' | pwsh.exe -NoProfile\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process powershell.exe '-Command Write-Output ready'\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"[Diagnostics.Process]::Start('cmd.exe','/c echo ready')\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"iex 'powershell.exe -Command Write-Output ready'\"",
        ),
        ("cmd.exe", "/d /c powershell.exe -Command Write-Output ready"),
        (
            "python.exe",
            "-c \"import subprocess; subprocess.run(['docker.exe','start','ruisheng-api'])\"",
        ),
        ("wsl.exe", '--distribution Ubuntu --exec sh -c "docker start ruisheng-api"'),
        ("bash.exe", '-c "docker start ruisheng-api"'),
        ("cscript.exe", r"C:\Unknown\startup.vbs"),
        ("docker.exe", "start ruisheng-api"),
    ]
    for host in (
        "python",
        "python3",
        "python3.12",
        "pythonw",
        "py",
        "pyw",
        "wsl",
        "bash",
        "sh",
        "zsh",
        "node",
        "perl",
        "ruby",
        "cscript",
        "mshta",
    ):
        rejected.extend(
            [
                (host + ".exe", ""),
                (rf"%localappdata%\Programs\{host}.exe", ""),
                (rf"%ProgramFiles%\Runtime\{host}.exe", ""),
                ("powershell.exe", f'-NoProfile -Command "Start-Process {host}.exe"'),
            ]
        )
    encoded = base64.b64encode("Write-Output ready".encode("utf-16le")).decode()
    rejected.append(("powershell.exe", f"-EncodedCommand {encoded}"))
    accepted = [
        ("docker.exe", "compose ps"),
        ("powershell.exe", f'-NoProfile -File "{launcher}"'),
        ("powershell.exe", '-NoProfile -Command "Write-Output ready"'),
        ("cmd.exe", "/d /c echo ready"),
        ("cmd.exe", '/q /D /s /c "echo ready"'),
        ("powershell.exe", '-NoP -C "Write-Output ready"'),
        ("powershell.exe", f"-NoProfile -EncodedCommand {encoded}"),
        ("powershell.exe", "-NoProfile -Command \"iex 'Write-Output ready'\""),
        ("powershell.exe", "-NoProfile -Command \"Set-Alias d docker.exe; iex 'd compose ps'\""),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process powershell.exe '-NoProfile -Command Write-Output ready'\"",
        ),
        (r"%localappdata%\Microsoft\OneDrive\OneDriveStandaloneUpdater.exe", "/reporting"),
    ]
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Test-UnprotectedStartupAction")}
$actions=ConvertFrom-Json {_ps_literal(json.dumps(rejected + accepted))}
$result=@(foreach ($entry in $actions) {{
  try {{ Test-UnprotectedStartupAction $entry[0] $entry[1] {_ps_literal(launcher)} }}
  catch {{ if ($_.Exception.Message -cne 'startup_task_arguments_invalid') {{ throw }}; $true }}
}})
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    expected = [True] * len(rejected) + [False] * len(accepted)
    observed = json.loads(result.stdout)
    assert observed == expected, [
        action
        for action, actual, target in zip(rejected + accepted, observed, expected, strict=True)
        if actual != target
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_forfiles_startup_tasks_are_rejected_by_classifier_and_installed_guard(
    executable, tmp_path
):
    # All command bodies remain classification data. Real NTFS guard files are
    # inspected, but no scheduled action, launcher or payload is executed.
    payload = '/C "cmd /d /c docker.exe start ruisheng-api"'
    rejected = [
        (host, payload)
        for host in (
            "forfiles",
            "forfiles.exe",
            "FORFILES.EXE",
            r"C:\Windows\System32\forfiles",
            r"C:\Windows\System32\forfiles.exe",
            r'"C:\Windows\System32\FoRfIlEs.ExE"',
            r"%SystemRoot%\System32\forfiles.exe",
            r"C:\Users\Example\AppData\Local\Tools\forfiles.exe",
            r"%localappdata%\Tools\forfiles.exe",
            r"%ProgramFiles%\Tools\forfiles.exe",
            r'"%ProgramFiles(x86)%\Tools\FORFILES.EXE"',
        )
    ]
    rejected.extend(
        [
            ("forfiles.exe", ""),
            ("forfiles.exe", '/C "cmd /d /c echo ready"'),
            ("cmd.exe", f"/d /c forfiles.exe {payload}"),
            ("cmd.exe", f"/d /c forfiles {payload}"),
        ]
    )
    for body in (
        "forfiles.exe /C 'cmd /d /c docker.exe start ruisheng-api'",
        "& 'C:\\Windows\\System32\\FORFILES.EXE' /C 'cmd /d /c docker.exe start ruisheng-api'",
        "Start-Process forfiles.exe -ArgumentList '/C', 'cmd /d /c docker.exe start ruisheng-api'",
        "[Diagnostics.Process]::Start('forfiles.exe', '/C cmd /d /c docker.exe start ruisheng-api')",
    ):
        encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
        rejected.append(("powershell.exe", f"-NoProfile -EncodedCommand {encoded}"))
    accepted = [
        ("docker.exe", "ps"),
        ("docker.exe", "compose ps"),
        (r"C:\Windows\System32\cleanmgr.exe", "/d %UNRELATED_NATIVE_ARGUMENT%"),
        (r"%localappdata%\Microsoft\OneDrive\OneDriveStandaloneUpdater.exe", "/reporting"),
        ("powershell.exe", '-NoProfile -Command "Write-Output forfiles.exe"'),
        ("powershell.exe", '-NoProfile -File "__GUARD_LAUNCHER__"'),
    ]
    source = rf"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Assert-InstalledMaintenanceGuards", "Test-UnprotectedStartupAction", "Test-ExactKeys")}
$root=Join-Path $env:SystemDrive ('rs-r9-forfiles-'+[Guid]::NewGuid().ToString('N'))
$launcherRoot=Join-Path $root 'Launcher'
$launcher=Join-Path $launcherRoot 'start_ruisheng_local.ps1'
$receiptPath=Join-Path $launcherRoot 'schema-upgrade-guard.json'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Protect-Fixture([string]$Path) {{
  $item=Get-Item -LiteralPath $Path -Force
  $acl=if ($item.PSIsContainer) {{ New-Object Security.AccessControl.DirectorySecurity }} else {{ New-Object Security.AccessControl.FileSecurity }}
  $acl.SetAccessRuleProtection($true,$false)
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))
  if ($PSVersionTable.PSEdition -eq 'Core') {{ [IO.FileSystemAclExtensions]::SetAccessControl($item,$acl) }}
  else {{ $item.SetAccessControl($acl) }}
}}
foreach ($path in @($root,$launcherRoot)) {{ [void](New-Item -ItemType Directory -Path $path); Protect-Fixture $path }}
[IO.File]::WriteAllText($launcher,"Write-Output 'inert fixture; never executed'"); Protect-Fixture $launcher
$receipt=[ordered]@{{schema_version=1;guard_version='bounded-schema-v1';launcher=$launcher;
  launcher_sha256=(Get-FileHash -LiteralPath $launcher).Hash.ToLowerInvariant();installed_at='2026-09-10T00:00:00Z'}}
[IO.File]::WriteAllText($receiptPath,($receipt | ConvertTo-Json -Compress)); Protect-Fixture $receiptPath
$expectedReceipt=(Get-FileHash -LiteralPath $receiptPath).Hash.ToLowerInvariant()
# Only the fixed installation paths, fixture owner and task enumeration change.
$body=${{function:Assert-InstalledMaintenanceGuards}}.ToString().Replace('C:\Program Files\Ruisheng\Launcher',$launcherRoot).Replace('@("S-1-5-18", "S-1-5-32-544")',('@("'+$sid+'", "S-1-5-18", "S-1-5-32-544")'))
Set-Item Function:Assert-InstalledMaintenanceGuards ([ScriptBlock]::Create($body))
$body=${{function:Assert-StartupScriptIdentity}}.ToString().Replace("@('S-1-5-18'","@('$sid','S-1-5-18'")
Set-Item Function:Assert-StartupScriptIdentity ([ScriptBlock]::Create($body))
function Get-ScheduledTask {{ [PSCustomObject]@{{ Actions=@($script:taskAction) }} }}
$actions=ConvertFrom-Json {_ps_literal(json.dumps(rejected + accepted))}
$observed=@(foreach ($entry in $actions) {{
  $execute=[string]$entry[0]; $arguments=([string]$entry[1]).Replace('__GUARD_LAUNCHER__',$launcher)
  try {{ $classified=Test-UnprotectedStartupAction $execute $arguments $launcher }}
  catch {{ if ($_.Exception.Message -cne 'startup_task_arguments_invalid') {{ throw }}; $classified=$true }}
  $script:taskAction=[PSCustomObject]@{{Execute=$execute;Arguments=$arguments;WorkingDirectory=''}}
  try {{
    $actualReceipt=Assert-InstalledMaintenanceGuards
    if ($actualReceipt -cne $expectedReceipt) {{ throw 'fixture_receipt_binding_changed' }}
    $guardRejected=$false
  }} catch {{
    if ($_.Exception.Message -cnotin @('unprotected_startup_task_present','startup_task_arguments_invalid')) {{ throw }}
    $guardRejected=$true
  }}
  [ordered]@{{classifier=$classified;guard=$guardRejected}}
}})
@{{fixture_root=$root;observed=$observed}} | ConvertTo-Json -Depth 4 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    (tmp_path / "retained-forfiles-guard-fixture.json").write_text(
        json.dumps(observed), encoding="utf-8"
    )
    expected = [True] * len(rejected) + [False] * len(accepted)
    assert observed["observed"] == [{"classifier": value, "guard": value} for value in expected], [
        {"action": action, "actual": actual, "expected": target}
        for action, actual, target in zip(
            rejected + accepted, observed["observed"], expected, strict=True
        )
        if actual != {"classifier": target, "guard": target}
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_scheduled_startup_actions_parse_executable_wrappers_and_arguments(executable):
    source = rf"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Test-UnprotectedStartupAction")}
$launcher='C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1'
$actions=@(
  @('docker','compose up -d'), @('docker.exe','compose up -d'),
  @('C:\Program Files\Docker\Docker\resources\bin\docker.exe','compose --project-name prod up -d'),
  @('cmd.exe','/d /c "docker.exe compose up -d"'),
  @('cmd.exe','/d /c "C:\Program Files\Docker\docker.exe" compose up -d'),
  @('powershell.exe','-NoProfile -Command "& ''C:\Program Files\Docker\docker.exe'' compose up -d"'),
  @('powershell.exe','-NoProfile -Command "Start-Process -FilePath ''C:\Program Files\Docker\docker.exe'' -ArgumentList ''compose up -d''"'),
  @('pwsh.exe',('-NoProfile -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes('docker.exe compose up -d')))),
  @('powershell.exe','-NoProfile -File "D:\old\start_ruisheng_local.ps1"'),
  @('powershell.exe',('-NoProfile -File "'+$launcher+'"')),
  @('C:\Windows\System32\cleanmgr.exe','/autoclean'), @('docker.exe','compose ps'),
  @('powershell.exe','-NoProfile -Command "Write-Output ''docker.exe compose up -d''"')
)
$result=@(foreach ($entry in $actions) {{ Test-UnprotectedStartupAction $entry[0] $entry[1] $launcher }})
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [True] * 9 + [False] * 4


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_startup_compounds_abbreviations_and_process_argument_binding(executable):
    rejected = [
        ("cmd.exe", '/d /c "echo starting & docker.exe compose up -d"'),
        ("cmd.exe", '/d /s /d /c "echo starting && docker.exe compose up -d"'),
        ("cmd.exe", '/d /c "echo starting || docker.exe start ruisheng-api"'),
        ("cmd.exe", '/d /c "echo starting | docker.exe compose up -d"'),
        ("cmd.exe", '/d /c ">nul echo starting & 2>nul docker.exe compose up -d"'),
        ("cmd.exe", '/d /c "echo starting 2>&1 & (docker.exe compose up -d)"'),
        ("cmd.exe", '/d /c "call docker.exe compose up -d"'),
        ("cmd.exe", '/d /c "start /b docker.exe compose up -d"'),
        ("cmd.exe", '/d /c start "Upgrade task" /b docker.exe compose up -d'),
        ("cmd.exe", '/d /c start /d "D:\\app" "Upgrade task" docker.exe compose up -d'),
        ("cmd.exe", '/d /c "echo starting & do^cker.exe compose up -d"'),
        ("cmd.exe", '/d /c "powershell.exe -NoProfile -f D:\\old\\start_ruisheng_local.ps1"'),
        ("powershell.exe", '-NoProfile -f "D:\\old\\start_ruisheng_local.ps1"'),
        ("powershell.exe", '-NoProfile -Fi "D:\\old\\start_ruisheng_local.ps1"'),
        ("powershell.exe", '-NoProfile -Co "docker.exe compose up -d"'),
        ("powershell.exe", "-NoProfile -Command \"Start-Process docker.exe 'compose up -d'\""),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process -FilePath docker.exe 'compose up -d'\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process -ArgumentList 'compose up -d' docker.exe\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process -FilePath:docker.exe -ArgumentList:'compose up -d'\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"Microsoft.PowerShell.Management\\Start-Process docker.exe 'compose up -d'\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process -File docker.exe -Args 'compose','up','-d'\"",
        ),
        ("powershell.exe", '-NoProfile -Command "Start-Process docker.exe $unknownArguments"'),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process $unknownExecutable 'compose up -d'\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process cmd.exe '/d /c echo starting & docker.exe compose up -d'\"",
        ),
        ("powershell.exe", '-NoProfile -Command "& docker.exe compose $unknownCommand"'),
        ("powershell.exe", '-NoProfile -Command "Set-Alias d docker.exe; d compose up -d"'),
        (
            "powershell.exe",
            '-NoProfile -Command "Set-Alias -Name:d -Value:docker.exe; d compose up -d"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "Set-Alias d docker.exe -Scope Script; d compose up -d"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "Set-Alias d docker.exe; Set-Alias e d; e compose up -d"',
        ),
        ("powershell.exe", "-NoProfile -Command \"Set-Alias d docker.exe; iex 'd compose up -d'\""),
        ("powershell.exe", "-NoProfile -Command \"Invoke-Expression 'docker.exe compose up -d'\""),
        ("powershell.exe", "-NoProfile -Command \"iex -Command:'docker.exe compose up -d'\""),
        (
            "powershell.exe",
            "-NoProfile -Command \"[Diagnostics.Process]::Start('docker.exe','compose up -d')\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"[System.Diagnostics.Process]::Start('docker.exe',$arguments)\"",
        ),
        ("powershell.exe", '-NoProfile -f "D:\\app\\remote_maintenance.ps1" -Action Status'),
    ]
    accepted = [
        ("cmd.exe", '/d /c echo "docker.exe compose up -d"'),
        ("cmd.exe", '/d /c "echo docker.exe compose up -d ^& harmless"'),
        ("cmd.exe", '/d /c "echo starting & docker.exe compose ps"'),
        ("cmd.exe", '/d /c echo text > "docker.exe compose up -d"'),
        ("cmd.exe", '/d /c "echo docker.exe compose up -d 2>&1"'),
        ("cmd.exe", '/d /c start "docker.exe compose up -d" cleanmgr.exe /autoclean'),
        ("powershell.exe", "-NoProfile -Command \"Start-Process docker.exe 'compose ps'\""),
        (
            "powershell.exe",
            "-NoProfile -Command \"Start-Process -FilePath:cleanmgr.exe -ArgumentList:'/autoclean'\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"Write-Output 'Start-Process docker.exe compose up -d'\"",
        ),
        ("powershell.exe", "-NoProfile -Command \"& { Write-Output 'docker.exe compose up -d' }\""),
        ("powershell.exe", '-NoProfile -Command "Set-Alias d docker.exe; d compose ps"'),
        ("powershell.exe", "-NoProfile -Command \"Invoke-Expression 'docker.exe compose ps'\""),
        (
            "powershell.exe",
            "-NoProfile -Command \"[Diagnostics.Process]::Start('docker.exe','compose ps')\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"[Diagnostics.Process]::Start('cleanmgr.exe','/autoclean')\"",
        ),
        ("docker.exe", 'inspect --format "up" ruisheng-api'),
        (
            "powershell.exe",
            '-NoProfile -f "C:\\Program Files\\Ruisheng\\Launcher\\start_ruisheng_local.ps1"',
        ),
    ]
    encoded_command = base64.b64encode("docker.exe compose up -d".encode("utf-16le")).decode()
    rejected.extend(
        ("powershell.exe", f"-NoProfile -{option} {encoded_command}")
        for option in ("e", "en", "enco", "EncodedC")
    )
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Test-UnprotectedStartupAction")}
$actions=ConvertFrom-Json {_ps_literal(json.dumps(rejected + accepted))}
$result=@(foreach ($entry in $actions) {{ Test-UnprotectedStartupAction $entry[0] $entry[1] 'C:\\Program Files\\Ruisheng\\Launcher\\start_ruisheng_local.ps1' }})
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [True] * len(rejected) + [False] * len(accepted)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_startup_unknown_scripts_environment_and_host_defaults(executable, tmp_path):
    # These files are classification inputs only: their contents must never run.
    scripts = []
    for name in ("resume.ps1", "resume.cmd", "resume.bat", "remote_maintenance.ps1"):
        script = tmp_path / name
        script.write_text("docker.exe compose up -d", encoding="utf-8")
        scripts.append(script)
    rejected = [(str(script), "-Action Status") for script in scripts]
    rejected += [
        ("powershell.exe", f'-NoProfile -File "{scripts[0]}"'),
        ("powershell.exe", f"-NoProfile -Command \"& '{scripts[0]}'\""),
        ("powershell.exe", f"-NoProfile -Command \"Start-Process '{scripts[0]}' $arguments\""),
        ("cmd.exe", f'/d /c call "{scripts[1]}"'),
        ("cmd.exe", "/d /c call .\\relative-resume.cmd"),
        ("cmd.exe", f'/d /c "{scripts[1].with_suffix("")}"'),
        ("cmd.exe", "/d /c .\\relative-resume"),
        ("cmd.exe", "/d /c D:\\app\\resume.cmd."),
        ("%ComSpec%", '/d /c "docker.exe compose up -d"'),
        ("cmd.exe", '/d /c "%ComSpec% /d /c docker.exe compose up -d"'),
        ("cmd.exe", '/d /c "%R4_STARTUP_EXE% compose up -d"'),
        ("cmd.exe", '/d /c "echo starting %R4_STARTUP_SUFFIX%"'),
        ("cmd.exe", "/d /c %R4_STARTUP_COMMAND%"),
        ("%R4_UNDEFINED%", "/d /c docker.exe compose up -d"),
        ("cmd.exe", '/d /c "%R4_UNDEFINED% compose up -d"'),
        ("%R4_CYCLE%", "/d /c docker.exe compose up -d"),
        ("cmd.exe", '/v:on /d /c "!R4_STARTUP_EXE! compose up -d"'),
        ("cmd.exe", '/d /c "set R4_STARTUP_EXE=docker.exe & %R4_STARTUP_EXE% compose up -d"'),
        (
            "powershell.exe",
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass docker.exe compose up -d",
        ),
        (
            "powershell.exe",
            "-NoProfile -WindowStyle Hidden -InputFormat Text -OutputFormat Text docker.exe compose up -d",
        ),
        ("powershell.exe", '-NoProfile "docker.exe compose up -d"'),
        ("powershell.exe", "-NoProfile docker.exe compose up -d -f harmless.yml"),
        ("powershell.exe", "-NotAHostOption Write-Output harmless"),
        ("powershell.exe", "-ExecutionPolicy"),
        ("powershell.exe", "-File"),
        ("powershell.exe", "-Command"),
        ("pwsh.exe", f'-NoProfile "{scripts[0]}"'),
        ("pwsh.exe", "-NoProfile docker.exe compose ps"),
    ]
    accepted = [
        ("%SystemRoot%\\System32\\cleanmgr.exe", "/autoclean"),
        ("%ComSpec%", '/d /c "echo safe & docker.exe compose ps"'),
        ("cmd.exe", '/d /c "%ComSpec% /d /c docker.exe compose ps"'),
        ("cmd.exe", '/d /c "%SystemRoot%\\System32\\cleanmgr.exe /autoclean"'),
        (
            "powershell.exe",
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass Write-Output harmless",
        ),
        (
            "powershell.exe",
            "-NoProfile -WindowStyle Hidden -InputFormat Text -OutputFormat Text docker.exe compose ps",
        ),
        ("powershell.exe", "-NoProfile Write-Output file"),
        ("powershell.exe", "-NoProfile Write-Output command"),
        ("powershell.exe", "-NoProfile -Command \"Write-Output 'docker.exe compose up -d'\""),
        (
            "pwsh.exe",
            '-NoProfile "C:\\Program Files\\Ruisheng\\Launcher\\start_ruisheng_local.ps1"',
        ),
        ("pwsh.exe", '-NoProfile -Command "docker.exe compose ps"'),
    ]
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Expand-StartupEnvironment", "Assert-StartupScriptIdentity", "Test-ApprovedStartupScript", "Test-UnprotectedStartupAction")}
$env:R4_STARTUP_EXE='docker.exe'; $env:R4_STARTUP_SUFFIX='& docker.exe compose up -d'
$env:R4_STARTUP_COMMAND='echo ready & docker.exe start ruisheng-api'
$env:R4_UNDEFINED=$null; $env:R4_CYCLE='%R4_CYCLE%'
$actions=ConvertFrom-Json {_ps_literal(json.dumps(rejected + accepted))}
$result=@(foreach ($entry in $actions) {{
  try {{ Test-UnprotectedStartupAction $entry[0] $entry[1] 'C:\\Program Files\\Ruisheng\\Launcher\\start_ruisheng_local.ps1' }}
  catch {{ if ($_.Exception.Message -cne 'startup_task_arguments_invalid') {{ throw }}; $true }}
}})
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed == [True] * len(rejected) + [False] * len(accepted), [
        action
        for action, value, expected in zip(
            rejected + accepted,
            observed,
            [True] * len(rejected) + [False] * len(accepted),
            strict=True,
        )
        if value != expected
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_startup_powershell_proof_grammar_rejects_unresolved_execution(executable, tmp_path):
    # Only the classifier sees these command strings. Even the existing script
    # fixtures are never invoked; both hosts must refuse omitted extensions.
    for suffix in (".ps1", ".cmd", ".bat"):
        (tmp_path / f"wrapper{suffix}").write_text("docker start ruisheng-api", encoding="utf-8")
    rejected = [
        (
            "powershell.exe",
            "-NoProfile -Command \"([scriptblock]::Create('docker start ruisheng-api')).Invoke()\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"$p = New-Object Diagnostics.Process; $p.StartInfo.FileName = 'docker.exe'; $p.StartInfo.Arguments = 'start ruisheng-api'; $p.Start()\"",
        ),
        ("powershell.exe", '-NoProfile -Command "$p.Start()"'),
        ("powershell.exe", '-NoProfile -Command "[Diagnostics.Process]::new().Start()"'),
        ("powershell.exe", '-NoProfile -Command "[Diagnostics.Process]::Start($startInfo)"'),
        (
            "powershell.exe",
            "-NoProfile -Command \"[scriptblock]::Create('Write-Output harmless')\"",
        ),
        ("powershell.exe", '-NoProfile -Command "& .\\wrapper"'),
        ("powershell.exe", f"-NoProfile -Command \"& '{tmp_path / 'wrapper'}'\""),
        ("powershell.exe", f"-NoProfile -Command \"& '{tmp_path / 'missing-wrapper'}'\""),
        ("powershell.exe", '-NoProfile -Command "& { & .\\wrapper }"'),
        ("powershell.exe", '-NoProfile -Command "Invoke-NoProfile -Command -ScriptBlock $payload"'),
        ("powershell.exe", '-NoProfile -Command "Import-Module unknown; Write-Output harmless"'),
        (
            "powershell.exe",
            '-NoProfile -Command "function Write-Output { docker start ruisheng-api }; Write-Output harmless"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "if ($false) { Set-Alias docker Write-Output }; docker start ruisheng-api"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "& { Set-Alias docker Write-Output }; docker start ruisheng-api"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "docker start ruisheng-api; Set-Alias docker Write-Output"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "Set-Alias d docker -Option Constant; Set-Alias d Write-Output; d start ruisheng-api"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "New-Alias d docker; New-Alias d Write-Output; d start ruisheng-api"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "Set-Alias docker Write-Output > Z:\\missing\\file; docker start ruisheng-api"',
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"Set-Alias iex Write-Output; iex 'docker start ruisheng-api'\"",
        ),
        ("powershell.exe", '-NoProfile -Command "Set-Alias icm Write-Output; icm $payload"'),
        (
            "powershell.exe",
            '-NoProfile -Command "Set-Item function:Write-Output $payload; Write-Output harmless"',
        ),
        ("powershell.exe", '-NoProfile -Command "EvilModule\\Write-Output harmless"'),
        (
            "powershell.exe",
            '-NoProfile -Command "EvilModule\\Set-Alias d Write-Output; d harmless"',
        ),
        ("powershell.exe", '-NoProfile -Command "[SomeType]$payload"'),
        ("powershell.exe", '-NoProfile -Command "[SomeType]::DangerousProperty"'),
        ("powershell.exe", '-NoProfile -Command "Start-Process cscript.exe $arguments"'),
        ("powershell.exe", '-NoProfile -Command "Start-Process cleanmgr.exe $arguments"'),
        ("powershell.exe", '-NoProfile -Command "Start-Process .\\wrapper"'),
        ("powershell.exe", '-NoProfile -Command "Start-Process C:\\Unknown\\startup.vbs"'),
        ("powershell.exe", '-NoProfile -Command "Start-Process C:\\Unknown\\startup.hta"'),
        (
            "powershell.exe",
            "-NoProfile -Command \"[Diagnostics.Process]::Start('C:\\Unknown\\startup.vbs')\"",
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"[Diagnostics.Process]::Start('C:\\Unknown\\wrapper')\"",
        ),
        ("cmd.exe", "/d /c C:\\Unknown\\startup.vbs"),
        ("cmd.exe", "/d /c cscript.com C:\\Unknown\\startup.vbs"),
        ("powershell.exe", '-NoProfile -Command "Start-Process cleanmgr.exe -Verb RunAs"'),
        (
            "powershell.exe",
            "-NoProfile -Command \"[Diagnostics.Process]::Start(@('docker.exe'),@('start ruisheng-api'))\"",
        ),
        ("powershell.exe", '-NoProfile -Command "unknown-command harmless"'),
    ]
    for host, extension in (
        ("cscript.exe", "vbs"),
        ("wscript.exe", "vbs"),
        ("mshta.exe", "hta"),
        ("rundll32.exe", "dll"),
    ):
        rejected.extend(
            [
                (host, f"C:\\Unknown\\startup.{extension}"),
                (host.removesuffix(".exe"), f"C:\\Unknown\\startup.{extension}"),
                ("cmd.exe", f"/d /c {host} C:\\Unknown\\startup.{extension}"),
                (
                    "powershell.exe",
                    f"-NoProfile -Command \"Start-Process {host} 'C:\\Unknown\\startup.{extension}'\"",
                ),
                (
                    "powershell.exe",
                    f'-NoProfile -Command "{host} C:\\Unknown\\startup.{extension}"',
                ),
            ]
        )
    accepted = [
        (
            "powershell.exe",
            "-NoProfile -Command \"Write-Output '([scriptblock]::Create(payload)).Invoke()'\"",
        ),
        ("powershell.exe", '-NoProfile -Command "Write-Output $PSVersionTable"'),
        ("powershell.exe", '-NoProfile -Command "echo harmless"'),
        (
            "powershell.exe",
            '-NoProfile -Command "Microsoft.PowerShell.Utility\\Write-Output harmless"',
        ),
        (
            "powershell.exe",
            '-NoProfile -Command "Get-Date; Get-Process; Get-Service; Get-Location"',
        ),
        ("powershell.exe", '-NoProfile -Command "& { Write-Output harmless; docker compose ps }"'),
        ("powershell.exe", '-NoProfile -Command "Set-Alias d docker.exe; d compose ps"'),
        (
            "powershell.exe",
            '-NoProfile -Command "Set-Alias docker Write-Output; docker start ruisheng-api"',
        ),
        (
            "powershell.exe",
            "-NoProfile -Command \"[Diagnostics.Process]::Start('docker.exe','compose ps')\"",
        ),
        ("powershell.exe", "-NoProfile -Command \"Start-Process cleanmgr.exe '/autoclean'\""),
        (
            "powershell.exe",
            '-NoProfile -Command "& C:\\Windows\\System32\\cleanmgr.exe /autoclean"',
        ),
        (
            "powershell.exe",
            '-NoProfile -File "C:\\Program Files\\Ruisheng\\Launcher\\start_ruisheng_local.ps1"',
        ),
    ]
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Test-UnprotectedStartupAction")}
Set-Location -LiteralPath {_ps_literal(tmp_path)}
$actions=ConvertFrom-Json {_ps_literal(json.dumps(rejected + accepted))}
$result=@(foreach ($entry in $actions) {{ Test-UnprotectedStartupAction $entry[0] $entry[1] 'C:\\Program Files\\Ruisheng\\Launcher\\start_ruisheng_local.ps1' }})
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    expected = [True] * len(rejected) + [False] * len(accepted)
    observed = json.loads(result.stdout)
    assert observed == expected, [
        action
        for action, value, target in zip(rejected + accepted, observed, expected, strict=True)
        if value != target
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_startup_native_task_paths_do_not_become_shell_commands(executable):
    accepted = [
        (r"%localappdata%\Microsoft\OneDrive\OneDriveStandaloneUpdater.exe", "/reporting"),
        (r"%localappdata%\Microsoft\OneDrive\OneDriveStandaloneUpdater.exe", ""),
        (r"%LOCALAPPDATA%\Microsoft\OneDrive\OneDriveStandaloneUpdater.exe", ""),
        (r"%windir%\system32\cleanmgr.exe", "/autocleanstoragesense /d %systemdrive%"),
        ('"%ProgramFiles%\\Windows Media Player\\wmpnscfg.exe"', ""),
        (r"C:\Windows\System32\cleanmgr.exe", "/d %UNRELATED_NATIVE_ARGUMENT%"),
    ]
    rejected = [
        (r"%localappdata%\Microsoft\OneDrive\resume.cmd", ""),
        (r"%localappdata%\Microsoft\OneDrive\resume.ps1", "-Action Status"),
        (r"%localappdata%\Microsoft\OneDrive\docker.exe", "compose up -d"),
        (r"%ProgramFiles%\PowerShell\7\pwsh.exe", "-NoProfile -Command docker.exe compose up -d"),
        (r"%localappdata%\cmd.exe", "/d /c docker.exe compose up -d"),
        (r"%UNDEFINED_DIRECTORY%\OneDriveStandaloneUpdater.exe", ""),
        (r"%localappdata%\Microsoft\%EXECUTABLE%", ""),
        (r"%localappdata%\Microsoft\OneDriveStandaloneUpdater.exe %INJECTED%", ""),
        (r"%localappdata%\..\cmd.exe", "/d /c docker.exe compose up -d"),
        ('"%localappdata%\\OneDriveStandaloneUpdater.exe" /d /c "docker.exe"', "compose up -d"),
        ("cmd.exe", '/d /c "%localappdata%\\Microsoft\\OneDrive\\OneDriveStandaloneUpdater.exe"'),
        ("cmd.exe", '/d /c "cleanmgr.exe /d %UNRESOLVED_COMMAND%"'),
        ("powershell.exe", '-NoProfile -Command "Write-Output %UNRESOLVED_COMMAND%"'),
    ]
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Test-UnprotectedStartupAction")}
# A stale Administrator task must not borrow this verifier's LocalAppData.
$env:LOCALAPPDATA='%private-unresolved-value%'; $env:ProgramFiles='%private-unresolved-value%'
$actions=ConvertFrom-Json {_ps_literal(json.dumps(accepted + rejected))}
$result=@(foreach ($entry in $actions) {{
  try {{ Test-UnprotectedStartupAction $entry[0] $entry[1] 'C:\\Program Files\\Ruisheng\\Launcher\\start_ruisheng_local.ps1' }}
  catch {{ if ($_.Exception.Message -cne 'startup_task_arguments_invalid') {{ throw }}; $true }}
}})
ConvertTo-Json -InputObject $result -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [False] * len(accepted) + [True] * len(rejected)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_installed_maintenance_guard_proves_real_leaf_and_all_ancestors(executable, tmp_path):
    # Only fixed paths, the non-elevated fixture user's SID and task enumeration
    # are adapted. Both production guard functions inspect real NTFS objects.
    source = rf"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Assert-InstalledMaintenanceGuards", "Assert-StartupScriptIdentity", "Test-ExactKeys")}
$root=Join-Path $env:SystemDrive ('rs-r7-guard-'+[Guid]::NewGuid().ToString('N'))
$parents=@("$root\installation","$root\installation\Program Files","$root\installation\Program Files\Ruisheng","$root\installation\Program Files\Ruisheng\Launcher")
$launcherRoot=$parents[-1]
$launcher="$launcherRoot\start_ruisheng_local.ps1"
$receiptPath="$launcherRoot\schema-upgrade-guard.json"
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Protect-Fixture([string]$Path,[string]$Right='', [switch]$InheritOnly) {{
  $item=Get-Item -LiteralPath $Path -Force
  $acl=if ($item.PSIsContainer) {{ New-Object Security.AccessControl.DirectorySecurity }} else {{ New-Object Security.AccessControl.FileSecurity }}
  $acl.SetAccessRuleProtection($true,$false)
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))
  if ($Right -in @('1073741824','268435456')) {{
    $generic=if ($Right -eq '1073741824') {{ 'GW' }} else {{ 'GA' }}
    $acl.SetSecurityDescriptorSddlForm($acl.GetSecurityDescriptorSddlForm('Access')+"(A;;$generic;;;WD)",[Security.AccessControl.AccessControlSections]::Access)
  }} elseif ($Right) {{
    $inheritance=if ($InheritOnly) {{ 'ContainerInherit,ObjectInherit' }} else {{ 'None' }}
    $propagation=if ($InheritOnly) {{ 'InheritOnly' }} else {{ 'None' }}
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),[Security.AccessControl.FileSystemRights]$Right,$inheritance,$propagation,'Allow'))
  }}
  if ($PSVersionTable.PSEdition -eq 'Core') {{ [IO.FileSystemAclExtensions]::SetAccessControl($item,$acl) }}
  else {{ $item.SetAccessControl($acl) }}
}}
foreach ($path in @($root)+$parents) {{ [void](New-Item -ItemType Directory -Path $path); Protect-Fixture $path }}
$content="Write-Output 'inert guard fixture; never executed'"
[IO.File]::WriteAllText($launcher,$content); Protect-Fixture $launcher
$launcherHash=(Get-FileHash -LiteralPath $launcher).Hash.ToLowerInvariant()
$receipt=[ordered]@{{schema_version=1;guard_version='bounded-schema-v1';launcher=$launcher;launcher_sha256=$launcherHash;installed_at='2026-09-10T00:00:00Z'}}
$receiptText=$receipt | ConvertTo-Json -Compress
[IO.File]::WriteAllText($receiptPath,$receiptText); Protect-Fixture $receiptPath
$expected=(Get-FileHash -LiteralPath $receiptPath).Hash.ToLowerInvariant()
function Get-ScheduledTask {{ @() }}
$body=${{function:Assert-InstalledMaintenanceGuards}}.ToString().Replace('C:\Program Files\Ruisheng\Launcher',$launcherRoot)
Set-Item Function:Assert-InstalledMaintenanceGuards ([ScriptBlock]::Create($body))
$body=${{function:Assert-StartupScriptIdentity}}.ToString().Replace("@('S-1-5-18'","@('$sid','S-1-5-18'")
Set-Item Function:Assert-StartupScriptIdentity ([ScriptBlock]::Create($body))
function Observe {{ try {{ return Assert-InstalledMaintenanceGuards }} catch {{ return $_.Exception.Message }} }}
$results=[ordered]@{{}}
# Trust in the helper cannot broaden the original main guard's leaf owners.
Assert-StartupScriptIdentity $launcher $launcherHash
$results.original_leaf_owner_policy=Observe
$body=${{function:Assert-InstalledMaintenanceGuards}}.ToString().Replace('@("S-1-5-18", "S-1-5-32-544")',('@("'+$sid+'", "S-1-5-18", "S-1-5-32-544")'))
Set-Item Function:Assert-InstalledMaintenanceGuards ([ScriptBlock]::Create($body))
$results.protected_control=Observe
foreach ($index in 0..($parents.Count-1)) {{
  $parent=$parents[$index]
  foreach ($right in @('Delete','DeleteSubdirectoriesAndFiles','ChangePermissions','TakeOwnership','WriteAttributes','WriteExtendedAttributes','1073741824','268435456')) {{
    Protect-Fixture $parent $right
    $results["parent_${{index}}_${{right}}"]=Observe
    Protect-Fixture $parent
  }}
  Protect-Fixture $parent 'CreateFiles,CreateDirectories'
  $results["parent_${{index}}_unrelated_creation"]=Observe
  Protect-Fixture $parent 'FullControl' -InheritOnly
  $results["parent_${{index}}_inherit_only"]=Observe
  Protect-Fixture $parent
}}
foreach ($index in 0..1) {{
  $leaf=@($receiptPath,$launcher)[$index]
  foreach ($right in @('WriteData','Delete','ChangePermissions','TakeOwnership')) {{
    Protect-Fixture $leaf $right
    $results["leaf_${{index}}_${{right}}"]=Observe
    Protect-Fixture $leaf
  }}
  $retained="$root\retained-leaf-$index"
  Move-Item -LiteralPath $leaf -Destination $retained
  $results["leaf_${{index}}_missing"]=Observe
  [void](New-Item -ItemType Directory -Path $leaf); Protect-Fixture $leaf
  $results["leaf_${{index}}_directory"]=Observe
  # Retain the replacement and original; no recursive deletion follows links.
  Move-Item -LiteralPath $leaf -Destination "$root\retained-directory-$index"
  Move-Item -LiteralPath $retained -Destination $leaf
}}
[IO.File]::AppendAllText($launcher,' ')
$results.launcher_hash_drift=Observe
[IO.File]::WriteAllText($launcher,$content)
foreach ($field in @('schema_version','guard_version','launcher','launcher_sha256','extra')) {{
  $changed=$receiptText | ConvertFrom-Json
  if ($field -eq 'schema_version') {{ $changed.schema_version=2 }}
  elseif ($field -eq 'extra') {{ $changed | Add-Member NoteProperty extra 'unexpected' }}
  else {{ $changed.$field='changed' }}
  [IO.File]::WriteAllText($receiptPath,($changed | ConvertTo-Json -Compress))
  $results["receipt_${{field}}_invalid"]=Observe
}}
[IO.File]::WriteAllText($receiptPath,'not valid JSON')
Protect-Fixture $parents[0] 'Delete'
$results.path_rejected_before_receipt_consumption=Observe
Protect-Fixture $parents[0]
[IO.File]::WriteAllText($receiptPath,$receiptText)
$changed=$receiptText | ConvertFrom-Json
$changed.installed_at='2026-09-10T00:00:01Z'
[IO.File]::WriteAllText($receiptPath,($changed | ConvertTo-Json -Compress))
$results.receipt_hash_changes=(Observe) -cne $expected
[IO.File]::WriteAllText($receiptPath,$receiptText)
foreach ($index in 0..($parents.Count-1)) {{
  $parent=$parents[$index]
  $retained="$root\retained-parent-$index"
  if (-not $parent.StartsWith($root+'\') -or -not $retained.StartsWith($root+'\')) {{ throw 'fixture_scope_invalid' }}
  Move-Item -LiteralPath $parent -Destination $retained
  [void](New-Item -ItemType Junction -Path $parent -Target $retained)
  $results["parent_${{index}}_junction"]=Observe
  [IO.Directory]::Move($parent,"$root\retained-junction-$index")
  Move-Item -LiteralPath $retained -Destination $parent
}}
$results.restored_control=Observe
@{{fixture_root=$root;expected=$expected;results=$results}} | ConvertTo-Json -Depth 3 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    (tmp_path / "retained-installed-guard-fixture.json").write_text(
        json.dumps(observed), encoding="utf-8"
    )
    expected = observed["expected"]
    acl_error = "installed_maintenance_guards_acl_invalid"
    assertions = {
        "original_leaf_owner_policy": acl_error,
        "protected_control": expected,
        "restored_control": expected,
        "launcher_hash_drift": "installed_maintenance_guard_drift",
        "path_rejected_before_receipt_consumption": acl_error,
        "receipt_hash_changes": True,
    }
    for index in range(4):
        for right in (
            "Delete",
            "DeleteSubdirectoriesAndFiles",
            "ChangePermissions",
            "TakeOwnership",
            "WriteAttributes",
            "WriteExtendedAttributes",
            "1073741824",
            "268435456",
        ):
            assertions[f"parent_{index}_{right}"] = acl_error
        assertions[f"parent_{index}_junction"] = acl_error
        assertions[f"parent_{index}_unrelated_creation"] = expected
        assertions[f"parent_{index}_inherit_only"] = expected
    for index in range(2):
        for right in ("WriteData", "Delete", "ChangePermissions", "TakeOwnership"):
            assertions[f"leaf_{index}_{right}"] = acl_error
        for replacement in ("missing", "directory"):
            assertions[f"leaf_{index}_{replacement}"] = "installed_maintenance_guards_missing"
    for field in ("schema_version", "guard_version", "launcher", "launcher_sha256", "extra"):
        assertions[f"receipt_{field}_invalid"] = "installed_maintenance_guards_invalid"
    assert observed["results"] == assertions


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_startup_approved_helpers_require_real_file_and_ancestor_protection(executable, tmp_path):
    # No elevation is needed: only these test-local identity constants are rebased.
    # The production path/owner/hash policy and all real ACL checks stay explicit.
    source = rf"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Test-UnprotectedStartupAction")}
$root=Join-Path $env:SystemDrive ('rs-r4-startup-'+[Guid]::NewGuid().ToString('N'))
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function New-FixtureAcl([string]$Path) {{
  $item=Get-Item -LiteralPath $Path
  $acl=if ($item.PSIsContainer) {{ New-Object Security.AccessControl.DirectorySecurity }} else {{ New-Object Security.AccessControl.FileSecurity }}
  $acl.SetAccessRuleProtection($true,$false)
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))
  return $acl
}}
function Protect-Fixture([string]$Path) {{
  Set-FixtureAcl $Path (New-FixtureAcl $Path)
}}
function Set-FixtureAcl([string]$Path,$Acl) {{
  $item=Get-Item -LiteralPath $Path
  if ($PSVersionTable.PSEdition -eq 'Core') {{ [IO.FileSystemAclExtensions]::SetAccessControl($item,$Acl) }}
  else {{ $item.SetAccessControl($Acl) }}
}}
foreach ($path in @($root,"$root\tools","$root\site","$root\other")) {{
  [void](New-Item -ItemType Directory -Path $path); Protect-Fixture $path
}}
$docker="$root\tools\start-docker-ruisheng.ps1"; $serial="$root\tools\serial_hardware_attach.ps1"
$hotpatch="$root\tools\hpatchmonTask.cmd"
$content="Write-Output 'fixture classification only'"
foreach ($path in @($docker,$serial,$hotpatch,"$root\other\hpatchmonTask.cmd","$root\other\start-docker-ruisheng.ps1")) {{
  [IO.File]::WriteAllText($path,$content); Protect-Fixture $path
}}
$hash=(Get-FileHash -LiteralPath $docker).Hash.ToLowerInvariant()
$results=[ordered]@{{}}
try {{ Assert-StartupScriptIdentity $docker $hash; $results.production_owner_rejected=$false }} catch {{ $results.production_owner_rejected=$true }}
$body=${{function:Test-ApprovedStartupScript}}.ToString().Replace('C:\Ruisheng',$root).Replace('C:\Windows\System32\hpatchmonTask.cmd',$hotpatch).Replace(
  '63806068e41fc1c3521a540c130a0eab747d99d9e0c8a3ada06a9e6a0569c388',$hash).Replace(
  '429cb0902fa6a24aa604b30ac9e0d53dc37575afb576159d9cd3176017be38ac',$hash).Replace(
  '5347ad556fbc6bb1faf408b466b6bd10180b9299923325794e9e5afb0118408f',$hash)
Set-Item Function:Test-ApprovedStartupScript ([ScriptBlock]::Create($body))
$body=${{function:Assert-StartupScriptIdentity}}.ToString().Replace("@('S-1-5-18'","@('$sid','S-1-5-18'")
Set-Item Function:Assert-StartupScriptIdentity ([ScriptBlock]::Create($body))
function Allowed([string]$Path,[string[]]$Values=@()) {{
  return -not (Test-UnprotectedStartupAction $Path '' 'C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1' -ParsedArguments $Values)
}}
$results.docker_allowed=Allowed $docker
$results.serial_allowed=Allowed $serial @('-ConfigPath',"$root\site\serial-hardware.json")
$results.hotpatch_allowed=Allowed $hotpatch
$results.hotpatch_cmd_allowed=-not (Test-UnprotectedStartupAction 'cmd.exe' ('/d /c "'+$hotpatch+'"') '')
$results.hotpatch_arguments_rejected=-not (Allowed $hotpatch @('/extra'))
$results.hotpatch_other_path_rejected=-not (Allowed "$root\other\hpatchmonTask.cmd")
[IO.File]::AppendAllText($hotpatch,' ')
$results.hotpatch_byte_drift_rejected=-not (Allowed $hotpatch)
[IO.File]::WriteAllText($hotpatch,$content)
Move-Item -LiteralPath $hotpatch -Destination "$root\retained-hotpatch.cmd"
$results.hotpatch_missing_rejected=-not (Allowed $hotpatch)
Move-Item -LiteralPath "$root\retained-hotpatch.cmd" -Destination $hotpatch
$acl=New-FixtureAcl $hotpatch
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'WriteData','Allow'))
Set-FixtureAcl $hotpatch $acl
$results.hotpatch_file_write_rejected=-not (Allowed $hotpatch)
Protect-Fixture $hotpatch
$results.same_name_rejected=-not (Allowed "$root\other\start-docker-ruisheng.ps1")
$results.parameters_rejected=-not (Allowed $docker @('-Command','docker compose up'))
$results.config_rejected=-not (Allowed $serial @('-ConfigPath',"$root\site\other.json"))
$results.extra_switch_rejected=-not (Allowed $serial @('-ConfigPath',"$root\site\serial-hardware.json",'-RunOnce'))
[IO.File]::AppendAllText($docker,' ')
$results.byte_drift_rejected=-not (Allowed $docker)
[IO.File]::WriteAllText($docker,$content)
Move-Item -LiteralPath $docker -Destination "$root\retained-helper.ps1"
$results.missing_rejected=-not (Allowed $docker)
Move-Item -LiteralPath "$root\retained-helper.ps1" -Destination $docker
$acl=New-FixtureAcl $docker
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'WriteData','Allow'))
Set-FixtureAcl $docker $acl
$results.file_write_rejected=-not (Allowed $docker)
Protect-Fixture $docker
foreach ($right in @('Delete','DeleteSubdirectoriesAndFiles','ChangePermissions','TakeOwnership')) {{
  $acl=New-FixtureAcl "$root\tools"
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),$right,'Allow'))
  Set-FixtureAcl "$root\tools" $acl
  $results["ancestor_${{right}}_rejected"]=-not (Allowed $docker)
  Protect-Fixture "$root\tools"
}}
$acl=New-FixtureAcl "$root\tools"
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'CreateDirectories, CreateFiles','Allow'))
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Delete','ContainerInherit','InheritOnly','Allow'))
Set-FixtureAcl "$root\tools" $acl
$results.unrelated_creation_allowed=Allowed $docker
Protect-Fixture "$root\tools"
Move-Item -LiteralPath "$root\tools" -Destination "$root\retained-tools"
[void](New-Item -ItemType Junction -Path "$root\tools" -Target "$root\retained-tools")
$results.ancestor_link_rejected=-not (Allowed $docker)
$results.hotpatch_ancestor_link_rejected=-not (Allowed $hotpatch)
@{{fixture_root=$root;results=$results}} | ConvertTo-Json -Depth 3 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    (tmp_path / "retained-startup-identity-fixture.json").write_text(
        json.dumps(observed), encoding="utf-8"
    )
    assert all(observed["results"].values()), observed


def test_startup_auxiliary_allowlist_is_fixed_to_reviewed_deployment_facts():
    body = _bounded_function("Test-ApprovedStartupScript")
    assert r"C:\Ruisheng\tools\start-docker-ruisheng.ps1" in body
    assert r"C:\Ruisheng\tools\serial_hardware_attach.ps1" in body
    assert r"C:\Ruisheng\site\serial-hardware.json" in body
    assert "63806068e41fc1c3521a540c130a0eab747d99d9e0c8a3ada06a9e6a0569c388" in body
    assert "429cb0902fa6a24aa604b30ac9e0d53dc37575afb576159d9cd3176017be38ac" in body
    assert r"C:\Windows\System32\hpatchmonTask.cmd" in body
    assert "5347ad556fbc6bb1faf408b466b6bd10180b9299923325794e9e5afb0118408f" in body


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_startup_windows_native_host_requires_fixed_identity_and_entrypoint(executable, tmp_path):
    # Fixed production identity constants alone are rebased to isolated inert
    # fixtures; all classifier, file/ancestor ACL, size and hash checks execute.
    source = rf"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Initialize-UpgradeProcessJob", "Test-UnprotectedStartupAction")}
$root=Join-Path $env:SystemDrive ('rs-r5-native-'+[Guid]::NewGuid().ToString('N'))
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Protect-Fixture([string]$Path,[string]$ExtraRight='') {{
  $item=Get-Item -LiteralPath $Path
  $acl=if ($item.PSIsContainer) {{ New-Object Security.AccessControl.DirectorySecurity }} else {{ New-Object Security.AccessControl.FileSecurity }}
  $acl.SetAccessRuleProtection($true,$false)
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))
  if ($ExtraRight) {{ $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),$ExtraRight,'Allow')) }}
  if ($PSVersionTable.PSEdition -eq 'Core') {{ [IO.FileSystemAclExtensions]::SetAccessControl($item,$acl) }} else {{ $item.SetAccessControl($acl) }}
}}
foreach ($path in @($root,"$root\System32","$root\other")) {{ [void](New-Item -ItemType Directory -Path $path); Protect-Fixture $path }}
$hostPath="$root\System32\rundll32.exe"
$content='inert native-host identity fixture' * 10000
$body=${{function:Test-ApprovedWindowsStartupHost}}.ToString()
$entries=@([regex]::Matches($body,"@\('([^']+)','([^']+)','([0-9a-f]{{64}})'\)"))
if ($entries.Count -ne 11) {{ throw 'fixed_entry_count' }}
foreach ($path in @($hostPath)+@($entries | ForEach-Object {{ "$root\System32\$($_.Groups[2].Value)" }})) {{
  [IO.File]::WriteAllText($path,$content); Protect-Fixture $path
}}
$fixtureHash=(Get-FileHash -LiteralPath $hostPath).Hash.ToLowerInvariant()
$body=$body.Replace('C:\Windows\System32',"$root\System32")
$body=[regex]::Replace($body,"'[0-9a-f]{{64}}'",("'"+$fixtureHash+"'"))
Set-Item Function:Test-ApprovedWindowsStartupHost ([ScriptBlock]::Create($body))
$results=[ordered]@{{}}
try {{ Assert-StartupScriptIdentity $hostPath $fixtureHash -FixedWindowsBinary; $results.production_owner_rejected=$false }} catch {{ $results.production_owner_rejected=$true }}
$body=${{function:Assert-StartupScriptIdentity}}.ToString().Replace("@('S-1-5-18'","@('$sid','S-1-5-18'")
Set-Item Function:Assert-StartupScriptIdentity ([ScriptBlock]::Create($body))
function Allowed([string]$Path,[string]$Arguments,[string]$WorkingDirectory='') {{
  -not (Test-UnprotectedStartupAction $Path $Arguments '' -WorkingDirectory $WorkingDirectory)
}}
$index=0
foreach ($entry in $entries) {{
  $arguments=$entry.Groups[1].Value.Replace('C:\Windows\System32',"$root\System32")
  $results["entry_$index"] = Allowed $hostPath $arguments
  $results["extra_arguments_$index"] = -not (Allowed $hostPath ($arguments+' extra'))
  $results["wrong_export_$index"] = -not (Allowed $hostPath ($arguments+'Changed'))
  $index++
}}
$arguments='Startupscan.dll,SusRunTask'; $dll="$root\System32\Startupscan.dll"
$results.working_directory_rejected=-not (Allowed $hostPath $arguments "$root\System32")
$results.relative_host_rejected=-not (Allowed 'rundll32.exe' $arguments)
$results.same_named_host_rejected=-not (Allowed "$root\other\rundll32.exe" $arguments)
$results.other_dll_path_rejected=-not (Allowed $hostPath ("$root\other\Startupscan.dll,SusRunTask"))
$results.export_case_rejected=-not (Allowed $hostPath 'Startupscan.dll,susruntask')
$results.quoted_dll_rejected=-not (Allowed $hostPath '"Startupscan.dll",SusRunTask')
$results.nested_rejected=Test-UnprotectedStartupAction $hostPath $arguments '' -Depth 1
$results.parsed_arguments_rejected=Test-UnprotectedStartupAction $hostPath '' '' -ParsedArguments @($arguments)
try {{ Assert-StartupScriptIdentity $dll $fixtureHash; $results.script_size_bound_preserved=$false }} catch {{ $results.script_size_bound_preserved=$true }}
foreach ($sidecar in @("$hostPath.local","$hostPath.manifest")) {{
  [IO.File]::WriteAllText($sidecar,'redirect'); Protect-Fixture $sidecar
  $results["sidecar_"+[IO.Path]::GetExtension($sidecar)]=-not (Allowed $hostPath $arguments)
  Move-Item -LiteralPath $sidecar -Destination ($sidecar+'.retained')
}}
[void](New-Item -ItemType Directory -Path "$hostPath.local"); Protect-Fixture "$hostPath.local"
$results.local_directory_rejected=-not (Allowed $hostPath $arguments)
Move-Item -LiteralPath "$hostPath.local" -Destination "$root\retained-local-directory"
foreach ($path in @($hostPath,$dll)) {{
  $label=[IO.Path]::GetFileName($path)
  [IO.File]::AppendAllText($path,'x')
  $results["byte_drift_$label"]=-not (Allowed $hostPath $arguments)
  [IO.File]::WriteAllText($path,$content)
  Protect-Fixture $path 'WriteData'
  $results["write_acl_$label"]=-not (Allowed $hostPath $arguments)
  Protect-Fixture $path
  Move-Item -LiteralPath $path -Destination ($path+'.retained')
  $results["missing_$label"]=-not (Allowed $hostPath $arguments)
  Move-Item -LiteralPath ($path+'.retained') -Destination $path
}}
[IO.File]::WriteAllBytes($dll,(New-Object byte[] (2MB+1)))
$results.binary_size_bound=-not (Allowed $hostPath $arguments)
try {{ Assert-StartupScriptIdentity $dll (Get-FileHash -LiteralPath $dll).Hash.ToLowerInvariant() -FixedWindowsBinary; $results.binary_size_independent_of_hash=$false }} catch {{ $results.binary_size_independent_of_hash=$true }}
[IO.File]::WriteAllText($dll,$content)
Protect-Fixture "$root\System32" 'DeleteSubdirectoriesAndFiles'
$results.ancestor_acl_rejected=-not (Allowed $hostPath $arguments)
Protect-Fixture "$root\System32"
foreach ($right in @('CreateFiles','CreateDirectories')) {{
  Protect-Fixture "$root\System32" $right
  $results["sidecar_creation_$right"]=-not (Allowed $hostPath $arguments)
  Protect-Fixture "$root\System32"
}}
Move-Item -LiteralPath "$root\System32" -Destination "$root\retained-System32"
[void](New-Item -ItemType Junction -Path "$root\System32" -Target "$root\retained-System32")
$results.ancestor_link_rejected=-not (Allowed $hostPath $arguments)
@{{fixture_root=$root;results=$results}} | ConvertTo-Json -Depth 3 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    (tmp_path / "retained-native-host-fixture.json").write_text(
        json.dumps(observed), encoding="utf-8"
    )
    assert all(observed["results"].values()), observed


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_startup_host_defaults_match_harmless_real_cli(executable, tmp_path):
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")
    marker = "startup-host-proof"
    script = tmp_path / "harmless.ps1"
    script.write_text(f"Write-Output '{marker}'", encoding="utf-8")
    for arguments, succeeds in (
        (
            ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "Write-Output", marker],
            executable == "powershell.exe",
        ),
        (["-NoProfile", str(script)], True),
        (["-NoProfile", "-Command", f"Write-Output '{marker}'"], True),
    ):
        result = subprocess.run(
            [resolved, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_windows_powershell_env(),
            timeout=15,
            check=False,
        )
        assert (result.returncode == 0) is succeeds, result.stderr
        if succeeds:
            assert result.stdout.strip() == marker


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_failure_cleanup_survives_journal_and_stop_failures_and_nested_refusal(executable):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Record-BoundedFailure", "Invoke-BoundedRecovery", "Test-BoundedCleanupAuthority", "Register-BoundedCleanupAuthority")}
$journal=@{{candidate=@{{logical_identity='same'}};migration=@{{phase='application_start_intent'}}}}
$script:BoundedRecoveryMutationStarted=$true;$script:BoundedRecoveryDatabaseVerified=$true;$script:calls=@()
function Assert-LocksOwned {{ }}
function Assert-MaintenanceState {{ param([switch]$Recovering) }}
function Get-BoundedCleanupJournal {{ param($Journal) return $Journal }}
function Assert-EntitlementFeature {{ throw 'entitlement_denied' }}
function Set-ApplicationRoleFence {{ param($Journal) $script:calls+='fence' }}
function Stop-BoundedApplications {{ param($Journal,[switch]$ContainersOnly) $script:calls+='stop';throw 'stop_failed' }}
function Stop-BoundedRestoreAssets {{ param($Journal) $script:calls+='restore_stop' }}
function Stop-BoundedSnapshot {{ param($Journal) $script:calls+='snapshot_stop' }}
function Write-JsonAtomic {{ $script:calls+='journal';throw 'disk_full' }}
function Write-Audit {{ $script:calls+='audit';throw 'disk_full' }}
try {{ Invoke-BoundedRecovery $journal }} catch {{ Record-BoundedFailure $journal 'service_health_failed' }}
@{{calls=$script:calls;error=$journal.error_code;phase=$journal.migration.phase}} | ConvertTo-Json -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed == {
        "calls": ["fence", "stop", "restore_stop", "snapshot_stop", "journal", "audit"],
        "error": "service_health_failed",
        "phase": "application_start_intent",
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_cleanup_loads_bound_persistent_state_without_process_flags(executable, tmp_path):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Get-BoundedCleanupJournal", "Record-BoundedFailure", "Test-BoundedCleanupAuthority", "Assert-MaintenanceState", "Convert-UpgradeJson", "Test-ExactKeys", "Get-Sha256Text")}
$StateDirectory={_ps_literal(tmp_path)}; $SiteRoot=$StateDirectory
$OperationId='03ba27e4-c647-4a2c-9ad5-ae087b39a0c9'; $Reason='isolated recovery test'
$BoundedSourceHead='0012_alarm_notification_runtime'; $BoundedTargetHead='0013_serial_polling_profile'; $BoundedMigrationSha256='c'*64
$JournalPath=Join-Path $StateDirectory "full-upgrade-$OperationId.json"
$MaintenanceStatePath=Join-Path $StateDirectory 'full-upgrade-maintenance.json'
function Assert-LocksOwned {{ if ($script:scenario -ceq 'lost_lock') {{ throw 'lock_lost' }} }}
function Assert-RestrictedDirectory {{ param($Path) }}
function Assert-RestrictedFile {{ param($Path) if ($script:scenario -ceq 'unprotected_journal' -and $Path -ceq $JournalPath) {{ throw 'acl_invalid' }} }}
function Read-ActiveRelease {{ if ($script:scenario -ceq 'active_drift') {{ return @{{logical_identity='sha256:'+('f'*64)}} }}; return $script:previous }}
function Assert-ActiveReleaseUnchanged {{ param($Before,$After) if ($Before.logical_identity -cne $After.logical_identity) {{ throw 'active_drift' }} }}
function Set-ApplicationRoleFence {{ $script:calls+='fence' }}
function Stop-BoundedApplications {{ param($Journal,[switch]$ContainersOnly) $script:calls+='stop' }}
function Stop-BoundedRestoreAssets {{ $script:calls+='restore_stop' }}
function Stop-BoundedSnapshot {{ $script:calls+='snapshot_stop' }}
function Write-JsonAtomic {{ $script:calls+='journal'; throw 'disk_full' }}
function Write-Audit {{ $script:calls+='audit'; throw 'disk_full' }}
$results=@(foreach ($script:scenario in @('active','wrong_operation','wrong_marker','wrong_migration','active_drift','unprotected_journal','completed','lost_lock')) {{
  $script:BoundedRecoveryMutationStarted=$false; $script:BoundedRecoveryDatabaseVerified=$false; $script:calls=@()
  $script:previous=@{{logical_identity='sha256:'+('a'*64);site_root=$SiteRoot}}
  $journal=@{{operation_id=$OperationId;reason_hash=(Get-Sha256Text $Reason);previous_release=$script:previous;candidate=@{{logical_identity='sha256:'+('b'*64)}};
    status='recovery_failed';error_code='';migration=@{{kind='bounded_0012_0013';script_sha256=$BoundedMigrationSha256;phase='application_start_intent'}}}}
  $marker=@{{schema_version=1;operation_id=$OperationId;site_root=$SiteRoot;status='active';source_identity=$journal.previous_release.logical_identity;
    candidate_identity=$journal.candidate.logical_identity;source_head=$BoundedSourceHead;target_head=$BoundedTargetHead;journal_path=$JournalPath;updated_at=[DateTimeOffset]::UtcNow.ToString('o')}}
  if ($script:scenario -ceq 'wrong_operation') {{ $journal.operation_id='03ba27e4-c647-4a2c-9ad5-ae087b39a0c8' }}
  if ($script:scenario -ceq 'wrong_marker') {{ $marker.candidate_identity='sha256:'+('f'*64) }}
  if ($script:scenario -ceq 'wrong_migration') {{ $journal.migration.script_sha256='d'*64 }}
  if ($script:scenario -ceq 'completed') {{ $marker.status='committed'; $journal.status='committed'; $journal.migration.phase='completed' }}
  [IO.File]::WriteAllText($JournalPath,($journal|ConvertTo-Json -Depth 10))
  [IO.File]::WriteAllText($MaintenanceStatePath,($marker|ConvertTo-Json -Depth 10))
  Record-BoundedFailure $journal 'entitlement_denied'
  @{{scenario=$script:scenario;calls=@($script:calls);status=$journal.status}}
}})
ConvertTo-Json -InputObject $results -Depth 5 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed[0]["calls"] == [
        "fence",
        "stop",
        "restore_stop",
        "snapshot_stop",
        "journal",
        "audit",
    ]
    for item in observed[1:6]:
        assert item["calls"] == ["journal", "audit"], item
    assert observed[6]["calls"] == ["audit"]
    assert observed[6]["status"] == "committed"
    assert observed[7]["calls"] == []


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_application_start_rechecks_original_installed_guard_receipt_before_roles(
    executable, tmp_path
):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Assert-BoundedMaintenanceGuards", "Start-BoundedApplications")}
$StateDirectory={_ps_literal(tmp_path)}; $OperationId='03ba27e4-c647-4a2c-9ad5-ae087b39a0c9'
function Assert-LocksOwned {{ }}
function Write-JsonAtomic {{ }}
function Assert-RestrictedFile {{ }}
function Save-BoundedPhase {{ }}
function Invoke-DockerText {{ param($Arguments) if ($Arguments[0] -ceq 'inspect') {{
  $service=$Arguments[1].Substring(9);$id=@{{gw='a';api='b';web='c'}}[$service]*64
  @{{Id=$id;State=@{{Running=$false;Status='created';Restarting=$false;Pid=0}};Image='image';HostConfig=@{{RestartPolicy=@{{Name='no'}}}};
    Config=@{{Labels=@{{'com.docker.compose.project'='ruisheng-prod';'com.docker.compose.service'=$service}}}}}} | ConvertTo-Json -Depth 5 -Compress
}} }}
function Assert-InstalledMaintenanceGuards {{ $script:calls+='guard'; if ($script:mode -ceq 'missing') {{ throw 'installed_maintenance_guards_missing' }}; if ($script:mode -ceq 'task') {{ throw 'unprotected_startup_task_present' }}; if ($script:mode -ceq 'changed') {{ return 'b'*64 }}; return 'a'*64 }}
function Restore-ApplicationRoles {{ $script:calls+='roles' }}
function Wait-BoundedHealthy {{ }}
function Assert-CurrentContainerIdentity {{ }}
$journal=@{{migration=@{{guard_receipt_sha256='a'*64}}}}
$release=@{{base=@('compose');images=@{{api=@{{image_id='image'}};gw=@{{image_id='image'}};web=@{{image_id='image'}}}}}}
$results=@(foreach ($script:mode in @('valid','changed','missing','task')) {{
  $script:calls=@();$failure=''
  try {{ Start-BoundedApplications $journal $release }} catch {{ $failure=$_.Exception.Message }}
  @{{calls=$script:calls;error=$failure}}
}})
ConvertTo-Json -InputObject $results -Depth 5 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed[0] == {"calls": ["guard", "roles"], "error": ""}
    assert [item["calls"] for item in observed[1:]] == [["guard"]] * 3
    assert [item["error"] for item in observed[1:]] == [
        "installed_maintenance_guard_drift",
        "installed_maintenance_guards_missing",
        "unprotected_startup_task_present",
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_acquired_cleanup_authority_survives_reads_and_preserves_pending_intent(executable):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Record-BoundedFailure", "Test-BoundedCleanupAuthority", "Register-BoundedCleanupAuthority", "Complete-BoundedMaintenance")}
$OperationId='03ba27e4-c647-4a2c-9ad5-ae087b39a0c9'; $SiteRoot='owned-site'
$journal=@{{previous_release=@{{logical_identity='source'}};candidate=@{{logical_identity='target'}};status='recovery_failed';migration=@{{
  phase='application_start_intent';source_images=@{{gw='image';api='image';web='image'}};target_images=@{{gw='image';api='image';web='image'}};
  docker_intent=@{{scope='role_restore';issued_at='newer-memory-intent'}};
  applications=@(@{{name='ruisheng-gw';restart='no'}},@{{name='ruisheng-api';restart='no'}},@{{name='ruisheng-web';restart='no'}});
  dependencies=@(@{{service='postgres';restart='no';retries=0}},@{{service='redis';restart='no';retries=0}})}}}}
function Assert-LocksOwned {{ }}
function Get-BoundedCleanupJournal {{ $script:calls+='unsafe_disk_dependency'; throw 'journal_read_or_acl_failed' }}
function Set-ApplicationRoleFence {{ $script:calls+='fence' }}
function Stop-BoundedApplications {{ param($Journal,[switch]$ContainersOnly) $script:calls+='stop' }}
function Stop-BoundedRestoreAssets {{ }}
function Stop-BoundedSnapshot {{ }}
function Write-JsonAtomic {{ $script:calls+='journal'; throw 'disk_full' }}
function Write-Audit {{ $script:calls+='audit'; throw 'disk_full' }}
function Convert-UpgradeJson {{ param($Text) ConvertFrom-Json $Text }}
function Invoke-DockerText {{ param($Arguments)
  if ($Arguments[0] -ceq 'inspect') {{
    $service=$Arguments[1].Substring(9)
    @{{Image='image';Config=@{{Labels=@{{'com.docker.compose.project'='ruisheng-prod';'com.docker.compose.service'=$service}}}}}}|ConvertTo-Json -Depth 5 -Compress
  }}
}}
function Save-BoundedPhase {{ param($Journal,$Phase) $Journal.migration.phase=$Phase }}
function Write-MaintenanceState {{ param($Journal,$Status) }}
Register-BoundedCleanupAuthority $journal
$script:calls=@()
Record-BoundedFailure $journal 'service_health_failed'
$first=@{{calls=$script:calls;intent=$journal.migration.docker_intent;authority=(Test-BoundedCleanupAuthority $journal)}}
$journal.migration.docker_intent=$null; $journal.status='committed'
Complete-BoundedMaintenance $journal 'committed'
$script:calls=@()
Record-BoundedFailure $journal 'entitlement_denied'
@{{first=$first;completed=@{{calls=$script:calls;authority=(Test-BoundedCleanupAuthority $journal);status=$journal.status}}}} | ConvertTo-Json -Depth 6 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["first"] == {
        "calls": ["fence", "stop", "journal", "audit"],
        "intent": {"scope": "role_restore", "issued_at": "newer-memory-intent"},
        "authority": True,
    }
    assert observed["completed"] == {
        "calls": ["unsafe_disk_dependency", "audit"],
        "authority": False,
        "status": "committed",
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_first_container_stop_is_fenced_and_stop_failures_do_not_skip_other_services(executable):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Stop-BoundedApplications")}
$journal=@{{migration=@{{source_images=@{{gw='image';api='image';web='image'}};target_images=@{{}}}}}}
$script:calls=@();$script:fenced=$false
function Assert-LocksOwned {{ }}
function Set-ApplicationRoleFence {{ param($Journal) $script:fenced=$true;$script:calls+='fence' }}
function Invoke-DockerText {{
  param([string[]]$Arguments)
  $service=$Arguments[-1].Replace('ruisheng-','')
  if ($Arguments[0] -ceq 'ps') {{ return 'exists' }}
  if ($Arguments[0] -ceq 'inspect' -and $Arguments.Count -eq 2) {{
    return (@{{Image='image';Config=@{{Labels=@{{'com.docker.compose.project'='ruisheng-prod';'com.docker.compose.service'=$service}}}}}} | ConvertTo-Json -Compress)
  }}
  if ($Arguments[0] -ceq 'inspect') {{ return 'false|no' }}
  if ($Arguments[0] -ceq 'stop') {{
    if (-not $script:fenced) {{ throw 'not_fenced' }}
    $script:calls+='stop_'+$service
    if ($service -ceq 'gw') {{ throw 'first_stop_interrupted' }}
  }}
}}
try {{ Stop-BoundedApplications $journal }} catch {{ $failure=$_.Exception.Message }}
@{{calls=$script:calls;error=$failure}} | ConvertTo-Json -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "calls": ["fence", "stop_gw", "stop_api", "stop_web"],
        "error": "first_stop_interrupted",
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_forced_descendant_cleanup_never_clears_production_daemon_intent(
    executable, upgrade_process_stub, tmp_path
):
    pids = tmp_path / "owned-pids.txt"
    journal_path = tmp_path / "journal.json"
    source = f"""
$ErrorActionPreference='Stop';$LeaseSeconds=120;$AcquiredLocks=@(1)
{_contained_process_loader()}
{_bounded_ast_loader("Invoke-DockerText", "Write-JsonAtomic")}
function Assert-LocksOwned {{ }}
function Renew-Locks {{ }}
function Get-Command {{ param($Name) @{{Source={_ps_literal(upgrade_process_stub)}}} }}
$JournalPath={_ps_literal(journal_path)}
$journal=@{{migration=@{{phase='application_prepare_intent'}}}}
$errors=@()
try {{ Invoke-DockerText @('create',{_ps_literal(pids)},'exit') 8 }} catch {{ $errors+=$_.Exception.Message }}
try {{ Invoke-DockerText @('start','known-production-container') 8 }} catch {{ $errors+=$_.Exception.Message }}
ConvertTo-Json -InputObject $errors -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "docker_process_tree_incomplete",
        "docker_mutation_completion_unknown",
    ]
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["migration"]["docker_intent"]["scope"] == "production"
    assert journal["migration"]["docker_intent"]["phase"] == "application_prepare_intent"
    _assert_owned_processes_stopped(pids)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_snapshot_whole_lifecycle_deadline_rejects_before_new_command(executable, tmp_path):
    source = f"""
$ErrorActionPreference='Stop';$AcquiredLocks=@()
{_bounded_ast_loader("Invoke-DockerText")}
$script:BoundedSnapshotDeadline=[DateTimeOffset]::UtcNow.AddMilliseconds(-1)
function Invoke-ContainedUpgradeProcess {{ throw 'must_not_launch' }}
try {{ Invoke-DockerText @('exec','ruisheng-postgres','pg_dump') 600 }} catch {{ $_.Exception.Message }}
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "backup_snapshot_deadline_exceeded"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_failure_cleanup_never_fences_a_drifted_database_using_stale_proof(executable):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Record-BoundedFailure", "Test-BoundedCleanupAuthority", "Set-ApplicationRoleFence")}
$script:BoundedRecoveryDatabaseVerified=$true;$script:BoundedRecoveryMutationStarted=$true
$script:calls=@();$journal=@{{candidate=@{{logical_identity='same'}};migration=@{{phase='application_start_intent'}}}}
function Assert-LocksOwned {{ }}
function Assert-BoundedDatabaseIdentity {{ $script:calls+='verify';throw 'dependency_database_identity_invalid' }}
function Get-BoundedCleanupJournal {{ param($Journal) return $Journal }}
function Invoke-DatabaseSql {{ $script:calls+='unsafe_sql' }}
function Stop-BoundedApplications {{ $script:calls+='stop' }}
function Stop-BoundedRestoreAssets {{ }}
function Stop-BoundedSnapshot {{ }}
function Write-JsonAtomic {{ $script:calls+='journal' }}
function Write-Audit {{ $script:calls+='audit' }}
Record-BoundedFailure $journal 'service_health_failed'
ConvertTo-Json -InputObject $script:calls -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["verify", "stop", "journal", "audit"]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_detached_exec_role_login_and_restart_release_retain_unknown_intent(executable):
    source = f"""
$ErrorActionPreference='Stop';$AcquiredLocks=@();$OperationId='06fe9214-ad53-4e65-b72c-a21e67b61e85'
{_bounded_ast_loader("Invoke-DockerText")}
function Write-JsonAtomic {{ param($Path,$Value) }}
function Get-Command {{ @{{Source='docker.exe'}} }}
function Invoke-ContainedUpgradeProcess {{ if ($script:clientCompletes) {{ return '' }}; throw 'docker_command_timeout' }}
$results=@(foreach ($command in @(
  @('exec','-d','-e',"PGAPPNAME=ruisheng-upgrade-snapshot-$OperationId",'ruisheng-postgres','psql'),
  @('exec','ruisheng-postgres','psql','-d','ruisheng','-Atqc','ALTER ROLE ruisheng_gw LOGIN;'),
  @('update','--restart=unless-stopped','ruisheng-api'),
  @('update','--restart=no','ruisheng-api'),
  @('exec','ruisheng-postgres','psql','-d','ruisheng','-Atqc','ALTER ROLE ruisheng_gw NOLOGIN;'),
  @('exec','ruisheng-postgres','psql','-d','ruisheng','-Atqc','SELECT 1')
)) {{
  $journal=@{{migration=@{{phase='application_start_intent';snapshot_holder=@{{}}}}}}
  try {{ Invoke-DockerText $command }} catch {{ }}
  [string]$journal.migration.docker_intent.scope
}})
$script:clientCompletes=$true
$journal=@{{migration=@{{phase='backup_snapshot_intent';snapshot_holder=@{{}}}}}}
Invoke-DockerText @('exec','-d','-e',"PGAPPNAME=ruisheng-upgrade-snapshot-$OperationId",'ruisheng-postgres','psql') | Out-Null
$results+=([string]$journal.migration.docker_intent.scope)
$script:clientCompletes=$false
$journal=@{{migration=@{{phase='application_start_intent';docker_intent=@{{scope='role_restore'}}}}}}
foreach ($cleanup in @(@('update','--restart=no','ruisheng-api'),@('exec','ruisheng-postgres','psql','-Atqc','ALTER ROLE ruisheng_gw NOLOGIN;'))) {{
  try {{ Invoke-DockerText $cleanup }} catch {{ $results+=([string]$journal.migration.docker_intent.scope+':'+$_.Exception.Message) }}
}}
ConvertTo-Json -InputObject $results -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "snapshot",
        "role_restore",
        "restart_restore",
        "",
        "",
        "",
        "snapshot",
        "role_restore:docker_command_timeout",
        "role_restore:docker_command_timeout",
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_snapshot_never_observed_started_cannot_be_marked_stopped(executable):
    source = f"""
$ErrorActionPreference='Stop';$OperationId='06fe9214-ad53-4e65-b72c-a21e67b61e85'
{_bounded_ast_loader("Stop-BoundedSnapshot")}
function Assert-LocksOwned {{ }}
function Assert-BoundedDatabaseIdentity {{ $true }}
function Invoke-DatabaseSql {{ '0' }}
function Write-JsonAtomic {{ throw 'must_not_record_completion' }}
$journal=@{{migration=@{{snapshot_holder=@{{application_name="ruisheng-upgrade-snapshot-$OperationId";lifetime_seconds=2460;observed_start=$false;stopped=$false}};
  docker_intent=@{{scope='snapshot';application_name="ruisheng-upgrade-snapshot-$OperationId"}}}}}}
try {{ Stop-BoundedSnapshot $journal }} catch {{ $failure=$_.Exception.Message }}
@{{error=$failure;stopped=$journal.migration.snapshot_holder.stopped;intent=$journal.migration.docker_intent.scope}} | ConvertTo-Json -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "error": "backup_snapshot_start_completion_unknown",
        "stopped": False,
        "intent": "snapshot",
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_unknown_writers_waits_for_actual_zero_with_remaining_query_budget(executable):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Assert-NoUnknownWriters")}
$OperationId='owned-test'; $script:calls=@(); $script:timeouts=@(); $script:queries=0
$script:clock=$null
function Assert-LocksOwned {{
  if ($null -eq $script:clock) {{ $script:clock=[Diagnostics.Stopwatch]::StartNew() }}
  $script:calls+='lock'
}}
function Invoke-DatabaseSql {{
  param($Sql,[int]$TimeoutSeconds)
  if ($Sql -cnotmatch "application_name<>'ruisheng-upgrade-snapshot-owned-test'$" -or $Sql -match 'pg_isready|pg_terminate_backend') {{ throw 'unsafe_query' }}
  if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt (15-$clock.Elapsed.TotalSeconds)) {{ throw 'query_budget_exceeded' }}
  $script:calls+='query'; $script:timeouts+=$TimeoutSeconds; $script:queries++
  if ($script:queries -eq 1) {{ Start-Sleep -Milliseconds 1100; '2' }}
  elseif ($script:queries -eq 2) {{ '1' }} else {{ '0' }}
}}
Assert-NoUnknownWriters
@{{calls=$script:calls;timeouts=$script:timeouts}} | ConvertTo-Json -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["calls"] == ["lock", "query", "lock", "query", "lock", "query", "lock"]
    assert observed["timeouts"][1] < observed["timeouts"][0] <= 14
    assert 1 <= observed["timeouts"][2] <= observed["timeouts"][1]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_unknown_writers_persistent_connection_exhausts_fixed_budget(executable):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Assert-NoUnknownWriters")}
$script:queries=0; $script:locks=0
function Assert-LocksOwned {{ $script:locks++ }}
function Invoke-DatabaseSql {{ param($Sql,[int]$TimeoutSeconds) $script:queries++; '1' }}
$clock=[Diagnostics.Stopwatch]::StartNew()
try {{ Assert-NoUnknownWriters; $failure='unexpected_pass' }} catch {{ $failure=$_.Exception.Message }}
@{{error=$failure;elapsed=$clock.Elapsed.TotalSeconds;queries=$script:queries;locks=$script:locks}} | ConvertTo-Json -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["error"] == "unknown_database_writer_present"
    assert 14 <= observed["elapsed"] < 16
    assert observed["queries"] > 1
    assert observed["locks"] == observed["queries"] + 1


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("count", ["1", "0"])
def test_unknown_writers_lock_loss_during_wait_or_before_release_rejects(executable, count):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Assert-NoUnknownWriters")}
$script:queries=0; $script:locks=0
function Assert-LocksOwned {{ $script:locks++; if ($script:locks -eq 2) {{ throw 'upgrade_lock_lost' }} }}
function Invoke-DatabaseSql {{ param($Sql,[int]$TimeoutSeconds) $script:queries++; '{count}' }}
try {{ Assert-NoUnknownWriters; $failure='unexpected_pass' }} catch {{ $failure=$_.Exception.Message }}
@{{error=$failure;queries=$script:queries;locks=$script:locks}} | ConvertTo-Json -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"error": "upgrade_lock_lost", "queries": 1, "locks": 2}


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_unknown_writers_failed_or_uncertain_query_is_not_retried(executable):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Assert-NoUnknownWriters")}
function Assert-LocksOwned {{ }}
function Invoke-DatabaseSql {{
  param($Sql,[int]$TimeoutSeconds)
  $script:queries++
  switch ($script:scenario) {{
    'failed' {{ throw 'docker_command_timeout' }}
    'missing' {{ return }}
    'multiple' {{ '1'; '0' }}
    'invalid' {{ 'unknown' }}
  }}
}}
$results=@(foreach ($script:scenario in @('failed','missing','multiple','invalid')) {{
  $script:queries=0
  try {{ Assert-NoUnknownWriters; $failure='unexpected_pass' }} catch {{ $failure=$_.Exception.Message }}
  @{{error=$failure;queries=$script:queries}}
}})
ConvertTo-Json -InputObject $results -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        {"error": error, "queries": 1}
        for error in ["docker_command_timeout", *["unknown_database_writer_present"] * 3]
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bounded_apply_initial_audit_is_independent_and_preserves_primary_error(
    executable, tmp_path
):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Invoke-BoundedApply", "Convert-UpgradeJson")}
$StateDirectory={_ps_literal(tmp_path)}; $EnvFile='unused'; $BoundedSourceHead='source'; $PersistentServices=@()
$images=@{{postgres=@{{image_id='pg'}};redis=@{{image_id='redis'}}}}
function Assert-LocksOwned {{ }}
function Get-BoundedRelease {{ @{{manifest=@{{alembic_head='source'}};images=$images}} }}
function Assert-CurrentContainerIdentity {{ }}
function Assert-InstalledMaintenanceGuards {{ 'c'*64 }}
function Get-DatabaseBackupEstimate {{ 1 }}
function Get-BoundedResources {{ @{{docker_memory=@{{sufficient=$true}};docker_data_volume=@{{sufficient=$true}}}} }}
function Assert-BoundedMigrationImage {{ }}
function Assert-BoundedSchema {{ }}
function Invoke-DatabaseSql {{ param($Sql) if ($Sql -match 'json_agg') {{ '[{{"name":"ruisheng_api","login":false}},{{"name":"ruisheng_gw","login":true}}]' }} else {{ '123' }} }}
function Get-ManagedApplications {{ }}
function Get-BoundedDependencyEvidence {{ }}
function Invoke-DockerText {{ '' }}
function New-EnvironmentBackupReceipt {{ @{{sha256='same'}} }}
function Write-JsonAtomic {{ }}
function Write-MaintenanceState {{ }}
function Register-BoundedCleanupAuthority {{ }}
function Save-BoundedPhase {{ throw $script:initialError }}
function Write-Audit {{
  param($Event,$Result,$CandidateIdentity,$ErrorCode)
  $script:calls+='audit'; $script:audit=@{{event=$Event;result=$Result;identity=$CandidateIdentity;error=$ErrorCode}}
  if ($script:auditFails) {{ throw 'audit_failed' }}
}}
function Invoke-BoundedRecovery {{
  param($Journal)
  $script:calls+='recovery'
  if ($script:recoveryFails) {{ throw 'secondary_recovery_failure' }}
  $Journal.status='rolled_back'; $Journal.error_code=''
}}
function Record-BoundedFailure {{ param($Journal,$Failure) $script:calls+='failure'; $Journal.error_code=$Failure }}
$results=@(foreach ($script:initialError in @('service_health_failed','unsafe message')) {{
  foreach ($script:auditFails in @($false,$true)) {{
    foreach ($script:recoveryFails in @($false,$true)) {{
      $script:calls=@(); $script:audit=$null
      $journal=@{{previous_release=@{{}};candidate=@{{logical_identity='candidate'}};source_environment_sha256='same';error_code=''}}
      Invoke-BoundedApply $journal @{{}} $images
      @{{calls=$script:calls;audit=$script:audit;status=$journal.status;error=$journal.error_code;roles=$journal.migration.roles}}
    }}
  }}
}})
ConvertTo-Json -InputObject $results -Depth 8 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    expected = []
    for initial_error in ("service_health_failed", "bounded_upgrade_failed"):
        for _audit_fails in (False, True):
            for recovery_fails in (False, True):
                expected.append(
                    {
                        "calls": ["audit", "recovery", "failure"]
                        if recovery_fails
                        else ["audit", "recovery"],
                        "audit": {
                            "event": "upgrade_apply_failed",
                            "result": "failed",
                            "identity": "candidate",
                            "error": initial_error,
                        },
                        "status": None if recovery_fails else "rolled_back",
                        "error": initial_error if recovery_fails else "",
                        "roles": [
                            {"name": "ruisheng_api", "login": False},
                            {"name": "ruisheng_gw", "login": True},
                        ],
                    }
                )
    assert observed == expected


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_application_start_uses_only_verified_container_ids(executable, tmp_path):
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Start-BoundedApplications")}
$StateDirectory={_ps_literal(tmp_path)};$OperationId='owned-test'
function Assert-LocksOwned {{ }}
function Write-JsonAtomic {{ }}
function Assert-RestrictedFile {{ }}
function Save-BoundedPhase {{ param($Journal,$Phase) $script:calls+=$Phase }}
function Assert-BoundedMaintenanceGuards {{ $script:calls+='guard' }}
function Restore-ApplicationRoles {{ $script:calls+='roles' }}
function Wait-BoundedHealthy {{ $script:calls+='healthy' }}
function Assert-CurrentContainerIdentity {{ $script:calls+='identity' }}
function Invoke-DockerText {{ param($Arguments)
  if ($Arguments[0] -ceq 'inspect') {{
    $service=$Arguments[1].Substring(9)
    $info=@{{Id=(@{{gw='a';api='b';web='c'}}[$service]*64);Image='image';State=@{{Status='created';Running=$false;Restarting=$false;Pid=0}};
      HostConfig=@{{RestartPolicy=@{{Name='no'}}}};Config=@{{Labels=@{{'com.docker.compose.project'='ruisheng-prod';'com.docker.compose.service'=$service}}}}}}
    if ($service -ceq 'api') {{ . ([ScriptBlock]::Create($script:mutation)) }}
    return ($info | ConvertTo-Json -Depth 6 -Compress)
  }}
  $script:commands+=,@($Arguments)
  if ($Arguments[0] -ceq 'start') {{ $script:calls+='start' }}
}}
$release=@{{base=@('compose','-f','owned.yml');images=@{{gw=@{{image_id='image'}};api=@{{image_id='image'}};web=@{{image_id='image'}}}}}}
$results=@(foreach ($script:mutation in @('',
  '$info.Id="short"','$info.Id="a"*64','$info.Image="wrong"','$info.State.Status="exited"',
  '$info.State.Running=$true','$info.State.Restarting=$true','$info.State.Pid=1',
  '$info.HostConfig.RestartPolicy.Name="always"',
  '$info.Config.Labels."com.docker.compose.project"="other"',
  '$info.Config.Labels."com.docker.compose.service"="other"')) {{
  $journal=@{{migration=@{{}}}};$script:calls=@();$script:commands=@();$failure=''
  try {{ Start-BoundedApplications $journal $release }} catch {{ $failure=$_.Exception.Message }}
  @{{error=$failure;calls=$script:calls;commands=$script:commands}}
}})
ConvertTo-Json -InputObject $results -Depth 8 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed[0]["error"] == ""
    assert observed[0]["commands"][1:] == [["start", char * 64] for char in "abc"]
    assert "--no-start" in observed[0]["commands"][0]
    assert "--no-deps" in observed[0]["commands"][0]
    assert observed[0]["calls"] == [
        "application_prepare_intent",
        "guard",
        "application_start_intent",
        "roles",
        "start",
        "start",
        "start",
        "healthy",
        "identity",
    ]
    for rejected in observed[1:]:
        assert rejected["error"] == "application_prepare_invalid"
        assert rejected["calls"] == ["application_prepare_intent"]
        assert len(rejected["commands"]) == 1


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_legacy_compose_start_reconciliation_requires_complete_observation(executable):
    bypasses = [
        "$journal.migration.docker_intent=$null",
        "$journal.migration.docker_intent.scope='snapshot'",
        "$journal.migration.docker_intent.scope='role_restore'",
        "$journal.migration.docker_intent.scope='restart_restore'",
        "$journal.migration.docker_intent.command='start'",
        "$journal.migration.docker_intent.phase='application_prepare_intent'",
        "$journal.migration.phase='migration_running'",
        "$journal.status='running'",
        "$journal.migration.application_start_attempted=$false",
        "$journal.migration.application_start_attempted='true'",
    ]
    refusals = [
        "$script:gate='locks'",
        "$script:gate='origin'",
        "$script:gate='database'",
        "$script:gate='backup'",
        "$script:gate='schema'",
        "$script:gate='migrators'",
        "$script:gate='writers'",
        "$script:gate='final_lock'",
        "$script:gate='audit'",
        "$script:gate='journal'",
        "$script:roleCount='1'",
        "$journal.migration.existing_migrators=@()",
        "$journal.migration.existing_migrators+=@{id='extra'}",
        "$journal.migration.container_id=''",
        "$migration.Id='wrong'",
        "$migration.State.Status='running'",
        "$migration.State.ExitCode=1",
        "$legacy.State.Status='running'",
        "$legacy.State.ExitCode=0",
        "$legacy.Config.Labels.'com.docker.compose.project'='other'",
        "$legacy.Config.Labels.'com.docker.compose.service'='other'",
        "$journal.migration.docker_intent.issued_at='invalid'",
        "$migration.State.FinishedAt='0001-01-01T00:00:00Z'",
        "$migration.State.FinishedAt='2026-09-11T00:00:02Z'",
        "$legacy.State.StartedAt='2026-09-10T23:59:59Z'",
        "$legacy.State.FinishedAt='2026-09-11T00:00:00Z'",
        "$legacy.State.FinishedAt='2099-01-01T00:00:00Z'",
        "$origin.recorded_at='2026-09-11T00:00:02Z'",
        "$apps.api.Id='short'",
        "$apps.api.Id=$apps.gw.Id",
        "$apps.api.Image='wrong'",
        "$apps.api.State.Status='exited'",
        "$apps.api.State.Running=$true",
        "$apps.api.State.Restarting=$true",
        "$apps.api.State.Pid=1",
        "$apps.api.State.StartedAt='2026-09-11T00:00:00Z'",
        "$apps.api.HostConfig.RestartPolicy.Name='always'",
        "$apps.api.Config.Labels.'com.docker.compose.project'='other'",
        "$apps.api.Config.Labels.'com.docker.compose.service'='other'",
    ]
    cases = [{"kind": "success", "mutation": ""}]
    cases += [
        {
            "kind": "success",
            "mutation": "$journal.error_code='dependency_database_identity_invalid'",
        }
    ]
    cases += [{"kind": "bypass", "mutation": item} for item in bypasses]
    cases += [
        {"kind": "origin_reject", "mutation": "$origin.error_code='" + error + "'"}
        for error in (
            "docker_command_timeout",
            "docker_process_tree_incomplete",
            "docker_mutation_completion_unknown",
        )
    ]
    cases += [{"kind": "refuse", "mutation": item} for item in refusals]
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Resolve-LegacyComposeStartFailure", "Convert-UpgradeJson")}
$JournalPath='unused';$BoundedTargetHead='0013_serial_polling_profile'
function Check-Gate {{ param($Name) $script:calls+=$Name;if ($script:gate -ceq $Name) {{ throw "rejected_$Name" }} }}
function Assert-LocksOwned {{
  $script:lockCount++; if ($script:lockCount -gt 1) {{ Check-Gate 'final_lock' }} else {{ Check-Gate 'locks' }}
}}
function Assert-BoundedDatabaseIdentity {{ Check-Gate 'database'; return $true }}
function Get-LegacyApplyFailure {{ if ($script:gate -ceq 'origin') {{ throw 'legacy_apply_failure_unverified' }};return $origin }}
function Assert-BoundedBackupReceipt {{ param($Journal,[switch]$RequireVerified)
  if (-not $RequireVerified) {{ throw 'verified_required' }};Check-Gate 'backup'
}}
function Assert-BoundedSchema {{ param($Head) if ($Head -cne $BoundedTargetHead) {{ throw 'wrong_head' }};Check-Gate 'schema' }}
function Assert-BoundedMigrationStopped {{ Check-Gate 'migrators' }}
function Assert-NoUnknownWriters {{ Check-Gate 'writers' }}
function Invoke-DatabaseSql {{ return $script:roleCount }}
function Invoke-DockerText {{ param($Arguments)
  if ($Arguments[0] -cne 'inspect') {{ throw 'unexpected_mutation' }}
  $info=if ($Arguments[1] -ceq ('d'*64)) {{ $migration }} elseif ($Arguments[1] -ceq ('e'*64)) {{ $legacy }}
    else {{ $apps[$Arguments[1].Substring(9)] }}
  ConvertTo-Json -InputObject @($info) -Depth 8 -Compress
}}
function Write-Audit {{ param($Event,$Result,$Identity,$ErrorCode,$Evidence)
  if ($Event -cne 'upgrade_application_start_failure_observed' -or $Result -cne 'observed' -or $Identity -cne 'candidate') {{ throw 'wrong_audit' }}
  Check-Gate 'audit';$script:auditEvidence=$Evidence
}}
function Write-JsonAtomic {{ param($Path,$Value) Check-Gate 'journal';$script:saved=$Value | ConvertTo-Json -Depth 12 -Compress }}
$cases=ConvertFrom-Json {_ps_literal(json.dumps(cases))}
$results=@(foreach ($form in @('dictionary','object')) {{ foreach ($case in $cases) {{
  $script:calls=@();$script:lockCount=0;$script:gate='';$script:roleCount='2';$script:saved='';$script:auditEvidence=$null
  $origin=@{{error_code='docker_command_failed';recorded_at='2026-09-11T00:00:04Z';record_hash=('f'*64)}}
  $journal=@{{status='recovery_failed';error_code='docker_command_failed';candidate=@{{logical_identity='candidate'}};migration=@{{
    phase='application_start_intent';application_start_attempted=$true;container_id=('d'*64);
    existing_migrators=@(@{{id=('e'*64)}});target_images=@{{gw='image';api='image';web='image'}};
    docker_intent=@{{scope='production';command='compose';phase='application_start_intent';issued_at='2026-09-11T00:00:01Z'}}}}}}
  $migration=@{{Id=('d'*64);State=@{{Status='exited';ExitCode=0;FinishedAt='2026-09-11T00:00:00Z'}}}}
  $legacy=@{{Id=('e'*64);State=@{{Status='exited';ExitCode=255;StartedAt='2026-09-11T00:00:02Z';FinishedAt='2026-09-11T00:00:03Z'}};
    Config=@{{Labels=@{{'com.docker.compose.project'='ruisheng-prod';'com.docker.compose.service'='migrate'}}}}}}
  $apps=@{{}};foreach ($service in @('gw','api','web')) {{
    $apps[$service]=@{{Id=(@{{gw='a';api='b';web='c'}}[$service]*64);Image='image';
      State=@{{Status='created';Running=$false;Restarting=$false;Pid=0;StartedAt='0001-01-01T00:00:00Z'}};
      HostConfig=@{{RestartPolicy=@{{Name='no'}}}};Config=@{{Labels=@{{'com.docker.compose.project'='ruisheng-prod';'com.docker.compose.service'=$service}}}}}}
  }}
  . ([ScriptBlock]::Create([string]$case.mutation))
  if ($form -ceq 'object') {{ $journal=Convert-UpgradeJson ($journal | ConvertTo-Json -Depth 10) }}
  $original=ConvertTo-Json -InputObject $journal.migration.docker_intent -Compress
  $failure='';try {{ Resolve-LegacyComposeStartFailure $journal }} catch {{ $failure=$_.Exception.Message }}
  @{{kind=$case.kind;mutation=$case.mutation;form=$form;error=$failure;calls=$script:calls;saved=$script:saved;
    preserved=((ConvertTo-Json -InputObject $journal.migration.docker_intent -Compress) -ceq $original);
    intent=$journal.migration.docker_intent;observation=$journal.migration.application_start_observation;audit=$script:auditEvidence}}
}} }})
ConvertTo-Json -InputObject $results -Depth 14 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    for item in observed:
        if item["kind"] == "success":
            assert item["error"] == "", item
            assert item["intent"] is None
            assert item["calls"] == [
                "locks",
                "database",
                "backup",
                "schema",
                "migrators",
                "writers",
                "final_lock",
                "audit",
                "journal",
            ]
            receipt = json.loads(item["saved"])["migration"]["application_start_observation"]
            assert receipt == item["observation"]
            assert receipt == item["audit"]
            assert receipt["original_failure_record_hash"] == "f" * 64
            assert receipt["legacy_exit_code"] == 255
            assert receipt["prior_intent"]["command"] == "compose"
            assert receipt["application_ids"] == ["a" * 64, "b" * 64, "c" * 64]
        else:
            assert item["preserved"] is True, item
            assert item["observation"] is None, item
            assert item["saved"] == "", item
            if item["kind"] == "bypass":
                assert item["calls"] == [] and item["error"] == "", item
            elif item["kind"] == "origin_reject":
                assert item["calls"] == ["locks"] and item["error"] == "", item
            else:
                assert item["error"], item


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_legacy_failure_audit_origin_and_observation_integrity(executable, tmp_path):
    origin = {
        "schema_version": 1,
        "recorded_at": "2026-09-11T00:00:04.0000000+00:00",
        "operation_id": "owned-operation",
        "event": "upgrade_apply_failed",
        "result": "failed",
        "candidate_identity": "candidate",
        "reason_hash": hashlib.sha256(b"owned reason").hexdigest(),
        "remote_user": "test-user",
        "remote_computer": "test-host",
        "error_code": "docker_command_failed",
    }
    cases = [
        ("valid", [origin], "", "docker_command_failed"),
        (
            "retry",
            [
                origin,
                {
                    **origin,
                    "event": "upgrade_recovery_failed",
                    "error_code": "dependency_database_identity_invalid",
                },
            ],
            "",
            "docker_command_failed",
        ),
        (
            "timeout",
            [
                {**origin, "error_code": "docker_command_timeout"},
                {**origin, "event": "upgrade_recovery_failed"},
            ],
            "",
            "docker_command_timeout",
        ),
        ("missing", [{**origin, "event": "upgrade_recovery_failed"}], "", None),
        ("duplicate", [origin, {**origin, "error_code": "docker_command_timeout"}], "", None),
        ("candidate", [{**origin, "candidate_identity": "other"}], "", None),
        ("reason", [{**origin, "reason_hash": "f" * 64}], "", None),
        ("operation", [{**origin, "operation_id": "other"}], "", None),
        ("result", [{**origin, "result": "observed"}], "", None),
        ("timestamp", [{**origin, "recorded_at": "invalid"}], "", None),
        ("before_intent", [{**origin, "recorded_at": "2026-09-10T00:00:00Z"}], "", None),
        ("future", [{**origin, "recorded_at": "2099-01-01T00:00:00Z"}], "", None),
        ("corrupt_tail", [origin], "not-json\n", None),
    ]
    for name, records, suffix, _expected in cases:
        previous_hash = "0" * 64
        lines = []
        for entry in records:
            payload = {**entry, "previous_hash": previous_hash}
            encoded = json.dumps(payload, separators=(",", ":"))
            previous_hash = hashlib.sha256(encoded.encode()).hexdigest()
            lines.append(encoded[:-1] + ',"record_hash":"' + previous_hash + '"}')
        (tmp_path / (name + ".jsonl")).write_text(
            "\n".join(lines) + "\n" + suffix, encoding="utf-8"
        )
    corrupt = (
        (tmp_path / "valid.jsonl").read_text(encoding="utf-8").replace("test-user", "changed-user")
    )
    (tmp_path / "corrupt_hash.jsonl").write_text(corrupt, encoding="utf-8")
    cases.append(("corrupt_hash", [], "", None))
    (tmp_path / "audit.lock").write_text("", encoding="utf-8")
    source = f"""
$ErrorActionPreference='Stop'
{_bounded_ast_loader("Get-LegacyApplyFailure", "Write-Audit", "Get-AuditLineHashMaterial", "Get-Sha256Text", "Convert-UpgradeJson")}
$OperationId='owned-operation';$Reason='owned reason';$AcquiredLocks=@()
$AuditLockPath={_ps_literal(tmp_path / "audit.lock")}
function Assert-LocksOwned {{ }}
function Assert-RestrictedFile {{ param($Path)
  if ($Path -cne $AuditPath) {{ throw 'wrong_audit_path' }}
  if ($script:rejectAuditAcl) {{ throw 'audit_file_acl_invalid' }}
}}
$journal=@{{candidate=@{{logical_identity='candidate'}};migration=@{{docker_intent=@{{issued_at='2026-09-11T00:00:01Z'}}}}}}
$observed=@(foreach ($name in (ConvertFrom-Json {_ps_literal(json.dumps([case[0] for case in cases]))})) {{
  $AuditPath=Join-Path {_ps_literal(tmp_path)} ($name+'.jsonl');$failure='';$record=$null
  try {{ $record=Get-LegacyApplyFailure $journal }} catch {{ $failure=$_.Exception.Message }}
  @{{name=$name;error=$failure;code=$record.error_code}}
}})
$AuditPath={_ps_literal(tmp_path / "valid.jsonl")}
$evidence=[ordered]@{{prior_intent=$journal.migration.docker_intent;application_ids=@(('a'*64),('b'*64),('c'*64));legacy_exit_code=255}}
Write-Audit 'upgrade_application_start_failure_observed' 'observed' 'candidate' -Evidence $evidence
Write-Audit 'upgrade_application_start_failure_observed' 'observed' 'candidate' -Evidence $evidence
$evidence.application_ids=@(('d'*64),('e'*64),('f'*64))
Write-Audit 'upgrade_application_start_failure_observed' 'observed' 'candidate' -Evidence $evidence
$verified=Get-LegacyApplyFailure $journal
$script:rejectAuditAcl=$true;$aclError=''
try {{ Get-LegacyApplyFailure $journal }} catch {{ $aclError=$_.Exception.Message }}
@{{observed=$observed;verified=$verified.error_code;acl_error=$aclError}} | ConvertTo-Json -Depth 5 -Compress
"""
    result = _run_bounded_powershell(executable, source)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    for item, case in zip(observed["observed"], cases, strict=True):
        assert item["name"] == case[0]
        if case[3] is None:
            assert item["error"] and item["code"] is None, item
        else:
            assert item["error"] == "" and item["code"] == case[3], item
    assert observed["verified"] == "docker_command_failed"
    assert observed["acl_error"] == "audit_file_acl_invalid"
    audits = [
        json.loads(line)
        for line in (tmp_path / "valid.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(audits) == 3  # Exact duplicate suppressed, different evidence retained.
    assert audits[1]["evidence"]["application_ids"] == [letter * 64 for letter in "abc"]
    assert audits[2]["evidence"]["application_ids"] == [letter * 64 for letter in "def"]
    assert audits[1]["evidence"]["legacy_exit_code"] == 255
