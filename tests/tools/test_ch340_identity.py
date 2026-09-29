"""A serial-less replacement adapter is pinned to its approved Windows instance."""

import json
import shutil
from datetime import UTC, datetime

import pytest

from tests.tools.test_serial_gateway_recovery import load, run
from tests.tools.test_serial_hardware import ROOT, _config, _write_config
from tools.validate_serial_hardware import (
    SerialHardwareError,
    load_site_config,
    validate_hardware_attestation,
)


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def shell(request):
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} unavailable")
    return executable


INSTANCE = r"USB\VID_1A86&PID_7523\5&TEST&0&2"


def ch340_config():
    config = _config()
    config["schema_version"] = 2
    adapter = config["adapter"]
    adapter.pop("serial_number")
    adapter.update(vendor_id="1A86", product_id="7523", instance_id=INSTANCE)
    return config


def ch340_auto_config():
    config = ch340_config()
    config["adapter"]["identity_policy"] = "single_present_device"
    return config


def test_ch340_config_and_attestation_bind_the_same_instance(tmp_path):
    site = load_site_config(_write_config(tmp_path, ch340_config()))
    assert site.adapter.instance_id == INSTANCE
    state = {
        "schema_version": 2,
        "result": "ready",
        "timestamp": datetime.now(UTC).isoformat(),
        "vendor_id": "1A86",
        "product_id": "7523",
        "instance_id": INSTANCE,
        "stable_path": "/dev/ruisheng-rs485",
        "device_path": "/dev/ttyUSB0",
        "bus_id": "4-2",
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    validate_hardware_attestation(path, site)
    state["instance_id"] = INSTANCE + "OTHER"
    path.write_text(json.dumps(state))
    with pytest.raises(SerialHardwareError, match="instance_id"):
        validate_hardware_attestation(path, site)


def test_ch340_auto_policy_accepts_rebound_instance(tmp_path):
    site = load_site_config(_write_config(tmp_path, ch340_auto_config()))
    state = {
        "schema_version": 2,
        "result": "ready",
        "timestamp": datetime.now(UTC).isoformat(),
        "vendor_id": "1A86",
        "product_id": "7523",
        "instance_id": r"USB\VID_1A86&PID_7523\6&TEST&0&1",
        "identity_policy": "single_present_device",
        "configured_instance_id": INSTANCE,
        "stable_path": "/dev/ruisheng-rs485",
        "device_path": "/dev/ttyUSB0",
        "bus_id": "4-1",
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    validate_hardware_attestation(path, site)

    state["configured_instance_id"] = INSTANCE + "OTHER"
    path.write_text(json.dumps(state))
    with pytest.raises(SerialHardwareError, match="configured_instance_id"):
        validate_hardware_attestation(path, site)


@pytest.mark.parametrize(
    "change",
    [
        {"instance_id": "COM6"},
        {"instance_id": r"USB\VID_0403&PID_6001\OTHER"},
        {"instance_id": INSTANCE + ";evil"},
        {"serial_number": "pretend"},
        {"product_id": "9999"},
    ],
)
def test_ch340_rejects_port_names_and_ambiguous_identity(tmp_path, change):
    config = ch340_config()
    config["adapter"].update(change)
    with pytest.raises(SerialHardwareError):
        load_site_config(_write_config(tmp_path, config))


def test_windows_device_selection_is_exact_and_rejects_duplicates(shell, tmp_path):
    result = run(
        shell,
        tmp_path,
        load("Get-TargetDevice", "Assert-Pattern", path=ROOT / "tools/serial_hardware_attach.ps1")
        + r"""
$script:ConfigSchema=2;$script:VendorId='1A86';$script:ProductId='7523';$script:SerialNumber='-'
$script:InstanceId='USB\VID_1A86&PID_7523\5&TEST&0&2'
function Invoke-Usbipd {param($Arguments) @{Devices=$devices}|ConvertTo-Json -Depth 5 -Compress}
$correct=@{InstanceId=$script:InstanceId;BusId='4-2'}
$decoy=@{InstanceId=$script:InstanceId+'OTHER';BusId='4-3'}
$results=@{}
foreach($scenario in @('exact','missing','duplicate','bad_bus')) {
 $devices=@($correct,$decoy)
 if($scenario -eq 'missing'){$devices=@($decoy)}
 if($scenario -eq 'duplicate'){$devices=@($correct,$correct)}
 if($scenario -eq 'bad_bus'){$devices=@(@{InstanceId=$script:InstanceId;BusId='4-2;bad'})}
 try{$results[$scenario]=(Get-TargetDevice).BusId}catch{$results[$scenario]=$_.Exception.Message}
}
$results|ConvertTo-Json -Compress
""",
    )
    assert result == {
        "exact": "4-2",
        "missing": "device_not_present",
        "duplicate": "device_identity_ambiguous",
        "bad_bus": "invalid_bus_id",
    }


def test_windows_device_selection_auto_rebinds_only_one_same_model(shell, tmp_path):
    result = run(
        shell,
        tmp_path,
        load("Get-TargetDevice", "Assert-Pattern", path=ROOT / "tools/serial_hardware_attach.ps1")
        + r"""
$script:ConfigSchema=2;$script:VendorId='1A86';$script:ProductId='7523';$script:SerialNumber='-'
$script:ConfiguredInstanceId='USB\VID_1A86&PID_7523\5&TEST&0&2';$script:InstanceId=$script:ConfiguredInstanceId
$script:IdentityPolicy='single_present_device'
function Invoke-Usbipd {param($Arguments) @{Devices=$devices}|ConvertTo-Json -Depth 5 -Compress}
$new=@{InstanceId='USB\VID_1A86&PID_7523\6&TEST&0&1';BusId='4-1'}
$results=@{}
foreach($scenario in @('rebound','ambiguous','missing')) {
 $devices=@($new)
 if($scenario -eq 'ambiguous'){$devices=@($new,@{InstanceId=$new.InstanceId+'B';BusId='4-3'})}
 if($scenario -eq 'missing'){$devices=@()}
 try{$results[$scenario]=(Get-TargetDevice).BusId}catch{$results[$scenario]=$_.Exception.Message}
}
$results.instance_id=$script:InstanceId
$results|ConvertTo-Json -Compress
""",
    )
    assert result == {
        "rebound": "4-1",
        "ambiguous": "device_identity_ambiguous",
        "missing": "device_not_present",
        "instance_id": r"USB\VID_1A86&PID_7523\6&TEST&0&1",
    }


def test_recovery_checks_ch340_against_protected_config(shell, tmp_path):
    # Relocate only fixed filesystem fixtures; run the real guard in both PS versions.
    state_path = tmp_path / "state.json"
    config_path = tmp_path / "config.json"
    # load() embeds source as base64; obtain a temporary launcher for both substitutions.
    launcher = ROOT / "tools/start_ruisheng_local.ps1"
    fixture = tmp_path / "launcher.ps1"
    fixture.write_text(
        launcher.read_text(encoding="utf-8")
        .replace(r"C:\Ruisheng\audit\serial-hardware-state.json", str(state_path))
        .replace(r"C:\Ruisheng\site\serial-hardware.json", str(config_path)),
        encoding="utf-8",
    )
    code = load(
        "Assert-SerialRecoveryHardware",
        "ConvertFrom-JsonPreservingDateStrings",
        "Get-RemainingTimeoutSeconds",
        path=fixture,
    )
    config = ch340_config()
    config_path.write_text(json.dumps(config))
    result = run(
        shell,
        tmp_path,
        code
        + f"$statePath='{state_path}'\n"
        + r"""
function Assert-SiteSerialPath {param($Path)}
function Invoke-NativeResult {param($FilePath,$Arguments,$TimeoutSeconds) @{ExitCode=0}}
$results=@{}
foreach($scenario in @('good','other_instance','old_schema','stale')) {
 $state=@{schema_version=2;result='ready';vendor_id='1A86';product_id='7523';
  instance_id='USB\VID_1A86&PID_7523\5&TEST&0&2';stable_path='/dev/ruisheng-rs485';
  device_path='/dev/ttyUSB0';bus_id='4-2';timestamp=[DateTimeOffset]::UtcNow.ToString('o')}
 if($scenario -eq 'other_instance'){$state.instance_id+='OTHER'}
 if($scenario -eq 'old_schema'){$state.schema_version=1}
 if($scenario -eq 'stale'){$state.timestamp=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o')}
 [IO.File]::WriteAllText($statePath,($state|ConvertTo-Json))
 try {Assert-SerialRecoveryHardware -Port '/dev/ruisheng-rs485' -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(20));$results[$scenario]='ready'}
 catch{$results[$scenario]=$_.Exception.Message}
}
$results|ConvertTo-Json -Compress
""",
    )
    assert result.pop("good") == "ready"
    assert all(value != "ready" for value in result.values())


def test_recovery_allows_rebound_instance_only_in_auto_policy(shell, tmp_path):
    state_path = tmp_path / "state.json"
    config_path = tmp_path / "config.json"
    launcher = ROOT / "tools/start_ruisheng_local.ps1"
    fixture = tmp_path / "launcher.ps1"
    fixture.write_text(
        launcher.read_text(encoding="utf-8")
        .replace(r"C:\Ruisheng\audit\serial-hardware-state.json", str(state_path))
        .replace(r"C:\Ruisheng\site\serial-hardware.json", str(config_path)),
        encoding="utf-8",
    )
    code = load(
        "Assert-SerialRecoveryHardware",
        "ConvertFrom-JsonPreservingDateStrings",
        "Get-RemainingTimeoutSeconds",
        path=fixture,
    )
    config_path.write_text(json.dumps(ch340_auto_config()))
    result = run(
        shell,
        tmp_path,
        code
        + f"$statePath='{state_path}'\n"
        + r"""
function Assert-SiteSerialPath {param($Path)}
function Invoke-NativeResult {param($FilePath,$Arguments,$TimeoutSeconds) @{ExitCode=0}}
$state=@{schema_version=2;result='ready';vendor_id='1A86';product_id='7523';
 instance_id='USB\VID_1A86&PID_7523\6&TEST&0&1';identity_policy='single_present_device';
 configured_instance_id='USB\VID_1A86&PID_7523\5&TEST&0&2';stable_path='/dev/ruisheng-rs485';
 device_path='/dev/ttyUSB0';bus_id='4-1';timestamp=[DateTimeOffset]::UtcNow.ToString('o')}
[IO.File]::WriteAllText($statePath,($state|ConvertTo-Json))
try {$result='ready';Assert-SerialRecoveryHardware -Port '/dev/ruisheng-rs485' -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(20))} catch {$result=$_.Exception.Message}
@{result=$result}|ConvertTo-Json -Compress
""",
    )
    assert result["result"] == "ready"
