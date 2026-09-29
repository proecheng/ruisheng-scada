"""Synchronize the self-contained site serial helper into remote entrypoints."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
helper = (ROOT / "tools/site_serial_compose.ps1").read_text(encoding="utf-8").rstrip()
for relative, anchor, injection in (
    (
        "tools/start_ruisheng_local.ps1",
        "function Invoke-DockerText {",
        "  if ($Mutation) { Assert-NoFullUpgradeMaintenance -SiteRoot $SiteRoot }",
    ),
    (
        "tools/remote_maintenance.ps1",
        "function Invoke-DockerText {",
        '  if ($Action -ne "Status" -and -not $DryRun) { Assert-NoFullUpgradeMaintenance -SiteRoot $SiteRoot }',
    ),
    (
        "tools/remote_full_upgrade/target-updater.ps1",
        "function Invoke-DockerText {",
        "  if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned -RenewIfDue }",
    ),
    (
        "tools/remote_hotfix_deploy.ps1",
        "function Invoke-Docker {",
        "  $resolvedDocker = Get-Command docker.exe -ErrorAction SilentlyContinue",
    ),
):
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"# BEGIN site serial compose\n.*?# END site serial compose\n", "", text, flags=re.S
    )
    text = text.replace("  $Arguments = Add-SiteSerialComposeArguments -Arguments $Arguments\n", "")
    text = text.replace(
        "  if ($Arguments[0] -ceq 'compose') { $Arguments = Add-SiteSerialComposeArguments -Arguments $Arguments }\n",
        "",
    )
    assert text.count(anchor) == 1
    position = text.index(anchor)
    prefix, rest = text[:position], text[position:]
    assert injection in rest
    rest = rest.replace(
        injection,
        "  if ($Arguments[0] -ceq 'compose') { $Arguments = Add-SiteSerialComposeArguments -Arguments $Arguments }\n"
        + injection,
        1,
    )
    text = prefix + helper + "\n" + rest
    if relative.endswith("remote_hotfix_deploy.ps1"):
        text = text.replace(
            "$composeArguments = Add-SiteSerialComposeArguments -Arguments $composeArguments\n", ""
        )
        preflight = "$rendered = & docker @composeArguments 2>&1"
        assert text.count(preflight) == 1
        text = text.replace(
            preflight,
            helper
            + "\n$composeArguments = Add-SiteSerialComposeArguments -Arguments $composeArguments\n"
            + preflight,
        )
    path.write_text(text, encoding="utf-8", newline="\n")
