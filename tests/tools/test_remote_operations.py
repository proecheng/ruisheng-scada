"""Contracts for the Windows remote-operation tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
DEBUG_SCRIPT = ROOT / "tools" / "remote_debug.ps1"
HOTFIX_SCRIPT = ROOT / "tools" / "remote_hotfix_deploy.ps1"
MAINTENANCE_SCRIPT = ROOT / "tools" / "remote_maintenance.ps1"
MAINTENANCE_PREPARE_SCRIPT = ROOT / "tools" / "remote_maintenance_prepare.ps1"
GUIDE = ROOT / "docs" / "REMOTE_DEBUG.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _powershell() -> str:
    executable = shutil.which("powershell.exe")
    if executable is None:
        pytest.skip("Windows PowerShell is unavailable")
    return executable


def _powershell_env() -> dict[str, str]:
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


def _qualification_toolchain_descriptor() -> dict[str, object]:
    return {
        "path": "qualification-toolchain.tar.gz",
        "sha256": "6" * 64,
        "format": "tar+gzip",
        "semantic_validator": "ruisheng.device-point-profile-validator/v5",
        "schema": {
            "path": "schemas/point-profile/point-profile-v1.schema.json",
            "sha256": "7" * 64,
        },
        "validator": {
            "path": "tools/validate_device_point_profile.py",
            "sha256": "8" * 64,
        },
        "producer": {
            "path": "tools/release_artifacts.py",
            "sha256": "9" * 64,
        },
        "receipt_producer": {
            "path": "tools/release_verification_receipt.py",
            "sha256": "b" * 64,
        },
        "toolchain_manifest": {
            "path": "qualification-toolchain-manifest.json",
            "sha256": "a" * 64,
        },
    }


def _candidate_manifest(schema_version: int = 2) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": schema_version,
        "candidate_id": "candidate",
        "source_commit": "b" * 40,
        "generated_at": "2026-08-27T00:00:00+00:00",
        "target_os": "linux",
        "target_architecture": "amd64",
        "alembic_head": "0006_alarm_event_state",
        "logical_identity": "sha256:" + "c" * 64,
        "tools": {
            "docker": "29.0.0/29.0.0",
            "docker_compose": "2.39.1",
            "git": "git version 2.51.0.windows.1",
            "python": "3.13.7",
            "release_artifacts": "1",
        },
        "authenticity": {
            "status": "SIGNED",
            "scheme": "openssh-sshsig",
            "publisher": "ruisheng-release",
            "namespace": "ruisheng-candidate-v1",
            "key_type": "ssh-ed25519",
            "key_fingerprint": "SHA256:" + "A" * 43,
            "signed_object": "SHA256SUMS",
            "signature_file": "SHA256SUMS.sig",
        },
        "images": [
            {
                "component": service,
                "source_reference": f"fixture/{service}:source",
                "repo_digest": f"fixture/{service}@sha256:" + "d" * 64,
                "candidate_reference": f"fixture/{service}:candidate",
                "image_id": f"sha256:{str(index) * 64}",
                "os": "linux",
                "architecture": "amd64",
                "archive": f"images/{service}.tar.gz",
                "sha256": "e" * 64,
            }
            for index, service in enumerate(("postgres", "redis", "api", "gw", "web"), start=1)
        ],
    }
    if schema_version == 3:
        manifest["qualification_toolchain"] = _qualification_toolchain_descriptor()
    return manifest


def _remote_template() -> str:
    match = re.search(
        r"\$remoteTemplate = @'\r?\n(.*?)\r?\n'@", _read(MAINTENANCE_SCRIPT), re.DOTALL
    )
    assert match is not None
    return match.group(1)


def _set_restricted_directory(path: Path, *, audit_mutex: bool = False) -> None:
    mutex = (
        "[IO.File]::WriteAllText((Join-Path $path '.remote-maintenance-audit.lock'), '')"
        if audit_mutex
        else ""
    )
    script = f"""
$ErrorActionPreference = 'Stop'
$path = {_ps_literal(path)}
New-Item -ItemType Directory -Path $path -Force | Out-Null
$security = New-Object Security.AccessControl.DirectorySecurity
$security.SetAccessRuleProtection($true, $false)
$security.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User)
$sidValues = @(
  [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
  'S-1-5-18',
  'S-1-5-32-544'
) | Select-Object -Unique
foreach ($sidValue in $sidValues) {{
  $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
  $rule = New-Object Security.AccessControl.FileSystemAccessRule(
    $sid,
    [Security.AccessControl.FileSystemRights]::FullControl,
    ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
      [Security.AccessControl.InheritanceFlags]::ObjectInherit),
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow
  )
  [void]$security.AddAccessRule($rule)
}}
[IO.Directory]::SetAccessControl($path, $security)
{mutex}
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_powershell_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _remote_layout(tmp_path: Path) -> dict[str, Path]:
    candidate = tmp_path / "candidate"
    site = tmp_path / "site"
    audit = tmp_path / "audit"
    candidate.mkdir()
    site.mkdir()
    for name in ("docker-compose.prod.yml", "site-network.override.yml"):
        (candidate / name).write_text(f"fixture:{name}\n", encoding="utf-8")
    manifest = _candidate_manifest()
    (candidate / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    (site / ".env.prod").write_text("SECRET_FIXTURE=never-return\n", encoding="utf-8")
    state = site / ".remote-maintenance-state"
    _set_restricted_directory(state)
    (state / "active-release.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidate_id": candidate.name,
                "logical_identity": manifest["logical_identity"],
                "source_commit": manifest["source_commit"],
                "candidate_root": str(candidate),
                "site_root": str(site),
                "committed_at": "2026-09-01T00:00:00+00:00",
                "operation_id": "00000000-0000-4000-8000-000000000099",
            }
        ),
        encoding="utf-8",
    )
    return {"candidate": candidate, "site": site, "audit": audit}


def _tree_digest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in path.rglob("*")
        if item.is_file()
    }


def _mock_preamble() -> str:
    compose_model = json.dumps(
        {
            "services": {
                service: {
                    "pull_policy": "never",
                    "image": f"fixture/{'api' if service == 'migrate' else service}:candidate",
                    "ports": [],
                }
                for service in ("postgres", "redis", "migrate", "gw", "api", "web")
            }
        },
        separators=(",", ":"),
    )
    return rf"""
function global:docker {{
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$DockerArguments)
  $argumentList = @($DockerArguments | ForEach-Object {{ "$_" }})
  [IO.File]::AppendAllText(
    $env:SIM_DOCKER_LOG,
    (($argumentList -join "`t") + [Environment]::NewLine),
    (New-Object Text.UTF8Encoding($false))
  )
  if ($argumentList[0] -eq 'compose' -and $argumentList -contains 'config') {{
    $global:LASTEXITCODE = 0
    Write-Output '{compose_model}'
    return
  }}
  if ($argumentList[0] -eq 'inspect' -and $argumentList -contains '{{{{json .State}}}}') {{
    $container = $argumentList[-1]
    $service = $container -replace '^ruisheng-', ''
    if ($env:SIM_FAIL_INSPECT -eq $service) {{
      $global:LASTEXITCODE = 43
      Write-Output 'simulated inspect failure'
      return
    }}
    $stopped = @()
    if (Test-Path -LiteralPath $env:SIM_STOPPED_FILE -PathType Leaf) {{
      $stopped = @(Get-Content -LiteralPath $env:SIM_STOPPED_FILE)
    }}
    $global:LASTEXITCODE = 0
    if ($stopped -contains $service) {{
      Write-Output '{{"Running":false,"Health":{{"Status":"healthy"}}}}'
    }} else {{
      Write-Output '{{"Running":true,"Health":{{"Status":"healthy"}}}}'
    }}
    return
  }}
  if ($argumentList[0] -eq 'ps' -and $argumentList -contains '{{{{.Names}}}}') {{
    $global:LASTEXITCODE = 0
    Write-Output 'ruisheng-postgres'
    Write-Output 'ruisheng-redis'
    Write-Output 'ruisheng-gw'
    Write-Output 'ruisheng-api'
    Write-Output 'ruisheng-web'
    return
  }}
  if ($argumentList[0] -eq 'inspect' -and $argumentList -contains '{{{{.Config.Image}}}}') {{
    $global:LASTEXITCODE = 0
    Write-Output 'fixture/image@sha256:0123456789abcdef'
    return
  }}
  if ($argumentList[0] -eq 'image' -and $argumentList[1] -eq 'inspect') {{
    $component = (($argumentList[-1] -split '/')[-1] -split ':')[0]
    $indexes = @{{
      postgres = '1'; redis = '2'; api = '3'; gw = '4'; web = '5'
    }}
    $global:LASTEXITCODE = 0
    Write-Output ('sha256:' + ($indexes[$component] * 64))
    return
  }}
  $stopIndex = [Array]::IndexOf($argumentList, 'stop')
  if ($stopIndex -ge 0) {{
    $service = $argumentList[$stopIndex + 1]
    if ($env:SIM_FAIL_STOP -eq $service) {{
      $global:LASTEXITCODE = 42
      Write-Output 'simulated stop failure'
      return
    }}
    if ($env:SIM_DRIFT_AFTER_STOP -eq $service) {{
      [IO.File]::AppendAllText($env:SIM_DRIFT_PATH, "`nchanged")
    }}
    [IO.File]::AppendAllText($env:SIM_STOPPED_FILE, $service + [Environment]::NewLine)
  }}
  $global:LASTEXITCODE = 0
}}

function global:sshd.exe {{
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$SshdArguments)
  $global:LASTEXITCODE = 0
  Write-Output "passwordauthentication $env:SIM_PASSWORD_AUTH"
  Write-Output 'kbdinteractiveauthentication no'
  Write-Output 'pubkeyauthentication yes'
  Write-Output 'authenticationmethods publickey'
  Write-Output 'gssapiauthentication no'
  Write-Output 'hostbasedauthentication no'
}}

$env:SSH_CONNECTION = '100.64.0.10 50123 100.64.0.20 22'
"""


def _render_remote_script(
    layout: dict[str, Path],
    *,
    action: str,
    reason: str = "approved test reason",
    operation_id: str = "00000000-0000-4000-8000-000000000001",
    dry_run: bool = False,
    approved: bool = True,
    scenario: str = "",
) -> str:
    rendered = _remote_template().replace(
        '$AuditDirectory = "C:\\Ruisheng\\audit"',
        f"$AuditDirectory = {_ps_literal(layout['audit'])}",
    )
    replacements = {
        "__ACTION__": _ps_literal(action),
        "__REASON__": _ps_literal(reason),
        "__OPERATION_ID__": _ps_literal(operation_id),
        "__TARGET__": _ps_literal("fixture@100.64.0.20"),
        "__CANDIDATE_ROOT__": _ps_literal(layout["candidate"]),
        "__SITE_ROOT__": _ps_literal(layout["site"]),
        "__LEASE_SECONDS__": "120",
        "__DRY_RUN__": "$true" if dry_run else "$false",
        "__APPROVED__": "$true" if approved else "$false",
    }
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    rendered = rendered.replace(
        '$CandidateRoot = ""', f"$CandidateRoot = {_ps_literal(layout['candidate'])}", 1
    )
    if scenario:
        rendered = rendered.replace(
            "\n$identity = Get-RemoteIdentity\n$posture = Get-SshPosture",
            f"\n{scenario}\n$identity = Get-RemoteIdentity\n$posture = Get-SshPosture",
            1,
        )
    return _mock_preamble() + "\n" + rendered


def _run_remote_script(
    tmp_path: Path,
    layout: dict[str, Path],
    *,
    action: str,
    reason: str = "approved test reason",
    operation_id: str = "00000000-0000-4000-8000-000000000001",
    dry_run: bool = False,
    approved: bool = True,
    password_auth: str = "no",
    scenario: str = "",
    fail_stop: str = "",
    drift_after_stop: str = "",
    fail_inspect: str = "",
) -> tuple[dict[str, Any], list[str]]:
    script_path = tmp_path / f"remote-{action}-{operation_id[-4:]}.ps1"
    command_log = tmp_path / f"docker-{action}-{operation_id[-4:]}.log"
    script_path.write_text(
        _render_remote_script(
            layout,
            action=action,
            reason=reason,
            operation_id=operation_id,
            dry_run=dry_run,
            approved=approved,
            scenario=scenario,
        ),
        encoding="utf-8",
    )
    env = _powershell_env()
    env.update(
        {
            "SIM_DOCKER_LOG": str(command_log),
            "SIM_PASSWORD_AUTH": password_auth,
            "SIM_FAIL_STOP": fail_stop,
            "SIM_DRIFT_AFTER_STOP": drift_after_stop,
            "SIM_FAIL_INSPECT": fail_inspect,
            "SIM_DRIFT_PATH": str(layout["candidate"] / "docker-compose.prod.yml"),
            "SIM_STOPPED_FILE": str(tmp_path / f"stopped-{action}-{operation_id[-4:]}.txt"),
        }
    )
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", script_path],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    json_lines = [line for line in completed.stdout.splitlines() if line.lstrip().startswith("{")]
    assert json_lines, completed.stdout + completed.stderr
    commands = command_log.read_text(encoding="utf-8").splitlines() if command_log.exists() else []
    return json.loads(json_lines[-1]), commands


def _restricted_scenario(*extra: str) -> str:
    return "\n".join(
        (
            "Assert-RestrictedDirectory -Path $StateDirectory",
            "Assert-RestrictedDirectory -Path $AuditDirectory",
            "Assert-RestrictedFile -Path (Join-Path $AuditDirectory '.remote-maintenance-audit.lock')",
            *extra,
        )
    )


def _prepare_restricted_layout(layout: dict[str, Path]) -> None:
    _set_restricted_directory(layout["site"])
    _set_restricted_directory(layout["site"] / ".remote-maintenance-state")
    _set_restricted_directory(layout["audit"], audit_mutex=True)


def _run_prepare_remote_template(
    tmp_path: Path, layout: dict[str, Path], *, password_auth: str
) -> subprocess.CompletedProcess[str]:
    script = _read(MAINTENANCE_PREPARE_SCRIPT)
    match = re.search(r"\$remoteTemplate = @'\r?\n(.*?)\r?\n'@", script, re.DOTALL)
    assert match is not None
    rendered = match.group(1)
    rendered = rendered.replace("__SITE_ROOT__", _ps_literal(layout["site"]))
    rendered = rendered.replace("__AUDIT_DIRECTORY__", _ps_literal(layout["audit"]))
    rendered = _mock_preamble() + "\n" + rendered
    script_path = tmp_path / f"prepare-{password_auth}.ps1"
    script_path.write_text(rendered, encoding="utf-8")
    env = _powershell_env()
    env.update(
        {
            "SIM_DOCKER_LOG": str(tmp_path / "prepare-docker.log"),
            "SIM_PASSWORD_AUTH": password_auth,
            "SIM_FAIL_STOP": "",
            "SIM_DRIFT_AFTER_STOP": "",
            "SIM_FAIL_INSPECT": "",
            "SIM_DRIFT_PATH": str(layout["candidate"] / "docker-compose.prod.yml"),
            "SIM_STOPPED_FILE": str(tmp_path / "prepare-stopped.txt"),
        }
    )
    return subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", script_path],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_remote_debug_tunnel_is_loopback_only_and_fail_fast() -> None:
    script = _read(DEBUG_SCRIPT)

    assert '"-o", "BatchMode=yes"' in script
    assert '"-o", "StrictHostKeyChecking=yes"' in script
    assert '"-o", "ExitOnForwardFailure=yes"' in script
    assert '"-o", "ServerAliveInterval=15"' in script
    assert '"-o", "ServerAliveCountMax=3"' in script
    assert '"127.0.0.1:${WebPort}:127.0.0.1:80"' in script
    assert '"127.0.0.1:${GwHealthPort}:127.0.0.1:9090"' in script
    assert '"127.0.0.1:${GwDevicePort}:127.0.0.1:5020"' in script
    assert "0.0.0.0:${" not in script


def test_remote_debug_state_guards_pid_reuse_and_supports_all_actions() -> None:
    script = _read(DEBUG_SCRIPT)

    assert '[ValidateSet("Start", "Stop", "Status", "Health", "Logs")]' in script
    assert "Get-CimInstance Win32_Process" in script
    assert 'if ($process.Name -notin @("ssh", "ssh.exe"))' in script
    assert "foreach ($marker in $markers)" in script
    assert "stale state removed" in script
    assert "A tunnel is already running with different settings" in script
    assert "Stop-Process -Id ([int]$state.pid)" in script
    assert "docker exec ruisheng-api python" in script
    assert "docker exec ruisheng-gw python" in script


def test_hotfix_supports_only_application_services_with_known_health_checks() -> None:
    script = _read(HOTFIX_SCRIPT)

    assert '[ValidateSet("api", "gw", "web")]' in script
    assert 'env_key        = "API_IMAGE"' in script
    assert 'env_key        = "GW_IMAGE"' in script
    assert 'env_key        = "WEB_IMAGE"' in script
    assert 'health_url     = "http://127.0.0.1:8000/api/health/ready"' in script
    assert 'health_url     = "http://127.0.0.1:9090/ready"' in script
    assert 'health_url     = "http://127.0.0.1/"' in script


def test_hotfix_build_uses_clean_commit_and_immutable_identity() -> None:
    script = _read(HOTFIX_SCRIPT)

    assert '"status", "--porcelain", "--untracked-files=all"' in script
    assert "The worktree must be clean" in script
    assert "$commit.Substring(0, 12)" in script
    assert '"ruisheng-hotfix/${Service}:$ShortCommit"' in script
    assert '"build", "--pull", "--platform", $Platform' in script
    assert '"org.opencontainers.image.revision=$Commit"' in script
    assert "Immutable hotfix image tag already exists locally" in script


def test_hotfix_verifies_archive_and_loaded_image_before_environment_change() -> None:
    script = _read(HOTFIX_SCRIPT)
    remote = script.split("$deploymentTemplate = @'", 1)[1].split("\n'@", 1)[0]

    hash_check = remote.index("Get-LeaseGuardedFileSha256 -Path $ArchivePath")
    load = remote.index('Invoke-Docker -Arguments @("image", "load"')
    inspect = remote.index('Invoke-Docker -Arguments @("image", "inspect"')
    environment_change = remote.index('$lines[$entryIndex] = "$EnvironmentKey=$ExpectedImage"')

    assert hash_check < load < inspect < environment_change
    assert "Loaded image ID does not match the manifest" in remote
    assert "Loaded image platform mismatch" in remote
    assert "Loaded image revision label mismatch" in remote
    assert "Get-FileHash -LiteralPath $archivePath -Algorithm SHA256" in script
    assert "schema_version  = 1" in script


def test_hotfix_renders_offline_compose_and_recreates_only_selected_service() -> None:
    script = _read(HOTFIX_SCRIPT)

    assert '"config", "--format", "json"' in script
    assert '[string]$serviceModel.pull_policy -ne "never"' in script
    assert '[string]$port.host_ip -notin @("127.0.0.1", "::1")' in script
    assert '@("up", "-d", "--no-deps", "--force-recreate", $Service)' in script
    assert (
        'Invoke-Docker -Arguments @("exec", $ContainerName, "python", "-c", $pythonProbe)' in script
    )
    assert 'Invoke-Docker -Arguments @("exec", $ContainerName, "wget"' in script
    assert "Service did not become ready" in script


def test_hotfix_has_atomic_environment_backup_and_automatic_rollback() -> None:
    script = _read(HOTFIX_SCRIPT)

    assert '"$EnvFile.pre-hotfix-$Service-$timestamp.bak"' in script
    assert "[IO.File]::Replace($temporaryEnv, $EnvFile, $backupPath)" in script
    assert "Copy-Item -LiteralPath $backupPath -Destination $rollbackTemp" in script
    assert "[IO.File]::Replace($rollbackTemp, $EnvFile, $rollbackBackup)" in script
    assert "Remove-Item -LiteralPath $rollbackBackup" in script
    assert "Deployment failed and the previous image was restored" in script
    assert "Rollback also failed" in script
    assert "[IO.FileMode]::CreateNew" in script
    assert 'Join-Path $SiteRoot ".remote-hotfix.lock"' in script
    assert "Another maintenance or hotfix operation is active" in script


def test_maintenance_surface_validates_reason_approval_and_key_only_posture() -> None:
    script = _read(MAINTENANCE_SCRIPT)

    assert '[ValidateSet("Status", "StopApp", "StartApp", "RestartApp")]' in script
    assert "$Reason.Length -lt 8" in script
    assert "$Reason.Length -gt 200" in script
    assert r"$Reason -match '[\x00-\x1f\x7f]'" in script
    assert "requires fresh approval through -Approved" in script
    assert '$password -eq "no"' in script
    assert '$keyboard -eq "no"' in script
    assert '$publicKey -eq "yes"' in script
    assert 'if (-not $posture.mutation_allowed) { throw "ssh_not_key_only" }' in script


def test_maintenance_prepare_is_explicit_key_only_and_docker_free() -> None:
    script = _read(MAINTENANCE_PREPARE_SCRIPT)
    remote = script.split("$remoteTemplate = @'", 1)[1].split("\n'@", 1)[0]

    assert "requires fresh approval through -Approved" in script
    assert 'if (-not $posture.mutation_allowed) { throw "ssh_not_key_only" }' in remote
    assert "SetAccessRuleProtection($true, $false)" in remote
    assert '"S-1-5-18"' in remote
    assert '"S-1-5-32-544"' in remote
    assert remote.index("Acquire-PreparationLock -Path $SharedLockPath") < remote.index(
        "Acquire-PreparationLock -Path $LegacyLockPath"
    )
    assert remote.index("Set-RestrictedDirectory -Path $SiteRoot") < remote.index(
        "Acquire-PreparationLock -Path $SharedLockPath"
    )
    assert remote.index("Acquire-PreparationLock -Path $LegacyLockPath") < remote.index(
        "Set-RestrictedDirectory -Path $AuditDirectory"
    )
    assert '"-o", "BatchMode=yes"' in script
    assert '"-o", "StrictHostKeyChecking=yes"' in script
    assert "docker" not in script.lower()


def test_maintenance_transport_and_identity_are_remote_derived() -> None:
    script = _read(MAINTENANCE_SCRIPT)

    assert '"-o", "BatchMode=yes"' in script
    assert '"-o", "StrictHostKeyChecking=yes"' in script
    assert '"powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"' in script
    assert "$transportScript | & ssh.exe @sshArguments" in script
    assert '$transportScript = "& {`r`n$remoteScript`r`n}`r`n"' in script
    assert "EncodedCommand" not in script
    assert len(_remote_template()) > 32_767
    assert "user           = [string]$env:USERNAME" in script
    assert "computer       = [string]$env:COMPUTERNAME" in script
    assert "ssh_connection = [string]$env:SSH_CONNECTION" in script
    assert "ConvertTo-PowerShellUtf8Expression $Reason" in script


def test_maintenance_stdin_transport_executes_script_larger_than_windows_command_line() -> None:
    payload = "#" + ("x" * 110_000) + "\n[ordered]@{ok=$true} | ConvertTo-Json -Compress"
    transport = f"& {{\r\n{payload}\r\n}}\r\n"

    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
        input=transport,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_powershell_env(),
    )

    assert len(transport) > 100_000
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1])["ok"] is True


def test_maintenance_dry_run_compares_read_only_snapshots() -> None:
    script = _read(MAINTENANCE_SCRIPT)
    remote = _remote_template()
    dry_run = remote.split('if ($Action -eq "Status" -or $DryRun) {', 1)[1].split(
        "if (-not $Approved)", 1
    )[0]

    assert "$before = Get-Snapshot" in script
    assert "$after = Get-Snapshot" in dry_run
    assert 'if ($before.identity -ne $after.identity) { throw "dry_run_state_changed" }' in dry_run
    assert '$status = "security_blocked"' in dry_run
    assert "Acquire-LeasedLock" not in dry_run
    assert "Write-TargetAudit" not in dry_run
    assert "Write-JsonAtomic" not in dry_run
    assert 'Invoke-DockerText -Arguments ($composeBase + @("stop"' not in dry_run
    assert "snapshot_equal" in script


def test_maintenance_lock_is_leased_pid_safe_and_reclaimed_fail_closed() -> None:
    script = _read(MAINTENANCE_SCRIPT)

    assert 'Join-Path $StateDirectory ".remote-maintenance.lock"' in script
    assert 'Join-Path $SiteRoot ".remote-hotfix.lock"' in script
    assert "[IO.FileMode]::CreateNew" in script
    assert "process_started_at = $ProcessStartedAt" in script
    assert "Get-Process -Id ([int]$Record.pid)" in script
    assert "process.StartTime.ToUniversalTime()" in script
    assert "-not $expired -or (Test-MatchingProcess -Record $existing)" in script
    assert 'throw "lock_conflict_unrecognized"' in script
    assert "stale_lock_reclaimed" in script
    assert script.index("Acquire-LeasedLock -Path $SharedLockPath") < script.index(
        "Acquire-LeasedLock -Path $LegacyLockPath"
    )


def test_hotfix_and_maintenance_use_the_same_fixed_lock_order() -> None:
    hotfix = _read(HOTFIX_SCRIPT)
    remote = hotfix.split("$deploymentTemplate = @'", 1)[1].split("\n'@", 1)[0]

    shared = 'Acquire-TransitionLock -Path $sharedLockPath -Name "shared-maintenance"'
    legacy = 'Acquire-TransitionLock -Path $legacyLockPath -Name "legacy-hotfix"'
    docker_mutation = 'Invoke-Docker -Arguments @("image", "load"'
    environment_mutation = '$lines[$entryIndex] = "$EnvironmentKey=$ExpectedImage"'

    assert remote.index(shared) < remote.index(legacy) < remote.index(docker_mutation)
    assert remote.index(legacy) < remote.index(environment_mutation)
    assert "[array]::Reverse($paths)" in remote
    assert "record.process_started_at" in remote
    assert "Get-Process -Id $reservedPid" in remote
    assert "[IO.FileMode]::CreateNew" in hotfix
    assert 'Join-Path $maintenanceStateDirectory ".remote-maintenance.lock"' in remote
    assert hotfix.rindex("Assert-RemoteMaintenanceLocksAvailable") < hotfix.index(
        "$preflight = Get-RemotePreflight"
    )
    assert hotfix.rindex("Assert-RemoteMaintenanceLocksAvailable") < hotfix.index(
        "$remoteDirectory = Send-HotfixArtifact"
    )
    assert hotfix.rindex("Start-RemoteHotfixReservation") < hotfix.index(
        "$preflight = Get-RemotePreflight"
    )
    assert hotfix.rindex("Start-RemoteHotfixReservation") < hotfix.index(
        "$remoteDirectory = Send-HotfixArtifact"
    )
    assert "$hotfixOperationId = __OPERATION_ID__" in remote
    assert 'throw "The reserved $Name lock was lost before deployment."' in remote
    assert "Assert-RemoteHotfixReservationAlive -Process $reservationProcess" in hotfix
    assert "$record.expires_at = [DateTimeOffset]::UtcNow.AddMinutes(5)" in hotfix
    assert "[Console]::Out.Flush()" in hotfix


def test_hotfix_deployment_handoff_survives_reservation_exit(tmp_path: Path) -> None:
    hotfix = _read(HOTFIX_SCRIPT)
    reservation = hotfix.split("function Start-RemoteHotfixReservation", 1)[1].split(
        "function Stop-RemoteHotfixReservation", 1
    )[0]
    template = reservation.split("$template = @'", 1)[1].split("\n'@", 1)[0]
    release_function = template.split("function Release-Locks {", 1)[1].split("\n\ntry {", 1)[0]
    shared = tmp_path / "shared.lock"
    legacy = tmp_path / "legacy.lock"
    invocation = f"""
$ErrorActionPreference = 'Stop'
$OperationId = '00000000-0000-4000-8000-000000000071'
$Action = 'hotfix-gw'
$ProcessStartedAt = (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
$Acquired = New-Object System.Collections.ArrayList
$shared = {_ps_literal(shared)}
$legacy = {_ps_literal(legacy)}
[void]$Acquired.Add($shared)
[void]$Acquired.Add($legacy)
$sharedRecord = [ordered]@{{
  schema_version=1; operation_id=$OperationId; action=$Action; pid=($PID + 100000);
  process_started_at='deployment-owner'; phase='deployment'
}}
$legacyRecord = [ordered]@{{
  schema_version=1; operation_id=$OperationId; action=$Action; pid=$PID;
  process_started_at=$ProcessStartedAt
}}
[IO.File]::WriteAllText($shared, ($sharedRecord | ConvertTo-Json -Compress))
[IO.File]::WriteAllText($legacy, ($legacyRecord | ConvertTo-Json -Compress))
function Release-Locks {{{release_function}
Release-Locks
[ordered]@{{shared=(Test-Path -LiteralPath $shared); legacy=(Test-Path -LiteralPath $legacy)}} |
  ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_powershell_env(),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1]) == {"shared": True, "legacy": True}

    remote = hotfix.split("$deploymentTemplate = @'", 1)[1].split("\n'@", 1)[0]
    assert 'phase -NotePropertyValue "deployment"' in remote
    assert "while (-not $process.WaitForExit(1000)) { Maintain-TransitionLocks }" in remote
    assert remote.index(
        'Acquire-TransitionLock -Path $sharedLockPath -Name "shared-maintenance"'
    ) < remote.index('Acquire-TransitionLock -Path $legacyLockPath -Name "legacy-hotfix"')
    assert "Get-LeaseGuardedFileSha256 -Path $ArchivePath" in remote


def test_hotfix_health_does_not_swallow_lock_loss() -> None:
    hotfix = _read(HOTFIX_SCRIPT)
    remote = hotfix.split("$deploymentTemplate = @'", 1)[1].split("\n'@", 1)[0]
    health_function = remote.split("function Test-ServiceReady {", 1)[1].split(
        "function Wait-ServiceReady", 1
    )[0]
    invocation = f"""
$ErrorActionPreference = 'Stop'
$Service = 'gw'
$ContainerName = 'fixture-gw'
$HealthUrl = 'http://127.0.0.1:9090/ready'
$script:calls = 0
function Invoke-Docker {{
  $script:calls++
  if ($script:calls -eq 1) {{ return '{{"Running":true,"Health":{{"Status":"healthy"}}}}' }}
  throw 'deployment_lock_unavailable'
}}
function Test-ServiceReady {{{health_function}
Test-ServiceReady
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", invocation],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_powershell_env(),
    )

    assert completed.returncode != 0
    assert "deployment_lock_unavailable" in completed.stderr
    first_env_mutation = remote.index("[IO.File]::Replace($temporaryEnv, $EnvFile, $backupPath)")
    rollback_mutation = remote.index("[IO.File]::Replace($rollbackTemp, $EnvFile, $rollbackBackup)")
    assert remote.rfind("Maintain-TransitionLocks -Force", 0, first_env_mutation) != -1
    assert (
        remote.rfind("Maintain-TransitionLocks -Force", 0, rollback_mutation) > first_env_mutation
    )
    assert "no unlocked rollback was attempted" in remote


def test_maintenance_lifecycle_order_migration_health_and_partial_recovery() -> None:
    script = _read(MAINTENANCE_SCRIPT)

    assert '$StopOrder = @("web", "api", "gw", "redis", "postgres")' in script
    assert '$composeBase + @("stop", $service)' in script
    assert 'throw "service_stop_verification_failed"' in script
    assert '$composeBase + @("up", "-d", "postgres", "redis")' in script
    assert '"--exit-code-from", "migrate", "migrate"' in script
    assert '"--force-recreate", "--abort-on-container-exit"' in script
    assert '$composeBase + @("up", "-d", "gw", "api", "web")' in script
    assert '$PolicyServices = @("postgres", "redis", "migrate", "gw", "api", "web")' in script
    assert 'throw "loaded_image_identity_mismatch"' in script
    assert '"exec", "ruisheng-api", "python"' in script
    assert '"exec", "ruisheng-gw", "python"' in script
    assert '"exec", "ruisheng-web", "wget"' in script
    assert 'throw "partial_stop"' in script
    assert "use a new operation identity for the approved recovery action" in script
    assert "keep healthy dependencies running" not in script


def test_maintenance_idempotency_audit_and_output_are_allowlisted() -> None:
    script = _read(MAINTENANCE_SCRIPT)

    assert 'status -in @("succeeded", "failed", "partial", "rejected", "uncertain")' in script
    assert 'throw "operation_identity_conflict"' in script
    assert 'Join-Path $AuditDirectory "remote-maintenance.jsonl"' in script
    assert "previous_hash" in script
    assert "record_hash" in script
    assert "Write-OperatorAudit" in script
    assert script.count("Get-Content -LiteralPath $AuditPath -Encoding UTF8") == 1
    assert "$script:AuditMaxFileBytes = 16 * 1024 * 1024" in script
    assert "$script:AuditMaxLineBytes = 64 * 1024" in script
    assert "$script:AuditMaxRecords = 50000" in script
    assert "New-SafeResult" in script
    assert "ConvertTo-Json -Depth 10 -Compress" in script
    assert "Config.Env" not in script
    assert "docker logs" not in script.lower()
    assert "request url" not in script.lower()


def test_maintenance_never_uses_destructive_or_exposing_commands() -> None:
    script = _read(MAINTENANCE_SCRIPT).lower()

    forbidden = (
        "compose down",
        '"down"',
        "down -v",
        "volume rm",
        "docker rm",
        "0.0.0.0:",
        "shutdown.exe",
        "restart-computer",
        "stop-computer",
    )
    for text in forbidden:
        assert text not in script


def test_maintenance_status_and_dry_run_make_no_target_writes(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)
    before = {name: _tree_digest(path) for name, path in layout.items()}

    status, status_commands = _run_remote_script(
        tmp_path,
        layout,
        action="Status",
        operation_id="00000000-0000-4000-8000-000000000011",
        password_auth="yes",
    )
    after_status = {name: _tree_digest(path) for name, path in layout.items()}
    dry_run, dry_run_commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000012",
        dry_run=True,
        password_auth="yes",
    )

    assert status["status"] == "observed"
    assert status["snapshot_equal"] is True
    assert dry_run["status"] == "security_blocked"
    assert dry_run["error_code"] == "ssh_not_key_only"
    assert dry_run["snapshot_equal"] is True
    assert dry_run["plan"]["stop_order"] == ["web", "api", "gw", "redis", "postgres"]
    assert dry_run["plan"]["preserves_volumes"] is True
    assert before == after_status == {name: _tree_digest(path) for name, path in layout.items()}
    status_execs = [command for command in status_commands if "\texec\t" in f"\t{command}\t"]
    assert len(status_execs) == 3
    dry_run_execs = [command for command in dry_run_commands if "\texec\t" in f"\t{command}\t"]
    assert len(dry_run_execs) == 3
    assert sorted(
        path.name
        for path in (layout["site"] / ".remote-maintenance-state").iterdir()
        if path.is_file()
    ) == ["active-release.json"]
    assert not layout["audit"].exists()


@pytest.mark.parametrize(
    ("schema_version", "action", "dry_run"),
    (
        (2, "Status", False),
        (2, "StopApp", True),
        (3, "Status", False),
        (3, "StopApp", True),
    ),
)
def test_maintenance_preflight_accepts_exact_release_manifest_v2_and_v3(
    tmp_path: Path, schema_version: int, action: str, dry_run: bool
) -> None:
    layout = _remote_layout(tmp_path)
    manifest_path = layout["candidate"] / "MANIFEST.json"
    manifest_path.write_text(json.dumps(_candidate_manifest(schema_version)), encoding="utf-8")
    before = {name: _tree_digest(path) for name, path in layout.items()}

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action=action,
        dry_run=dry_run,
        operation_id=f"00000000-0000-4000-8000-{schema_version:012d}",
    )

    assert result["preflight"] == {"ok": True, "error_code": ""}
    assert result["status"] == ("observed" if action == "Status" else "planned")
    assert not any("\tstop\t" in f"\t{command}\t" for command in commands)
    assert before == {name: _tree_digest(path) for name, path in layout.items()}


@pytest.mark.parametrize(
    "mutation",
    (
        "legacy-v1",
        "unknown-version",
        "string-version",
        "boolean-version",
        "v2-with-v3-descriptor",
        "v3-without-descriptor",
        "v3-with-null-descriptor",
        "v3-with-array-descriptor",
        "v3-descriptor-missing-receipt",
        "v3-descriptor-unknown-field",
        "v3-descriptor-bad-validator-path",
        "v3-descriptor-numeric-sha",
        "v3-descriptor-uppercase-format",
        "unknown-root-field",
    ),
)
def test_maintenance_preflight_rejects_legacy_unknown_and_mixed_manifest_schemas(  # noqa: PLR0912
    tmp_path: Path, mutation: str
) -> None:
    layout = _remote_layout(tmp_path)
    manifest = _candidate_manifest(3 if mutation.startswith("v3-") else 2)
    if mutation == "legacy-v1":
        manifest["schema_version"] = 1
    elif mutation == "unknown-version":
        manifest["schema_version"] = 99
    elif mutation == "string-version":
        manifest["schema_version"] = "2"
    elif mutation == "boolean-version":
        manifest["schema_version"] = True
    elif mutation == "v2-with-v3-descriptor":
        manifest["qualification_toolchain"] = _qualification_toolchain_descriptor()
    elif mutation == "v3-without-descriptor":
        manifest.pop("qualification_toolchain")
    elif mutation == "v3-with-null-descriptor":
        manifest["qualification_toolchain"] = None
    elif mutation == "v3-with-array-descriptor":
        manifest["qualification_toolchain"] = [_qualification_toolchain_descriptor()]
    elif mutation == "v3-descriptor-missing-receipt":
        descriptor = manifest["qualification_toolchain"]
        assert isinstance(descriptor, dict)
        descriptor.pop("receipt_producer")
    elif mutation == "v3-descriptor-unknown-field":
        descriptor = manifest["qualification_toolchain"]
        assert isinstance(descriptor, dict)
        descriptor["attacker_selected"] = True
    elif mutation == "v3-descriptor-bad-validator-path":
        descriptor = manifest["qualification_toolchain"]
        assert isinstance(descriptor, dict)
        validator = descriptor["validator"]
        assert isinstance(validator, dict)
        validator["path"] = "tools/attacker.py"
    elif mutation == "v3-descriptor-numeric-sha":
        descriptor = manifest["qualification_toolchain"]
        assert isinstance(descriptor, dict)
        descriptor["sha256"] = 6
    elif mutation == "v3-descriptor-uppercase-format":
        descriptor = manifest["qualification_toolchain"]
        assert isinstance(descriptor, dict)
        descriptor["format"] = "TAR+GZIP"
    elif mutation == "unknown-root-field":
        manifest["attacker_selected"] = True
    manifest_path = layout["candidate"] / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = {name: _tree_digest(path) for name, path in layout.items()}

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="Status",
        operation_id="00000000-0000-4000-8000-000000000098",
    )

    assert result["status"] == "observed"
    assert result["preflight"] == {"ok": False, "error_code": "manifest_schema_invalid"}
    assert not any("\tstop\t" in f"\t{command}\t" for command in commands)
    assert before == {name: _tree_digest(path) for name, path in layout.items()}


def test_maintenance_status_rejects_manifest_image_retargeting_without_writes(
    tmp_path: Path,
) -> None:
    layout = _remote_layout(tmp_path)
    manifest_path = layout["candidate"] / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["images"][0]["image_id"] = "sha256:" + "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = {name: _tree_digest(path) for name, path in layout.items()}

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="Status",
        operation_id="00000000-0000-4000-8000-000000000013",
    )

    assert result["status"] == "observed"
    assert result["preflight"]["ok"] is False
    assert result["preflight"]["error_code"] == "loaded_image_identity_mismatch"
    assert not any("\tstop\t" in f"\t{command}\t" for command in commands)
    assert before == {name: _tree_digest(path) for name, path in layout.items()}


def test_maintenance_password_auth_blocks_before_docker_or_state(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000021",
        password_auth="yes",
    )

    assert result["status"] == "rejected"
    assert result["error_code"] == "ssh_not_key_only"
    assert commands == []
    assert sorted(
        path.name
        for path in (layout["site"] / ".remote-maintenance-state").iterdir()
        if path.is_file()
    ) == ["active-release.json"]
    assert not layout["audit"].exists()


def test_maintenance_prepare_blocks_password_auth_then_provisions_restricted_acl(
    tmp_path: Path,
) -> None:
    layout = _remote_layout(tmp_path)
    blocked = _run_prepare_remote_template(tmp_path, layout, password_auth="yes")

    assert blocked.returncode != 0
    assert "ssh_not_key_only" in blocked.stderr
    assert sorted(
        path.name
        for path in (layout["site"] / ".remote-maintenance-state").iterdir()
        if path.is_file()
    ) == ["active-release.json"]
    assert not layout["audit"].exists()

    prepared = _run_prepare_remote_template(tmp_path, layout, password_auth="no")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    assert json.loads(prepared.stdout.splitlines()[-1])["status"] == "prepared"

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000023",
    )
    assert result["status"] == "succeeded"
    assert any("\tstop\t" in f"\t{command}\t" for command in commands)
    mutation_commands = [command for command in commands if "\tstop\t" in f"\t{command}\t"]
    assert mutation_commands
    assert all(f"{result['operation_id']}.inputs" in command for command in mutation_commands)
    assert not (
        layout["site"] / ".remote-maintenance-state" / f"{result['operation_id']}.inputs"
    ).exists()


def test_maintenance_rejects_unrestricted_state_without_docker(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)
    state = layout["site"] / ".remote-maintenance-state"
    acl_script = f"""
$path = {_ps_literal(state)}
& icacls.exe $path /inheritance:e | Out-Null
if ($LASTEXITCODE -ne 0) {{ throw 'failed to enable fixture ACL inheritance' }}
"""
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", acl_script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_powershell_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    layout["audit"].mkdir()
    (layout["audit"] / ".remote-maintenance-audit.lock").write_text("", encoding="utf-8")

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StartApp",
        operation_id="00000000-0000-4000-8000-000000000022",
    )

    assert result["status"] == "rejected"
    assert result["error_code"] in {
        "restricted_acl_inheritance_enabled",
        "restricted_acl_invalid",
    }
    assert commands == []


@pytest.mark.parametrize(
    ("lock_setup", "error_code"),
    [
        (
            """
$record = [ordered]@{
  schema_version=1; lock_name='shared-maintenance';
  operation_id='10000000-0000-4000-8000-000000000031'; action='hotfix-gw'; pid=999999;
  process_started_at='2000-01-01T00:00:00Z'; target='fixture';
  acquired_at=[DateTimeOffset]::UtcNow.ToString('o');
  expires_at=[DateTimeOffset]::UtcNow.AddMinutes(5).ToString('o')
}
[IO.File]::WriteAllText($SharedLockPath, ($record | ConvertTo-Json -Compress))
""",
            "lock_conflict_active",
        ),
        (
            "[IO.File]::WriteAllText($SharedLockPath, 'legacy-unstructured-lock')",
            "lock_conflict_unrecognized",
        ),
        (
            """
$record = [ordered]@{
  schema_version=1; lock_name='shared-maintenance';
  operation_id='20000000-0000-4000-8000-000000000031'; action='hotfix-api'; pid=$PID;
  process_started_at=$ProcessStartedAt; target='fixture';
  acquired_at=[DateTimeOffset]::UtcNow.AddMinutes(-10).ToString('o');
  expires_at=[DateTimeOffset]::UtcNow.AddMinutes(-5).ToString('o')
}
[IO.File]::WriteAllText($SharedLockPath, ($record | ConvertTo-Json -Compress))
""",
            "lock_conflict_active",
        ),
    ],
)
def test_maintenance_lock_conflicts_are_fail_closed_without_audit_or_docker(
    tmp_path: Path, lock_setup: str, error_code: str
) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000031",
        scenario=_restricted_scenario(lock_setup),
    )

    assert result["status"] == "rejected"
    assert result["error_code"] == error_code
    assert commands == []
    assert not (layout["audit"] / "remote-maintenance.jsonl").exists()
    assert (layout["site"] / ".remote-maintenance-state" / ".remote-maintenance.lock").exists()


def test_maintenance_does_not_reclaim_incomplete_structured_expired_lock(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)
    setup = """
$record = [ordered]@{
  schema_version=1; lock_name='shared-maintenance';
  operation_id='40000000-0000-4000-8000-000000000031'; action='hotfix-web';
  pid=2147480000; process_started_at='2000-01-01T00:00:00Z';
  acquired_at=[DateTimeOffset]::UtcNow.AddMinutes(-10).ToString('o');
  expires_at=[DateTimeOffset]::UtcNow.AddMinutes(-5).ToString('o')
}
[IO.File]::WriteAllText($SharedLockPath, ($record | ConvertTo-Json -Compress))
"""

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="50000000-0000-4000-8000-000000000031",
        scenario=_restricted_scenario(setup),
    )

    assert result["status"] == "rejected"
    assert result["error_code"] == "lock_conflict_unrecognized"
    assert commands == []
    assert (layout["site"] / ".remote-maintenance-state" / ".remote-maintenance.lock").exists()


@pytest.mark.parametrize(
    "record_identity",
    [
        "pid=2147480000; process_started_at='2000-01-01T00:00:00Z'",
        "pid=$PID; process_started_at='2000-01-01T00:00:00Z'",
    ],
)
def test_maintenance_reclaims_only_expired_nonmatching_process_locks(
    tmp_path: Path, record_identity: str
) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)
    setup = f"""
$record = [ordered]@{{
  schema_version=1; lock_name='shared-maintenance';
  operation_id='30000000-0000-4000-8000-000000000041'; action='hotfix-web'; {record_identity};
  target='fixture'; acquired_at=[DateTimeOffset]::UtcNow.AddMinutes(-10).ToString('o');
  expires_at=[DateTimeOffset]::UtcNow.AddMinutes(-5).ToString('o')
}}
[IO.File]::WriteAllText($SharedLockPath, ($record | ConvertTo-Json -Compress))
"""

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000041",
        scenario=_restricted_scenario(setup),
    )

    stops = [command.split("\t")[-1] for command in commands if "\tstop\t" in f"\t{command}\t"]
    assert result["status"] == "succeeded"
    assert stops == ["web", "api", "gw", "redis", "postgres"]
    assert list(
        (layout["site"] / ".remote-maintenance-state").glob(".remote-maintenance.lock.stale.*")
    )
    audit_text = (layout["audit"] / "remote-maintenance.jsonl").read_text(encoding="utf-8")
    assert "stale_lock_reclaimed" in audit_text


def test_maintenance_partial_stop_stops_on_first_failure(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000051",
        scenario=_restricted_scenario(),
        fail_stop="api",
    )

    stops = [command.split("\t")[-1] for command in commands if "\tstop\t" in f"\t{command}\t"]
    assert result["status"] == "partial"
    assert result["error_code"] == "docker_command_failed"
    assert result["stopped"] == ["web"]
    assert result["remaining"] == ["api", "gw", "redis", "postgres"]
    assert stops == ["web", "api"]


def test_maintenance_configuration_drift_blocks_the_next_stop_phase(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000052",
        scenario=_restricted_scenario(),
        drift_after_stop="web",
    )

    stops = [command.split("\t")[-1] for command in commands if "\tstop\t" in f"\t{command}\t"]
    assert result["status"] == "partial"
    assert result["error_code"] == "configuration_drift"
    assert result["stopped"] == ["web"]
    assert stops == ["web"]


def test_maintenance_stop_verification_fails_closed_on_inspect_error(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000053",
        scenario=_restricted_scenario(),
        fail_inspect="web",
    )

    stops = [command.split("\t")[-1] for command in commands if "\tstop\t" in f"\t{command}\t"]
    assert result["status"] == "failed"
    assert result["error_code"] == "service_state_unavailable"
    assert result["stopped"] == []
    assert stops == ["web"]


def test_maintenance_reconciles_abandoned_executing_operation_as_uncertain(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)
    setup = """
$record = [ordered]@{
  schema_version=1; operation_id=$OperationId; action=$Action; target=$RequestedTarget;
  candidate_id=(Split-Path -Leaf $CandidateRoot); reason_hash=(Get-Sha256Text -Text $Reason);
  status='executing'; ok=$false; audit_id='60000000-0000-4000-8000-000000000054';
  started_at=[DateTimeOffset]::UtcNow.AddMinutes(-10).ToString('o');
  stopped=@('web'); started=@(); remaining=@('api','gw','redis','postgres');
  recovery_hint=''; error_code=''; services=@()
}
Write-JsonAtomic -Path $OperationPath -Value $record
"""

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000054",
        scenario=_restricted_scenario(setup),
    )

    assert result["status"] == "uncertain", result
    assert result["error_code"] == "operation_result_uncertain"
    assert result["stopped"] == ["web"]
    assert not any("\tstop\t" in f"\t{command}\t" for command in commands)
    audit_text = (layout["audit"] / "remote-maintenance.jsonl").read_text(encoding="utf-8")
    assert audit_text.count('"result":"uncertain"') == 1


def test_maintenance_terminal_replay_returns_correlated_result_without_docker(
    tmp_path: Path,
) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)
    audit_id = "10000000-0000-4000-8000-000000000061"
    setup = f"""
$script:auditId = '{audit_id}'
Write-TargetAudit -Event 'lifecycle_completed' -Result 'succeeded'
$record = [ordered]@{{
  schema_version=1; operation_id=$OperationId; action=$Action; target=$RequestedTarget;
  candidate_id=(Split-Path -Leaf $CandidateRoot);
  reason_hash=(Get-Sha256Text -Text $Reason); status='succeeded'; ok=$true;
  audit_id=$auditId; started_at=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o');
  completed_at=[DateTimeOffset]::UtcNow.ToString('o'); stopped=@('web'); started=@();
  remaining=@(); recovery_hint=''; error_code=''; services=@()
}}
Write-JsonAtomic -Path $OperationPath -Value $record
"""

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000061",
        scenario=_restricted_scenario(setup),
    )

    assert result["status"] == "succeeded"
    assert result["audit_id"] == audit_id
    assert result["stopped"] == ["web"]
    assert commands == []
    assert "SECRET_FIXTURE" not in json.dumps(result)
    assert "approved test reason" not in json.dumps(result)


def _audit_chain_text(record_count: int) -> str:
    previous_hash = "0" * 64
    lines: list[str] = []
    for index in range(record_count):
        payload = {
            "schema_version": 1,
            "operation_id": f"00000000-0000-4000-8000-{index + 700:012d}",
            "audit_id": f"10000000-0000-4000-8000-{index + 700:012d}",
            "event": "lifecycle_completed",
            "result": "succeeded",
            "previous_hash": previous_hash,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        previous_hash = hashlib.sha256(encoded).hexdigest()
        lines.append(
            json.dumps(
                {**payload, "record_hash": previous_hash},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("scenario", "audit_text", "error_code"),
    (
        (
            "$script:AuditMaxFileBytes = 128",
            "x" * 129,
            "audit_file_limit_exceeded",
        ),
        (
            "$script:AuditMaxFileBytes = 1024; $script:AuditMaxLineBytes = 64",
            "x" * 65,
            "audit_line_limit_exceeded",
        ),
        (
            "$script:AuditMaxRecords = 1",
            _audit_chain_text(2),
            "audit_record_limit_exceeded",
        ),
    ),
)
def test_target_audit_resource_budgets_fail_closed_before_lifecycle_mutation(
    tmp_path: Path, scenario: str, audit_text: str, error_code: str
) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)
    audit_path = layout["audit"] / "remote-maintenance.jsonl"
    audit_path.write_text(audit_text, encoding="utf-8")

    result, commands = _run_remote_script(
        tmp_path,
        layout,
        action="StopApp",
        operation_id="00000000-0000-4000-8000-000000000062",
        scenario=_restricted_scenario(scenario),
    )

    assert result["status"] == "rejected"
    assert result["error_code"] == error_code
    assert not any("\tstop\t" in f"\t{command}\t" for command in commands)
    assert audit_path.read_text(encoding="utf-8") == audit_text


def test_target_audit_cache_rejects_same_size_same_timestamp_content_drift(tmp_path: Path) -> None:
    layout = _remote_layout(tmp_path)
    _prepare_restricted_layout(layout)
    audit_path = layout["audit"] / "remote-maintenance.jsonl"
    original = _audit_chain_text(1)
    audit_path.write_text(original, encoding="utf-8")
    marker = tmp_path / "audit-cache-rejected.txt"
    scenario = _restricted_scenario(
        f"""
$originalBytes = [IO.File]::ReadAllBytes($AuditPath)
$originalTime = (Get-Item -LiteralPath $AuditPath -Force).LastWriteTimeUtc
[void](Get-TargetAuditSnapshot -ForceRefresh)
$changed = [Text.Encoding]::UTF8.GetString($originalBytes).Replace(
  '"schema_version":1', '"schema_version":2'
)
$utf8 = New-Object Text.UTF8Encoding($false)
try {{
  [IO.File]::WriteAllText($AuditPath, $changed, $utf8)
  [IO.File]::SetLastWriteTimeUtc($AuditPath, $originalTime)
  try {{
    [void](Get-TargetAuditSnapshot)
    throw 'audit_cache_accepted_changed_content'
  }}
  catch {{
    if ([string]$_.Exception.Message -ne 'audit_chain_invalid') {{ throw }}
    [IO.File]::WriteAllText({_ps_literal(marker)}, 'rejected', $utf8)
  }}
}}
finally {{
  [IO.File]::WriteAllBytes($AuditPath, $originalBytes)
  [IO.File]::SetLastWriteTimeUtc($AuditPath, $originalTime)
  $script:TargetAuditSnapshot = $null
}}
"""
    )

    result, _commands = _run_remote_script(
        tmp_path,
        layout,
        action="Status",
        operation_id="00000000-0000-4000-8000-000000000064",
        scenario=scenario,
    )

    assert result["status"] == "observed"
    assert marker.read_text(encoding="utf-8") == "rejected"
    assert audit_path.read_text(encoding="utf-8") == original


def test_operator_audit_file_budget_rejects_append_without_modifying_chain(tmp_path: Path) -> None:
    audit = tmp_path / "operator-audit-budget"
    _set_restricted_directory(audit, audit_mutex=True)
    audit_path = audit / "remote-maintenance.jsonl"
    original = "x" * 129
    audit_path.write_text(original, encoding="utf-8")
    function_source = _read(MAINTENANCE_SCRIPT).split("if ($Target -notmatch", 1)[0]
    invocation = f"""
$script:AuditMaxFileBytes = 128
$result = [pscustomobject]@{{
  operation_id='00000000-0000-4000-8000-000000000063';
  audit_id='10000000-0000-4000-8000-000000000063'; status='succeeded';
  identity=[pscustomobject]@{{user='fixture-user';computer='fixture-host'}}
}}
Write-OperatorAudit -Result $result -RequestedAction 'StopApp' `
  -RequestedTarget 'fixture@100.64.0.20' -AuditDirectory {_ps_literal(audit)}
"""
    script_path = tmp_path / "operator-audit-budget.ps1"
    script_path.write_text(function_source + invocation, encoding="utf-8")

    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", script_path],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_powershell_env(),
    )

    assert completed.returncode != 0
    assert "operator_audit_file_limit_exceeded" in completed.stderr
    assert audit_path.read_text(encoding="utf-8") == original


def _assert_jsonl_hash_chain(path: Path, expected_lines: int) -> None:
    previous_hash = "0" * 64
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(records) == expected_lines
    for record in records:
        assert record["previous_hash"] == previous_hash
        payload = {key: value for key, value in record.items() if key != "record_hash"}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        assert record["record_hash"] == hashlib.sha256(encoded).hexdigest()
        previous_hash = record["record_hash"]


def test_operator_audit_concurrent_appends_preserve_hash_chain(tmp_path: Path) -> None:
    audit = tmp_path / "operator-audit"
    _set_restricted_directory(audit, audit_mutex=True)
    function_source = _read(MAINTENANCE_SCRIPT).split("if ($Target -notmatch", 1)[0]
    processes: list[subprocess.Popen[str]] = []
    for index in range(8):
        operation_id = f"00000000-0000-4000-8000-{index + 100:012d}"
        audit_id = f"10000000-0000-4000-8000-{index + 100:012d}"
        invocation = f"""
$result = [pscustomobject]@{{
  operation_id='{operation_id}'; audit_id='{audit_id}'; status='succeeded';
  identity=[pscustomobject]@{{user='fixture-user';computer='fixture-host'}}
}}
Write-OperatorAudit -Result $result -RequestedAction 'StopApp' `
  -RequestedTarget 'fixture@100.64.0.20' -AuditDirectory {_ps_literal(audit)}
"""
        script_path = tmp_path / f"operator-audit-{index}.ps1"
        script_path.write_text(function_source + invocation, encoding="utf-8")
        processes.append(
            subprocess.Popen(
                [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=_powershell_env(),
            )
        )
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stdout + stderr

    _assert_jsonl_hash_chain(audit / "remote-maintenance.jsonl", expected_lines=8)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_remote_operation_scripts_parse_in_both_powershell_editions(
    executable: str, tmp_path: Path
) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        pytest.skip(f"{executable} is unavailable")

    parse_targets: list[Path] = []
    for path in (DEBUG_SCRIPT, HOTFIX_SCRIPT, MAINTENANCE_SCRIPT, MAINTENANCE_PREPARE_SCRIPT):
        parse_targets.append(path)
        templates = re.findall(r"@'\r?\n(.*?)\r?\n'@", _read(path), flags=re.DOTALL)
        for index, template in enumerate(templates):
            if '$ErrorActionPreference = "Stop"' not in template:
                continue
            rendered = re.sub(r"__[A-Z0-9_]+__", "'parser-placeholder'", template)
            template_path = tmp_path / f"{path.stem}-template-{index}.ps1"
            template_path.write_text(rendered, encoding="utf-8")
            parse_targets.append(template_path)

    for path in parse_targets:
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


def test_hotfix_dry_run_and_api_migration_gate_precede_mutating_steps() -> None:
    script = _read(HOTFIX_SCRIPT)

    dry_run = script.index("if ($DryRun)")
    tests = script.index("Invoke-ServiceTests", dry_run)
    build = script.index("New-HotfixArtifact", dry_run)
    transfer = script.index("Send-HotfixArtifact", dry_run)

    assert dry_run < tests < build < transfer
    assert (
        '"diff", "--name-only", "$($preflight.source_commit)..$commit", "--", "alembic"' in script
    )
    assert "API hotfix contains database migration changes" in script
    assert '"-EncodedCommand", $encoded' in script
    assert '"-o", "BatchMode=yes"' in script
    assert '"-o", "StrictHostKeyChecking=yes"' in script


def test_remote_debug_guide_keeps_production_boundary_explicit() -> None:
    guide = _read(GUIDE)

    assert ".\\tools\\remote_debug.ps1 Start" in guide
    assert ".\\tools\\remote_debug.ps1 Stop" in guide
    assert ".\\tools\\remote_debug.ps1 Health" in guide
    assert ".\\tools\\remote_hotfix_deploy.ps1 -Service gw -DryRun" in guide
    assert ".\\tools\\remote_maintenance.ps1 Status" in guide
    assert ".\\tools\\remote_maintenance.ps1 StopApp" in guide
    assert ".\\tools\\remote_maintenance.ps1 StartApp" in guide
    assert ".\\tools\\remote_maintenance.ps1 RestartApp" in guide
    assert ".\\tools\\remote_maintenance_prepare.ps1 -Approved" in guide
    assert "-Approved" in guide
    assert "snapshot_equal" in guide
    assert "127.0.0.1:18080" in guide
    assert "不开放目标机应用端口" in guide
    assert "BLOCKED" in guide
