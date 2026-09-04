from __future__ import annotations

import base64
import json
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


def _function_loader(name: str) -> str:
    encoded_path = base64.b64encode(str(LAUNCHER).encode()).decode()
    return f"""
$launcherPath = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_path}'))
$source = Get-Content -LiteralPath $launcherPath -Raw
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
        r"if \(@\(\$health.*?already_ready.*?\}\s*else\s*\{",
        script,
        re.DOTALL,
    )
    assert fast_path is not None
    assert "Invoke-DockerText" not in fast_path.group(0)


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
    assert "startup_task_changed = $false" in script
    lowered = script.casefold()
    assert "runas" not in lowered
    assert "register-scheduledtask" not in lowered
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
        Path(Path(sys.executable).anchor) / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
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
