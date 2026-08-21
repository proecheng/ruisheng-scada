"""Contracts for the Windows remote-debug and single-service hotfix tools."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
DEBUG_SCRIPT = ROOT / "tools" / "remote_debug.ps1"
HOTFIX_SCRIPT = ROOT / "tools" / "remote_hotfix_deploy.ps1"
GUIDE = ROOT / "docs" / "REMOTE_DEBUG.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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

    hash_check = remote.index("Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256")
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
    assert "& docker exec $ContainerName python -c $pythonProbe" in script
    assert "& docker exec $ContainerName wget" in script
    assert "Service did not become ready" in script


def test_hotfix_has_atomic_environment_backup_and_automatic_rollback() -> None:
    script = _read(HOTFIX_SCRIPT)

    assert '"$EnvFile.pre-hotfix-$Service-$timestamp.bak"' in script
    assert "[IO.File]::Replace($temporaryEnv, $EnvFile, $backupPath)" in script
    assert "Copy-Item -LiteralPath $backupPath -Destination $rollbackTemp" in script
    assert "[IO.File]::Replace($rollbackTemp, $EnvFile, $null)" in script
    assert "Deployment failed and the previous image was restored" in script
    assert "Rollback also failed" in script
    assert "[IO.FileMode]::CreateNew" in script
    assert 'Join-Path $SiteRoot ".remote-hotfix.lock"' in script
    assert "Another remote hotfix is running" in script


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
    assert "127.0.0.1:18080" in guide
    assert "不开放目标机应用端口" in guide
    assert "BLOCKED" in guide
