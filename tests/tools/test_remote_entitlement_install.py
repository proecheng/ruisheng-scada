from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools import entitlement

ROOT = Path(__file__).parents[2]
REMOTE = ROOT / "tools" / "remote_entitlement_install.ps1"
TARGET = ROOT / "tools" / "target_entitlement_verifier.ps1"
OPERATION = "00000000-0000-4000-8000-000000000001"
SITE = "site-1"
GRANT_ID = "grant-one"
PASSWORD = b"correct horse battery staple"
START = "2026-09-04T00:00:00+00:00"
END = "2027-09-04T00:00:00+00:00"
GRACE = "2027-09-11T00:00:00+00:00"
SSH_OPTIONS = [
    "BatchMode=yes",
    "StrictHostKeyChecking=yes",
    "PreferredAuthentications=publickey",
    "PubkeyAuthentication=yes",
    "PasswordAuthentication=no",
    "KbdInteractiveAuthentication=no",
    "GSSAPIAuthentication=no",
    "HostbasedAuthentication=no",
    "IdentitiesOnly=yes",
    "ConnectTimeout=10",
    "ServerAliveInterval=15",
    "ServerAliveCountMax=3",
]

STUB_SOURCE = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;

public static class EntitlementTransportStub
{
    private static string Required(string name)
    {
        string value = Environment.GetEnvironmentVariable(name);
        if (String.IsNullOrEmpty(value)) throw new InvalidOperationException("missing " + name);
        return value;
    }

    private static int Record(string kind, string[] args)
    {
        string log = Required("ENT_STUB_LOG");
        int sshCalls = File.Exists(log)
            ? File.ReadAllLines(log).Count(line => line.StartsWith("ssh|", StringComparison.Ordinal))
            : 0;
        string encoded = Convert.ToBase64String(Encoding.UTF8.GetBytes(String.Join("\0", args)));
        File.AppendAllText(log, kind + "|" + encoded + Environment.NewLine, new UTF8Encoding(false));
        return kind == "ssh" ? sshCalls + 1 : 0;
    }

    private static string J(string value)
    {
        return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    public static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        string executable = Process.GetCurrentProcess().MainModule.FileName;
        string kind = Path.GetFileName(executable).StartsWith("scp", StringComparison.OrdinalIgnoreCase)
            ? "scp" : "ssh";
        int call = Record(kind, args);
        string mode = Required("ENT_STUB_MODE");
        if (kind == "scp")
        {
            if (mode == "scp-fail") { Console.Error.Write("upload failed"); return 23; }
            return 0;
        }

        string site = Required("ENT_STUB_SITE");
        string operation = Required("ENT_STUB_OPERATION");
        string incoming = @"C:\ProgramData\Ruisheng\entitlements\incoming\" + operation + "-"
            + Required("ENT_STUB_GRANT_SHA") + ".json";
        if (mode == "status" || mode == "status-uncertain")
        {
            bool uncertain = mode == "status-uncertain";
            Console.Out.Write("{\"schema_version\":1,\"ok\":" + (uncertain ? "false" : "true")
                + ",\"status\":\"" + (uncertain ? "uncertain" : "missing") + "\","
                + "\"site_id\":\"" + J(site) + "\",\"entitlement_dependent\":false,"
                + "\"features\":[],"
                + "\"safety_preserved\":true,\"collection_preserved\":true,"
                + "\"alarms_preserved\":true,\"data_preserved\":true}");
            return uncertain ? 2 : 0;
        }
        if (call == 1)
        {
            if (mode == "replace-source")
            {
                File.WriteAllText(Required("ENT_STUB_SOURCE"), "replaced after approval", new UTF8Encoding(false));
            }
            Console.Out.Write("{\"schema_version\":1,\"ok\":true,\"status\":\"prepared\","
                + "\"operation_id\":\"" + operation + "\",\"site_id\":\"" + J(site) + "\","
                + "\"incoming_path\":\"" + J(incoming) + "\"}");
            return 0;
        }
        if (call >= 3 || (mode == "scp-fail" && call == 2))
        {
            Console.Out.Write("{\"schema_version\":1,\"ok\":true,\"status\":\"cleaned\","
                + "\"operation_id\":\"" + operation + "\",\"site_id\":\"" + J(site) + "\","
                + "\"incoming_path\":\"" + J(incoming) + "\",\"removed\":false}");
            return 0;
        }
        if (mode == "timeout") { Thread.Sleep(5000); return 0; }
        if (mode == "oversized") { Console.Out.Write(new String('A', 70000)); return 0; }
        if (mode == "malformed") { Console.Out.Write("not-json"); return 0; }
        if (mode == "rejected" || mode == "uncertain")
        {
            string status = mode == "uncertain" ? "uncertain" : "rejected";
            string code = mode == "uncertain" ? "transaction_uncertain" : "signature_invalid";
            Console.Out.Write("{\"schema_version\":1,\"ok\":false,\"status\":\"" + status + "\","
                + "\"error_code\":\"" + code + "\",\"safety_preserved\":true,"
                + "\"collection_preserved\":true,\"alarms_preserved\":true,\"data_preserved\":true}");
            return 2;
        }
        string digest = mode == "mismatch" ? new String('0', 64) : Required("ENT_STUB_GRANT_SHA");
        Console.Out.Write("{\"schema_version\":1,\"ok\":true,\"status\":\"installed\","
            + "\"idempotent\":false,\"operation_id\":\"" + operation + "\","
            + "\"site_id\":\"" + J(site) + "\",\"grant_id\":\"" + Required("ENT_STUB_GRANT_ID") + "\","
            + "\"grant_sha256\":\"" + digest + "\",\"serial\":1,"
            + "\"starts_at\":\"" + Required("ENT_STUB_START") + "\","
            + "\"expires_at\":\"" + Required("ENT_STUB_END") + "\","
            + "\"grace_until\":\"" + Required("ENT_STUB_GRACE") + "\","
            + "\"safety_preserved\":true,\"collection_preserved\":true,"
            + "\"alarms_preserved\":true,\"data_preserved\":true}");
        return 0;
    }
}
"""


def _powershell(name: str = "powershell.exe") -> str:
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} is unavailable")
    return executable


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if os.name != "nt":
        return environment
    system_root = Path(environment.get("SystemRoot", r"C:\Windows"))
    program_files = Path(environment.get("ProgramFiles", r"C:\Program Files"))
    user_profile = Path(environment.get("USERPROFILE", str(Path.home())))
    environment["PSModulePath"] = os.pathsep.join(
        (
            str(user_profile / "Documents" / "WindowsPowerShell" / "Modules"),
            str(program_files / "WindowsPowerShell" / "Modules"),
            str(system_root / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
        )
    )
    return environment


@pytest.fixture(scope="session")
def transport_stubs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    stub_dir = tmp_path_factory.mktemp("entitlement-transport-stubs")
    ssh = stub_dir / "ssh.exe"
    command = (
        "$ErrorActionPreference='Stop';$source=[Console]::In.ReadToEnd();"
        f"Add-Type -TypeDefinition $source -OutputAssembly {_ps_literal(ssh)} "
        "-OutputType ConsoleApplication"
    )
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        input=STUB_SOURCE,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    shutil.copy2(ssh, stub_dir / "scp.exe")
    purelib = Path(sysconfig.get_path("purelib"))
    shutil.copytree(purelib / "cryptography", stub_dir / "vendor/cryptography")
    for backend in purelib.glob("_cffi_backend*"):
        if backend.is_file():
            shutil.copy2(backend, stub_dir / "vendor" / backend.name)
    return stub_dir


@pytest.fixture
def grant_path(tmp_path: Path) -> Path:
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(PASSWORD),
        )
    )
    grant = entitlement.issue_grant(
        private_key_path=private_path,
        private_key_password=PASSWORD,
        key_id="entitlement-2026",
        site_id=SITE,
        customer_id="customer-1",
        plan="annual",
        features=["support", "upgrade"],
        serial=1,
        issued=datetime(2026, 9, 4, tzinfo=UTC),
        start=datetime(2026, 9, 4, tzinfo=UTC),
        end=datetime(2027, 9, 4, tzinfo=UTC),
        grant_id=GRANT_ID,
        now=datetime(2026, 9, 4, tzinfo=UTC),
    )
    path = tmp_path / "grant.json"
    path.write_bytes(entitlement.canonical_artifact_bytes(grant))
    return path


def _environment(stub_dir: Path, log_path: Path, mode: str, grant: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{stub_dir}{os.pathsep}{environment['PATH']}"
    environment.update(
        {
            "ENT_STUB_LOG": str(log_path),
            "ENT_STUB_MODE": mode,
            "ENT_STUB_SITE": SITE,
            "ENT_STUB_OPERATION": OPERATION,
            "ENT_STUB_GRANT_ID": GRANT_ID,
            "ENT_STUB_GRANT_SHA": entitlement.sha256_bytes(grant.read_bytes()),
            "ENT_STUB_START": START,
            "ENT_STUB_END": END,
            "ENT_STUB_GRACE": GRACE,
            "ENT_STUB_SOURCE": str(grant),
        }
    )
    return environment


def _test_remote_script(
    stub_dir: Path, tmp_path: Path, *, fail_snapshot_cleanup: bool = False
) -> Path:
    source = REMOTE.read_text(encoding="utf-8")
    source = source.replace(
        r"C:\Windows\System32\OpenSSH\ssh.exe", str(stub_dir / "ssh.exe")
    ).replace(r"C:\Windows\System32\OpenSSH\scp.exe", str(stub_dir / "scp.exe"))
    source = source.replace(
        r"$env:LOCALAPPDATA\Ruisheng\entitlement-snapshots", str(tmp_path / "snapshots")
    )
    source = source.replace(
        r"C:\ProgramData\Ruisheng\entitlement-build\vendor",
        str((stub_dir / "vendor").resolve()),
    )
    start = source.index("function Set-LocalProtectedAcl")
    end = source.index("function New-ProtectedGrantSnapshot")
    source = (
        source[:start]
        + "function Set-LocalProtectedAcl { param($Path,$Kind) }\n"
        + "function Assert-LocalVendorSet { }\n\n"
        + source[end:]
    )
    start = source.index("function Assert-FixedExecutable")
    end = source.index("function Get-NormalizedLocalPath")
    source = (
        source[:start]
        + "function Assert-FixedExecutable { param($Path,$Publisher) }\n\n"
        + source[end:]
    )
    if fail_snapshot_cleanup:
        start = source.index("function Remove-ProtectedGrantSnapshot")
        end = source.index("function ConvertTo-NativeArgument", start)
        source = (
            source[:start]
            + 'function Remove-ProtectedGrantSnapshot { throw "grant_snapshot_cleanup_failed" }\n\n'
            + source[end:]
        )
    script = tmp_path / "remote_entitlement_install.ps1"
    script.write_text(source, encoding="utf-8")
    shutil.copy2(ROOT / "tools/entitlement.py", tmp_path / "entitlement.py")
    return script


def _run_install(
    stub_dir: Path,
    tmp_path: Path,
    grant: Path,
    mode: str,
    *,
    timeout: int = 10,
    target: str = "operator@100.64.0.2",
    extra: list[str] | None = None,
    path_value: str | None = None,
    local_python_path: str | None = None,
    fail_snapshot_cleanup: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    log_path = tmp_path / f"{mode}.log"
    audit_dir = tmp_path / f"audit-{mode}"
    test_script = _test_remote_script(
        stub_dir, tmp_path, fail_snapshot_cleanup=fail_snapshot_cleanup
    )
    command = [
        _powershell(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(test_script),
        "-Action",
        "Install",
        "-Target",
        target,
        "-SiteId",
        SITE,
        "-GrantPath",
        str(grant),
        "-OperationId",
        OPERATION,
        "-Reason",
        "approved annual entitlement",
        "-Approved",
        "-TransportTimeoutSeconds",
        str(timeout),
        "-AuditDirectory",
        str(audit_dir),
        "-LocalPythonPath",
        local_python_path or sys.executable,
    ]
    if extra:
        command.extend(extra)
    environment = _environment(stub_dir, log_path, mode, grant)
    if path_value is not None:
        environment["PATH"] = path_value
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=environment,
    )
    return completed, log_path, audit_dir / "entitlement-install.jsonl"


def _calls(path: Path) -> list[tuple[str, list[str]]]:
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        kind, encoded = line.split("|", 1)
        arguments = base64.b64decode(encoded).decode("utf-8").split("\0")
        calls.append((kind, arguments))
    return calls


def _audit(path: Path) -> dict[str, object]:
    return cast(
        dict[str, object], json.loads(path.read_text(encoding="utf-8-sig").splitlines()[-1])
    )


def _decoded_remote(arguments: list[str]) -> str:
    index = arguments.index("-EncodedCommand")
    return base64.b64decode(arguments[index + 1]).decode("utf-16-le")


def _assert_key_only(arguments: list[str]) -> None:
    values = [arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == "-o"]
    assert values == SSH_OPTIONS


def test_valid_install_uses_fixed_paths_key_only_transport_and_correlated_receipt(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    completed, log_path, audit_path = _run_install(transport_stubs, tmp_path, grant_path, "valid")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "installed"
    assert result["grant_sha256"] == entitlement.sha256_bytes(grant_path.read_bytes())
    assert _audit(audit_path)["result"] == "installed"

    calls = _calls(log_path)
    assert [kind for kind, _ in calls] == ["ssh", "scp", "ssh", "ssh"]
    for _, arguments in calls:
        _assert_key_only(arguments)
    prepare = _decoded_remote(calls[0][1])
    install = _decoded_remote(calls[2][1])
    cleanup = _decoded_remote(calls[3][1])
    grant_sha256 = entitlement.sha256_bytes(grant_path.read_bytes())
    assert "C:\\ProgramData\\Ruisheng\\bin\\target_entitlement_verifier.ps1" in prepare
    assert "-Action 'Prepare'" in prepare
    assert f"-OperationId '{OPERATION}'" in prepare
    assert "-Action 'Install'" in install
    assert (
        f"C:\\ProgramData\\Ruisheng\\entitlements\\incoming\\{OPERATION}-{grant_sha256}.json"
    ) in install
    assert "-Action 'Cleanup'" in cleanup
    scp_arguments = calls[1][1]
    assert scp_arguments[-2] != str(grant_path)
    assert str(tmp_path / "snapshots") in scp_arguments[-2]
    assert scp_arguments[-1] == (
        f"operator@100.64.0.2:C:/ProgramData/Ruisheng/entitlements/incoming/"
        f"{OPERATION}-{grant_sha256}.json"
    )

    target = TARGET.read_text(encoding="utf-8")
    cleanup_action = target.split('elseif ($Action -eq "Cleanup")', 1)[1].split(
        'elseif ($Action -eq "Status")', 1
    )[0]
    assert 'throw "cleanup_failed"' in cleanup_action


def test_grant_replacement_after_approval_cannot_change_uploaded_snapshot(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    approved_digest = entitlement.sha256_bytes(grant_path.read_bytes())
    completed, log_path, _ = _run_install(transport_stubs, tmp_path, grant_path, "replace-source")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert entitlement.sha256_bytes(grant_path.read_bytes()) != approved_digest
    assert json.loads(completed.stdout)["grant_sha256"] == approved_digest
    scp_arguments = next(args for kind, args in _calls(log_path) if kind == "scp")
    assert scp_arguments[-2] != str(grant_path)
    assert str(tmp_path / "snapshots") in scp_arguments[-2]


def test_ipv6_scp_target_is_bracketed(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    completed, log_path, _ = _run_install(
        transport_stubs,
        tmp_path,
        grant_path,
        "valid",
        target="operator@fd7a:115c:a1e0::1",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = _calls(log_path)
    ssh_calls = [args for kind, args in calls if kind == "ssh"]
    assert ssh_calls and all("operator@fd7a:115c:a1e0::1" in args for args in ssh_calls)
    scp_arguments = next(args for kind, args in calls if kind == "scp")
    assert scp_arguments[-1].startswith("operator@[fd7a:115c:a1e0::1]:")


def test_path_hijack_does_not_override_fixed_transport_tools(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    hijack = tmp_path / "path-hijack"
    hijack.mkdir()
    (hijack / "ssh.exe").write_text("not an executable", encoding="ascii")
    (hijack / "scp.exe").write_text("not an executable", encoding="ascii")
    completed, log_path, _ = _run_install(
        transport_stubs, tmp_path, grant_path, "valid", path_value=str(hijack)
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert [kind for kind, _ in _calls(log_path)] == ["ssh", "scp", "ssh", "ssh"]


def test_local_python_path_must_be_absolute_before_transport(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    completed, log_path, _ = _run_install(
        transport_stubs,
        tmp_path,
        grant_path,
        "valid",
        local_python_path="python.exe",
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_code"] == "local_python_path_invalid"
    assert not log_path.exists()


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_fixed_executable_requires_valid_expected_publisher(
    executable: str, tmp_path: Path
) -> None:
    source = REMOTE.read_text(encoding="utf-8")
    function = _function(source, "Assert-FixedExecutable", "Get-NormalizedLocalPath")
    ssh = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/OpenSSH/ssh.exe"
    if not ssh.is_file():
        pytest.skip("Windows OpenSSH is unavailable")
    unsigned_root = tmp_path / f"unsigned-{executable}"
    unsigned_root.mkdir()
    unsigned = unsigned_root / "ssh.exe"
    unsigned.write_bytes(b"not signed")
    wrong_signer = tmp_path / "python.exe"
    shutil.copy2(ssh, wrong_signer)
    python = Path(sys.executable)
    harness = f"""
$ErrorActionPreference='Stop'
{function}
function Capture([string]$Path,[string]$Publisher) {{
  try {{ Assert-FixedExecutable $Path $Publisher; return 'ok' }}
  catch {{ return $_.Exception.Message }}
}}
[ordered]@{{
  microsoft=Capture {_ps_literal(ssh)} 'Microsoft'
  python=Capture {_ps_literal(python)} 'PythonSoftwareFoundation'
  wrong_publisher=Capture {_ps_literal(wrong_signer)} 'PythonSoftwareFoundation'
  unsigned=Capture {_ps_literal(unsigned)} 'Microsoft'
}} | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "microsoft": "ok",
        "python": "ok",
        "wrong_publisher": "fixed_executable_publisher_invalid",
        "unsigned": "fixed_executable_signature_invalid",
    }


def test_local_grant_inspector_disables_site_and_explicitly_injects_vendor() -> None:
    source = REMOTE.read_text(encoding="utf-8")
    assert '$arguments = @("-I", "-S", "-B", "-c", $bootstrap' in source
    assert "base64.b64decode('$vendorPathBase64').decode('utf-8')" in source
    assert "Assert-LocalVendorSet" in source


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_local_acl_boundary_rejects_an_extra_writer(executable: str, tmp_path: Path) -> None:
    source = REMOTE.read_text(encoding="utf-8")
    functions = (
        _function(source, "Set-LocalProtectedAcl", "Get-LocalPrincipalSid")
        + _function(source, "Get-LocalPrincipalSid", "Assert-LocalProtectedAcl")
        + _function(source, "Assert-LocalProtectedAcl", "Get-SafeLocalTreeItems")
    )
    root = tmp_path / f"local-trust-{executable}"
    trusted = root / "release-allowed-signers"
    harness = f"""
$ErrorActionPreference='Stop'
{functions}
$root={_ps_literal(root)}
$trusted={_ps_literal(trusted)}
[void](New-Item -ItemType Directory -Path $root)
[IO.File]::WriteAllText($trusted,'trusted',[Text.Encoding]::ASCII)
Set-LocalProtectedAcl $root 'Directory'
Set-LocalProtectedAcl $trusted 'File'
function Capture {{
  try {{ Assert-LocalProtectedAcl -Path $trusted -Kind File -RequireProtected; return 'ok' }}
  catch {{ return $_.Exception.Message }}
}}
$valid=Capture
$icacls=Join-Path $env:SystemRoot 'System32\\icacls.exe'
& $icacls $trusted /grant '*S-1-5-32-545:(F)' | Out-Null
if($LASTEXITCODE -ne 0){{throw 'icacls_failed'}}
[ordered]@{{valid=$valid;extra_writer=Capture}} | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "valid": "ok",
        "extra_writer": "local_acl_invalid",
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_local_vendor_closed_set_rejects_an_unlisted_pth(executable: str, tmp_path: Path) -> None:
    source = REMOTE.read_text(encoding="utf-8")
    functions = (
        _function(source, "Set-LocalProtectedAcl", "Get-LocalPrincipalSid")
        + _function(source, "Get-LocalPrincipalSid", "Assert-LocalProtectedAcl")
        + _function(source, "Assert-LocalProtectedAcl", "Get-SafeLocalTreeItems")
        + _function(source, "Get-SafeLocalTreeItems", "Get-StrictLocalAscii")
        + _function(source, "Get-StrictLocalAscii", "Assert-LocalVendorSet")
        + _function(source, "Assert-LocalVendorSet", "New-ProtectedGrantSnapshot")
    )
    build = tmp_path / f"local-build-{executable}"
    vendor = build / "vendor"
    manifest = build / "vendor-manifest.sha256"
    package = vendor / "package.py"
    injected = vendor / "startup.pth"
    harness = f"""
$ErrorActionPreference='Stop'
{functions}
$script:LocalBuildRoot={_ps_literal(build)}
$script:LocalVendorRoot={_ps_literal(vendor)}
$script:LocalVendorManifestPath={_ps_literal(manifest)}
$script:MaxVendorManifestBytes=1MB
$script:MaxVendorFiles=4096
$script:MaxVendorTreeItems=8192
$script:MaxVendorBytes=128MB
[void](New-Item -ItemType Directory -Path $script:LocalVendorRoot -Force)
[IO.File]::WriteAllText({_ps_literal(package)},'trusted',[Text.Encoding]::ASCII)
$hash=(Get-FileHash -LiteralPath {_ps_literal(package)} -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($script:LocalVendorManifestPath,"$hash`tpackage.py`n",[Text.Encoding]::ASCII)
Set-LocalProtectedAcl $script:LocalBuildRoot 'Directory'
Set-LocalProtectedAcl $script:LocalVendorRoot 'Directory'
Set-LocalProtectedAcl {_ps_literal(package)} 'File'
Set-LocalProtectedAcl $script:LocalVendorManifestPath 'File'
function Capture {{
  try {{ Assert-LocalVendorSet; return 'ok' }}
  catch {{ return $_.Exception.Message }}
}}
$valid=Capture
[IO.File]::WriteAllText({_ps_literal(injected)},'import attacker',[Text.Encoding]::ASCII)
Set-LocalProtectedAcl {_ps_literal(injected)} 'File'
[ordered]@{{valid=$valid;unlisted_pth=Capture}} | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "valid": "ok",
        "unlisted_pth": "local_vendor_file_set_invalid",
    }


@pytest.mark.parametrize(
    "mode,expected_code",
    [
        ("mismatch", "install_receipt_mismatch"),
        ("malformed", "install_receipt_invalid"),
        ("oversized", "subprocess_output_exceeded"),
        ("timeout", "subprocess_timeout"),
        ("uncertain", "install_transport_failed"),
    ],
)
def test_uncertain_install_receipts_are_audited_as_ambiguous_and_cleanup_is_attempted(
    transport_stubs: Path,
    tmp_path: Path,
    grant_path: Path,
    mode: str,
    expected_code: str,
) -> None:
    completed, log_path, audit_path = _run_install(
        transport_stubs, tmp_path, grant_path, mode, timeout=1 if mode == "timeout" else 10
    )
    assert completed.returncode == 2
    failure = json.loads(completed.stdout)
    assert failure["error_code"] == expected_code
    assert failure["status"] == "uncertain"
    assert failure["safety_preserved"] is True
    record = _audit(audit_path)
    assert record["result"] == "ambiguous_commit"
    assert record["error_code"] == expected_code
    assert any(
        "-Action 'Cleanup'" in _decoded_remote(args)
        for kind, args in _calls(log_path)
        if kind == "ssh"
    )


def test_explicit_target_rejection_is_audited_as_failure(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    completed, _, audit_path = _run_install(transport_stubs, tmp_path, grant_path, "rejected")
    assert completed.returncode == 2
    failure = json.loads(completed.stdout)
    assert failure["status"] == "rejected"
    assert failure["error_code"] == "signature_invalid"
    assert _audit(audit_path)["result"] == "failed"


def test_final_snapshot_cleanup_failure_preserves_uncertain_install_status(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    completed, _, _ = _run_install(
        transport_stubs,
        tmp_path,
        grant_path,
        "malformed",
        fail_snapshot_cleanup=True,
    )
    failure = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert failure["status"] == "uncertain"
    assert failure["error_code"] == "grant_snapshot_cleanup_failed"


def test_pre_dispatch_upload_failure_is_not_ambiguous(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    completed, _, audit_path = _run_install(transport_stubs, tmp_path, grant_path, "scp-fail")
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_code"] == "grant_upload_failed"
    assert _audit(audit_path)["result"] == "failed"


def test_remote_path_override_is_not_an_exposed_parameter(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    completed, log_path, _ = _run_install(
        transport_stubs,
        tmp_path,
        grant_path,
        "valid",
        extra=["-RemoteVerifierPath", r"C:\ProgramData\Ruisheng\..\evil.ps1"],
    )
    assert completed.returncode != 0
    assert not log_path.exists()


def test_non_tailscale_target_is_rejected_before_transport(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    completed, log_path, _ = _run_install(
        transport_stubs,
        tmp_path,
        grant_path,
        "valid",
        target="operator@203.0.113.10",
    )
    assert completed.returncode == 2
    failure = json.loads(completed.stdout)
    assert failure["error_code"] == "target_not_tailscale"
    assert not log_path.exists()


def test_status_is_one_read_only_fixed_verifier_call(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    log_path = tmp_path / "status.log"
    audit_dir = tmp_path / "status-audit"
    test_script = _test_remote_script(transport_stubs, tmp_path)
    completed = subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(test_script),
            "-Action",
            "Status",
            "-Target",
            "operator@100.64.0.2",
            "-SiteId",
            SITE,
            "-AuditDirectory",
            str(audit_dir),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_environment(transport_stubs, log_path, "status", grant_path),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "missing"
    calls = _calls(log_path)
    assert len(calls) == 1
    assert calls[0][0] == "ssh"
    _assert_key_only(calls[0][1])
    command = _decoded_remote(calls[0][1])
    assert "-Action 'Status'" in command
    assert "-GrantPath" not in command
    assert "-Reason" not in command
    assert _audit(audit_dir / "entitlement-install.jsonl")["result"] == "status_observed"


def test_status_preserves_valid_target_transaction_uncertainty(
    transport_stubs: Path, tmp_path: Path, grant_path: Path
) -> None:
    log_path = tmp_path / "status-uncertain.log"
    audit_dir = tmp_path / "status-uncertain-audit"
    test_script = _test_remote_script(transport_stubs, tmp_path)
    completed = subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(test_script),
            "-Action",
            "Status",
            "-Target",
            "operator@100.64.0.2",
            "-SiteId",
            SITE,
            "-AuditDirectory",
            str(audit_dir),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_environment(transport_stubs, log_path, "status-uncertain", grant_path),
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert result["status"] == "uncertain"
    assert result["ok"] is False
    assert _audit(audit_dir / "entitlement-install.jsonl")["result"] == "status_observed"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_target_rejects_nonexact_incoming_path_before_runtime_access(executable: str) -> None:
    reason = "approved annual entitlement"
    grant_sha256 = "0" * 64
    completed = subprocess.run(
        [
            _powershell(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(TARGET),
            "-Action",
            "Install",
            "-SiteId",
            SITE,
            "-OperationId",
            OPERATION,
            "-Reason",
            reason,
            "-GrantSha256",
            grant_sha256,
            "-ReasonSha256",
            entitlement.sha256_bytes(reason.encode("utf-8")),
            "-GrantPath",
            rf"C:\ProgramData\Ruisheng\entitlements\incoming\..\{OPERATION}-{grant_sha256}.json",
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_code"] == "grant_path_invalid"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_target_rejects_caller_site_spoofing(executable: str, tmp_path: Path) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = _function(source, "Get-StrictAscii", "Assert-VendorSet") + _function(
        source, "Get-PinnedSiteIdentity", "ConvertTo-NativeArgument"
    )
    identity = tmp_path / f"site-identity-{executable}"
    identity.write_text("site-a\n", encoding="ascii", newline="\n")
    harness = f"""
$ErrorActionPreference='Stop'
$script:SiteIdentityPath={_ps_literal(identity)}
$SiteId='site-b'
{functions}
try {{ Assert-PinnedSiteIdentity; [Console]::Out.Write('unexpected') }}
catch {{ [Console]::Out.Write($_.Exception.Message); exit 2 }}
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert completed.returncode == 2
    assert completed.stdout == "site_mismatch"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_inherited_transaction_acl_repair_is_narrow(executable: str) -> None:
    source = TARGET.read_text(encoding="utf-8")
    function = _function(
        source, "Repair-InheritedTransactionAcl", "Repair-InheritedTransactionAcls"
    )
    harness = f"""
$ErrorActionPreference='Stop'
$script:AllowedSids=@('S-1-5-18','S-1-5-32-544')
$script:RepairCalls=0
$script:Mode='exact'
function Test-Path {{ param([string]$LiteralPath,[string]$PathType); return $true }}
function Get-Item {{ param([string]$LiteralPath,[switch]$Force); return [pscustomobject]@{{Attributes=[IO.FileAttributes]::Normal}} }}
function Get-IdentitySid($Identity) {{ return [string]$Identity }}
function Rule([string]$Sid,[bool]$Inherited,[string]$Type='Allow') {{
  return [pscustomobject]@{{
    IdentityReference=$Sid;IsInherited=$Inherited
    AccessControlType=[Security.AccessControl.AccessControlType]::$Type
    FileSystemRights=[Security.AccessControl.FileSystemRights]::FullControl
    PropagationFlags=[Security.AccessControl.PropagationFlags]::None
  }}
}}
function Get-Acl {{
  if($script:Mode-eq'exact'){{return [pscustomobject]@{{AreAccessRulesProtected=$false;Access=@((Rule 'S-1-5-18' $true),(Rule 'S-1-5-32-544' $true))}}}}
  if($script:Mode-eq'explicit'){{return [pscustomobject]@{{AreAccessRulesProtected=$false;Access=@((Rule 'S-1-5-18' $true),(Rule 'S-1-5-32-544' $false))}}}}
  return [pscustomobject]@{{AreAccessRulesProtected=$false;Access=@((Rule 'S-1-5-18' $true),(Rule 'S-1-5-11' $true))}}
}}
function Set-ProtectedAcl {{ param([string]$Path,[string]$Kind);$script:RepairCalls++ }}
{function}
Repair-InheritedTransactionAcl 'current.json'
$exact=$script:RepairCalls
$script:Mode='exact';Repair-InheritedTransactionAcl 'operations' 'Directory'
$directory=$script:RepairCalls
$script:Mode='explicit';Repair-InheritedTransactionAcl 'current.json'
$explicit=$script:RepairCalls
$script:Mode='foreign';Repair-InheritedTransactionAcl 'current.json'
[ordered]@{{exact=$exact;directory=$directory;after_explicit=$explicit;after_foreign=$script:RepairCalls}}|ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "exact": 1,
        "directory": 2,
        "after_explicit": 2,
        "after_foreign": 2,
    }
    repair_all = _function(source, "Repair-InheritedTransactionAcls", "Get-StrictAscii")
    for protected_name in (
        "$script:StatePath",
        "$script:TimeStatePath",
        "$script:AuditPath",
        '"$($script:AuditPath).1"',
        '"$($script:AuditPath).2"',
        '".transaction.lock"',
        '"transaction.json"',
    ):
        assert protected_name in repair_all
    assert 'Repair-InheritedTransactionAcl $operations "Directory"' in repair_all


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_target_site_mismatch_is_written_to_protected_audit(
    executable: str, tmp_path: Path, transport_stubs: Path
) -> None:
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / f"site-b-private-{executable}.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(PASSWORD),
        )
    )
    public_path = tmp_path / f"site-b-public-{executable}"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        + b" entitlement-2026\n"
    )
    issued = datetime.now(UTC)
    grant = entitlement.issue_grant(
        private_key_path=private_path,
        private_key_password=PASSWORD,
        key_id="entitlement-2026",
        site_id="site-a",
        customer_id="customer-b",
        plan="annual",
        features=["remote-support"],
        serial=1,
        issued=issued,
        start=issued,
        end=issued.replace(year=issued.year + 1),
        grant_id=f"grant-site-b-{executable.replace('.', '-')}",
        now=issued,
    )
    entitlement_root = tmp_path / f"target-entitlements-{executable}"
    incoming_root = entitlement_root / "incoming"
    incoming_root.mkdir(parents=True)
    grant_bytes = entitlement.canonical_artifact_bytes(grant)
    grant_sha256 = entitlement.sha256_bytes(grant_bytes)
    reason = "approved cross-site rejection test"
    reason_sha256 = entitlement.sha256_bytes(reason.encode("utf-8"))
    incoming_grant = incoming_root / f"{OPERATION}-{grant_sha256}.json"
    incoming_grant.write_bytes(grant_bytes)
    reservation = {
        "grant_sha256": grant_sha256,
        "operation_id": OPERATION,
        "reason_sha256": reason_sha256,
        "schema_version": 1,
        "site_id": "site-b",
    }
    (incoming_root / f"{OPERATION}.reservation.json").write_bytes(
        entitlement.canonical_artifact_bytes(reservation)
    )
    site_identity = tmp_path / f"pinned-site-{executable}"
    site_identity.write_text("site-a\n", encoding="ascii", newline="\n")
    state_path = entitlement_root / "current.json"
    audit_path = entitlement_root / "audit.jsonl"
    time_path = entitlement_root / "last-seen.json"
    entitlement_script = tmp_path / f"entitlement-{executable}.py"
    shutil.copy2(ROOT / "tools" / "entitlement.py", entitlement_script)
    source = TARGET.read_text(encoding="utf-8")
    replacements = {
        r"C:\ProgramData\Ruisheng\runtime\python.exe": sys.executable,
        r"C:\ProgramData\Ruisheng\entitlement-runtime\vendor": str(
            (transport_stubs / "vendor").resolve()
        ),
        r"C:\ProgramData\Ruisheng\bin\entitlement.py": str(entitlement_script.resolve()),
        r"C:\ProgramData\Ruisheng\trust\entitlement-public-key": str(public_path),
        r"C:\ProgramData\Ruisheng\trust\entitlement-site-id": str(site_identity),
        r"C:\ProgramData\Ruisheng\entitlement-runtime-state.json": str(
            tmp_path / f"runtime-state-{executable}.json"
        ),
        r"C:\ProgramData\Ruisheng\entitlement-runtime-use.lock": str(
            tmp_path / f"runtime-use-{executable}.lock"
        ),
        r"C:\ProgramData\Ruisheng\entitlement-bootstrap-journal.json": str(
            tmp_path / f"no-bootstrap-journal-{executable}.json"
        ),
        r"C:\ProgramData\Ruisheng\entitlements\incoming": str(incoming_root),
        r"C:\ProgramData\Ruisheng\entitlements\current.json": str(state_path),
        r"C:\ProgramData\Ruisheng\entitlements\audit.jsonl": str(audit_path),
        r"C:\ProgramData\Ruisheng\entitlements\last-seen.json": str(time_path),
        r"C:\ProgramData\Ruisheng\entitlements": str(entitlement_root),
    }
    for original, replacement in replacements.items():
        source = source.replace(original, replacement)

    def replace_function(text: str, name: str, next_name: str, replacement: str) -> str:
        start = text.index("function " + name)
        end = text.index("function " + next_name, start)
        return text[:start] + replacement + "\n\n" + text[end:]

    source = replace_function(
        source,
        "Assert-ProtectedItem",
        "Set-ProtectedAcl",
        "function Assert-ProtectedItem { param([string]$Path,[string]$Kind) }",
    )
    source = replace_function(
        source,
        "Set-ProtectedAcl",
        "Ensure-ProtectedDirectory",
        "function Set-ProtectedAcl { param([string]$Path,[string]$Kind) }",
    )
    source = replace_function(
        source,
        "Enter-RuntimeUseLock",
        "Exit-RuntimeUseLock",
        "function Enter-RuntimeUseLock { return $null }",
    )
    source = replace_function(
        source,
        "Assert-ProtectedTree",
        "Set-ProtectedTree",
        "function Assert-ProtectedTree { param([string]$Root) }",
    )
    source = replace_function(
        source,
        "Repair-InheritedTransactionAcls",
        "Get-StrictAscii",
        "function Repair-InheritedTransactionAcls { }",
    )
    source = replace_function(
        source,
        "Assert-PinnedRuntime",
        "Get-PinnedSiteIdentity",
        "function Assert-PinnedRuntime { }",
    )
    script = tmp_path / f"target-verifier-site-mismatch-{executable}.ps1"
    # Windows PowerShell 5.1 treats UTF-8 without a BOM as the active code page.
    # The injected interpreter path may contain non-ASCII workspace components.
    script.write_text(source, encoding="utf-8-sig")
    completed = subprocess.run(
        [
            _powershell(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Action",
            "Install",
            "-SiteId",
            "site-b",
            "-OperationId",
            OPERATION.upper(),
            "-Reason",
            reason,
            "-GrantSha256",
            grant_sha256,
            "-ReasonSha256",
            reason_sha256,
            "-GrantPath",
            str(incoming_grant),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_powershell_environment(),
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["error_code"] == "site_mismatch"
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["error_code"] == "site_mismatch"
    assert entries[-1]["site_id"] == "site-a"
    assert not state_path.exists()


def _function(source: str, name: str, next_name: str) -> str:
    return (
        "function "
        + name
        + source.split("function " + name, 1)[1].split("function " + next_name, 1)[0]
    )


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_local_vendor_manifest_uses_a_streaming_byte_limit(executable: str, tmp_path: Path) -> None:
    source = REMOTE.read_text(encoding="utf-8")
    function = _function(source, "Read-LocalBoundedBytes", "Assert-LocalVendorSet")
    vendor_validation = _function(source, "Assert-LocalVendorSet", "New-ProtectedGrantSnapshot")
    assert "ReadAllBytes($manifest.FullName)" not in vendor_validation
    assert "Read-LocalBoundedBytes $manifest.FullName" in vendor_validation
    payload = tmp_path / f"bounded-manifest-{executable}.txt"
    payload.write_bytes(b"12345")
    harness = f"""
$ErrorActionPreference='Stop'
{function}
$exact=[Convert]::ToBase64String([byte[]](Read-LocalBoundedBytes {_ps_literal(payload)} 5 'bounded'))
try {{ Read-LocalBoundedBytes {_ps_literal(payload)} 4 'bounded' > $null; $errorCode='missed' }}
catch {{ $errorCode=$_.Exception.Message }}
[ordered]@{{exact=$exact;error_code=$errorCode}} | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "exact": base64.b64encode(b"12345").decode("ascii"),
        "error_code": "bounded",
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_bootstrap_journal_status_advances_time_but_install_does_not(
    executable: str, tmp_path: Path, transport_stubs: Path
) -> None:
    private = Ed25519PrivateKey.generate()
    public_path = tmp_path / f"journal-public-{executable}"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        + b" entitlement-journal-test\n"
    )
    entitlement_script = tmp_path / f"journal-entitlement-{executable}.py"
    shutil.copy2(ROOT / "tools" / "entitlement.py", entitlement_script)
    entitlement_root = tmp_path / f"journal-entitlements-{executable}"
    incoming_root = entitlement_root / "incoming"
    incoming_root.mkdir(parents=True)
    time_path = entitlement_root / "last-seen.json"
    bootstrap_journal = tmp_path / f"bootstrap-journal-{executable}.json"
    bootstrap_journal.write_text("{}\n", encoding="ascii", newline="\n")

    source = TARGET.read_text(encoding="utf-8")
    replacements = {
        r"C:\ProgramData\Ruisheng\runtime\python.exe": sys.executable,
        r"C:\ProgramData\Ruisheng\entitlement-runtime\vendor": str(
            (transport_stubs / "vendor").resolve()
        ),
        r"C:\ProgramData\Ruisheng\bin\entitlement.py": str(entitlement_script.resolve()),
        r"C:\ProgramData\Ruisheng\trust\entitlement-public-key": str(public_path),
        r"C:\ProgramData\Ruisheng\entitlement-bootstrap-journal.json": str(bootstrap_journal),
        r"C:\ProgramData\Ruisheng\entitlements\incoming": str(incoming_root),
        r"C:\ProgramData\Ruisheng\entitlements\current.json": str(
            entitlement_root / "current.json"
        ),
        r"C:\ProgramData\Ruisheng\entitlements\audit.jsonl": str(entitlement_root / "audit.jsonl"),
        r"C:\ProgramData\Ruisheng\entitlements\last-seen.json": str(time_path),
        r"C:\ProgramData\Ruisheng\entitlements": str(entitlement_root),
    }
    for original, replacement in replacements.items():
        source = source.replace(original, replacement)
    for name, next_name, replacement in (
        (
            "Assert-ProtectedItem",
            "Set-ProtectedAcl",
            "function Assert-ProtectedItem { param([string]$Path,[string]$Kind); "
            "$type=if($Kind -eq 'File'){'Leaf'}else{'Container'}; "
            "if(-not(Test-Path -LiteralPath $Path -PathType $type)){throw 'protected_item_missing'} }",
        ),
        (
            "Set-ProtectedAcl",
            "Ensure-ProtectedDirectory",
            "function Set-ProtectedAcl { param([string]$Path,[string]$Kind) }",
        ),
        (
            "Enter-RuntimeUseLock",
            "Exit-RuntimeUseLock",
            "function Enter-RuntimeUseLock { return $null }",
        ),
        (
            "Assert-ProtectedTree",
            "Set-ProtectedTree",
            "function Assert-ProtectedTree { param([string]$Root) }",
        ),
        (
            "Repair-InheritedTransactionAcls",
            "Get-StrictAscii",
            "function Repair-InheritedTransactionAcls { }",
        ),
    ):
        source = _replace_function(source, name, next_name, replacement)
    script = tmp_path / f"target-journal-{executable}.ps1"
    script.write_text(source, encoding="utf-8-sig")

    def run(action: str) -> subprocess.CompletedProcess[str]:
        arguments = [
            _powershell(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Action",
            action,
            "-SiteId",
            "site-journal",
        ]
        if action == "Install":
            reason = "approved journal behavior test"
            grant_sha256 = "0" * 64
            arguments.extend(
                [
                    "-OperationId",
                    OPERATION,
                    "-Reason",
                    reason,
                    "-GrantSha256",
                    grant_sha256,
                    "-ReasonSha256",
                    entitlement.sha256_bytes(reason.encode("utf-8")),
                    "-GrantPath",
                    str(incoming_root / f"{OPERATION}-{grant_sha256}.json"),
                ]
            )
        return subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=_powershell_environment(),
        )

    status = run("Status")
    assert status.returncode == 2, status.stdout + status.stderr
    status_result = json.loads(status.stdout)
    assert status_result["status"] == "uncertain"
    assert status_result["error_code"] == "bootstrap_transaction_uncertain"
    assert time_path.is_file()
    observed_time = time_path.read_bytes()

    bootstrap_journal.unlink()
    bootstrap_journal.mkdir()
    damaged = run("Status")
    assert damaged.returncode == 2, damaged.stdout + damaged.stderr
    damaged_result = json.loads(damaged.stdout)
    assert damaged_result["status"] == "uncertain"
    assert damaged_result["error_code"] == "bootstrap_transaction_uncertain"
    bootstrap_journal.rmdir()
    bootstrap_journal.write_text("{}\n", encoding="ascii", newline="\n")

    install = run("Install")
    assert install.returncode == 2, install.stdout + install.stderr
    install_result = json.loads(install.stdout)
    assert install_result["status"] == "uncertain"
    assert install_result["error_code"] == "bootstrap_transaction_uncertain"
    assert time_path.read_bytes() == observed_time


def _replace_function(source: str, name: str, next_name: str, replacement: str) -> str:
    start = source.index("function " + name)
    end = source.index("function " + next_name, start)
    return source[:start] + replacement + "\n\n" + source[end:]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_grant_prepare_reserves_capacity_for_pending_uploads(
    executable: str, tmp_path: Path
) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = _function(source, "Get-GrantIncomingPaths", "Enter-RuntimeUseLock")
    incoming = tmp_path / f"grant-capacity-{executable}"
    incoming.mkdir()
    stale_operation = "00000000-0000-4000-8000-000000000002"
    stale = {
        "grant_sha256": "1" * 64,
        "operation_id": stale_operation,
        "reason_sha256": "2" * 64,
        "schema_version": 1,
        "site_id": SITE,
    }
    stale_path = incoming / f"{stale_operation}.reservation.json"
    stale_path.write_bytes(entitlement.canonical_artifact_bytes(stale))
    harness = f"""
$ErrorActionPreference='Stop'
$script:IncomingRoot={_ps_literal(incoming)}
$script:IncomingLockPath=Join-Path $script:IncomingRoot '.incoming.lock'
$script:MaxReservationBytes=512
$script:MaxGrantBytes=10
$script:MaxIncomingBytes=10
$script:MaxIncomingReservations=2048
$OperationId={_ps_literal(OPERATION)}
$GrantSha256={"0" * 64!r}
$ReasonSha256={"3" * 64!r}
$SiteId={_ps_literal(SITE)}
function Assert-ProtectedItem {{ param([string]$Path,[string]$Kind) }}
function Set-ProtectedAcl {{ param([string]$Path,[string]$Kind) }}
function Read-BoundedBytes {{ param([string]$Path,[long]$Maximum,[string]$ErrorCode); return [IO.File]::ReadAllBytes($Path) }}
function Get-StrictAscii {{ param([byte[]]$Bytes,[string]$ErrorCode); return [Text.Encoding]::ASCII.GetString($Bytes) }}
function Assert-ExactFields {{ param($Value,[string[]]$Expected) }}
{functions}
$paths=Get-GrantIncomingPaths
$script:ReservationPath=$paths.reservation
Prepare-GrantReservation
[ordered]@{{stale_exists=(Test-Path -LiteralPath {_ps_literal(stale_path)});new_exists=(Test-Path -LiteralPath $script:ReservationPath)}}|ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"stale_exists": False, "new_exists": True}


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_target_authorize_enforces_signed_features_and_status(  # noqa: PLR0915
    executable: str, tmp_path: Path, transport_stubs: Path
) -> None:
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / f"authorize-private-{executable}.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(PASSWORD),
        )
    )
    public_path = tmp_path / f"authorize-public-{executable}"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        + b" entitlement-2026\n"
    )
    entitlement_root = tmp_path / f"authorize-entitlements-{executable}"
    entitlement_root.mkdir()
    state_path = entitlement_root / "current.json"
    time_path = entitlement_root / "last-seen.json"
    site_identity = tmp_path / f"authorize-site-{executable}"
    site_identity.write_text("site-a\n", encoding="ascii", newline="\n")
    entitlement_script = tmp_path / f"authorize-entitlement-{executable}.py"
    shutil.copy2(ROOT / "tools" / "entitlement.py", entitlement_script)

    source = TARGET.read_text(encoding="utf-8")
    replacements = {
        r"C:\ProgramData\Ruisheng\runtime\python.exe": sys.executable,
        r"C:\ProgramData\Ruisheng\entitlement-runtime\vendor": str(
            (transport_stubs / "vendor").resolve()
        ),
        r"C:\ProgramData\Ruisheng\bin\entitlement.py": str(entitlement_script.resolve()),
        r"C:\ProgramData\Ruisheng\trust\entitlement-public-key": str(public_path),
        r"C:\ProgramData\Ruisheng\trust\entitlement-site-id": str(site_identity),
        r"C:\ProgramData\Ruisheng\entitlement-runtime-state.json": str(
            tmp_path / f"authorize-runtime-state-{executable}.json"
        ),
        r"C:\ProgramData\Ruisheng\entitlement-runtime-use.lock": str(
            tmp_path / f"authorize-runtime-use-{executable}.lock"
        ),
        r"C:\ProgramData\Ruisheng\entitlement-bootstrap-journal.json": str(
            tmp_path / f"authorize-no-bootstrap-{executable}.json"
        ),
        r"C:\ProgramData\Ruisheng\entitlements\current.json": str(state_path),
        r"C:\ProgramData\Ruisheng\entitlements\audit.jsonl": str(entitlement_root / "audit.jsonl"),
        r"C:\ProgramData\Ruisheng\entitlements\last-seen.json": str(time_path),
        r"C:\ProgramData\Ruisheng\entitlements": str(entitlement_root),
    }
    for original, replacement in replacements.items():
        source = source.replace(original, replacement)
    for name, next_name, replacement in (
        (
            "Assert-ProtectedItem",
            "Set-ProtectedAcl",
            "function Assert-ProtectedItem { param([string]$Path,[string]$Kind) }",
        ),
        (
            "Set-ProtectedAcl",
            "Ensure-ProtectedDirectory",
            "function Set-ProtectedAcl { param([string]$Path,[string]$Kind) }",
        ),
        (
            "Enter-RuntimeUseLock",
            "Exit-RuntimeUseLock",
            "function Enter-RuntimeUseLock { return $null }",
        ),
        (
            "Assert-ProtectedTree",
            "Set-ProtectedTree",
            "function Assert-ProtectedTree { param([string]$Root) }",
        ),
        (
            "Repair-InheritedTransactionAcls",
            "Get-StrictAscii",
            "function Repair-InheritedTransactionAcls { }",
        ),
        (
            "Assert-PinnedRuntime",
            "Get-PinnedSiteIdentity",
            "function Assert-PinnedRuntime { }",
        ),
    ):
        source = _replace_function(source, name, next_name, replacement)
    script = tmp_path / f"target-authorize-{executable}.ps1"
    script.write_text(source, encoding="utf-8-sig")
    observed = datetime.now(UTC).replace(microsecond=0)
    serial = 0

    def install_state(
        *, features: list[str], start: datetime, end: datetime, grace_days: int = 7
    ) -> None:
        nonlocal serial
        serial += 1
        grant = entitlement.issue_grant(
            private_key_path=private_path,
            private_key_password=PASSWORD,
            key_id="entitlement-2026",
            site_id="site-a",
            customer_id="customer-a",
            plan="annual",
            features=features,
            serial=serial,
            issued=min(observed, start),
            start=start,
            end=end,
            grace_days=grace_days,
            grant_id=f"authorize-{executable}-{serial}",
            now=observed,
        )
        state_path.write_bytes(entitlement.canonical_artifact_bytes(grant))

    def authorize(feature: str) -> tuple[int, dict[str, object]]:
        completed = subprocess.run(
            [
                _powershell(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script),
                "-Action",
                "Authorize",
                "-SiteId",
                "site-a",
                "-Feature",
                feature,
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=_powershell_environment(),
        )
        return completed.returncode, cast(dict[str, object], json.loads(completed.stdout))

    install_state(
        features=["remote-support"],
        start=observed - timedelta(days=1),
        end=observed + timedelta(days=1),
    )
    active_code, active = authorize("remote-support")
    assert active_code == 0
    assert active["status"] == "authorized"
    assert active["entitlement_status"] == "active"
    absent_code, absent = authorize("security-patches")
    assert absent_code == 2
    assert absent["error_code"] == "entitlement_feature_denied"

    install_state(
        features=["remote-support"],
        start=observed - timedelta(days=30),
        end=observed - timedelta(days=1),
    )
    grace_code, grace = authorize("remote-support")
    assert grace_code == 0
    assert grace["entitlement_status"] == "grace"

    install_state(
        features=["remote-support"],
        start=observed - timedelta(days=30),
        end=observed - timedelta(days=8),
    )
    expired_code, expired = authorize("remote-support")
    assert expired_code == 2
    assert expired["error_code"] == "entitlement_feature_denied"

    install_state(
        features=["remote-support"],
        start=observed + timedelta(days=1),
        end=observed + timedelta(days=2),
    )
    pending_code, pending = authorize("remote-support")
    assert pending_code == 2
    assert pending["error_code"] == "entitlement_feature_denied"

    state_path.unlink()
    missing_code, missing = authorize("remote-support")
    assert missing_code == 2
    assert missing["error_code"] == "entitlement_feature_denied"
    time_path.write_bytes(b"not-canonical\n")
    uncertain_code, uncertain = authorize("remote-support")
    assert uncertain_code == 2
    assert uncertain["status"] != "authorized"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_vendor_manifest_rejects_missing_and_extra_files(executable: str, tmp_path: Path) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = (
        _function(source, "Get-SafeTreeItems", "Assert-ProtectedTree")
        + _function(source, "Get-StrictAscii", "Assert-VendorSet")
        + _function(source, "Assert-VendorSet", "Assert-PinnedRuntime")
    )
    vendor = tmp_path / f"vendor-{executable}"
    vendor.mkdir()
    manifest = tmp_path / f"manifest-{executable}.sha256"
    payload = vendor / "cryptography.pyd"
    payload.write_bytes(b"trusted")
    digest = __import__("hashlib").sha256(payload.read_bytes()).hexdigest()
    manifest.write_text(f"{digest}\tcryptography.pyd\n", encoding="ascii", newline="\n")
    harness = f"""
$ErrorActionPreference='Stop'
$script:VendorRoot={_ps_literal(vendor)}
$script:VendorManifestPath={_ps_literal(manifest)}
$script:MaxManifestBytes=8388608
$script:MaxVendorFiles=10000
$script:MaxVendorBytes=536870912
$script:MaxTreeItems=20000
function Assert-ProtectedItem {{ param([string]$Path,[string]$Kind); if(-not (Test-Path -LiteralPath $Path)){{throw 'protected_item_missing'}} }}
{functions}
try {{ Assert-VendorSet; [Console]::Out.Write('ok') }} catch {{ [Console]::Out.Write($_.Exception.Message); exit 2 }}
"""
    valid = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr
    (vendor / "unexpected.py").write_text("pass\n", encoding="ascii")
    invalid = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert invalid.returncode == 2
    assert invalid.stdout == "vendor_file_set_invalid"
    manifest.unlink()
    missing = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert missing.returncode == 2
    assert missing.stdout == "protected_item_missing"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_default_inherited_acl_is_rejected(executable: str, tmp_path: Path) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = _function(source, "Get-IdentitySid", "Assert-ProtectedItem") + _function(
        source, "Assert-ProtectedItem", "Set-ProtectedAcl"
    )
    path = tmp_path / f"ordinary-{executable}"
    path.mkdir()
    harness = f"""
$ErrorActionPreference='Stop'
$script:AllowedSids=@('S-1-5-18','S-1-5-32-544')
{functions}
try {{ Assert-ProtectedItem -Path {_ps_literal(path)} -Kind Directory; [Console]::Out.Write('unexpected') }}
catch {{ [Console]::Out.Write($_.Exception.Message); exit 2 }}
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert completed.returncode == 2
    assert completed.stdout in {"path_acl_unprotected", "path_owner_invalid", "path_acl_invalid"}
