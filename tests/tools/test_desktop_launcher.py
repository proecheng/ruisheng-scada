from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "tools" / "start_ruisheng_local.ps1"
INSTALLER = ROOT / "tools" / "install_ruisheng_desktop_launcher.ps1"
DOC = ROOT / "docs" / "REMOTE_DEBUG.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell.exe")
    if executable is None:
        pytest.skip("PowerShell is not available")
    return executable


def _function_loader(name: str, path: Path = LAUNCHER) -> str:
    encoded_path = base64.b64encode(str(path).encode()).decode()
    return f"""
$launcherPath = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_path}'))
$source = Get-Content -LiteralPath $launcherPath -Raw
if ([IO.Path]::GetFileName($launcherPath) -eq 'remote_maintenance.ps1') {{
  $source = [regex]::Match($source, '(?s)\\$remoteTemplate = @''\\r?\\n(.*?)\\r?\\n''@').Groups[1].Value
}}
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) {{ throw 'launcher_parse_failed' }}
$functionAst = $ast.Find({{ param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq '{name}'
}}, $true)
if ($null -eq $functionAst) {{ throw 'function_not_found' }}
Invoke-Expression $functionAst.Extent.Text
"""


def test_launcher_uses_only_the_protected_active_release() -> None:
    script = _read(LAUNCHER)
    assert '$CandidateSitesRoot = "C:\\Ruisheng\\candidates"' in script
    assert "^site(?:-[a-z0-9][a-z0-9._-]{0,57})?$" in script
    assert "active-release.json" in script
    assert "active_site_root_ambiguous" in script
    assert "Assert-RestrictedDirectory" in script
    assert "Assert-RestrictedFile" in script
    assert "Assert-TrustedContainerRoot" in script
    assert "trusted_root_unapproved_writer" in script
    assert "active_release_candidate_outside_root" in script
    assert "Assert-ActiveReleaseUnchanged" in script
    assert "ConvertFrom-JsonPreservingDateStrings" in script
    assert "Get-ChildItem -LiteralPath $CandidateSitesRoot -Directory -Force" in script
    assert "$siteMatches = New-Object System.Collections.ArrayList" in script
    assert "$matches = New-Object System.Collections.ArrayList" not in script


def test_manifest_compose_and_image_identity_are_closed() -> None:
    script = _read(LAUNCHER)
    for token in (
        "Assert-CandidateManifest",
        "manifest_authenticity_invalid",
        '"SIGNED"',
        '"openssh-sshsig"',
        '"ruisheng-release"',
        "Assert-ComposePolicy",
        "compose_service_unexpected",
        'pull_policy -cne "never"',
        "non_loopback_port",
        "Assert-LoadedImageIdentity",
        "loaded_image_identity_mismatch",
        "Assert-ServiceImageIdentity",
        "container_image_identity_mismatch",
        "Assert-RunningPortBindings",
        "non_loopback_runtime_port",
        "published_port_set_invalid",
        "compose_network_mode_invalid",
        "Assert-HostWebReady",
        "host_web_health_failed",
        "Assert-NoUnexpectedProjectContainers",
        "Resolve-EffectiveImages",
        "Resolve-HotfixImageIdentity",
        '"C:\\Ruisheng\\hotfix"',
        "hotfix_archive_identity_mismatch",
        "State.ExitCode -notin @(128, 255)",
    ):
        assert token in script
    assert '$PolicyServices = @("postgres", "redis", "migrate", "gw", "api", "web")' in script
    assert '$PersistentServices = @("postgres", "redis", "gw", "api", "web")' in script
    assert '@("127.0.0.1", "::1")' in script


def test_mutation_is_guarded_by_dual_leases_and_drift_checks() -> None:
    script = _read(LAUNCHER)
    shared = 'Acquire-LeasedLock -Path $SharedLockPath -Name "shared-maintenance"'
    legacy = 'Acquire-LeasedLock -Path $LegacyLockPath -Name "legacy-hotfix"'
    first_up = '"up", "-d", "--no-build", "postgres", "redis"'
    assert script.index(shared) < script.index(legacy) < script.index(first_up)
    assert 'action = "StartApp"' in script
    assert "ConvertTo-ValidatedLockRecord" in script
    assert script.count("ConvertFrom-JsonPreservingDateStrings -Json") >= 5
    assert "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}" in script
    assert "Renew-Locks" in script
    assert "Assert-LocksOwned" in script
    assert "[ValidateRange(900, 3600)]" in script
    assert "docker_mutation_timeout_uncertain" in script
    assert "$PreserveLocksOnExit" in script
    assert "Assert-NoConfigurationDrift" in script
    assert "Initialize-VerifiedInputs" in script
    assert "Assert-VerifiedInputIntegrity" in script
    assert "Open-VerifiedInputGuards" in script
    assert script.index("Initialize-VerifiedInputs") < script.index(first_up)
    assert script.index(legacy) < script.index('$auditResult = "already_ready"')


def test_start_order_timeout_and_idempotent_fast_path_are_explicit() -> None:
    script = _read(LAUNCHER)
    dependencies = '"up", "-d", "--no-build", "postgres", "redis"'
    migration = '"up", "--no-build", "--no-deps", "--force-recreate", "--abort-on-container-exit"'
    applications = '"up", "-d", "--no-build", "gw", "api", "web"'
    assert script.index(dependencies) < script.index(migration) < script.index(applications)
    assert "Wait-DependenciesHealthy" in script
    assert "Wait-AllHealthy" in script
    assert "service_health_timeout" in script
    assert "docker_desktop_timeout" in script
    assert '$auditResult = "already_ready"' in script
    fast_path = re.search(
        r"if \(@\(\$health \| Where-Object \{ -not \$_\.ready \}\)\.Count -eq 0\) "
        r'\{\s*\$auditResult = "already_ready"\s*\}\s*elseif',
        script,
        re.DOTALL,
    )
    assert fast_path is not None
    assert "Invoke-DockerText" not in fast_path.group(0)
    assert "elseif ($RetainedUpgradeContainersPresent)" in script
    assert "-ExpectedHead ([string]$activeRelease.Manifest.alembic_head)" in script


def test_launcher_never_uses_remote_or_destructive_operations() -> None:
    script = _read(LAUNCHER).casefold()
    forbidden = (
        "ssh.exe",
        "invoke-command",
        "new-pssession",
        '"down"',
        '"pull"',
        '"volume", "rm"',
        '"system", "prune"',
        "unregister-scheduledtask",
        "register-scheduledtask",
        "stop-computer",
        "restart-computer",
    )
    for token in forbidden:
        assert token not in script


def test_launcher_supports_headless_acceptance_and_safe_browser_url() -> None:
    script = _read(LAUNCHER)
    assert "[switch]$NoBrowser" in script
    assert "[switch]$NoUi" in script
    assert 'Start-Process -FilePath "http://127.0.0.1/"' in script
    assert "url=http://127.0.0.1/" in script
    assert "Write-LauncherAudit" in script
    assert '$AuditDirectory = "C:\\Ruisheng\\launcher-audit"' in script
    assert "desktop-launcher.jsonl" in script
    assert ".desktop-launcher-audit.lock" in script
    assert "previous_hash" in script
    assert "record_hash" in script
    assert "Initialize-DockerEnvironment" in script
    assert "Assert-LocalDockerContext" in script
    assert '"--host", $DockerEndpoint' in script
    assert "docker_context_not_local" in script
    assert '$DockerEndpoint = "npipe:////./pipe/dockerDesktopLinuxEngine"' in script
    assert '"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"' in script


def test_installer_protects_payload_and_creates_non_elevated_shortcut() -> None:
    script = _read(INSTALLER)
    assert '$InstallRoot = "C:\\Program Files\\Ruisheng\\Launcher"' in script
    assert '$LauncherUser = "lenovo"' in script
    assert '"S-1-5-32-544"' in script
    assert '"S-1-5-18"' in script
    assert "ReadAndExecute" in script
    assert "SetAccessRuleProtection($true, $false)" in script
    assert "launcher_acl_unapproved_writer" in script
    assert "launcher_install_root_not_approved" in script
    assert "launcher_audit_root_not_approved" in script
    assert "Set-RuntimeAuditDirectoryAcl" in script
    assert "Set-RuntimeAuditFileAcl" in script
    assert "shortcut_name_invalid" in script
    assert "launcher_payload_linked" in script
    assert "Install-FileAtomic" in script
    assert "shortcut_target_linked" in script
    assert "desktop_path_required_for_different_user" in script
    assert "Assert-ProtectedSourceFile" in script
    assert "launcher_install_in_progress" in script
    assert "New-RuishengIcon" in script
    assert "WScript.Shell" in script
    assert "CreateShortcut" in script
    assert "ExecutionPolicy Bypass" in script
    assert "requires_elevation_to_run = $false" in script
    assert "startup_task_changed = [bool]$EnableSerialRecovery" in script
    assert "[switch]$EnableSerialRecovery" in script
    assert re.search(r"if \(\$EnableSerialRecovery\) \{\s+Register-SerialRecoveryTask", script)
    lowered = script.casefold()
    assert "runas" not in lowered
    assert "set-scheduledtask" not in lowered


def test_documentation_explains_local_launcher_boundaries() -> None:
    doc = _read(DOC)
    for token in (
        "桌面一键启动",
        "润盛监控系统",
        "Docker Desktop",
        "http://127.0.0.1/",
        "-NoBrowser -NoUi",
        "不会修改开机任务",
        "不代表生产放行",
    ):
        assert token in doc


def test_compose_policy_rejects_host_network_mode(tmp_path: Path) -> None:
    services: dict[str, object] = {}
    images: dict[str, object] = {}
    for service in ("postgres", "redis", "migrate", "gw", "api", "web"):
        component = "api" if service == "migrate" else service
        model: dict[str, object] = {
            "pull_policy": "never",
            "image": f"fixture/{component}:candidate",
            "ports": [],
        }
        if service != "migrate":
            model["container_name"] = f"ruisheng-{service}"
        if service == "gw":
            model["ports"] = [
                {"target": 5020, "published": 5020, "host_ip": "127.0.0.1"},
                {"target": 9090, "published": 9090, "host_ip": "127.0.0.1"},
            ]
        if service == "web":
            model["ports"] = [{"target": 80, "published": 80, "host_ip": "127.0.0.1"}]
            model["network_mode"] = "host"
        services[service] = model
        if service != "migrate":
            images[service] = {"candidate_reference": f"fixture/{service}:candidate"}
    encoded_model = base64.b64encode(
        json.dumps({"services": services}, separators=(",", ":")).encode()
    ).decode()
    encoded_images = base64.b64encode(json.dumps(images, separators=(",", ":")).encode()).decode()
    test_script = tmp_path / "compose-policy.ps1"
    test_script.write_text(
        _function_loader("Test-ExactJsonObjectKeys")
        + _function_loader("Assert-ComposePolicy")
        + f"""
$PolicyServices = @('postgres', 'redis', 'migrate', 'gw', 'api', 'web')
$PersistentServices = @('postgres', 'redis', 'gw', 'api', 'web')
$ContainerNames = @{{postgres='ruisheng-postgres';redis='ruisheng-redis';gw='ruisheng-gw';api='ruisheng-api';web='ruisheng-web'}}
$model = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_model}')) | ConvertFrom-Json
$imageObject = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_images}')) | ConvertFrom-Json
$images = @{{}}
foreach ($property in $imageObject.PSObject.Properties) {{ $images[$property.Name] = $property.Value }}
try {{
  Assert-ComposePolicy -Model $model -Images $images
  exit 2
}}
catch {{
  if ([string]$_.Exception.Message -eq 'compose_network_mode_invalid') {{ exit 0 }}
  Write-Error $_
  exit 3
}}
""",
        encoding="ascii",
    )
    result = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", test_script],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_binding_check_precedes_first_compose_mutation() -> None:
    script = _read(LAUNCHER)
    assert "$bindings.PSObject.Properties | ForEach-Object { $_.Name }" in script
    assert "$actualNames = @($bindings.PSObject.Properties.Name)" not in script
    main = script[script.index("$activeRelease = $null") :]
    assert main.index("Assert-RunningPortBindings") < main.index(
        '"up", "-d", "--no-build", "postgres", "redis"'
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 is Windows-only")
def test_windows_powershell51_parses_scripts_and_exposes_acl_methods(tmp_path: Path) -> None:
    powershell = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    if not powershell.is_file():
        pytest.skip("Windows PowerShell 5.1 is not available")
    encoded_files = ",".join(
        "'" + base64.b64encode(str(path).encode()).decode() + "'" for path in (LAUNCHER, INSTALLER)
    )
    script = tmp_path / "ps51-contract.ps1"
    script.write_text(
        f"""
$files = @({encoded_files}) | ForEach-Object {{
  [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_))
}}
foreach ($file in $files) {{
  $tokens = $null; $errors = $null
  [void][Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
  if ($errors.Count -ne 0) {{ exit 2 }}
}}
if ($null -eq [IO.Directory].GetMethod('SetAccessControl', [type[]]@([string], [Security.AccessControl.DirectorySecurity]))) {{ exit 3 }}
if ($null -eq [IO.File].GetMethod('SetAccessControl', [type[]]@([string], [Security.AccessControl.FileSecurity]))) {{ exit 4 }}
""",
        encoding="ascii",
    )
    result = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def guard_shell(request) -> str:
    executable = shutil.which(request.param)
    if executable is None:
        pytest.skip(f"{request.param} unavailable")
    return executable


def _run_guard_ps(shell: str, tmp_path: Path, source: str) -> dict:
    path = tmp_path / "guard-test.ps1"
    path.write_text(
        "$ErrorActionPreference='Stop'\n[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)\n"
        + source,
        encoding="utf-8-sig",
    )
    env = os.environ.copy()
    if Path(shell).name.lower() == "powershell.exe":
        env["PSModulePath"] = str(
            Path(env.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/Modules"
        )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


_RETAINED_FIXTURE = r"""
$SiteRoot = $site
$PolicyServices = @('postgres','redis','migrate','gw','api','web')
$PersistentServices = @('postgres','redis','gw','api','web')
$ContainerNames = @{}
$containers = @()
foreach ($service in $PersistentServices) {
  $ContainerNames[$service] = "ruisheng-$service"
  $containers += @{Id=([string]($containers.Count+1)).PadLeft(64,'0');
    Name="/ruisheng-$service"; Config=@{Labels=@{'com.docker.compose.service'=$service}};
    State=@{Status='running'}}
}
$containers += @{Id=('6'*64);Name='/ruisheng-prod-migrate-1';
  Config=@{Labels=@{'com.docker.compose.service'='migrate'}};State=@{Status='exited';ExitCode=255}}
$journalPath = Join-Path $stateDirectory "full-upgrade-$operation.json"
$journal = @{schema_version=1;operation_id=$operation;action='full-upgrade';
  status='committed';upgrade_kind='bounded_0012_0013';previous_release=@{site_root=$site};
  candidate=@{candidate_id='deploy-20260911.1'};
  migration=@{kind='bounded_0012_0013';phase='completed';target_images=@{api=('sha256:'+('a'*64))};
    container_name="ruisheng-migrate-$operation";container_id=('7'*64)}}
function Write-Journal {
  [IO.File]::WriteAllText($journalPath,($journal|ConvertTo-Json -Depth 10 -Compress),(New-Object Text.UTF8Encoding($false)))
}
Write-Journal
function New-RetainedContainer { param([bool]$Proof,[int]$Index)
  $name = if ($Proof) { "ruisheng-migration-proof-$operation-$(([string]$Index).PadLeft(32,'0'))" }
    else { "ruisheng-migrate-$operation" }
  $service = if ($Proof) { 'api' } else { 'migrate' }
  $image = 'sha256:'+('a'*64)
  return @{Id=([string]$Index).PadLeft(64,'0');Name="/$name";Image=$image;
    Config=@{Image=$(if ($Proof) {$image} else {'ruisheng-candidate/api:deploy-20260911.1'});
      Labels=@{'com.docker.compose.service'=$service;'com.docker.compose.project'='ruisheng-prod';
        'com.ruisheng.upgrade.operation'=$operation;'com.docker.compose.oneoff'=$(if ($Proof) { $null } else {'True'})}};
    State=@{Status='exited';Running=$false;Paused=$false;Restarting=$false;OOMKilled=$false;
      Dead=$false;Pid=0;ExitCode=0;Error=''};RestartCount=0;Mounts=@();
    HostConfig=@{RestartPolicy=@{Name='no';MaximumRetryCount=0};NetworkMode=$(if ($Proof) {'none'} else {'ruisheng-prod_default'});
      ReadonlyRootfs=$Proof;Privileged=$false;PublishAllPorts=$false;PortBindings=@{};
      Binds=@();Mounts=@();VolumesFrom=@();Devices=@();DeviceRequests=@();CapAdd=@();
      CapDrop=@('ALL');SecurityOpt=@('no-new-privileges');
      PidMode='';IpcMode='private';UTSMode='';AutoRemove=$false};
    NetworkSettings=@{Ports=@{};Networks=@{none=@{}}}}
}
$migrator = New-RetainedContainer $false 7
$migrator.Id = '7'*64
$containers += $migrator
foreach ($index in 8..11) { $containers += (New-RetainedContainer $true $index) }
$script:inspectIds = @()
function Invoke-DockerText { param($Arguments,$TimeoutSeconds)
  if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 30) { throw 'invalid_timeout' }
  if ($Arguments[0] -ceq 'ps') {
    $full = $Arguments -contains '--no-trunc'
    return (($containers | ForEach-Object {
      $row = "$($_.Name.TrimStart('/'))|$($_.Config.Labels.'com.docker.compose.service')|$($_.State.Status)"
      if ($full) { "$($_.Id)|$row" } else { $row }
    }) -join "`n")
  }
  if ($Arguments[0] -ceq 'container' -and $Arguments[1] -ceq 'inspect') {
    $id = $Arguments[-1]
    $script:inspectIds += $id
    if ($id -cnotmatch '^[0-9a-f]{64}$') { throw 'mutable_inspect_identity' }
    return (ConvertTo-Json -InputObject @($containers | Where-Object { $_.Id -ceq $id }) -Depth 12 -Compress)
  }
  throw 'unexpected_docker_call'
}
function Observe-Project {
  try { Assert-NoUnexpectedProjectContainers -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(60)); 'allowed' }
  catch { $_.Exception.Message }
}
"""


def test_launcher_accepts_completed_upgrade_field_container_set(
    guard_shell: str, tmp_path: Path
) -> None:
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        _retained_loader()
        + _GUARD_FIXTURE
        + _RETAINED_FIXTURE
        + "@{result=(Observe-Project); inspected=$script:inspectIds} | ConvertTo-Json -Compress",
    )
    assert result["result"] == "allowed"
    assert len(result["inspected"]) == 5
    assert all(re.fullmatch("[0-9a-f]{64}", entry) for entry in result["inspected"])


def _retained_loader() -> str:
    return "".join(
        _function_loader(name)
        for name in (
            "Get-AllowedSids",
            "Assert-RestrictedDirectory",
            "Assert-RestrictedFile",
            "ConvertFrom-JsonPreservingDateStrings",
            "Get-RemainingTimeoutSeconds",
            "Get-RetainedUpgradeJournal",
            "Assert-RetainedUpgradeContainer",
            "Assert-NoUnexpectedProjectContainers",
        )
    )


def test_retained_upgrade_refuses_untrusted_identity_state_and_configuration(
    guard_shell: str, tmp_path: Path
) -> None:
    mutations = {
        "operation_label": "$containers[7].Config.Labels.'com.ruisheng.upgrade.operation'='wrong'",
        "operation_name": "$containers[7].Name=$containers[7].Name.Replace($operation,'------------------------------------')",
        "image": "$containers[7].Image='sha256:'+('b'*64)",
        "image_reference": "$containers[7].Config.Image='unapproved:latest'",
        "running": "$containers[7].State.Running=$true",
        "restarting": "$containers[7].State.Restarting=$true",
        "paused": "$containers[7].State.Paused=$true",
        "exit_code": "$containers[7].State.ExitCode=1",
        "pid": "$containers[7].State.Pid=10",
        "restart_policy": "$containers[7].HostConfig.RestartPolicy.Name='always'",
        "restart_count": "$containers[7].RestartCount=1",
        "network": "$containers[7].HostConfig.NetworkMode='host'",
        "extra_network": "$containers[7].NetworkSettings.Networks=[pscustomobject]@{none=@{};extra=@{}}",
        "read_only": "$containers[7].HostConfig.ReadonlyRootfs=$false",
        "privileged": "$containers[7].HostConfig.Privileged=$true",
        "devices": "$containers[7].HostConfig.Devices=@(@{PathOnHost='/dev/sda'})",
        "mounts": "$containers[7].Mounts=@(@{Destination='/host';RW=$true})",
        "bindings": "$containers[7].HostConfig.PortBindings=[pscustomobject]@{'80/tcp'=@(@{HostPort='80'})}",
        "capabilities": "$containers[7].HostConfig.CapDrop=@()",
        "security": "$containers[7].HostConfig.SecurityOpt=@()",
        "new_migrator_id": "$journal.migration.container_id='8'*64",
        "new_migrator_name": "$journal.migration.container_name='different'",
        "new_migrator_oneoff": "$containers[6].Config.Labels.'com.docker.compose.oneoff'='False'",
        "new_migrator_failed": "$containers[6].State.ExitCode=255",
        "journal_site": "$journal.previous_release.site_root='C:\\elsewhere'",
        "journal_status": "$journal.status='recovery_failed'",
        "journal_phase": "$journal.migration.phase='application_start_intent'",
        "journal_operation": "$journal.operation_id='wrong'",
        "journal_kind": "$journal.upgrade_kind='same_head'",
        "journal_schema_type": "$journal.schema_version='1'",
        "pid_type": "$containers[7].State.Pid=$false",
        "unknown_service": "$containers[0].Config.Labels.'com.docker.compose.service'='extra'",
        "duplicate_api": "$containers += $containers[3]",
        "duplicate_migrator": "$extra=$containers[5]|ConvertTo-Json -Depth 12|ConvertFrom-Json;$extra.Id='f'*64;$extra.Name='/extra-migrate';$containers += $extra",
    }
    cases = json.dumps(mutations, separators=(",", ":"))
    encoded = base64.b64encode(cases.encode()).decode()
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        _retained_loader()
        + _GUARD_FIXTURE
        + _RETAINED_FIXTURE
        + f"""
$baselineContainers = ConvertTo-Json -InputObject $containers -Depth 12
$baselineJournal = $journal | ConvertTo-Json -Depth 12
$cases = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')) | ConvertFrom-Json
$results = @{{}}
foreach ($case in $cases.PSObject.Properties) {{
  $containers = $baselineContainers | ConvertFrom-Json
  $journal = $baselineJournal | ConvertFrom-Json
  Invoke-Expression $case.Value
  Write-Journal
  $results[$case.Name] = Observe-Project
}}
$containers = $baselineContainers | ConvertFrom-Json
$journal = $baselineJournal | ConvertFrom-Json
Write-Journal
[IO.File]::WriteAllText($journalPath,'broken json')
$results.corrupt_journal = Observe-Project
Remove-Item -LiteralPath $journalPath
$results.missing_journal = Observe-Project
Write-Journal
$originalAcl = Get-Acl -LiteralPath $journalPath
$badAcl = Get-Acl -LiteralPath $journalPath
$badAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
  (New-Object Security.Principal.SecurityIdentifier('S-1-1-0')), 'Write', 'Allow')))
Set-Acl -LiteralPath $journalPath -AclObject $badAcl
$results.untrusted_journal_acl = Observe-Project
Set-Acl -LiteralPath $journalPath -AclObject $originalAcl
try {{ Assert-NoUnexpectedProjectContainers -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(-1)) }}
catch {{ $results.expired = $_.Exception.Message }}
$results | ConvertTo-Json -Compress
""",
    )
    assert result.pop("expired") == "service_health_timeout"
    assert all(value == "unexpected_project_container" for value in result.values()), result


def test_retained_start_uses_fixed_ids_dependency_order_and_closed_guards(
    guard_shell: str, tmp_path: Path
) -> None:
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        _retained_loader()
        + "".join(
            _function_loader(name)
            for name in (
                "Test-RetainedArrayEqual",
                "Get-RetainedBindSource",
                "Get-RetainedStartConfigurations",
                "Assert-RetainedStartConfiguration",
                "Get-RetainedStartService",
                "Assert-RetainedStartGuards",
                "Invoke-RetainedUpgradeStart",
            )
        )
        + _GUARD_FIXTURE
        + _RETAINED_FIXTURE
        + r"""
$ExpectedImages=@{}
$ComposeModel=@{services=@{};volumes=@{pgdata=@{name='ruisheng-prod_ruisheng-pgdata'}};
  networks=@{default=@{name='ruisheng-prod_default'}}}
foreach ($container in $containers[0..4]) {
  $service=$container.Config.Labels.'com.docker.compose.service'
  $container.Image='sha256:'+('b'*64)
  $container.Config.Image="ruisheng-candidate/${service}:deploy-20260911.1"
  $container.Config.Labels.'com.docker.compose.project'='ruisheng-prod'
  $container.Config.Entrypoint=@('/docker-entrypoint.sh')
  $container.Config.Cmd=@('default-command')
  $container.Config.User='';$container.Config.WorkingDir='/app'
  $container.Config.Env=@('PATH=/usr/bin','FROM_IMAGE=default','OVERRIDE=chosen','EMPTY=')
  $container.HostConfig=@{NetworkMode='ruisheng-prod_default';Privileged=$false;ReadonlyRootfs=$false;
    AutoRemove=$false;PublishAllPorts=$false;PidMode='';UTSMode='';IpcMode='private';Tmpfs=$null}
  $container.NetworkSettings=@{Networks=@{'ruisheng-prod_default'=@{}}}
  $container.Mounts=@()
  $serviceModel=@{environment=@{OVERRIDE='chosen';EMPTY=''};networks=@{default=@{}};volumes=@()}
  if ($service -ceq 'postgres') {
    $container.Mounts=@(@{Type='volume';Name='ruisheng-prod_ruisheng-pgdata';Destination='/data';RW=$true})
    $serviceModel.volumes=@(@{type='volume';source='pgdata';target='/data';read_only=$false})
  }
  if ($service -ceq 'web') {
    $container.Mounts=@(@{Type='bind';Source='C:\site\acl.conf';Destination='/acl.conf';RW=$false})
    $serviceModel.volumes=@(@{type='bind';source='C:\site\acl.conf';target='/acl.conf';read_only=$true})
    $serviceModel.entrypoint=@('/special-web')
    $container.Config.Entrypoint=@('/special-web');$container.Config.Cmd=@()
  }
  if ($service -ceq 'redis') {
    $serviceModel.command=@('redis-server','--appendonly','yes')
    $container.Config.Cmd=$serviceModel.command
  }
  $ComposeModel.services[$service]=$serviceModel
  $container.State=@{Status='exited';Running=$false;Pid=0;Error='';Paused=$false;
    Restarting=$false;OOMKilled=$false;Dead=$false}
  $ExpectedImages[$service]=@{image_id=$container.Image;candidate_reference=$container.Config.Image}
}
$ComposeModel=$ComposeModel|ConvertTo-Json -Depth 12|ConvertFrom-Json
$baseline=ConvertTo-Json -InputObject $containers -Depth 12
function Invoke-DockerText { param($Arguments,$TimeoutSeconds,[switch]$Mutation)
  if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 30) { throw 'invalid_timeout' }
  if ($Arguments[0] -ceq 'image' -and $Arguments[1] -ceq 'inspect') {
    return ConvertTo-Json -InputObject @(@{Id=('sha256:'+('b'*64));Config=@{
      Env=@('PATH=/usr/bin','FROM_IMAGE=default','OVERRIDE=old');Entrypoint=@('/docker-entrypoint.sh');
      Cmd=@('default-command');User='';WorkingDir='/app'}}) -Depth 6 -Compress
  }
  if ($Arguments[0] -ceq 'container' -and $Arguments[1] -ceq 'inspect' -and -not $Mutation) {
    $identity=$Arguments[-1]
    return ConvertTo-Json -Depth 12 -InputObject @($containers | Where-Object {
      $_.Id -ceq $identity -or $_.Name -ceq "/$identity"
    }) -Compress
  }
  if ($Arguments[0] -ceq 'start' -and $Mutation -and $Arguments.Count -eq 2) {
    if ($Arguments[1] -cnotmatch '^[0-9a-f]{64}$') { throw 'mutable_start_identity' }
    $info=@($containers|Where-Object {$_.Id -ceq $Arguments[1]})[0]
    if ($info.State.Running) { throw 'running_service_restarted' }
    $script:events += 'start:'+$info.Config.Labels.'com.docker.compose.service'
    $info.State.Status='running';$info.State.Running=$true;$info.State.Pid=10
    return $info.Id
  }
  if ($Arguments[0] -ceq 'exec' -and -not $Mutation -and $Arguments[1] -ceq $containers[0].Id -and
      $Arguments[-1] -ceq 'SELECT version_num FROM alembic_version') {
    $script:events += 'schema'
    return $schemaHead
  }
  throw 'unexpected_docker_call'
}
function Assert-LocksOwned { if ($failure -ceq 'lock') {throw 'maintenance_lock_lost'} }
function Renew-Locks { }
function Assert-NoFullUpgradeMaintenance { param($SiteRoot)
  if ($failure -ceq 'maintenance') { throw 'full_upgrade_maintenance_active' }
}
function Assert-NoConfigurationDrift { param($Expected)
  if ($failure -ceq 'drift') { throw 'configuration_drift' }
}
function Resolve-ActiveRelease { param($ResolvedSiteRoot) @{Pointer=@{}} }
function Assert-ActiveReleaseUnchanged { param($Before,$After)
  if ($failure -ceq 'pointer') { throw 'active_release_changed' }
}
function Assert-LoadedImageIdentity { param($Images)
  if ($failure -ceq 'loaded_image') { throw 'loaded_image_identity_mismatch' }
}
function Assert-NoUnexpectedProjectContainers { param($Deadline)
  if ($failure -ceq 'unknown_container') { throw 'unexpected_project_container' }
}
function Assert-RunningPortBindings { param($Deadline)
  if ($failure -ceq 'port') { throw 'non_loopback_runtime_port' }
}
function Write-LauncherAudit { param($Event,$Result) }
function Wait-DependenciesHealthy { param($ExpectedHashes,$OriginalActive,$Deadline)
  if ($null -eq $Deadline) {throw 'deadline_not_shared'}
  if (@($containers[0..1]|Where-Object {-not $_.State.Running}).Count) {throw 'dependencies_not_started'}
  $script:events += 'dependencies_healthy'
}
function Wait-AllHealthy { param($ExpectedHashes,$OriginalActive,$Deadline)
  if (@($containers[0..4]|Where-Object {-not $_.State.Running}).Count) {throw 'services_not_started'}
  $script:events += 'all_healthy'
  if ($failure -ceq 'health') {throw 'service_health_timeout'}
  @(@{ready=$true})
}
$results=@{}
foreach ($scenario in @('stopped','mixed','all_running','desktop_bind','missing','image','paused','pid','manifest','wrong_volume','volume_subpath',
    'wrong_env','extra_env','duplicate_env','command','entrypoint','user','workdir','mount_rw','bind_source',
    'extra_mount','network','privileged','devices','capabilities',
    'schema','lock','maintenance','drift','pointer','loaded_image','unknown_container','port','expired','health')) {
  $containers=$baseline|ConvertFrom-Json
  $failure=$scenario
  $schemaHead='0013_serial_polling_profile'
  $expectedHead=$schemaHead
  $deadline=[DateTimeOffset]::UtcNow.AddSeconds(60)
  $script:events=@()
  switch ($scenario) {
    'mixed' {foreach ($index in @(0,2,4)) {$containers[$index].State.Status='running';$containers[$index].State.Running=$true;$containers[$index].State.Pid=10}}
    'desktop_bind' {$containers[4].Mounts[0].Source='/run/desktop/mnt/host/c/site/acl.conf'}
    'all_running' {foreach ($index in 0..4) {$containers[$index].State.Status='running';$containers[$index].State.Running=$true;$containers[$index].State.Pid=10}}
    'missing' {$containers=$containers|Where-Object {$_.Name -cne '/ruisheng-web'}}
    'image' {$containers[4].Image='sha256:'+('c'*64)}
    'paused' {$containers[4].State.Paused=$true}
    'pid' {$containers[4].State.Pid=42}
    'wrong_volume' {$containers[0].Mounts[0].Name='other-database-with-same-head'}
    'volume_subpath' {$containers[0].HostConfig|Add-Member -NotePropertyName Mounts -NotePropertyValue @(@{Type='volume';Source=$containers[0].Mounts[0].Name;Target=$containers[0].Mounts[0].Destination;VolumeOptions=@{Subpath='other-db'}}) -Force}
    'wrong_env' {$containers[0].Config.Env[1]='FROM_IMAGE=changed'}
    'extra_env' {$containers[0].Config.Env += 'EXTRA=unapproved'}
    'duplicate_env' {$containers[0].Config.Env += 'PATH=/usr/bin'}
    'command' {$containers[0].Config.Cmd=@('different-command')}
    'entrypoint' {$containers[0].Config.Entrypoint=@('/different-entrypoint')}
    'user' {$containers[0].Config.User='unapproved'}
    'workdir' {$containers[0].Config.WorkingDir='/different'}
    'mount_rw' {$containers[4].Mounts[0].RW=$true}
    'bind_source' {$containers[4].Mounts[0].Source='C:\different\acl.conf'}
    'extra_mount' {$containers[0].Mounts += $containers[4].Mounts[0]}
    'network' {$containers[0].HostConfig.NetworkMode='different-network'}
    'privileged' {$containers[0].HostConfig.Privileged=$true}
    'devices' {$containers[0].HostConfig|Add-Member -NotePropertyName Devices -NotePropertyValue @(@{PathOnHost='/dev/sda';PathInContainer='/dev/sda';CgroupPermissions='rwm'})}
    'capabilities' {$containers[0].HostConfig|Add-Member -NotePropertyName CapAdd -NotePropertyValue @('SYS_ADMIN')}
    'manifest' {$expectedHead='0012_alarm_notification_runtime'}
    'schema' {$schemaHead='0012_alarm_notification_runtime'}
    'expired' {$deadline=[DateTimeOffset]::UtcNow.AddSeconds(-1)}
  }
  $beforeRetained=ConvertTo-Json -InputObject @($containers|Where-Object {$_.Name -match 'migrat'}) -Depth 12 -Compress
  try {
    [void](Invoke-RetainedUpgradeStart -ExpectedHashes @{} -OriginalActive @{} -ExpectedHead $expectedHead -Deadline $deadline -ComposeModel $ComposeModel)
    $outcome='allowed'
  } catch {$outcome=$_.Exception.Message}
  $afterRetained=ConvertTo-Json -InputObject @($containers|Where-Object {$_.Name -match 'migrat'}) -Depth 12 -Compress
  $results[$scenario]=@{outcome=$outcome;events=$script:events;retained_unchanged=($beforeRetained -ceq $afterRetained)}
}
$results|ConvertTo-Json -Depth 6 -Compress
""",
    )
    assert result["stopped"]["outcome"] == "allowed", result
    assert result["desktop_bind"]["outcome"] == "allowed", result
    assert result["stopped"]["events"] == [
        "start:postgres",
        "start:redis",
        "dependencies_healthy",
        "schema",
        "start:gw",
        "start:api",
        "start:web",
        "all_healthy",
    ]
    assert result["mixed"]["events"] == [
        "start:redis",
        "dependencies_healthy",
        "schema",
        "start:api",
        "all_healthy",
    ]
    assert result["all_running"]["events"] == ["dependencies_healthy", "schema", "all_healthy"]
    assert result["schema"]["outcome"] == "retained_upgrade_requires_controlled_start"
    assert result["schema"]["events"] == [
        "start:postgres",
        "start:redis",
        "dependencies_healthy",
        "schema",
    ]
    assert result["health"]["outcome"] == "service_health_timeout"
    for scenario in (
        "missing",
        "image",
        "paused",
        "pid",
        "manifest",
        "wrong_volume",
        "volume_subpath",
        "wrong_env",
        "extra_env",
        "duplicate_env",
        "command",
        "entrypoint",
        "user",
        "workdir",
        "mount_rw",
        "bind_source",
        "extra_mount",
        "network",
        "privileged",
        "devices",
        "capabilities",
        "lock",
        "maintenance",
        "drift",
        "pointer",
        "loaded_image",
        "unknown_container",
        "port",
        "expired",
    ):
        assert result[scenario]["outcome"] != "allowed", (scenario, result[scenario])
        assert result[scenario]["events"] == [], (scenario, result[scenario])
    assert all(value["retained_unchanged"] for value in result.values())


_GUARD_FIXTURE = r"""
$site = Join-Path $PSScriptRoot 'site'
$stateDirectory = Join-Path $site '.remote-maintenance-state'
[void](New-Item -ItemType Directory -Path $stateDirectory -Force)
$allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18','S-1-5-32-544') | Select-Object -Unique
foreach ($directory in @($site,$stateDirectory)) {
  $acl = New-Object Security.AccessControl.DirectorySecurity
  $acl.SetAccessRuleProtection($true,$false)
  $acl.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User)
  foreach ($sid in $allowed) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      (New-Object Security.Principal.SecurityIdentifier($sid)), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    [void]$acl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $directory -AclObject $acl
}
$markerPath=Join-Path $stateDirectory 'full-upgrade-maintenance.json'
$operation='00000000-0000-4000-8000-000000000099'
$marker=[ordered]@{schema_version=1;operation_id=$operation;site_root=$site;status='committed';
 source_identity=('sha256:'+('a'*64));candidate_identity=('sha256:'+('b'*64));
 source_head='0012_alarm_notification_runtime';target_head='0013_serial_polling_profile';
 journal_path=(Join-Path $stateDirectory "full-upgrade-$operation.json");updated_at='2000-01-01T00:00:00.0000000+00:00'}
$markerJson=$marker|ConvertTo-Json -Compress
function Write-Marker { param($Record)
  [IO.File]::WriteAllText($markerPath,($Record|ConvertTo-Json -Depth 6 -Compress),(New-Object Text.UTF8Encoding($false)))
}
function Observe-Guard {
  try { Assert-NoFullUpgradeMaintenance -SiteRoot $site; return 'allowed' }
  catch { return $_.Exception.Message }
}
"""


@pytest.mark.parametrize(
    "script_name",
    [
        "start_ruisheng_local.ps1",
        "remote_maintenance.ps1",
        "remote_hotfix_deploy.ps1",
        "remote_admin_bootstrap.ps1",
    ],
)
def test_persistent_marker_closed_contract_in_both_shells(
    guard_shell: str, tmp_path: Path, script_name: str
) -> None:
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        _function_loader("Assert-NoFullUpgradeMaintenance", ROOT / "tools" / script_name)
        + _GUARD_FIXTURE
        + r"""
$results=[ordered]@{absent=(Observe-Guard)}
foreach($status in @('committed','rolled_back','active')) {
  $marker.status=$status;Write-Marker $marker;$results[$status]=Observe-Guard
}
$marker.status='committed'
$invalid=[ordered]@{schema_version=@($true,'1',1.5,$null);operation_id=@('not-a-uuid',$null);
 site_root=@('C:\wrong',123);status=@('expired','COMMITTED',$null);source_identity=@('sha256:bad',('sha256:'+('A'*64)));
 candidate_identity=@('bad',123);source_head=@('0011');target_head=@('0014');journal_path=@('C:\wrong');
 updated_at=@('2000-01-01','bad',123)}
foreach($field in $invalid.Keys) {
  $index=0
  foreach($value in $invalid[$field]) {
    $record=$markerJson|ConvertFrom-Json;$record.$field=$value;Write-Marker $record
    $results["$field-$index"]=Observe-Guard;$index++
  }
}
foreach($field in @($marker.Keys)) {
  $record=$markerJson|ConvertFrom-Json;$record.PSObject.Properties.Remove($field);Write-Marker $record
  $results["missing-$field"]=Observe-Guard
}
$record=$markerJson|ConvertFrom-Json
$record|Add-Member NoteProperty expires_at '2000-01-01T00:00:00.0000000+00:00'
Write-Marker $record;$results.extra=Observe-Guard
foreach($invalidJson in @('{','null','[]','true',(' '*17000))) {
  [IO.File]::WriteAllText($markerPath,$invalidJson);$results["json-$($invalidJson.Length)"]=Observe-Guard
}
Write-Marker $marker
$originalAcl=Get-Acl -LiteralPath $markerPath
$unsafeAcl=Get-Acl -LiteralPath $markerPath
$unsafeAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
  (New-Object Security.Principal.SecurityIdentifier('S-1-1-0')),'Write','Allow')))
Set-Acl -LiteralPath $markerPath -AclObject $unsafeAcl
$results.unsafe_acl=Observe-Guard
Set-Acl -LiteralPath $markerPath -AclObject $originalAcl
Remove-Item -LiteralPath $markerPath
[void](New-Item -ItemType Directory -Path $markerPath)
$results.directory=Observe-Guard
Remove-Item -LiteralPath $markerPath
$linked=Join-Path $site 'linked';[void](New-Item -ItemType Directory -Path $linked)
[void](New-Item -ItemType Junction -Path $markerPath -Target $linked)
$results.reparse=Observe-Guard
$results|ConvertTo-Json -Compress
""",
    )
    assert result.pop("absent") == "allowed"
    assert result.pop("committed") == "allowed"
    assert result.pop("rolled_back") == "allowed"
    assert result.pop("active") == "full_upgrade_maintenance_active"
    assert result.pop("unsafe_acl") == "full_upgrade_maintenance_acl_invalid"
    assert all(value == "full_upgrade_maintenance_invalid" for value in result.values()), result


def test_launcher_checks_before_docker_and_after_both_locks(
    guard_shell: str, tmp_path: Path
) -> None:
    main = _read(LAUNCHER).split("$activeRelease = $null", 1)[1]
    before_docker = main.split('  Write-Stage "Verifying the active release"', 1)[0]
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        _function_loader("Assert-NoFullUpgradeMaintenance")
        + _GUARD_FIXTURE
        + r"""
$marker.status='active';Write-Marker $marker
$script:dockerStarted=$false
function Resolve-SiteRoot { return $site }
function Write-Stage {}
function Initialize-DockerEnvironment {}
function Find-DockerExecutable { return 'fixture-docker' }
function Assert-LocalDockerContext {}
function Start-OrReuseDockerDesktop { $script:dockerStarted=$true }
"""
        + before_docker
        + r"""
} catch { $failure=$_.Exception.Message }
@{started=$script:dockerStarted;error=$failure}|ConvertTo-Json -Compress
""",
    )
    assert result == {"started": False, "error": "full_upgrade_maintenance_active"}
    shared = main.index('Acquire-LeasedLock -Path $SharedLockPath -Name "shared-maintenance"')
    legacy = main.index('Acquire-LeasedLock -Path $LegacyLockPath -Name "legacy-hotfix"')
    guard = main.index("Assert-NoFullUpgradeMaintenance", legacy)
    assert shared < legacy < guard < main.index('"up", "-d", "--no-build", "postgres", "redis"')


def test_installer_attests_only_reviewed_launcher_and_preserves_old_payload(
    guard_shell: str, tmp_path: Path
) -> None:
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        _function_loader("Assert-LauncherGuardSource", INSTALLER)
        + _function_loader("Install-FileAtomic", INSTALLER)
        + r"""
Assert-LauncherGuardSource -Path $launcherPath.Replace('install_ruisheng_desktop_launcher.ps1','start_ruisheng_local.ps1')
$fake=Join-Path $PSScriptRoot 'old.ps1'
[IO.File]::WriteAllText($fake,'# bounded-schema-v1; function Assert-NoFullUpgradeMaintenance {}')
try { Assert-LauncherGuardSource $fake;$fakeResult='allowed' } catch {$fakeResult=$_.Exception.Message}
$destination=Join-Path $PSScriptRoot 'installed.ps1';$staged=Join-Path $PSScriptRoot 'staged.ps1'
[IO.File]::WriteAllText($destination,'previous launcher');[IO.File]::WriteAllText($staged,'new launcher')
$oldHash=(Get-FileHash $destination -Algorithm SHA256).Hash.ToLowerInvariant()
$launcherUserSid='fixture'
function Set-LauncherFileAcl {}
function Assert-LauncherAcl {}
Install-FileAtomic $staged $destination
$backup=@(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.replace.bak')
@{fake=$fakeResult;installed=[IO.File]::ReadAllText($destination);backup=[IO.File]::ReadAllText($backup[0].FullName);
 hash_valid=([IO.File]::ReadAllText($backup[0].FullName+'.sha256') -ceq $oldHash)}|ConvertTo-Json -Compress
""",
    )
    assert result == {
        "fake": "launcher_maintenance_guard_unapproved",
        "installed": "new launcher",
        "backup": "previous launcher",
        "hash_valid": True,
    }


@pytest.mark.parametrize("script_name", ["start_ruisheng_local.ps1", "remote_maintenance.ps1"])
def test_guarded_leases_roundtrip_real_timestamps(
    guard_shell: str, tmp_path: Path, script_name: str
) -> None:
    names = [
        "ConvertTo-ValidatedLockRecord",
        "New-LockRecord",
        "Acquire-LeasedLock",
        "Renew-Locks",
        "Release-Locks",
        "Write-JsonAtomic",
    ]
    names += (
        ["ConvertFrom-JsonPreservingDateStrings", "Assert-LocksOwned"]
        if script_name.startswith("start_")
        else ["Convert-CoordinationJson"]
    )
    loader = "".join(_function_loader(name, ROOT / "tools" / script_name) for name in names)
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        loader
        + r"""
$OperationId='00000000-0000-4000-8000-000000000098';$Action='StartApp';$LeaseSeconds=900
$ProcessStartedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
$AcquiredLocks=New-Object Collections.ArrayList
$ReclaimedLocks=New-Object Collections.ArrayList
$shared=Join-Path $PSScriptRoot 'shared.lock';$legacy=Join-Path $PSScriptRoot 'legacy.lock'
Acquire-LeasedLock $shared 'shared-maintenance';Acquire-LeasedLock $legacy 'legacy-hotfix'
if(Get-Command Assert-LocksOwned -ErrorAction SilentlyContinue){Assert-LocksOwned}
Renew-Locks
if(Get-Command Assert-LocksOwned -ErrorAction SilentlyContinue){Assert-LocksOwned}
Release-Locks
@{shared=(Test-Path $shared);legacy=(Test-Path $legacy)}|ConvertTo-Json -Compress
""",
    )
    assert result == {"shared": False, "legacy": False}


@pytest.mark.parametrize("script_name", ["start_ruisheng_local.ps1", "remote_maintenance.ps1"])
def test_orphaned_lease_is_reclaimed_before_expiry(
    guard_shell: str, tmp_path: Path, script_name: str
) -> None:
    names = [
        "ConvertTo-ValidatedLockRecord",
        "Test-MatchingProcess",
        "New-LockRecord",
        "Acquire-LeasedLock",
        "Release-Locks",
    ]
    names += (
        ["ConvertFrom-JsonPreservingDateStrings", "Write-JsonAtomic"]
        if script_name.startswith("start_")
        else ["Convert-CoordinationJson"]
    )
    loader = "".join(_function_loader(name, ROOT / "tools" / script_name) for name in names)
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        loader
        + r"""
$OperationId='00000000-0000-4000-8000-000000000099';$Action='StartApp';$LeaseSeconds=900
$ProcessStartedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
$AcquiredLocks=New-Object Collections.ArrayList;$ReclaimedLocks=New-Object Collections.ArrayList
$shared=Join-Path $PSScriptRoot 'shared.lock'
$record=[ordered]@{schema_version=1;lock_name='shared-maintenance';operation_id='00000000-0000-4000-8000-000000000098';action='StartApp';pid=2147483647;process_started_at=(Get-Date).ToUniversalTime().ToString('o');target='fixture';acquired_at=(Get-Date).ToUniversalTime().ToString('o');expires_at=(Get-Date).ToUniversalTime().AddMinutes(10).ToString('o')}
[IO.File]::WriteAllText($shared,($record|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false)))
Acquire-LeasedLock $shared 'shared-maintenance';$reclaimed=@(Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'shared.lock.stale.*');Release-Locks
@{lock_present=(Test-Path $shared);stale_count=$reclaimed.Count}|ConvertTo-Json -Compress
""",
    )
    assert result == {"lock_present": False, "stale_count": 1}


def test_hotfix_assembled_reservation_bounds_guards_and_lease_roundtrip(
    guard_shell: str, tmp_path: Path
) -> None:
    hotfix = ROOT / "tools" / "remote_hotfix_deploy.ps1"
    loader = "".join(
        _function_loader(name, hotfix)
        for name in [
            "Assert-NoFullUpgradeMaintenance",
            "Convert-CoordinationJson",
            "ConvertTo-PowerShellLiteral",
            "Start-RemoteHotfixReservation",
        ]
    )
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        loader
        + _GUARD_FIXTURE
        + r"""
$SiteRoot=$site;$Service='api';$HotfixOperationId=$operation
$definition=${function:Start-RemoteHotfixReservation}.ToString()
$prefix=$definition.Substring(0,$definition.IndexOf('$startInfo = New-Object Diagnostics.ProcessStartInfo'))
$payload=& ([scriptblock]::Create($prefix+'; $script'))
$encodedLength=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($payload)).Length
$remotePrefix=$payload.Substring(0,$payload.IndexOf('  [ordered]@{ ok=$true; operation_id=$OperationId; action=$Action }'))
$bounded=$remotePrefix+'  Renew-Locks; throw "fixture_finished" } finally { Release-Locks }'
$results=[ordered]@{encoded_length=$encodedLength}
foreach($case in @('absent','active','race')) {
  if($case -eq 'active'){$marker.status='active';Write-Marker $marker}
  if($case -eq 'race'){Remove-Item -LiteralPath $markerPath}
  $scriptToRun=$bounded
  if($case -eq 'race') {
    $scriptToRun=$bounded.Replace('Acquire-Lock -Path $LegacyLockPath -Name "legacy-hotfix"',
      'Acquire-Lock -Path $LegacyLockPath -Name "legacy-hotfix"; $marker.status="active"; Write-Marker $marker')
  }
  try {& ([scriptblock]::Create($scriptToRun))}catch{$results[$case]=$_.Exception.Message}
  if((Test-Path (Join-Path $stateDirectory '.remote-maintenance.lock')) -or
     (Test-Path (Join-Path $site '.remote-hotfix.lock'))){throw 'fixture_lock_not_released'}
}
$results|ConvertTo-Json -Compress
""",
    )
    assert result.pop("encoded_length") <= 24000
    assert result == {
        "absent": "fixture_finished",
        "active": "full_upgrade_maintenance_active",
        "race": "full_upgrade_maintenance_active",
    }


def test_hotfix_handoff_guard_and_real_lease_roundtrip(guard_shell: str, tmp_path: Path) -> None:
    hotfix = ROOT / "tools" / "remote_hotfix_deploy.ps1"
    loader = "".join(
        _function_loader(name, hotfix)
        for name in [
            "Assert-NoFullUpgradeMaintenance",
            "Convert-CoordinationJson",
            "ConvertTo-PowerShellLiteral",
            "Invoke-RemoteDeployment",
        ]
    )
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        loader
        + _GUARD_FIXTURE
        + r"""
$SiteRoot=$site;$Service='api';$HotfixOperationId=$operation;$CandidateRoot='C:\fixture\candidate';$Platform='linux/amd64'
$Configuration=@{env_key='API_IMAGE';container_name='ruisheng-api';health_url='http://127.0.0.1/health'}
$preflight=@{active_identity=('sha256:'+('a'*64))}
$artifact=@{Manifest=@{source_commit=('a'*40)};ImageReference='fixture:api';ArchivePath='C:\fixture\api.tar';ManifestPath='C:\fixture\manifest.json'}
function Invoke-RemotePowerShell {param([string]$Script) return $Script}
$payload=Invoke-RemoteDeployment -Artifact $artifact -RemoteDirectory 'C:\fixture\upload'
$ast=[Management.Automation.Language.Parser]::ParseInput($payload,[ref]$null,[ref]$null)
foreach($name in @('Read-ReservedLock','Write-TransitionLockAtomic','Acquire-TransitionLock','Assert-TransitionLocksOwned',
  'Renew-TransitionLocks','Maintain-TransitionLocks','Release-TransitionLocks')) {
  $definition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
  Invoke-Expression $definition.Extent.Text
}
$acquiredLocks=New-Object Collections.ArrayList
$processStartedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o');$lockLeaseMinutes=5
$shared=Join-Path $stateDirectory '.remote-maintenance.lock';$legacy=Join-Path $site '.remote-hotfix.lock'
foreach($entry in @(@{path=$shared;name='shared-maintenance'},@{path=$legacy;name='legacy-hotfix'})) {
  $record=@{schema_version=1;operation_id=$HotfixOperationId;action='hotfix-api';pid=$PID;
    lock_name=$entry.name;process_started_at=$processStartedAt;expires_at=[DateTimeOffset]::UtcNow.AddMinutes(5).ToString('o')}
  [IO.File]::WriteAllText($entry.path,($record|ConvertTo-Json -Compress))
  Acquire-TransitionLock -Path $entry.path -Name $entry.name -ReservedPid $PID -ReservedProcessStartedAt $processStartedAt
}
$nextLockCheckAt=[DateTimeOffset]::MinValue;$nextLockRenewalAt=[DateTimeOffset]::MinValue
Assert-TransitionLocksOwned;Renew-TransitionLocks;Assert-TransitionLocksOwned;Maintain-TransitionLocks -Force
$marker.status='active';Write-Marker $marker
try {Maintain-TransitionLocks -Force;$blocked=$false} catch {$blocked=$_.Exception.Message -eq 'deployment_lock_unavailable'}
Release-TransitionLocks
@{blocked=$blocked;shared=(Test-Path $shared);legacy=(Test-Path $legacy);
 serialized_guard=$payload.Contains('function Assert-NoFullUpgradeMaintenance');
 handoff_checked=($payload.IndexOf('Assert-NoFullUpgradeMaintenance -SiteRoot $SiteRoot',
 $payload.IndexOf('$sharedReservation = Read-ReservedLock')) -lt $payload.IndexOf('Acquire-TransitionLock -Path $sharedLockPath'))}|ConvertTo-Json -Compress
""",
    )
    assert result == {
        "blocked": True,
        "shared": False,
        "legacy": False,
        "serialized_guard": True,
        "handoff_checked": True,
    }


def test_hotfix_full_payload_uses_stdin_with_real_local_process(
    guard_shell: str, tmp_path: Path
) -> None:
    hotfix = ROOT / "tools" / "remote_hotfix_deploy.ps1"
    loader = "".join(
        _function_loader(name, hotfix)
        for name in [
            "Convert-CoordinationJson",
            "Invoke-NativeText",
            "Invoke-RemotePowerShell",
        ]
    )
    shell64 = base64.b64encode(guard_shell.encode()).decode()
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        loader
        + f"""
$localShell=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{shell64}'))
"""
        + r"""
$RepositoryRoot=$PSScriptRoot;$Target='fixture@127.0.0.1'
$realNative=${function:Invoke-NativeText}
function Invoke-NativeText {
  param([string]$FilePath,[string[]]$ArgumentList,[string]$InputText)
  if($FilePath -cne 'ssh.exe' -or ($ArgumentList[-2..-1] -join ' ') -cne '-Command -' -or
    ($ArgumentList -join ' ').Length -gt 1000){throw 'fixture_transport_not_bounded'}
  return & $realNative -FilePath $localShell -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-Command','-') -InputText $InputText
}
$payload=('#'+('x'*70000)+"`n")+'[ordered]@{status="complete";unicode=[char]0x6DA6}|ConvertTo-Json -Compress'
$reply=Invoke-RemotePowerShell -Script $payload
$reply
""",
    )
    assert result == {"status": "complete", "unicode": "润"}


def test_installer_guard_receipt_has_protected_acl_exact_schema_and_retained_backup(
    guard_shell: str, tmp_path: Path
) -> None:
    loader = "".join(
        _function_loader(name, INSTALLER)
        for name in [
            "Install-FileAtomic",
            "Assert-LauncherGuardSource",
            "Set-LauncherDirectoryAcl",
            "Set-LauncherFileAcl",
            "Assert-LauncherAcl",
            "Write-GuardInstallReceipt",
        ]
    )
    result = _run_guard_ps(
        guard_shell,
        tmp_path,
        loader
        + r"""
$fixtureLauncher=Join-Path $PSScriptRoot 'start_ruisheng_local.ps1'
$reviewedLauncher=$launcherPath.Replace('install_ruisheng_desktop_launcher.ps1','start_ruisheng_local.ps1')
[IO.File]::Copy($reviewedLauncher,$fixtureLauncher)
# The current user owns the isolated fixture; Builtin Users stands in for the read-only launcher user.
$AdministratorsSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$SystemSid='S-1-5-18';$launcherUserSid='S-1-5-32-545'
Set-LauncherDirectoryAcl $PSScriptRoot $launcherUserSid
Set-LauncherFileAcl $fixtureLauncher $launcherUserSid
$receiptFunction=${function:Write-GuardInstallReceipt}.ToString().Replace(
  '"C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1"','$fixtureLauncher')
Set-Item Function:\Write-GuardInstallReceipt ([scriptblock]::Create($receiptFunction))
$hash=(Get-FileHash $fixtureLauncher -Algorithm SHA256).Hash.ToLowerInvariant()
Write-GuardInstallReceipt $fixtureLauncher $launcherUserSid $hash
$receiptPath=Join-Path $PSScriptRoot 'schema-upgrade-guard.json'
$first=[IO.File]::ReadAllText($receiptPath)
Write-GuardInstallReceipt $fixtureLauncher $launcherUserSid $hash
Assert-LauncherAcl $receiptPath $launcherUserSid
$receipt=[IO.File]::ReadAllText($receiptPath)|ConvertFrom-Json
$backups=@(Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'schema-upgrade-guard.json.*.replace.bak')
$rules=@((Get-Acl $receiptPath).Access|Where-Object {$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $launcherUserSid})
@{keys=@($receipt.PSObject.Properties.Name);schema=$receipt.schema_version;guard=$receipt.guard_version;
 hash=($receipt.launcher_sha256 -ceq $hash);launcher=($receipt.launcher -ceq $fixtureLauncher);
 readonly=($rules.Count -eq 1 -and ($rules[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write) -eq 0);
 backup=($backups.Count -eq 1 -and [IO.File]::ReadAllText($backups[0].FullName) -ceq $first)}|ConvertTo-Json -Compress
""",
    )
    assert result == {
        "keys": ["schema_version", "guard_version", "launcher", "launcher_sha256", "installed_at"],
        "schema": 1,
        "guard": "bounded-schema-v1",
        "hash": True,
        "launcher": True,
        "readonly": True,
        "backup": True,
    }
