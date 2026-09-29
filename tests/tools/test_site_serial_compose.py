from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tools/site_serial_compose.ps1"


def canonical() -> str:
    port = "/dev/ruisheng-rs485"
    return json.dumps(
        {
            "services": {
                "gw": {
                    "environment": {
                        "GW_SERIAL_PORTS": json.dumps(
                            [{"port": port, "baud_rate": 9600}], separators=(",", ":")
                        )
                    },
                    "devices": [{"source": port, "target": port, "permissions": "rw"}],
                }
            }
        },
        separators=(",", ":"),
    )


def run_ps(body: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not executable:
        pytest.skip("PowerShell unavailable")
    source = "$ErrorActionPreference='Stop'\n" + HELPER.read_text(encoding="utf-8") + "\n" + body
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "check.ps1"
        script.write_text(source, encoding="utf-8-sig")
        return subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace('"rw"', '"rwm"'),
        lambda s: s.replace('"devices":', '"privileged":true,"devices":'),
        lambda s: s.replace('"target":"/dev/ruisheng-rs485"', '"target":"/dev/sda"'),
        lambda s: s.replace("9600", "0"),
        lambda s: s.replace("9600", '"9600"'),
        lambda s: s.replace('"services":', '"services":{},"services":'),
        lambda s: s.replace("/dev/ruisheng-rs485", "${UNTRUSTED_PATH}"),
    ],
)
def test_rejects_hardware_override_expansion(change) -> None:
    value = base64.b64encode(change(canonical()).encode()).decode()
    result = run_ps(
        f"$j=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{value}')); Assert-SiteSerialContent $j"
    )
    assert result.returncode != 0
    assert "site_serial_" in result.stderr


def test_accepts_only_expected_device_mapping_and_injects_last() -> None:
    value = base64.b64encode(canonical().encode()).decode()
    result = run_ps(rf"""
$j=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{value}'))
Assert-SiteSerialContent $j
function Get-SiteSerialOverride {{ return 'C:\Ruisheng\site\site-serial.override.json' }}
$a=Add-SiteSerialComposeArguments @('compose','-f','base.yml','-f','network.yml','--env-file','site.env','up','-d','gw')
ConvertTo-Json -InputObject $a -Compress
""")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "compose",
        "-f",
        "base.yml",
        "-f",
        "network.yml",
        "--env-file",
        "site.env",
        "-f",
        r"C:\Ruisheng\site\site-serial.override.json",
        "up",
        "-d",
        "gw",
    ]


def test_absent_configuration_and_non_compose_commands_preserve_arguments() -> None:
    result = run_ps(r"""
function Get-SiteSerialOverride { return '' }
$a=Add-SiteSerialComposeArguments @('compose','-f','base.yml','config','--format','json')
$b=Add-SiteSerialComposeArguments @('exec','ruisheng-postgres','psql')
ConvertTo-Json -InputObject @($a,$b) -Compress
""")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        ["compose", "-f", "base.yml", "config", "--format", "json"],
        ["exec", "ruisheng-postgres", "psql"],
    ]


def test_does_not_mistake_option_values_for_commands() -> None:
    result = run_ps(r"""
function Get-SiteSerialOverride { return 'C:\Ruisheng\site\site-serial.override.json' }
$a=Add-SiteSerialComposeArguments @('compose','--project-directory','up','-f','config','config','--format','json')
ConvertTo-Json -InputObject $a -Compress
""")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[1:5] == ["--project-directory", "up", "-f", "config"]
    assert json.loads(result.stdout)[-5:] == [
        "-f",
        r"C:\Ruisheng\site\site-serial.override.json",
        "config",
        "--format",
        "json",
    ]


def test_snapshot_holds_file_against_replacement_and_rejects_late_creation(tmp_path: Path) -> None:
    config = tmp_path / "serial.json"
    config.write_text(canonical(), encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8").replace(
        r"C:\Ruisheng\site\site-serial.override.json", str(config)
    )
    helper64 = base64.b64encode(helper.encode()).decode()
    result = run_ps(rf"""
Invoke-Expression ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{helper64}')))
function Assert-SiteSerialPath {{ param([string]$Path) }}
$path=Get-SiteSerialOverride
$blocked=$false
try {{ [IO.File]::WriteAllText($path,'{{}}') }} catch {{ $blocked=$true }}
if (-not $blocked) {{ throw 'configuration_not_pinned' }}
[void](Get-SiteSerialOverride)
$script:SiteSerialSnapshot.guard.Dispose()
$script:SiteSerialSnapshot=@{{exists=$false}}
try {{ [void](Get-SiteSerialOverride); throw 'late_creation_not_rejected' }} catch {{
 if ($_.Exception.Message -ne 'site_serial_configuration_changed') {{ throw }}
}}
""")
    assert result.returncode == 0, result.stderr


def test_all_entrypoints_embed_identical_helper() -> None:
    helper = HELPER.read_text(encoding="utf-8").strip()
    for path, count in (
        ("tools/start_ruisheng_local.ps1", 1),
        ("tools/remote_maintenance.ps1", 1),
        ("tools/remote_full_upgrade/target-updater.ps1", 1),
        ("tools/remote_hotfix_deploy.ps1", 2),
    ):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert text.count(helper) == count
        assert "$Arguments = Add-SiteSerialComposeArguments -Arguments $Arguments" in text
