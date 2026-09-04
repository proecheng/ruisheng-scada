from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
REMOTE = ROOT / "tools" / "remote_entitlement_runtime_install.ps1"
TARGET = ROOT / "tools" / "target_entitlement_runtime_installer.ps1"
TARGET_VERIFIER = ROOT / "tools" / "target_entitlement_verifier.ps1"
OPERATION = "00000000-0000-4000-8000-000000000001"
SITE = "site-win-oaucm8uqugh"
SIGNED_FILES = [
    "entitlement-public-key",
    "entitlement.py",
    "runtime-metadata.json",
    "target_entitlement_runtime_installer.ps1",
    "target_entitlement_verifier.ps1",
    "vendor-manifest.sha256",
    "vendor.zip",
]
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

public static class RuntimeTransportStub
{
    private static string Required(string name)
    {
        string value = Environment.GetEnvironmentVariable(name);
        if (String.IsNullOrEmpty(value)) throw new InvalidOperationException("missing " + name);
        return value;
    }

    public static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        string executable = Process.GetCurrentProcess().MainModule.FileName;
        string kind = Path.GetFileName(executable).StartsWith("scp", StringComparison.OrdinalIgnoreCase)
            ? "scp" : "ssh";
        string stdin = Console.In.ReadToEnd();
        string log = Required("RUNTIME_STUB_LOG");
        int calls = File.Exists(log)
            ? File.ReadAllLines(log).Count(line => line.StartsWith("ssh|", StringComparison.Ordinal))
            : 0;
        string encodedArgs = Convert.ToBase64String(Encoding.UTF8.GetBytes(String.Join("\0", args)));
        string encodedStdin = Convert.ToBase64String(Encoding.UTF8.GetBytes(stdin));
        File.AppendAllText(log, kind + "|" + encodedArgs + "|" + encodedStdin + Environment.NewLine,
            new UTF8Encoding(false));
        string mode = Required("RUNTIME_STUB_MODE");
        if (kind == "scp")
        {
            if (mode == "upload-fail") { Console.Error.Write("upload failed"); return 23; }
            return 0;
        }
        int call = calls + 1;
        string operation = Required("RUNTIME_STUB_OPERATION");
        string site = Required("RUNTIME_STUB_SITE");
        if (call == 1)
        {
            if (mode == "replace-source")
            {
                File.WriteAllText(Path.Combine(Required("RUNTIME_STUB_BUNDLE"), "entitlement.py"),
                    "replaced after approval", new UTF8Encoding(false));
            }
            Console.Out.Write("{\"schema_version\":1,\"ok\":true,\"status\":\"prepared\"," +
                "\"operation_id\":\"" + operation + "\",\"site_id\":\"" + site + "\"," +
                "\"incoming_path\":\"C:\\\\ProgramData\\\\Ruisheng\\\\entitlement-bootstrap-incoming\\\\" + operation + "\"}");
            return 0;
        }
        if (call >= 3 || (mode == "upload-fail" && call >= 2))
        {
            Console.Out.Write("{\"schema_version\":1,\"ok\":true,\"status\":\"cleaned\"," +
                "\"operation_id\":\"" + operation + "\",\"removed\":true}");
            return 0;
        }
        if (mode == "timeout") { Thread.Sleep(5000); return 0; }
        if (mode == "malformed") { Console.Out.Write("not-json"); return 0; }
        if (mode == "rejected" || mode == "busy")
        {
            bool busy = mode == "busy";
            Console.Out.Write("{\"schema_version\":1,\"ok\":false,\"status\":\"" +
                (busy ? "uncertain" : "rejected") + "\",\"error_code\":\"" +
                (busy ? "bootstrap_busy" : "bootstrap_signature_invalid") +
                "\",\"safety_preserved\":true," +
                "\"collection_preserved\":true,\"alarms_preserved\":true,\"data_preserved\":true}");
            return 2;
        }
        Console.Out.Write("{\"schema_version\":1,\"ok\":true,\"status\":\"runtime_installed\"," +
            "\"operation_id\":\"" + operation + "\",\"site_id\":\"" + site + "\"," +
            "\"entitlement_sha256\":\"" + Required("RUNTIME_ENTITLEMENT_SHA") + "\"," +
            "\"verifier_sha256\":\"" + Required("RUNTIME_VERIFIER_SHA") + "\"," +
            "\"public_key_sha256\":\"" + Required("RUNTIME_PUBLIC_KEY_SHA") + "\"," +
            "\"vendor_archive_sha256\":\"" + Required("RUNTIME_VENDOR_SHA") + "\"," +
            "\"runtime_epoch\":1,\"entitlement_key_generation\":1," +
            "\"services_restarted\":false,\"device_configuration_changed\":false}");
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
    system_root = Path(environment.get("SYSTEMROOT", r"C:\Windows"))
    program_files = Path(environment.get("ProgramFiles", r"C:\Program Files"))
    user_profile = Path(environment.get("USERPROFILE", str(Path.home())))
    environment["PSModulePath"] = os.pathsep.join(
        (
            str(user_profile / "Documents/WindowsPowerShell/Modules"),
            str(program_files / "WindowsPowerShell/Modules"),
            str(system_root / "System32/WindowsPowerShell/v1.0/Modules"),
        )
    )
    return environment


@pytest.fixture(scope="session")
def runtime_transport_stubs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    stub_dir = tmp_path_factory.mktemp("runtime-transport-stubs")
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
    return stub_dir


@pytest.fixture
def signed_bundle(tmp_path: Path) -> tuple[Path, Path]:
    ssh_keygen = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/OpenSSH/ssh-keygen.exe"
    )
    if not ssh_keygen.is_file():
        pytest.skip("Windows OpenSSH ssh-keygen is unavailable")
    key = tmp_path / "release-key"
    generated = subprocess.run(
        [str(ssh_keygen), "-q", "-t", "ed25519", "-N", "", "-C", "test-release", "-f", str(key)],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    fields = (tmp_path / "release-key.pub").read_text(encoding="ascii").split()
    allowed = tmp_path / "release-allowed-signers"
    allowed.write_text(
        f"ruisheng-release {fields[0]} {fields[1]}\n", encoding="ascii", newline="\n"
    )

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "entitlement-public-key").write_text(
        f"ssh-ed25519 {fields[1]} entitlement-test\n", encoding="ascii", newline="\n"
    )
    shutil.copy2(ROOT / "tools/entitlement.py", bundle / "entitlement.py")
    shutil.copy2(TARGET, bundle / "target_entitlement_runtime_installer.ps1")
    shutil.copy2(
        ROOT / "tools/target_entitlement_verifier.ps1", bundle / "target_entitlement_verifier.ps1"
    )
    (bundle / "runtime-metadata.json").write_text(
        '{"entitlement_key_generation":1,"runtime_epoch":1,"schema_version":1}\n',
        encoding="ascii",
        newline="\n",
    )
    (bundle / "vendor-manifest.sha256").write_text(
        "0" * 64 + "\tx.py\n", encoding="ascii", newline="\n"
    )
    (bundle / "vendor.zip").write_bytes(b"test-vendor-archive")
    lines = [
        f"{hashlib.sha256((bundle / name).read_bytes()).hexdigest()}  {name}"
        for name in SIGNED_FILES
    ]
    (bundle / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    signed = subprocess.run(
        [
            str(ssh_keygen),
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            "ruisheng-entitlement-runtime-v1",
            str(bundle / "SHA256SUMS"),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert signed.returncode == 0, signed.stderr.decode(errors="replace")
    return bundle, allowed


def _environment(stub_dir: Path, log_path: Path, mode: str, bundle: Path) -> dict[str, str]:
    environment = _powershell_environment()
    environment["PATH"] = f"{stub_dir}{os.pathsep}{environment['PATH']}"
    environment.update(
        {
            "RUNTIME_STUB_LOG": str(log_path),
            "RUNTIME_STUB_MODE": mode,
            "RUNTIME_STUB_OPERATION": OPERATION,
            "RUNTIME_STUB_SITE": SITE,
            "RUNTIME_ENTITLEMENT_SHA": hashlib.sha256(
                (bundle / "entitlement.py").read_bytes()
            ).hexdigest(),
            "RUNTIME_VERIFIER_SHA": hashlib.sha256(
                (bundle / "target_entitlement_verifier.ps1").read_bytes()
            ).hexdigest(),
            "RUNTIME_PUBLIC_KEY_SHA": hashlib.sha256(
                (bundle / "entitlement-public-key").read_bytes()
            ).hexdigest(),
            "RUNTIME_VENDOR_SHA": hashlib.sha256((bundle / "vendor.zip").read_bytes()).hexdigest(),
            "RUNTIME_STUB_BUNDLE": str(bundle),
        }
    )
    return environment


def _run(
    stub_dir: Path,
    tmp_path: Path,
    bundle: Path,
    allowed: Path,
    mode: str,
    *,
    timeout: int = 10,
    target: str = "operator@100.64.0.2",
    path_value: str | None = None,
    fail_snapshot_cleanup: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    log = tmp_path / f"{mode}.log"
    audit = tmp_path / f"audit-{mode}"
    source = REMOTE.read_text(encoding="utf-8")
    source = source.replace(
        r"C:\Windows\System32\OpenSSH\ssh.exe", str(stub_dir / "ssh.exe")
    ).replace(r"C:\Windows\System32\OpenSSH\scp.exe", str(stub_dir / "scp.exe"))
    source = source.replace(
        r"C:\ProgramData\Ruisheng\publisher-trust\release-allowed-signers", str(allowed)
    )
    source = source.replace(
        r"$env:LOCALAPPDATA\Ruisheng\entitlement-runtime-snapshots",
        str(tmp_path / "runtime-snapshots"),
    )
    start = source.index("function Assert-FixedExecutable")
    end = source.index("function Get-NormalizedLocalPath")
    source = (
        source[:start]
        + "function Assert-FixedExecutable { param($Path,$Publisher) }\n\n"
        + source[end:]
    )
    start = source.index("function Assert-LocalReleaseTrustAcl")
    end = source.index("function New-ProtectedBundleSnapshot")
    source = (
        source[:start]
        + "function Assert-LocalReleaseTrustAcl { param($Path,$Kind,[switch]$RequireProtected) }\n\n"
        + source[end:]
    )
    if fail_snapshot_cleanup:
        start = source.index("function Remove-ProtectedBundleSnapshot")
        end = source.index("function ConvertTo-NativeArgument", start)
        source = (
            source[:start]
            + 'function Remove-ProtectedBundleSnapshot { throw "bundle_snapshot_cleanup_failed" }\n\n'
            + source[end:]
        )
    test_script = tmp_path / "remote_entitlement_runtime_install.ps1"
    test_script.write_text(source, encoding="utf-8")
    environment = _environment(stub_dir, log, mode, bundle)
    if path_value is not None:
        environment["PATH"] = path_value
    completed = subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(test_script),
            "-Target",
            target,
            "-SiteId",
            SITE,
            "-BundlePath",
            str(bundle),
            "-OperationId",
            OPERATION,
            "-Approved",
            "-TransportTimeoutSeconds",
            str(timeout),
            "-AuditDirectory",
            str(audit),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=environment,
    )
    return completed, log, audit / "entitlement-runtime-install.jsonl"


def _calls(path: Path) -> list[tuple[str, list[str], str]]:
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        kind, encoded_args, encoded_stdin = line.split("|", 2)
        args = base64.b64decode(encoded_args).decode("utf-8").split("\0")
        stdin = base64.b64decode(encoded_stdin).decode("utf-8")
        calls.append((kind, args, stdin))
    return calls


def _remote_script(stdin: str) -> str:
    return base64.b64decode(stdin.strip()).decode("utf-8")


def _assert_key_only(arguments: list[str]) -> None:
    values = [arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == "-o"]
    assert values == SSH_OPTIONS


def test_runtime_bundle_is_uploaded_then_preverified_before_signed_installer_executes(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, log, audit = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "valid")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "runtime_installed"
    assert json.loads(audit.read_text(encoding="utf-8-sig"))["result"] == "runtime_installed"
    calls = _calls(log)
    assert [kind for kind, _, _ in calls] == ["ssh", "scp", "ssh", "ssh"]
    for _, arguments, _ in calls:
        _assert_key_only(arguments)
    assert calls[1][1][-1] == (
        f"operator@100.64.0.2:C:/ProgramData/Ruisheng/entitlement-bootstrap-incoming/{OPERATION}/"
    )
    uploaded_sources = [path for path in calls[1][1] if str(tmp_path / "runtime-snapshots") in path]
    assert len(uploaded_sources) == len(SIGNED_FILES) + 2
    assert all(str(bundle) not in path for path in uploaded_sources)
    prepare = _remote_script(calls[0][2])
    execute = _remote_script(calls[2][2])
    cleanup = _remote_script(calls[3][2])
    for remote_script in (prepare, execute, cleanup):
        assert ".reservation.json" in remote_script
        assert "sums_sha256" in remote_script
        assert "bundle_bytes" in remote_script
        assert "bootstrap_operation_conflict" in remote_script
    assert "$maxReservations=2048" in prepare
    assert "$maxIncomingBytes=134217728" in prepare
    assert "bootstrap_incoming_store_full" in prepare
    assert prepare.index("$lock=EnterLock") < prepare.index("$reservations=@{}")
    assert "$actualBytes-ne$bundleBytes" in execute
    assert "ProtectFile $item.FullName" in execute
    assert execute.index("-Y verify") < execute.index("& $installer")
    assert execute.index("bootstrap_installer_changed") < execute.index("& $installer")
    assert "Remove-Item -LiteralPath $bundle -Recurse -Force" not in execute
    assert "Remove-Item -LiteralPath $bundle -Recurse -Force" in cleanup
    assert cleanup.index("$lock=EnterLock") < cleanup.index(
        "Remove-Item -LiteralPath $bundle -Recurse -Force"
    )
    assert cleanup.index("bootstrap_operation_conflict") < cleanup.index(
        "Remove-Item -LiteralPath $bundle -Recurse -Force"
    )
    target = TARGET.read_text(encoding="utf-8")
    target_main = target.split("$bootstrapLock = Enter-BootstrapLock", 1)[1]
    assert target_main.index("$bundleRoot =") < target_main.index("Get-AuthenticatedSums")
    assert "$MaxBundleBytes = 128MB" in target


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_runtime_prepare_rejects_conflicting_digest_and_preserves_recovery_evidence(
    executable: str,
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, log, _ = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "valid")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    prepare = _remote_script(_calls(log)[0][2])
    incoming = tmp_path / f"incoming-{executable}"
    prepare = prepare.replace(
        r"C:\ProgramData\Ruisheng\entitlement-bootstrap-incoming", str(incoming)
    )
    prepare = prepare.replace(
        r"C:\ProgramData\Ruisheng\entitlement-bootstrap.lock",
        str(tmp_path / f"bootstrap-{executable}.lock"),
    )
    localized: list[str] = []
    for line in prepare.splitlines():
        if line.startswith("function AssertItem("):
            localized.append(
                "function AssertItem([string]$path,[string]$kind){"
                "$type=if($kind -eq 'File'){'Leaf'}else{'Container'};"
                "if(-not(Test-Path -LiteralPath $path -PathType $type)){throw 'path_missing'}}"
            )
        elif line.startswith("function Protect("):
            localized.append("function Protect([string]$path,[string]$kind){}")
        elif line.startswith(
            "$identity=[Security.Principal.WindowsIdentity]::GetCurrent()"
        ) or line.startswith("AssertItem 'C:\\ProgramData\\Ruisheng"):
            continue
        else:
            localized.append(line)
    prepare = "\n".join(localized)

    first = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", prepare],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads(first.stdout)["status"] == "prepared"

    reservation = incoming / f"{OPERATION}.reservation.json"
    operation_root = incoming / OPERATION
    sentinel = operation_root / "partial-upload.bin"
    sentinel.write_bytes(b"retain for deterministic retry")
    original_reservation = reservation.read_bytes()
    sums_digest = hashlib.sha256((bundle / "SHA256SUMS").read_bytes()).hexdigest()
    encoded_digest = base64.b64encode(sums_digest.encode("ascii")).decode("ascii")
    conflicting_digest = "f" * 64 if sums_digest != "f" * 64 else "e" * 64
    encoded_conflict = base64.b64encode(conflicting_digest.encode("ascii")).decode("ascii")
    conflicting_prepare, replacements = re.subn(
        re.escape(encoded_digest), encoded_conflict, prepare, count=1
    )
    assert replacements == 1

    conflict = subprocess.run(
        [
            _powershell(executable),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            conflicting_prepare,
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert conflict.returncode != 0
    assert "bootstrap_operation_conflict" in conflict.stdout + conflict.stderr
    assert reservation.read_bytes() == original_reservation
    assert sentinel.read_bytes() == b"retain for deterministic retry"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("limit_kind", ["reservations", "bytes"])
def test_runtime_prepare_enforces_incoming_reservation_limits(
    executable: str,
    limit_kind: str,
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, log, _ = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "valid")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    prepare = _remote_script(_calls(log)[0][2])
    incoming = tmp_path / f"limited-incoming-{executable}-{limit_kind}"
    lock_path = tmp_path / f"limited-bootstrap-{executable}-{limit_kind}.lock"
    prepare = prepare.replace(
        r"C:\ProgramData\Ruisheng\entitlement-bootstrap-incoming", str(incoming)
    ).replace(r"C:\ProgramData\Ruisheng\entitlement-bootstrap.lock", str(lock_path))
    if limit_kind == "reservations":
        prepare = prepare.replace("$maxReservations=2048", "$maxReservations=1")
    else:
        bundle_bytes = sum(path.stat().st_size for path in bundle.iterdir())
        prepare = prepare.replace(
            "$maxIncomingBytes=134217728", f"$maxIncomingBytes={bundle_bytes}"
        )
    localized: list[str] = []
    for line in prepare.splitlines():
        if line.startswith("function AssertItem("):
            localized.append(
                "function AssertItem([string]$path,[string]$kind){"
                "$type=if($kind -eq 'File'){'Leaf'}else{'Container'};"
                "if(-not(Test-Path -LiteralPath $path -PathType $type)){throw 'path_missing'}}"
            )
        elif line.startswith("function Protect("):
            localized.append("function Protect([string]$path,[string]$kind){}")
        elif line.startswith(
            "$identity=[Security.Principal.WindowsIdentity]::GetCurrent()"
        ) or line.startswith("AssertItem 'C:\\ProgramData\\Ruisheng"):
            continue
        else:
            localized.append(line)
    prepare = "\n".join(localized)

    incoming.mkdir()
    existing_operation = "00000000-0000-4000-8000-000000000002"
    existing_reservation = {
        "bundle_bytes": 1,
        "operation_id": existing_operation,
        "schema_version": 1,
        "site_id": SITE,
        "sums_sha256": "0" * 64,
    }
    (incoming / f"{existing_operation}.reservation.json").write_text(
        json.dumps(existing_reservation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
        newline="\n",
    )
    (incoming / existing_operation).mkdir()
    rejected = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", prepare],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert rejected.returncode != 0
    assert "bootstrap_incoming_store_full" in rejected.stdout + rejected.stderr
    assert not (incoming / f"{OPERATION}.reservation.json").exists()
    assert not (incoming / OPERATION).exists()


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_local_runtime_control_files_use_a_streaming_byte_limit(
    executable: str, tmp_path: Path
) -> None:
    source = REMOTE.read_text(encoding="utf-8")
    function = _function(source, "Read-LocalBoundedBytes", "Get-LocalBundleIdentity")
    bundle_identity = _function(source, "Get-LocalBundleIdentity", "Invoke-SshScript")
    assert "ReadAllBytes($sumsPath)" not in bundle_identity
    assert "ReadAllBytes($script:LocalAllowedSignersPath)" not in bundle_identity
    assert "ReadAllBytes($metadataPath)" not in bundle_identity
    assert bundle_identity.count("Read-LocalBoundedBytes") == 3
    payload = tmp_path / f"bounded-runtime-{executable}.txt"
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
def test_runtime_snapshot_enforces_total_limit_while_copying(
    executable: str, tmp_path: Path
) -> None:
    source = REMOTE.read_text(encoding="utf-8")
    function = _function(source, "New-ProtectedBundleSnapshot", "Remove-ProtectedBundleSnapshot")
    bundle = tmp_path / f"oversized-snapshot-source-{executable}"
    snapshot = tmp_path / f"oversized-snapshot-target-{executable}"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"123")
    (bundle / "b").write_bytes(b"456")
    harness = f"""
$ErrorActionPreference='Stop'
$BundlePath={_ps_literal(bundle)}
$OperationId={_ps_literal(OPERATION)}
$script:ExpectedBundleFiles=@('a','b')
$script:MaxBundleFileBytes=10
$script:MaxBundleBytes=5
$script:SnapshotRoot={_ps_literal(snapshot)}
$script:SnapshotHandles=New-Object Collections.Generic.List[IDisposable]
$script:SnapshotDirectory=''
function Get-NormalizedLocalPath([string]$Path,[string]$ErrorCode){{return [IO.Path]::GetFullPath($Path)}}
function Set-LocalProtectedAcl([string]$Path,[string]$Kind){{}}
{function}
try{{[void](New-ProtectedBundleSnapshot);[Console]::Out.Write('unexpected')}}
catch{{[Console]::Out.Write($_.Exception.Message);exit 2}}
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
    assert completed.stdout == "bundle_size_invalid"


@pytest.mark.parametrize(
    ("holder_executable", "contender_executable"),
    [("powershell.exe", "pwsh.exe"), ("pwsh.exe", "powershell.exe")],
)
def test_runtime_use_lock_excludes_replacement_across_processes(
    holder_executable: str,
    contender_executable: str,
    tmp_path: Path,
) -> None:
    verifier = TARGET_VERIFIER.read_text(encoding="utf-8")
    installer = TARGET.read_text(encoding="utf-8")
    holder_function = _function(verifier, "Enter-RuntimeUseLock", "Exit-RuntimeUseLock")
    holder_exit = _function(verifier, "Exit-RuntimeUseLock", "Get-SafeTreeItems")
    contender_function = _function(installer, "Enter-RuntimeUseLock", "Exit-RuntimeUseLock")
    contender_exit = _function(installer, "Exit-RuntimeUseLock", "Get-AuthenticatedSums")
    contender_function = contender_function.replace("AddSeconds(30)", "AddSeconds(1)")
    lock_path = tmp_path / "runtime-use.lock"
    lock_path.write_bytes(b"0\n")
    ready_path = tmp_path / "runtime-use.ready"
    holder_script = f"""
$ErrorActionPreference='Stop'
$script:RuntimeUseLockPath={_ps_literal(lock_path)}
function Assert-ProtectedItem {{ param([string]$Path,[string]$Kind) }}
{holder_function}
{holder_exit}
$lock=Enter-RuntimeUseLock
try {{ [IO.File]::WriteAllText({_ps_literal(ready_path)},'locked'); Start-Sleep -Seconds 4 }}
finally {{ Exit-RuntimeUseLock $lock }}
"""
    contender_script = f"""
$ErrorActionPreference='Stop'
$RuntimeUseLockPath={_ps_literal(lock_path)}
function Assert-ProtectedItem {{ param([string]$Path,[string]$Kind) }}
function Write-ProtectedAsciiAtomic {{ param([string]$Path,[string]$Text) }}
function Fail([string]$Code) {{ throw $Code }}
{contender_function}
{contender_exit}
try {{ $lock=Enter-RuntimeUseLock; Exit-RuntimeUseLock $lock; [Console]::Out.Write('acquired') }}
catch {{ [Console]::Out.Write($_.Exception.Message); exit 2 }}
"""
    holder = subprocess.Popen(
        [
            _powershell(holder_executable),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            holder_script,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_powershell_environment(),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready_path.exists(), holder.communicate(timeout=5)
        blocked = subprocess.run(
            [
                _powershell(contender_executable),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                contender_script,
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=_powershell_environment(),
        )
        assert blocked.returncode == 2, blocked.stdout + blocked.stderr
        assert blocked.stdout == "bootstrap_runtime_busy"
        holder_stdout, holder_stderr = holder.communicate(timeout=10)
        assert holder.returncode == 0, holder_stdout + holder_stderr
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=5)

    acquired = subprocess.run(
        [
            _powershell(contender_executable),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            contender_script,
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        env=_powershell_environment(),
    )
    assert acquired.returncode == 0, acquired.stdout + acquired.stderr
    assert acquired.stdout == "acquired"


def test_release_signed_runtime_bundle_installs_and_replays_exact_receipt(  # noqa: PLR0915
    signed_bundle: tuple[Path, Path], tmp_path: Path
) -> None:
    bundle, allowed = signed_bundle
    ssh_keygen = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/OpenSSH/ssh-keygen.exe"
    )
    target_root = tmp_path / "target-program-data"
    incoming_parent = target_root / "entitlement-bootstrap-incoming"
    incoming_parent.mkdir(parents=True)
    operation_bundle = incoming_parent / OPERATION
    shutil.move(str(bundle), operation_bundle)
    bundle = operation_bundle
    trust_root = target_root / "trust"
    trust_root.mkdir()
    shutil.copy2(allowed, trust_root / "release-allowed-signers")
    key_blob = base64.b64decode(allowed.read_text(encoding="ascii").split()[2])
    (trust_root / "release-key-fingerprint").write_text(
        "SHA256:"
        + base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("=")
        + "\n",
        encoding="ascii",
        newline="\n",
    )

    verifier_source = TARGET_VERIFIER.read_text(encoding="utf-8")
    verifier_source = verifier_source.replace(
        r"C:\ProgramData\Ruisheng\runtime\python.exe", sys.executable
    ).replace(r"C:\ProgramData\Ruisheng", str(target_root))
    verifier_source = _replace_ps_function(
        verifier_source,
        "Assert-ProtectedItem",
        "Set-ProtectedAcl",
        "function Assert-ProtectedItem { param([string]$Path,[string]$Kind) }",
    )
    verifier_source = _replace_ps_function(
        verifier_source,
        "Set-ProtectedAcl",
        "Ensure-ProtectedDirectory",
        "function Set-ProtectedAcl { param([string]$Path,[string]$Kind) }",
    )
    verifier_source = _replace_ps_function(
        verifier_source,
        "Repair-InheritedTransactionAcls",
        "Get-StrictAscii",
        "function Repair-InheritedTransactionAcls { }",
    )
    (bundle / "target_entitlement_verifier.ps1").write_text(verifier_source, encoding="utf-8-sig")

    installer_source = TARGET.read_text(encoding="utf-8")
    installer_source = installer_source.replace(
        r"C:\ProgramData\Ruisheng\runtime\python.exe", sys.executable
    ).replace(r"C:\ProgramData\Ruisheng", str(target_root))
    installer_source = _replace_ps_function(
        installer_source,
        "Assert-ProtectedItem",
        "Set-ProtectedAcl",
        "function Assert-ProtectedItem { param([string]$Path,[string]$Kind) }",
    )
    installer_source = _replace_ps_function(
        installer_source,
        "Set-ProtectedAcl",
        "Ensure-ProtectedDirectory",
        "function Set-ProtectedAcl { param([string]$Path,[string]$Kind) }",
    )
    installer_source = _replace_ps_function(
        installer_source,
        "Assert-SignedExecutable",
        "Assert-ReleaseTrust",
        "function Assert-SignedExecutable { param($Path,$ExpectedName,$Organization) }",
    )
    admin_start = installer_source.index(
        "$identity = [Security.Principal.WindowsIdentity]::GetCurrent()"
    )
    admin_end = installer_source.index("$bootstrapLock =", admin_start)
    installer_source = installer_source[:admin_start] + installer_source[admin_end:]
    (bundle / "target_entitlement_runtime_installer.ps1").write_text(
        installer_source, encoding="utf-8-sig"
    )

    vendor_payload = b"value = 1\n"
    with zipfile.ZipFile(bundle / "vendor.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("x.py", vendor_payload)
    (bundle / "vendor-manifest.sha256").write_text(
        hashlib.sha256(vendor_payload).hexdigest() + "\tx.py\n",
        encoding="ascii",
        newline="\n",
    )

    def sign_bundle() -> None:
        lines = [
            f"{hashlib.sha256((bundle / name).read_bytes()).hexdigest()}  {name}"
            for name in SIGNED_FILES
        ]
        (bundle / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
        signature = bundle / "SHA256SUMS.sig"
        signature.unlink(missing_ok=True)
        signed = subprocess.run(
            [
                str(ssh_keygen),
                "-Y",
                "sign",
                "-f",
                str(tmp_path / "release-key"),
                "-n",
                "ruisheng-entitlement-runtime-v1",
                str(bundle / "SHA256SUMS"),
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert signed.returncode == 0, signed.stderr.decode(errors="replace")

    sign_bundle()
    installer = bundle / "target_entitlement_runtime_installer.ps1"

    def run_installer() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                _powershell("powershell.exe"),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(installer),
                "-OperationId",
                OPERATION,
                "-SiteId",
                SITE,
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=_powershell_environment(),
        )

    first = run_installer()
    assert first.returncode == 0, first.stdout + first.stderr
    first_receipt = json.loads(first.stdout)
    assert first_receipt["status"] == "runtime_installed"
    receipt_path = target_root / "entitlement-runtime-operations" / f"{OPERATION}.json"
    record = json.loads(receipt_path.read_text(encoding="ascii"))
    assert record["status"] == "terminal"
    assert record["receipt"] == first_receipt

    replay = run_installer()
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert json.loads(replay.stdout) == first_receipt

    state_path = target_root / "entitlement-runtime-state.json"
    installed_state = state_path.read_bytes()
    retained_receipt = receipt_path.read_bytes()
    (bundle / "runtime-metadata.json").write_text(
        '{"entitlement_key_generation":1,"runtime_epoch":2,"schema_version":1}\n',
        encoding="ascii",
        newline="\n",
    )
    sign_bundle()
    conflict = run_installer()
    assert conflict.returncode != 0
    assert "bootstrap_operation_conflict" in conflict.stdout + conflict.stderr
    assert state_path.read_bytes() == installed_state
    assert receipt_path.read_bytes() == retained_receipt


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_runtime_operation_retention_prunes_only_oldest_terminal_receipt(
    executable: str, tmp_path: Path
) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = _function(source, "Assert-ExactRuntimeFields", "Complete-RuntimeOperation")
    operations_root = tmp_path / f"runtime-operations-{executable}"
    operations_root.mkdir()
    digest = "a" * 64
    operation_ids = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]

    def terminal_record(operation_id: str) -> dict[str, object]:
        receipt: dict[str, object] = {
            "schema_version": 1,
            "ok": True,
            "status": "runtime_installed",
            "operation_id": operation_id,
            "site_id": SITE,
            "entitlement_sha256": "b" * 64,
            "verifier_sha256": "c" * 64,
            "public_key_sha256": "d" * 64,
            "vendor_archive_sha256": "e" * 64,
            "runtime_epoch": 1,
            "entitlement_key_generation": 1,
            "services_restarted": False,
            "device_configuration_changed": False,
        }
        return {
            "bundle_sums_sha256": digest,
            "operation_id": operation_id,
            "receipt": receipt,
            "schema_version": 1,
            "site_id": SITE,
            "status": "terminal",
        }

    for index, operation_id in enumerate(operation_ids):
        path = operations_root / f"{operation_id}.json"
        path.write_text(
            json.dumps(terminal_record(operation_id), separators=(",", ":")) + "\n",
            encoding="ascii",
            newline="\n",
        )
        timestamp = 1_700_000_000 + index
        os.utime(path, (timestamp, timestamp))

    harness = f"""
$ErrorActionPreference='Stop'
$RuntimeOperationsRoot={_ps_literal(operations_root)}
$MaxRuntimeOperationReceipts=3
$MaxRuntimeOperationBytes=65536
$MaxRuntimeOperationReceiptBytes=8192
$OperationId='{OPERATION}'
$SiteId='{SITE}'
$script:BundleSumsSha256='{digest}'
function Fail([string]$Code) {{ throw $Code }}
function Assert-ProtectedItem {{ param([string]$Path,[string]$Kind) }}
function Ensure-ProtectedDirectory([string]$Path) {{
  if(-not(Test-Path -LiteralPath $Path)){{[void](New-Item -ItemType Directory -Path $Path)}}
}}
function Get-StrictAscii([string]$Path,[long]$Maximum) {{
  return [IO.File]::ReadAllText($Path,[Text.Encoding]::ASCII)
}}
function Write-ProtectedAsciiAtomic([string]$Path,[string]$Text) {{
  [IO.File]::WriteAllText($Path,$Text,[Text.Encoding]::ASCII)
}}
{functions}
$lookup=Prepare-RuntimeOperation
$countBeforeCreate=@(Get-ChildItem -LiteralPath $RuntimeOperationsRoot -File).Count
$created=Prepare-RuntimeOperation -Create
[ordered]@{{lookup=$lookup;count_before_create=$countBeforeCreate;created=$created}} |
  ConvertTo-Json -Depth 4 -Compress
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
        "lookup": {"exists": False, "terminal": False, "receipt": None},
        "count_before_create": 3,
        "created": {
            "exists": True,
            "terminal": False,
            "receipt": None,
        },
    }
    assert not (operations_root / f"{operation_ids[0]}.json").exists()
    assert all((operations_root / f"{value}.json").is_file() for value in operation_ids[1:])
    new_record = json.loads((operations_root / f"{OPERATION}.json").read_text(encoding="ascii"))
    assert new_record == {
        "bundle_sums_sha256": digest,
        "operation_id": OPERATION,
        "schema_version": 1,
        "site_id": SITE,
        "status": "executing",
    }
    main = source.split("$bootstrapLock = Enter-BootstrapLock", 1)[1]
    assert main.index("$idempotent = Assert-RuntimeAdvance") < main.index(
        "[void](Prepare-RuntimeOperation -Create)"
    )


def test_runtime_source_replacement_cannot_change_uploaded_snapshot(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    approved = hashlib.sha256((bundle / "entitlement.py").read_bytes()).hexdigest()
    completed, log, _ = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "replace-source")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert hashlib.sha256((bundle / "entitlement.py").read_bytes()).hexdigest() != approved
    upload = next(args for kind, args, _ in _calls(log) if kind == "scp")
    uploaded_sources = [path for path in upload if str(tmp_path / "runtime-snapshots") in path]
    assert len(uploaded_sources) == len(SIGNED_FILES) + 2
    assert all(str(bundle) not in path for path in uploaded_sources)


def test_runtime_bundle_source_rejects_extra_files_before_transport(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    (bundle / "unexpected.txt").write_text("not signed\n", encoding="ascii")
    completed, log, audit = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "valid")
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_code"] == "bundle_file_set_invalid"
    assert not log.exists()
    assert json.loads(audit.read_text(encoding="utf-8-sig"))["result"] == "failed"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_runtime_transport_requires_microsoft_signed_executable(
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
    harness = f"""
$ErrorActionPreference='Stop'
{function}
function Capture([string]$Path) {{
  try {{ Assert-FixedExecutable $Path 'Microsoft'; return 'ok' }}
  catch {{ return $_.Exception.Message }}
}}
[ordered]@{{valid=Capture {_ps_literal(ssh)};unsigned=Capture {_ps_literal(unsigned)}}} |
  ConvertTo-Json -Compress
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
        "unsigned": "fixed_executable_signature_invalid",
    }


def test_runtime_transport_checks_local_release_trust_acl() -> None:
    source = REMOTE.read_text(encoding="utf-8")
    assert (
        "Assert-LocalReleaseTrustAcl -Path $script:LocalTrustRoot -Kind Directory -RequireProtected"
    ) in source
    assert (
        "Assert-LocalReleaseTrustAcl -Path $script:LocalAllowedSignersPath -Kind File"
    ) in source


def test_runtime_ipv6_scp_target_is_bracketed(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, log, _ = _run(
        runtime_transport_stubs,
        tmp_path,
        bundle,
        allowed,
        "valid",
        target="operator@fd7a:115c:a1e0::1",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = _calls(log)
    ssh_calls = [args for kind, args, _ in calls if kind == "ssh"]
    assert ssh_calls and all("operator@fd7a:115c:a1e0::1" in args for args in ssh_calls)
    upload = next(args for kind, args, _ in calls if kind == "scp")
    assert upload[-1].startswith("operator@[fd7a:115c:a1e0::1]:")


def test_runtime_path_hijack_does_not_override_fixed_tools(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    hijack = tmp_path / "path-hijack"
    hijack.mkdir()
    for name in ("ssh.exe", "scp.exe", "ssh-keygen.exe"):
        (hijack / name).write_text("not an executable", encoding="ascii")
    completed, log, _ = _run(
        runtime_transport_stubs,
        tmp_path,
        bundle,
        allowed,
        "valid",
        path_value=str(hijack),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert [kind for kind, _, _ in _calls(log)] == ["ssh", "scp", "ssh", "ssh"]


def test_tampered_signed_bundle_is_rejected_before_transport(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    (bundle / "entitlement.py").write_bytes((bundle / "entitlement.py").read_bytes() + b"\n")
    completed, log, audit = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "valid")
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_code"] == "bundle_hash_invalid"
    assert not log.exists()
    assert json.loads(audit.read_text(encoding="utf-8-sig"))["result"] == "failed"


def test_explicit_target_rejection_is_not_reported_as_uncertain(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, log, audit = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "rejected")
    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["status"] == "rejected"
    assert result["error_code"] == "bootstrap_signature_invalid"
    assert json.loads(audit.read_text(encoding="utf-8-sig"))["result"] == "failed"
    calls = _calls(log)
    assert [kind for kind, _, _ in calls] == ["ssh", "scp", "ssh", "ssh"]
    cleanup = _remote_script(calls[-1][2])
    assert "Remove-Item -LiteralPath $bundle -Recurse -Force" in cleanup
    assert "Remove-Item -LiteralPath $reservation -Force" not in cleanup


def test_busy_runtime_execution_is_uncertain_and_does_not_cleanup_remote_bundle(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, log, audit = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "busy")
    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["status"] == "uncertain"
    assert result["error_code"] == "runtime_execution_transport_failed"
    assert json.loads(audit.read_text(encoding="utf-8-sig"))["result"] == "ambiguous_commit"
    assert [kind for kind, _, _ in _calls(log)] == ["ssh", "scp", "ssh"]


def test_final_snapshot_cleanup_failure_preserves_uncertain_runtime_status(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, _, _ = _run(
        runtime_transport_stubs,
        tmp_path,
        bundle,
        allowed,
        "malformed",
        fail_snapshot_cleanup=True,
    )
    failure = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert failure["status"] == "uncertain"
    assert failure["error_code"] == "bundle_snapshot_cleanup_failed"


def test_runtime_upload_failure_cleans_remote_bundle(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, log, audit = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "upload-fail")
    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["status"] == "rejected"
    assert result["error_code"] == "bundle_upload_failed"
    calls = _calls(log)
    assert [kind for kind, _, _ in calls] == ["ssh", "scp", "ssh"]
    assert "Remove-Item -LiteralPath $bundle -Recurse -Force" in _remote_script(calls[-1][2])
    assert json.loads(audit.read_text(encoding="utf-8-sig"))["result"] == "failed"


def test_malformed_post_dispatch_receipt_is_ambiguous(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, _, audit = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "malformed")
    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["status"] == "uncertain"
    assert result["error_code"] == "runtime_receipt_invalid"
    assert json.loads(audit.read_text(encoding="utf-8-sig"))["result"] == "ambiguous_commit"


def test_non_tailscale_target_is_rejected_before_transport(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    completed, log, _ = _run(
        runtime_transport_stubs, tmp_path, bundle, allowed, "valid", target="operator@203.0.113.5"
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_code"] == "target_not_tailscale"
    assert not log.exists()


def test_local_runtime_bundle_rejects_generation_two_before_transport(
    runtime_transport_stubs: Path,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, allowed = signed_bundle
    (bundle / "runtime-metadata.json").write_text(
        '{"entitlement_key_generation":2,"runtime_epoch":2,"schema_version":1}\n',
        encoding="ascii",
        newline="\n",
    )
    lines = [
        f"{hashlib.sha256((bundle / name).read_bytes()).hexdigest()}  {name}"
        for name in SIGNED_FILES
    ]
    sums = bundle / "SHA256SUMS"
    sums.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    (bundle / "SHA256SUMS.sig").unlink()
    ssh_keygen = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/OpenSSH/ssh-keygen.exe"
    )
    signed = subprocess.run(
        [
            str(ssh_keygen),
            "-Y",
            "sign",
            "-f",
            str(bundle.parent / "release-key"),
            "-n",
            "ruisheng-entitlement-runtime-v1",
            str(sums),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert signed.returncode == 0, signed.stderr.decode(errors="replace")
    completed, log, audit = _run(runtime_transport_stubs, tmp_path, bundle, allowed, "valid")
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["error_code"] == "entitlement_key_generation_unsupported"
    assert not log.exists()
    assert json.loads(audit.read_text(encoding="utf-8-sig"))["result"] == "failed"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_runtime_epoch_rejects_downgrade_conflict_and_allows_exact_replay(
    executable: str,
) -> None:
    source = TARGET.read_text(encoding="utf-8")
    function = _function(source, "Assert-RuntimeAdvance", "ConvertTo-CanonicalState")
    harness = f"""
$ErrorActionPreference='Stop'
function Fail([string]$Code) {{ throw $Code }}
{function}
    function State([long]$Epoch,[long]$Generation,[string]$Digest,[string]$PublicKey) {{
      return [pscustomobject]@{{runtime_epoch=$Epoch;entitlement_key_generation=$Generation;bundle_sums_sha256=$Digest;public_key_sha256=$PublicKey}}
}}
function Capture($Requested,$Existing) {{
  try {{ return [ordered]@{{ok=$true;idempotent=[bool](Assert-RuntimeAdvance $Requested $Existing)}} }}
  catch {{ return [ordered]@{{ok=$false;error_code=$_.Exception.Message}} }}
}}
$digest='{"a" * 64}'
$other='{"b" * 64}'
$key='{"c" * 64}'
$otherKey='{"d" * 64}'
$existing=State 2 1 $digest $key
[ordered]@{{
  older_epoch=(Capture (State 1 1 $other $key) $existing)
  unsupported_generation=(Capture (State 3 2 $other $key) $existing)
  key_change=(Capture (State 3 1 $other $otherKey) $existing)
  epoch_conflict=(Capture (State 2 1 $other $key) $existing)
  exact_replay=(Capture (State 2 1 $digest $key) $existing)
  advance=(Capture (State 3 1 $other $key) $existing)
}} | ConvertTo-Json -Depth 4 -Compress
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
    result = json.loads(completed.stdout)
    assert result["older_epoch"]["error_code"] == "bootstrap_runtime_downgrade"
    assert (
        result["unsupported_generation"]["error_code"]
        == "bootstrap_entitlement_key_generation_unsupported"
    )
    assert result["key_change"]["error_code"] == "bootstrap_entitlement_key_change_unsupported"
    assert result["epoch_conflict"]["error_code"] == "bootstrap_runtime_epoch_conflict"
    assert result["exact_replay"] == {"ok": True, "idempotent": True}
    assert result["advance"] == {"ok": True, "idempotent": False}


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_target_runtime_metadata_rejects_generation_two(executable: str, tmp_path: Path) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = _function(source, "Get-StrictAscii", "Assert-ReleaseTrust") + _function(
        source, "Get-RuntimeMetadata", "Get-ExistingRuntimeState"
    )
    metadata = tmp_path / f"runtime-metadata-{executable}.json"
    metadata.write_text(
        '{"entitlement_key_generation":2,"runtime_epoch":2,"schema_version":1}\n',
        encoding="ascii",
        newline="\n",
    )
    harness = f"""
$ErrorActionPreference='Stop'
$script:BundleSumsSha256='{"a" * 64}'
function Fail([string]$Code) {{ throw $Code }}
{functions}
try {{ Get-RuntimeMetadata {_ps_literal(metadata)} '{"b" * 64}';exit 0 }}
catch {{ [Console]::Out.Write($_.Exception.Message);exit 2 }}
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
    assert completed.stdout == "bootstrap_entitlement_key_generation_unsupported"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_target_verifier_binds_generation_one_state_to_public_key(
    executable: str, tmp_path: Path
) -> None:
    source = TARGET_VERIFIER.read_text(encoding="utf-8")
    functions = _function(source, "Get-StrictAscii", "Assert-RuntimeState") + _function(
        source, "Assert-RuntimeState", "Assert-VendorSet"
    )
    public_key = tmp_path / f"entitlement-public-key-{executable}"
    public_key.write_text("trusted-key\n", encoding="ascii", newline="\n")
    actual_digest = hashlib.sha256(public_key.read_bytes()).hexdigest()
    entitlement_script = tmp_path / f"entitlement-{executable}.py"
    verifier_script = tmp_path / f"verifier-{executable}.ps1"
    python_runtime = tmp_path / f"python-{executable}.exe"
    vendor_manifest = tmp_path / f"vendor-{executable}.sha256"
    for component in (entitlement_script, verifier_script, python_runtime, vendor_manifest):
        component.write_bytes(component.name.encode())

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    runtime_state = tmp_path / f"runtime-state-{executable}.json"

    def run_state(generation: int, public_key_digest: str) -> subprocess.CompletedProcess[str]:
        runtime_state.write_text(
            json.dumps(
                {
                    "bundle_sums_sha256": "a" * 64,
                    "entitlement_key_generation": generation,
                    "entitlement_sha256": digest(entitlement_script),
                    "public_key_sha256": public_key_digest,
                    "python_sha256": digest(python_runtime),
                    "runtime_epoch": 2,
                    "schema_version": 1,
                    "vendor_manifest_sha256": digest(vendor_manifest),
                    "verifier_sha256": digest(verifier_script),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="ascii",
            newline="\n",
        )
        harness = f"""
$ErrorActionPreference='Stop'
$script:RuntimeStatePath={_ps_literal(runtime_state)}
$script:PublicKeyPath={_ps_literal(public_key)}
$script:EntitlementScript={_ps_literal(entitlement_script)}
$script:VerifierPath={_ps_literal(verifier_script)}
$script:PythonPath={_ps_literal(python_runtime)}
$script:VendorManifestPath={_ps_literal(vendor_manifest)}
function Assert-ProtectedItem([string]$Path,[string]$Kind) {{ }}
{functions}
try {{ Assert-RuntimeState;[Console]::Out.Write('ok');exit 0 }}
catch {{ [Console]::Out.Write($_.Exception.Message);exit 2 }}
"""
        return subprocess.run(
            [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=_powershell_environment(),
        )

    valid = run_state(1, actual_digest)
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert valid.stdout == "ok"
    generation_two = run_state(2, actual_digest)
    assert generation_two.returncode == 2
    assert generation_two.stdout == "entitlement_key_generation_unsupported"
    changed_key = run_state(1, "b" * 64)
    assert changed_key.returncode == 2
    assert changed_key.stdout == "runtime_public_key_mismatch"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_existing_runtime_state_requires_site_identity(executable: str, tmp_path: Path) -> None:
    source = TARGET.read_text(encoding="utf-8")
    function = _function(source, "Assert-SiteProvisioningState", "Assert-RuntimeAdvance")
    missing_site = tmp_path / f"missing-site-{executable}"
    harness = f"""
$ErrorActionPreference='Stop'
$SiteIdentityPath={_ps_literal(missing_site)}
$SiteId='site-a'
function Fail([string]$Code) {{ throw $Code }}
function Ensure-SiteIdentity([string]$ExpectedSiteId) {{ }}
{function}
try {{ Assert-SiteProvisioningState ([pscustomobject]@{{schema_version=1}});exit 0 }}
catch {{ [Console]::Out.Write($_.Exception.Message);exit 2 }}
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
    assert completed.stdout == "bootstrap_site_identity_missing"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_runtime_journal_rollback_restores_old_components_and_state(
    executable: str, tmp_path: Path
) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = _function(
        source, "Remove-ReplacementDestination", "Restore-BootstrapJournal"
    ) + _function(source, "Restore-BootstrapJournal", "Recover-BootstrapTransaction")
    old_destination = tmp_path / f"old-destination-{executable}.txt"
    old_destination.write_text("new", encoding="ascii")
    old_backup = tmp_path / f"old-backup-{executable}.txt"
    old_backup.write_text("old", encoding="ascii")
    new_destination = tmp_path / f"new-destination-{executable}.txt"
    new_destination.write_text("partial", encoding="ascii")
    backed_destination = tmp_path / f"backed-destination-{executable}.txt"
    backed_backup = tmp_path / f"backed-backup-{executable}.txt"
    backed_backup.write_text("old-after-backup", encoding="ascii")
    backed_staged = tmp_path / f"backed-staged-{executable}.txt"
    backed_staged.write_text("new-after-backup", encoding="ascii")
    untouched_destination = tmp_path / f"untouched-destination-{executable}.txt"
    untouched_destination.write_text("old-before-backup", encoding="ascii")
    untouched_staged = tmp_path / f"untouched-staged-{executable}.txt"
    untouched_staged.write_text("new-before-backup", encoding="ascii")
    runtime_state = tmp_path / f"runtime-state-{executable}.json"
    runtime_state.write_text("new-state", encoding="ascii")
    site_identity = tmp_path / f"site-identity-{executable}"
    site_identity.write_text("site-a\n", encoding="ascii", newline="\n")

    def file_identity(path: Path) -> str:
        return f"file:{hashlib.sha256(path.read_bytes()).hexdigest()}"

    old_state = base64.b64encode(b"old-state").decode("ascii")
    new_state = base64.b64encode(b"new-state").decode("ascii")
    harness = f"""
$ErrorActionPreference='Stop'
$RuntimeStatePath={_ps_literal(runtime_state)}
$SiteIdentityPath={_ps_literal(site_identity)}
$SiteId='site-a'
$MaxBundleFileBytes=1048576
function Fail([string]$Code) {{ throw $Code }}
function Get-StrictAscii([string]$Path,[long]$Maximum) {{
  return [IO.File]::ReadAllText($Path,[Text.Encoding]::ASCII)
}}
function Write-ProtectedAsciiAtomic([string]$Path,[string]$Text) {{
  [IO.File]::WriteAllText($Path,$Text,[Text.Encoding]::ASCII)
}}
{functions}
$journal=[pscustomobject]@{{
  phase='replacing'
  old_runtime_state_present=$true
  old_runtime_state_b64='{old_state}'
  new_runtime_state_b64='{new_state}'
  site_identity_created=$true
  replacements=@(
    [pscustomobject]@{{index=0;kind='File';destination={_ps_literal(old_destination)};backup={_ps_literal(old_backup)};staged={_ps_literal(tmp_path / "missing-staged-0")};existed=$true;old_identity='{file_identity(old_backup)}';new_identity='{file_identity(old_destination)}'}},
    [pscustomobject]@{{index=1;kind='File';destination={_ps_literal(new_destination)};backup={_ps_literal(tmp_path / "missing-backup-1")};staged={_ps_literal(tmp_path / "missing-staged-1")};existed=$false;old_identity='absent';new_identity='{file_identity(new_destination)}'}},
    [pscustomobject]@{{index=2;kind='File';destination={_ps_literal(backed_destination)};backup={_ps_literal(backed_backup)};staged={_ps_literal(backed_staged)};existed=$true;old_identity='{file_identity(backed_backup)}';new_identity='{file_identity(backed_staged)}'}},
    [pscustomobject]@{{index=3;kind='File';destination={_ps_literal(untouched_destination)};backup={_ps_literal(tmp_path / "missing-backup-3")};staged={_ps_literal(untouched_staged)};existed=$true;old_identity='{file_identity(untouched_destination)}';new_identity='{file_identity(untouched_staged)}'}}
  )
}}
Restore-BootstrapJournal $journal
[ordered]@{{
  restored=[IO.File]::ReadAllText({_ps_literal(old_destination)})
  partial_exists=(Test-Path -LiteralPath {_ps_literal(new_destination)})
  restored_after_backup=[IO.File]::ReadAllText({_ps_literal(backed_destination)})
  preserved_before_backup=[IO.File]::ReadAllText({_ps_literal(untouched_destination)})
  state=[IO.File]::ReadAllText($RuntimeStatePath)
  site_exists=(Test-Path -LiteralPath $SiteIdentityPath)
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
        "restored": "old",
        "partial_exists": False,
        "restored_after_backup": "old-after-backup",
        "preserved_before_backup": "old-before-backup",
        "state": "old-state",
        "site_exists": False,
    }


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_pre_root_runtime_journal_is_recoverable(executable: str, tmp_path: Path) -> None:
    source = TARGET.read_text(encoding="utf-8")
    assert source.index("Write-BootstrapJournal $journal") < source.index(
        "[void](New-Item -ItemType Directory -Path $transactionRoot)"
    )
    functions = (
        _function(source, "Remove-ReplacementDestination", "Restore-BootstrapJournal")
        + _function(source, "Restore-BootstrapJournal", "Recover-BootstrapTransaction")
        + _function(source, "Recover-BootstrapTransaction", "Set-ProtectedTree")
    )
    transaction_prefix = str(tmp_path / f"entitlement-bootstrap-{executable}-")
    functions = functions.replace(
        r"C:\ProgramData\Ruisheng\entitlement-bootstrap-", transaction_prefix
    )
    operation = OPERATION
    transaction_root = Path(transaction_prefix + operation)
    journal_path = tmp_path / f"journal-{executable}.json"
    bin_root = tmp_path / f"bin-{executable}"
    trust_root = tmp_path / f"trust-{executable}"
    runtime_root = tmp_path / f"runtime-{executable}"
    destinations = [
        bin_root / "entitlement.py",
        bin_root / "target_entitlement_verifier.ps1",
        trust_root / "entitlement-public-key",
        runtime_root,
    ]
    replacements = [
        {
            "index": index,
            "destination": str(destination),
            "staged": str(transaction_root / f"staged-{index}"),
            "backup": str(transaction_root / f"backup-{index}"),
            "kind": "Directory" if index == 3 else "File",
            "existed": False,
            "old_identity": "absent",
            "new_identity": "",
        }
        for index, destination in enumerate(destinations)
    ]
    journal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation_id": operation,
                "phase": "prepared",
                "transaction_root": str(transaction_root),
                "site_identity_created": False,
                "old_runtime_state_present": False,
                "old_runtime_state_b64": "",
                "new_runtime_state_b64": base64.b64encode(b"new-state").decode("ascii"),
                "replacements": replacements,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
        newline="\n",
    )
    harness = f"""
$ErrorActionPreference='Stop'
$BootstrapJournalPath={_ps_literal(journal_path)}
$RuntimeStatePath={_ps_literal(tmp_path / f"runtime-state-{executable}.json")}
$SiteIdentityPath={_ps_literal(tmp_path / f"site-{executable}")}
$BinRoot={_ps_literal(bin_root)}
$TrustRoot={_ps_literal(trust_root)}
$RuntimeRoot={_ps_literal(runtime_root)}
function Fail([string]$Code) {{ throw $Code }}
function Assert-ProtectedItem([string]$Path,[string]$Kind) {{ }}
function Get-StrictAscii([string]$Path) {{ return [IO.File]::ReadAllText($Path,[Text.Encoding]::ASCII) }}
function Write-ProtectedAsciiAtomic([string]$Path,[string]$Text) {{ [IO.File]::WriteAllText($Path,$Text,[Text.Encoding]::ASCII) }}
{functions}
try {{ Recover-BootstrapTransaction;[Console]::Out.Write('recovered');exit 0 }}
catch {{ [Console]::Out.Write($_.Exception.Message);exit 2 }}
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
    assert completed.stdout == "recovered"
    assert not journal_path.exists()
    assert not transaction_root.exists()


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_runtime_journal_refuses_third_state_and_retains_recovery_evidence(
    executable: str, tmp_path: Path
) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = (
        _function(source, "Remove-ReplacementDestination", "Restore-BootstrapJournal")
        + _function(source, "Restore-BootstrapJournal", "Recover-BootstrapTransaction")
        + _function(source, "Recover-BootstrapTransaction", "Set-ProtectedTree")
    )
    transaction_prefix = str(tmp_path / f"entitlement-bootstrap-third-{executable}-")
    functions = functions.replace(
        r"C:\ProgramData\Ruisheng\entitlement-bootstrap-", transaction_prefix
    )
    transaction_root = Path(transaction_prefix + OPERATION)
    transaction_root.mkdir()
    journal_path = tmp_path / f"third-state-journal-{executable}.json"
    bin_root = tmp_path / f"third-bin-{executable}"
    trust_root = tmp_path / f"third-trust-{executable}"
    runtime_root = tmp_path / f"third-runtime-{executable}"
    bin_root.mkdir()
    trust_root.mkdir()
    destination = bin_root / "entitlement.py"
    destination.write_text("unrecognized-third-state", encoding="ascii")
    old_bytes = b"approved-old-state"
    new_bytes = b"approved-new-state"
    later_destination = trust_root / "entitlement-public-key"
    later_destination.write_bytes(new_bytes)
    destinations = [
        destination,
        bin_root / "target_entitlement_verifier.ps1",
        trust_root / "entitlement-public-key",
        runtime_root,
    ]
    replacements = []
    for index, path in enumerate(destinations):
        kind = "Directory" if index == 3 else "File"
        replacements.append(
            {
                "index": index,
                "destination": str(path),
                "staged": str(transaction_root / f"staged-{index}"),
                "backup": str(transaction_root / f"backup-{index}"),
                "kind": kind,
                "existed": index == 0,
                "old_identity": (
                    f"file:{hashlib.sha256(old_bytes).hexdigest()}" if index == 0 else "absent"
                ),
                "new_identity": (
                    f"file:{hashlib.sha256(new_bytes).hexdigest()}"
                    if index in {0, 2}
                    else ("directory:" if kind == "Directory" else "file:") + "0" * 64
                ),
            }
        )
    journal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation_id": OPERATION,
                "phase": "replacing",
                "transaction_root": str(transaction_root),
                "site_identity_created": False,
                "old_runtime_state_present": False,
                "old_runtime_state_b64": "",
                "new_runtime_state_b64": base64.b64encode(b"new-state").decode("ascii"),
                "replacements": replacements,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
        newline="\n",
    )
    harness = f"""
$ErrorActionPreference='Stop'
$BootstrapJournalPath={_ps_literal(journal_path)}
$RuntimeStatePath={_ps_literal(tmp_path / f"third-runtime-state-{executable}.json")}
$SiteIdentityPath={_ps_literal(tmp_path / f"third-site-{executable}")}
$SiteId='site-a'
$BinRoot={_ps_literal(bin_root)}
$TrustRoot={_ps_literal(trust_root)}
$RuntimeRoot={_ps_literal(runtime_root)}
$MaxBundleFileBytes=1048576
$MaxVendorFiles=10000
$MaxVendorBytes=536870912
function Fail([string]$Code) {{ throw $Code }}
function Assert-ProtectedItem([string]$Path,[string]$Kind) {{ }}
function Get-StrictAscii([string]$Path,[long]$Maximum=1048576) {{ return [IO.File]::ReadAllText($Path,[Text.Encoding]::ASCII) }}
function Write-ProtectedAsciiAtomic([string]$Path,[string]$Text) {{ [IO.File]::WriteAllText($Path,$Text,[Text.Encoding]::ASCII) }}
{functions}
try {{ Recover-BootstrapTransaction;[Console]::Out.Write('unexpected');exit 0 }}
catch {{ [Console]::Out.Write($_.Exception.Message);exit 2 }}
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
    assert completed.stdout == "bootstrap_transaction_uncertain"
    assert destination.read_text(encoding="ascii") == "unrecognized-third-state"
    assert later_destination.read_bytes() == new_bytes
    assert journal_path.exists()
    assert transaction_root.exists()


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_runtime_uses_the_python_entitlement_transaction_lock(
    executable: str, tmp_path: Path
) -> None:
    source = TARGET.read_text(encoding="utf-8")
    functions = _function(
        source, "Enter-EntitlementTransactionLock", "Exit-EntitlementTransactionLock"
    ) + _function(source, "Exit-EntitlementTransactionLock", "Get-AuthenticatedSums")
    lock = tmp_path / f"shared-{executable}.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path;import sys,time;"
                "from tools.entitlement import transaction_lock;"
                "\nwith transaction_lock(Path(sys.argv[1])): print('locked',flush=True);time.sleep(10)"
            ),
            str(lock),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
        harness = f"""
$ErrorActionPreference='Stop'
$EntitlementRoot={_ps_literal(tmp_path)}
$EntitlementTransactionLockPath={_ps_literal(lock)}
function Ensure-ProtectedDirectory([string]$Path) {{ }}
function Assert-ProtectedItem([string]$Path,[string]$Kind) {{ }}
function Set-ProtectedAcl([string]$Path,[string]$Kind) {{ }}
function Fail([string]$Code) {{ throw $Code }}
{functions}
try {{ $stream=Enter-EntitlementTransactionLock;Exit-EntitlementTransactionLock $stream;exit 0 }}
catch {{ [Console]::Out.Write($_.Exception.Message);exit 2 }}
"""
        started = time.monotonic()
        completed = subprocess.run(
            [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=_powershell_environment(),
        )
        assert time.monotonic() - started < 5
        assert completed.returncode == 2
        assert completed.stdout == "bootstrap_entitlement_busy"
    finally:
        holder.terminate()
        holder.wait(timeout=10)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_exact_replay_rejects_component_or_full_validation_drift(
    executable: str, tmp_path: Path
) -> None:
    source = TARGET.read_text(encoding="utf-8")
    function = (
        "function Assert-InstalledRuntime"
        + source.split("function Assert-InstalledRuntime", 1)[1].split("\nif ($OperationId", 1)[0]
    )
    bin_root = tmp_path / f"bin-{executable}"
    trust_root = tmp_path / f"trust-{executable}"
    runtime_root = tmp_path / f"runtime-{executable}"
    bin_root.mkdir()
    trust_root.mkdir()
    runtime_root.mkdir()
    files = {
        "entitlement.py": bin_root / "entitlement.py",
        "target_entitlement_verifier.ps1": bin_root / "target_entitlement_verifier.ps1",
        "entitlement-public-key": trust_root / "entitlement-public-key",
        "vendor-manifest.sha256": runtime_root / "vendor-manifest.sha256",
    }
    sums: dict[str, str] = {}
    for name, path in files.items():
        path.write_text(f"trusted {name}\n", encoding="ascii")
        sums[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    files["entitlement.py"].write_text("drifted\n", encoding="ascii")
    sums_literal = ";".join(f"'{name}'='{digest}'" for name, digest in sums.items())
    harness = f"""
$ErrorActionPreference='Stop'
$BinRoot={_ps_literal(bin_root)}
$TrustRoot={_ps_literal(trust_root)}
$RuntimeRoot={_ps_literal(runtime_root)}
function Get-ExistingRuntimeState {{ return [pscustomobject]@{{value='same'}} }}
function ConvertTo-CanonicalState($State) {{ return 'same' }}
function Assert-ProtectedItem([string]$Path,[string]$Kind) {{ }}
function Ensure-SiteIdentity([string]$ExpectedSiteId) {{ }}
function Invoke-InstalledRuntimeValidation {{ }}
function Fail([string]$Code) {{ throw $Code }}
{function}
$sums=@{{{sums_literal}}}
try {{ Assert-InstalledRuntime $sums ([pscustomobject]@{{value='same'}});exit 0 }}
catch {{ [Console]::Out.Write($_.Exception.Message);exit 2 }}
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
    assert completed.stdout == "bootstrap_runtime_component_drift"

    files["entitlement.py"].write_text("trusted entitlement.py\n", encoding="ascii")
    full_validation_harness = harness.replace(
        "function Invoke-InstalledRuntimeValidation { }",
        "function Invoke-InstalledRuntimeValidation { throw 'bootstrap_runtime_full_validation_failed' }",
    )
    full_validation = subprocess.run(
        [
            _powershell(executable),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            full_validation_harness,
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=_powershell_environment(),
    )
    assert full_validation.returncode == 2
    assert full_validation.stdout == "bootstrap_runtime_full_validation_failed"

    verifier = TARGET_VERIFIER.read_text(encoding="utf-8")
    pinned_runtime = _function(verifier, "Assert-PinnedRuntime", "Assert-PinnedSiteIdentity")
    assert "Assert-RuntimeState" in pinned_runtime
    assert "Assert-VendorSet" in pinned_runtime
    main = verifier.split("$output = $null", 1)[1]
    validate_runtime = main.split('if ($Action -eq "ValidateRuntime")', 1)[1].split(
        'elseif ($Action -eq "Prepare")', 1
    )[0]
    assert main.index("Assert-PinnedRuntime") < main.index('if ($Action -eq "ValidateRuntime")')
    assert main.index("$script:PinnedSiteId = Get-PinnedSiteIdentity") < main.index(
        'if ($Action -eq "ValidateRuntime")'
    )
    assert "Assert-PinnedSiteIdentity" in validate_runtime
    assert '"verify", "--grant", $script:StatePath' in validate_runtime


def _function(source: str, name: str, next_name: str) -> str:
    return (
        "function "
        + name
        + source.split("function " + name, 1)[1].split("function " + next_name, 1)[0]
    )


def test_path_identity_uses_ordinal_linear_lookup() -> None:
    source = TARGET.read_text(encoding="utf-8")
    function = _function(source, "Get-PathIdentity", "Assert-RecognizedIdentity")
    assert "Collections.Hashtable" in function
    assert "[StringComparer]::Ordinal" in function
    assert "Where-Object" not in function


def test_bootstrap_journal_forces_uncertain_even_when_runtime_validation_fails() -> None:
    source = TARGET_VERIFIER.read_text(encoding="utf-8")
    main = source.split("$output = $null", 1)[1]
    journal = main.split('if ($Action -in @("Status", "Authorize", "Install")', 1)[1].split(
        "\n  Assert-PinnedRuntime", 1
    )[0]
    assert "try {" in journal
    assert "catch { }" in journal
    assert 'catch { throw "bootstrap_transaction_uncertain" }' in journal
    assert "Get-VerifiedEntitlementStatus" in journal
    assert "Assert-PinnedRuntime" not in journal


def _replace_ps_function(source: str, name: str, next_name: str, replacement: str) -> str:
    start = source.index("function " + name)
    end = source.index("function " + next_name, start)
    return source[:start] + replacement + "\n\n" + source[end:]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_vendor_archive_rejects_path_escape(executable: str, tmp_path: Path) -> None:
    source = TARGET.read_text(encoding="utf-8")
    function = _function(source, "Expand-AuthenticatedVendor", "Set-ProtectedTree")
    archive = tmp_path / f"escape-{executable}.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.py", "bad")
    destination = tmp_path / f"expanded-{executable}"
    harness = f"""
$ErrorActionPreference='Stop'
$MaxVendorFiles=10000
$MaxVendorBytes=536870912
function Fail([string]$Code) {{ throw $Code }}
{function}
try {{ Expand-AuthenticatedVendor {_ps_literal(archive)} {_ps_literal(destination)}; exit 0 }}
catch {{ [Console]::Out.Write($_.Exception.Message); exit 2 }}
"""
    completed = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 2
    assert completed.stdout == "bootstrap_vendor_archive_invalid"
    assert not (tmp_path / "escape.py").exists()


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_release_trust_binds_allowed_signer_to_fingerprint(
    executable: str,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    _, allowed = signed_bundle
    key_blob = base64.b64decode(allowed.read_text(encoding="ascii").split()[2])
    fingerprint = tmp_path / f"fingerprint-{executable}"
    fingerprint.write_text(
        "SHA256:" + base64.b64encode(hashlib.sha256(key_blob).digest()).decode().rstrip("=") + "\n",
        encoding="ascii",
        newline="\n",
    )
    source = TARGET.read_text(encoding="utf-8")
    function = _function(source, "Assert-ReleaseTrust", "Assert-EntitlementPublicKey")
    harness = f"""
$ErrorActionPreference='Stop'
$AllowedSigners={_ps_literal(allowed)}
$ReleaseFingerprint={_ps_literal(fingerprint)}
function Fail([string]$Code) {{ throw $Code }}
function Assert-ProtectedItem([string]$Path,[string]$Kind) {{ }}
function Get-StrictAscii([string]$Path) {{
  $encoding=[Text.Encoding]::GetEncoding(20127,[Text.EncoderFallback]::ExceptionFallback,[Text.DecoderFallback]::ExceptionFallback)
  return $encoding.GetString([IO.File]::ReadAllBytes($Path))
}}
{function}
try {{ Assert-ReleaseTrust; [Console]::Out.Write('ok') }}
catch {{ [Console]::Out.Write($_.Exception.Message); exit 2 }}
"""
    valid = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert valid.stdout == "ok"
    fingerprint.write_text("SHA256:" + "A" * 43 + "\n", encoding="ascii", newline="\n")
    invalid = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert invalid.returncode == 2
    assert invalid.stdout == "bootstrap_release_trust_invalid"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_entitlement_public_key_requires_canonical_ed25519_blob(
    executable: str,
    signed_bundle: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    bundle, _ = signed_bundle
    key_path = tmp_path / f"entitlement-key-{executable}"
    shutil.copy2(bundle / "entitlement-public-key", key_path)
    source = TARGET.read_text(encoding="utf-8")
    functions = _function(source, "Get-StrictAscii", "Assert-ReleaseTrust") + _function(
        source, "Assert-EntitlementPublicKey", "Enter-BootstrapLock"
    )
    harness = f"""
$ErrorActionPreference='Stop'
function Fail([string]$Code) {{ throw $Code }}
{functions}
try {{ Assert-EntitlementPublicKey {_ps_literal(key_path)}; [Console]::Out.Write('ok') }}
catch {{ [Console]::Out.Write($_.Exception.Message); exit 2 }}
"""
    valid = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert valid.stdout == "ok"
    key_path.write_text("ssh-ed25519 AAAA entitlement-test\n", encoding="ascii", newline="\n")
    invalid = subprocess.run(
        [_powershell(executable), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert invalid.returncode == 2
    assert invalid.stdout == "bootstrap_entitlement_key_invalid"
