"""A missing serial device must not turn recovery into a general app autostart."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "tools/start_ruisheng_local.ps1"
INSTALLER = ROOT / "tools/install_ruisheng_desktop_launcher.ps1"


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def shell(request):
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} unavailable")
    return executable


def load(*names, path=LAUNCHER, replacement=None):
    source = path.read_text(encoding="utf-8")
    if replacement:
        source = source.replace(*replacement)
    encoded = base64.b64encode(source.encode()).decode()
    functions = ",".join(f"'{name}'" for name in names)
    return f"""
$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'parse_failed' }}
foreach($name in @({functions})) {{
  $node=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name}},$true)
  if($null -eq $node) {{throw 'function_missing'}}
  . ([ScriptBlock]::Create($node.Extent.Text))
}}
"""


def run(shell, tmp_path, script):
    path = tmp_path / "harness.ps1"
    path.write_text("$ErrorActionPreference='Stop'\n" + script, encoding="utf-8-sig")
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path)],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


FAILURE_STATE = r"""
$failureState=[pscustomobject]@{Status='exited';Running=$false;ExitCode=255;Pid=0;Paused=$false;
  Restarting=$false;Dead=$false;OOMKilled=$false;
  Error='error gathering device information while adding custom device "/dev/ruisheng-rs485": no such file or directory'}
"""


def test_only_the_observed_docker_start_failure_is_eligible(shell, tmp_path):
    result = run(
        shell,
        tmp_path,
        load("Test-SerialDeviceStartFailure")
        + FAILURE_STATE
        + r"""
$baseline=$failureState|ConvertTo-Json
$cases=@{
 good=''; manual_stop='$state.ExitCode=0;$state.Error=""';
 crash='$state.ExitCode=1'; killed='$state.ExitCode=137';
 running='$state.Status="running";$state.Running=$true;$state.Pid=10';
 creating='$state.Status="created"'; missing_flag='$state.PSObject.Properties.Remove("Dead")';
 string_flag='$state.Dead="false"'; oom='$state.OOMKilled=$true';
 boolean_exit='$state.ExitCode=$true'; string_exit='$state.ExitCode="255"';
 permission='$state.Error=$state.Error.Replace("no such file or directory","permission denied")';
 other_device='$state.Error=$state.Error.Replace("/dev/ruisheng-rs485","/dev/sda")';
 prefix='$state.Error="unexpected "+$state.Error'; pid='$state.Pid=7'; paused='$state.Paused=$true'
}
$results=@{}
foreach($case in $cases.GetEnumerator()) {
 $state=$baseline|ConvertFrom-Json
 if($case.Value){. ([ScriptBlock]::Create($case.Value))}
 $results[$case.Key]=Test-SerialDeviceStartFailure $state
}
$results|ConvertTo-Json -Compress
""",
    )
    assert result.pop("good") is True
    assert all(v is False for v in result.values()), result


def test_recovery_exception_keeps_container_identity_and_default_start_guards(shell, tmp_path):
    result = run(
        shell,
        tmp_path,
        load(
            "Test-SerialDeviceStartFailure",
            "Get-RetainedStartService",
            "Get-RemainingTimeoutSeconds",
            "ConvertFrom-JsonPreservingDateStrings",
        )
        + FAILURE_STATE
        + r"""
$ContainerNames=@{gw='ruisheng-gw';api='ruisheng-api'}
$ExpectedImages=@{gw=@{image_id='sha256:expected';candidate_reference='candidate:gw'};api=@{}}
$info=@{Id=('a'*64);Name='/ruisheng-gw';Image='sha256:expected';State=$failureState;
 Config=@{Image='candidate:gw';Labels=@{'com.docker.compose.project'='ruisheng-prod';'com.docker.compose.service'='gw'}}}
$baseline=$info|ConvertTo-Json -Depth 8
function Invoke-DockerText {param($Arguments,$TimeoutSeconds) ConvertTo-Json -InputObject @($info) -Depth 8}
function Assert-RetainedStartConfiguration {param($Info,$Expected)
 $script:configurationChecked=$true
 if($scenario -eq 'config_drift'){throw 'configuration_mismatch'}
}
$results=@{}
foreach($scenario in @('default','recover','id_drift','image_drift','project_drift','config_drift')) {
 $info=$baseline|ConvertFrom-Json
 $script:configurationChecked=$false
 if($scenario -eq 'image_drift'){$info.Image='sha256:other'}
 if($scenario -eq 'project_drift'){$info.Config.Labels.'com.docker.compose.project'='other'}
 $expectedId=if($scenario -eq 'id_drift'){'b'*64}else{'a'*64}
 try {
  [void](Get-RetainedStartService -Service gw -Configuration @{} -ExpectedId $expectedId `
    -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(30)) -AllowSerialDeviceStartFailure:($scenario -ne 'default'))
  $outcome='allowed'
 }catch{$outcome=$_.Exception.Message}
 $results[$scenario]=@{outcome=$outcome;configuration_checked=$script:configurationChecked}
}
$results|ConvertTo-Json -Depth 4 -Compress
""",
    )
    assert result["recover"] == {"outcome": "allowed", "configuration_checked": True}
    assert all(v["outcome"] != "allowed" for k, v in result.items() if k != "recover")


def test_recovery_only_starts_gateway_and_rechecks_peers_hardware_and_leases(shell, tmp_path):
    result = run(
        shell,
        tmp_path,
        load(
            "Invoke-SerialGatewayRecovery",
            "Test-SerialDeviceStartFailure",
            "Get-RemainingTimeoutSeconds",
        )
        + FAILURE_STATE
        + r"""
$PersistentServices=@('postgres','redis','gw','api','web')
$model=@{services=@{gw=@{devices=@(@{source='/dev/ruisheng-rs485';target='/dev/ruisheng-rs485';permissions='rw'})}}}
function Assert-RetainedStartGuards {param($ExpectedHashes,$OriginalActive,$Deadline)
 $script:guardCount++
 if($scenario -eq 'lock_lost' -and $script:guardCount -eq 2){throw 'lock_lost'}
 if($scenario -eq 'maintenance'){throw 'full_upgrade_maintenance_active'}
 if($scenario -eq 'release_drift'){throw 'active_release_identity_drift'}
}
function Get-RetainedStartConfigurations {param($Model,$Deadline) @{gw=@{};api=@{};web=@{};postgres=@{};redis=@{}}}
function Get-RetainedStartService {param($Service,$Configuration,$Deadline,$ExpectedId,[switch]$AllowSerialDeviceStartFailure)
 $script:reads++
 $info=$script:services[$Service]
 if($scenario -eq 'peer_stopped' -and $Service -eq 'api'){$info.State.Running=$false}
 if($scenario -eq 'peer_stops_late' -and $Service -eq 'api' -and $script:reads -gt 5){$info.State.Running=$false}
 if($scenario -eq 'id_changes' -and $script:reads -gt 5){$info.Id='f'*64}
 if($ExpectedId -and $info.Id -cne $ExpectedId){throw 'identity_changed'}
 return $info
}
function Wait-DependenciesHealthy {param($ExpectedHashes,$OriginalActive,$Deadline)
 if($scenario -eq 'database_unhealthy'){throw 'database_unhealthy'}
}
function Invoke-DockerText {param($Arguments,$TimeoutSeconds,[switch]$Mutation)
 if($Arguments[0] -eq 'exec'){
  if($Mutation -or $Arguments[-1] -ne 'SELECT version_num FROM alembic_version'){throw 'unexpected_query'}
  if($scenario -eq 'schema'){return '0012_alarm_notification_runtime'}
  return '0013_serial_polling_profile'
 }
 if($Arguments.Count -ne 2 -or $Arguments[0] -ne 'start' -or -not $Mutation -or
    $Arguments[1] -cne $script:services.gw.Id){throw 'unexpected_mutation'}
 $script:starts+=,$Arguments
 if($scenario -eq 'start_error'){throw 'docker_command_failed'}
 $script:services.gw.State.Status='running';$script:services.gw.State.Running=$true
 $script:services.gw.State.Pid=10;$script:services.gw.State.Error=''
}
function Assert-SerialRecoveryHardware {param($Port,$Deadline)
 $script:hardwareChecks++
 if($scenario -eq 'hardware_absent' -or ($scenario -eq 'hardware_lost' -and $script:hardwareChecks -eq 2)){throw 'hardware_unready'}
}
function Write-LauncherAudit {param($Event,$Result)
 if($scenario -eq 'audit_failed'){throw 'audit_failed'}
}
function Wait-AllHealthy {param($ExpectedHashes,$OriginalActive,$Deadline)
 if($scenario -eq 'health_failed'){throw 'service_health_timeout'}
 if(-not $script:services.gw.State.Running){throw 'gateway_not_started'}
 @(@{ready=$true})
}
$results=@{}
foreach($scenario in @('good','manual_stop','gateway_running','wrong_alias','restart_disabled','peer_stopped',
 'peer_stops_late','id_changes','lock_lost','maintenance','release_drift','database_unhealthy','schema',
 'hardware_absent','hardware_lost','audit_failed','start_error','health_failed')){
 $script:services=@{};$script:starts=@();$script:guardCount=0;$script:reads=0;$script:hardwareChecks=0
 $index=0
 foreach($service in $PersistentServices){
  $index++
  $state=$failureState|ConvertTo-Json|ConvertFrom-Json
  if($service -ne 'gw'){$state.Status='running';$state.Running=$true;$state.Pid=10;$state.Error=''}
  $script:services[$service]=@{Id=([string]$index)*64;State=$state;HostConfig=@{RestartPolicy=@{Name='unless-stopped'}}}
 }
 switch($scenario){
  'manual_stop' {$script:services.gw.State.Error='';$script:services.gw.State.ExitCode=0}
  'gateway_running' {$script:services.gw.State.Running=$true;$script:services.gw.State.Status='running'}
  'wrong_alias' {$script:services.gw.State.Error=$script:services.gw.State.Error.Replace('rs485','other')}
  'restart_disabled' {$script:services.gw.HostConfig.RestartPolicy.Name='no'}
 }
 try{[void](Invoke-SerialGatewayRecovery -ExpectedHashes @{} -OriginalActive @{} -ExpectedHead '0013_serial_polling_profile' `
   -ComposeModel $model -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(60)));$outcome='recovered'}
 catch{$outcome=$_.Exception.Message}
 $results[$scenario]=@{outcome=$outcome;starts=$script:starts;hardware_checks=$script:hardwareChecks}
}
$results|ConvertTo-Json -Depth 6 -Compress
""",
    )
    assert result["good"] == {
        "outcome": "recovered",
        "starts": [["start", "3" * 64]],
        "hardware_checks": 2,
    }
    for name, value in result.items():
        if name != "good":
            assert value["outcome"] != "recovered", (name, value)
        if name not in {"good", "start_error", "health_failed"}:
            assert value["starts"] == [], (name, value)


def test_hardware_attestation_must_be_fresh_and_device_node_present(shell, tmp_path):
    state_path = tmp_path / "hardware-state.json"
    result = run(
        shell,
        tmp_path,
        load(
            "Assert-SerialRecoveryHardware",
            "ConvertFrom-JsonPreservingDateStrings",
            "Get-RemainingTimeoutSeconds",
            replacement=(r"C:\Ruisheng\audit\serial-hardware-state.json", str(state_path)),
        )
        + f"$statePath='{state_path}'\n"
        + r"""
function Assert-SiteSerialPath {param($Path) if($scenario -eq 'untrusted'){throw 'untrusted_path'}}
function Invoke-NativeResult {param($FilePath,$Arguments,$TimeoutSeconds)
 if(($Arguments -join ',') -cne '-d,docker-desktop,-u,root,--,test,-c,/dev/ruisheng-rs485'){throw 'unexpected_command'}
 @{ExitCode=$(if($scenario -eq 'missing_node'){1}else{0})}
}
$results=@{}
foreach($scenario in @('good','stale','future','wrong_alias','wrong_usb','unready','missing_node','untrusted','bad_json')){
 $state=@{schema_version=1;result='ready';stable_path='/dev/ruisheng-rs485';vendor_id='0403';product_id='6001';
  serial_number='TEST123';device_path='/dev/ttyUSB0';bus_id='4-1';timestamp=[DateTimeOffset]::UtcNow.ToString('o')}
 switch($scenario){
  'stale' {$state.timestamp=[DateTimeOffset]::UtcNow.AddSeconds(-60).ToString('o')}
  'future' {$state.timestamp=[DateTimeOffset]::UtcNow.AddMinutes(1).ToString('o')}
  'wrong_alias' {$state.stable_path='/dev/ruisheng-other'}
  'wrong_usb' {$state.product_id='9999'}
  'unready' {$state.result='failed'}
 }
 [IO.File]::WriteAllText($statePath,($state|ConvertTo-Json))
 if($scenario -eq 'bad_json'){[IO.File]::WriteAllText($statePath,'{broken')}
 try{Assert-SerialRecoveryHardware -Port '/dev/ruisheng-rs485' -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(30));$outcome='ready'}
 catch{$outcome=$_.Exception.Message}
 $results[$scenario]=$outcome
}
$results|ConvertTo-Json -Compress
""",
    )
    assert result.pop("good") == "ready"
    assert all(value != "ready" for value in result.values()), result


def test_scheduler_uses_opt_in_bounded_single_instance_recovery_mode(shell, tmp_path):
    result = run(
        shell,
        tmp_path,
        load("Register-SerialRecoveryTask", path=INSTALLER)
        + r"""
function Assert-LauncherGuardSource {param($Path)}
function Assert-LauncherAcl {param($Path,$UserSid)}
function Get-ScheduledTask {param($TaskName,$ErrorAction) $null}
function New-ScheduledTaskAction {param($Execute,$Argument) @{runtime=$Execute;arguments=$Argument}}
function New-ScheduledTaskTrigger {param([switch]$Once,$At,$RepetitionInterval,[switch]$AtStartup)
 [pscustomobject]@{once=[bool]$Once;startup=[bool]$AtStartup;interval=$RepetitionInterval.TotalSeconds;Delay=''}
}
function New-ScheduledTaskPrincipal {param($UserId,$LogonType,$RunLevel) @{sid=$UserId;logon=$LogonType;level=$RunLevel}}
function New-ScheduledTaskSettingsSet {param($MultipleInstances,[switch]$StartWhenAvailable,$ExecutionTimeLimit,
 [switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries)
 @{instances=$MultipleInstances;limit_seconds=$ExecutionTimeLimit.TotalSeconds}
}
function Register-ScheduledTask {param($TaskName,$Action,$Trigger,$Principal,$Settings,[switch]$Force)
 $script:registered=@{name=$TaskName;action=$Action;triggers=$Trigger;principal=$Principal;settings=$Settings}
}
Register-SerialRecoveryTask -LauncherPath 'C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1' `
 -RuntimePath 'C:\Program Files\PowerShell\7\pwsh.exe' -UserSid 'S-1-5-21-test'
$script:registered|ConvertTo-Json -Depth 6 -Compress
""",
    )
    assert result["name"] == "Ruisheng-Serial-Gateway-Recovery"
    assert "-RecoverSerialGateway -NoUi -NoBrowser" in result["action"]["arguments"]
    assert "-WindowStyle Hidden" in result["action"]["arguments"]
    assert result["settings"] == {"instances": "IgnoreNew", "limit_seconds": 240}
    assert result["triggers"][0]["interval"] == 60
    assert result["triggers"][1]["startup"] is True
    assert result["principal"]["sid"] == "S-1-5-21-test"
