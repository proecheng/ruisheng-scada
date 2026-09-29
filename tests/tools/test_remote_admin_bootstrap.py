"""Executable Windows boundary tests; never connect to SSH or production Docker."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "tools" / "remote_admin_bootstrap.ps1"


def ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def shell(request) -> str:
    if os.name != "nt":
        pytest.skip("Windows DPAPI acceptance requires Windows")
    executable = shutil.which(request.param)
    if not executable and request.param == "pwsh.exe":
        bundled = (
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe"
        )
        executable = str(bundled) if bundled.is_file() else None
    if not executable:
        pytest.skip(f"{request.param} unavailable")
    return executable


@pytest.fixture
def vault_parent(shell: str):
    parent = Path(r"C:\ProgramData\Ruisheng") / f"admin-bootstrap-test-{uuid4().hex}"
    result = invoke(
        shell,
        f"Initialize-BootstrapCredentialDirectory {ps_literal(parent)}; @{{ready=$true}}|ConvertTo-Json -Compress",
    )
    assert result["ready"]
    try:
        yield parent
    finally:
        assert parent.parent == Path(r"C:\ProgramData\Ruisheng") and parent.name.startswith(
            "admin-bootstrap-test-"
        )
        shutil.rmtree(parent)


def invoke(
    shell: str, body: str, *, timeout: int = 40, expected_exit: int = 0
) -> dict[str, object]:
    source = f"$ErrorActionPreference='Stop'; . {ps_literal(SCRIPT)} -LibraryOnly\n{body}"
    encoded = base64.b64encode(source.encode("utf-16-le")).decode()
    env = os.environ.copy()
    if Path(shell).name.lower() == "powershell.exe":
        env["PSModulePath"] = os.pathsep.join(
            [
                str(
                    Path(env.get("SystemRoot", r"C:\Windows"))
                    / "System32/WindowsPowerShell/v1.0/Modules"
                ),
                str(
                    Path(env.get("ProgramFiles", r"C:\Program Files")) / "WindowsPowerShell/Modules"
                ),
            ]
        )
    result = subprocess.run(
        [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-OutputFormat",
            "Text",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == expected_exit, result.stderr
    assert not result.stderr
    return json.loads(result.stdout)


def test_dpapi_roundtrip_no_clobber_and_acl(shell: str, vault_parent: Path) -> None:
    tmp_path = vault_parent
    result = invoke(
        shell,
        f"""
$directory={ps_literal(tmp_path / "vault")}
Initialize-BootstrapCredentialDirectory $directory
$path=Join-Path $directory 'site-test.dpapi'
$password=New-BootstrapPassword
$record=[pscustomobject]@{{schema_version=1;operation_id=[guid]::NewGuid().ToString('D');
site_id='site-test';user_name='rs_admin';target='operator@100.1.2.3';expected_computer='TEST';
site_root='C:\\Ruisheng\\site';expected_candidate_id='deploy-test';password=$password}}
$saved=Save-BootstrapCredential $path $record
$bytes=[IO.File]::ReadAllBytes($path)
$before=[Convert]::ToBase64String($bytes)
$again=Read-BootstrapCredential $path
$duplicate=$false
try {{ [void](Save-BootstrapCredential $path $record) }} catch {{ $duplicate=$true }}
$noninteractive=$false
try {{ Show-BootstrapCredential $path }} catch {{ $noninteractive=$true }}
[ordered]@{{roundtrip=($again.password -ceq $password);length=$password.Length;
safe_ascii=($password -cmatch '^[A-Za-z0-9_-]{{32}}$');duplicate_rejected=$duplicate;
unchanged=($before -ceq [Convert]::ToBase64String([IO.File]::ReadAllBytes($path)));
ciphertext_only=([Text.Encoding]::UTF8.GetString($bytes).IndexOf($password) -lt 0);
noninteractive_rejected=$noninteractive}} | ConvertTo-Json -Compress
""",
    )
    assert result == {
        "roundtrip": True,
        "length": 32,
        "safe_ascii": True,
        "duplicate_rejected": True,
        "unchanged": True,
        "ciphertext_only": True,
        "noninteractive_rejected": True,
    }


def test_tampered_dpapi_and_broad_acl_fail_closed(shell: str, vault_parent: Path) -> None:
    tmp_path = vault_parent
    result = invoke(
        shell,
        f"""
$directory={ps_literal(tmp_path / "vault")};Initialize-BootstrapCredentialDirectory $directory
$path=Join-Path $directory 'site-test.dpapi'
[IO.File]::WriteAllBytes($path,[byte[]]@(1,2,3,4));Set-Acl -LiteralPath $path -AclObject (New-BootstrapAcl)
$tampered=$false;try {{ Read-BootstrapCredential $path }} catch {{ $tampered=$true }}
$acl=New-BootstrapAcl -Directory
$rule=New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier('S-1-1-0')),'ReadAndExecute','Allow')
[void]$acl.AddAccessRule($rule)
$other=Join-Path {ps_literal(tmp_path)} 'broad-vault'
if ($PSVersionTable.PSVersion.Major -lt 6) {{[void][IO.Directory]::CreateDirectory($other,$acl)}}
else {{[void][IO.FileSystemAclExtensions]::Create((New-Object IO.DirectoryInfo($other)),$acl)}}
$broad=$false;try {{ Initialize-BootstrapCredentialDirectory $other }} catch {{ $broad=$true }}
[ordered]@{{tampered=$tampered;broad_acl=$broad}}|ConvertTo-Json -Compress
""",
    )
    assert result == {"tampered": True, "broad_acl": True}


def test_credentials_exist_before_dispatch_retries_reuse_and_no_secret_argv(
    shell: str, vault_parent: Path
) -> None:
    tmp_path = vault_parent
    result = invoke(
        shell,
        f"""
$Action='Create';$Approved=$true;$ExpectedCandidateId='deploy-test';$CredentialDirectory={ps_literal(tmp_path / "vault")}
$script:observed=@()
function Invoke-BootstrapNative {{
  param($File,$Arguments,$InputBytes,$Timeout)
$envelope=[Text.Encoding]::UTF8.GetString($InputBytes)|ConvertFrom-Json
  $request=$envelope.request|ConvertFrom-Json
  $saved=Read-BootstrapCredential (Join-Path $CredentialDirectory "$SiteId.dpapi")
  $script:observed+=,[ordered]@{{saved_first=($saved.password -ceq $request.password);
    argv_clean=((-join $Arguments).IndexOf($request.password) -lt 0);
    operation=$request.operation_id;request_limit=([Text.Encoding]::UTF8.GetByteCount($envelope.request) -le 4096);
    command_short=($Arguments[-1].Length -lt 4000);timeout=$Timeout;framed=($InputBytes[-1] -eq 10)}}
  throw 'simulated disconnect with no receipt'
}}
$first=Invoke-RemoteAdminBootstrap|ConvertFrom-Json
$second=Invoke-RemoteAdminBootstrap|ConvertFrom-Json
[ordered]@{{first=$first.status;second=$second.status;count=$script:observed.Count;
  saved_first=(@($script:observed|Where-Object {{-not $_.saved_first}}).Count -eq 0);
  argv_clean=(@($script:observed|Where-Object {{-not $_.argv_clean}}).Count -eq 0);
  same_operation=($script:observed[0].operation -ceq $script:observed[1].operation);
  request_limit=$script:observed[0].request_limit;command_short=$script:observed[0].command_short;
  timeout=$script:observed[0].timeout;framed=$script:observed[0].framed}}|ConvertTo-Json -Compress
""",
    )
    assert result == {
        "first": "unknown",
        "second": "unknown",
        "count": 2,
        "saved_first": True,
        "argv_clean": True,
        "same_operation": True,
        "request_limit": True,
        "command_short": True,
        "timeout": 60000,
        "framed": True,
    }


def test_credential_save_failure_never_dispatches(shell: str, vault_parent: Path) -> None:
    tmp_path = vault_parent
    result = invoke(
        shell,
        f"""
$Action='Create';$Approved=$true;$ExpectedCandidateId='deploy-test';$CredentialDirectory={ps_literal(tmp_path / "vault")}
$script:dispatched=$false
function Save-BootstrapCredential {{ throw 'simulated DPAPI or ACL failure' }}
function Invoke-BootstrapNative {{ $script:dispatched=$true;throw 'must not dispatch' }}
$failed=$false;try {{ Invoke-RemoteAdminBootstrap }} catch {{ $failed=$true }}
[ordered]@{{rejected=$failed;dispatched=$script:dispatched}}|ConvertTo-Json -Compress
""",
    )
    assert result == {"rejected": True, "dispatched": False}


def test_short_loader_real_native_transport_and_bounds(shell: str) -> None:
    result = invoke(
        shell,
        f"""
$command=ConvertTo-BootstrapRemoteCommand
$envelope=[ordered]@{{script='[ordered]@{{status="loader_ok";length=$BootstrapRequestJson.Length}}|ConvertTo-Json -Compress';request='test request'}}
$bytes=[Text.Encoding]::UTF8.GetBytes(($envelope|ConvertTo-Json -Compress))
$reply=Invoke-BootstrapNative {ps_literal(shell)} @('-NoLogo','-NoProfile','-NonInteractive','-OutputFormat','Text','-EncodedCommand',$command) $bytes 10000
$decoded=$reply.Output|ConvertFrom-Json
$overflow=$false
$spam=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes('[Console]::Write("x"*70000)'))
try {{ [void](Invoke-BootstrapNative {ps_literal(shell)} @('-NoProfile','-EncodedCommand',$spam) @() 10000) }} catch {{$overflow=$true}}
$timedout=$false
$hang=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes('Start-Sleep -Seconds 10'))
$clock=[Diagnostics.Stopwatch]::StartNew()
try {{ [void](Invoke-BootstrapNative {ps_literal(shell)} @('-NoProfile','-EncodedCommand',$hang) @() 300) }} catch {{$timedout=$true}}
[ordered]@{{status=$decoded.status;length=$decoded.length;short=($command.Length -lt 4000);
overflow=$overflow;timeout=$timedout;bounded=($clock.ElapsedMilliseconds -lt 4000)}}|ConvertTo-Json -Compress
""",
    )
    assert result == {
        "status": "loader_ok",
        "length": 12,
        "short": True,
        "overflow": True,
        "timeout": True,
        "bounded": True,
    }


def test_generated_remote_script_parses_and_wrong_machine_stops_before_docker(shell: str) -> None:
    result = invoke(
        shell,
        """
$remote=Get-BootstrapRemoteScript;$tokens=$null;$errors=$null
[void][Management.Automation.Language.Parser]::ParseInput($remote,[ref]$tokens,[ref]$errors)
$BootstrapRequestJson=([ordered]@{schema_version=1;action='Create';operation_id=[guid]::NewGuid().ToString('D');
site_id='site-test';user_name='rs_admin';target='wrong@100.0.0.1';expected_computer='WRONG';
site_root='C:\\does-not-exist';expected_candidate_id='deploy-test';password='A'*32}|ConvertTo-Json -Compress)
$reply=& ([scriptblock]::Create($remote))|ConvertFrom-Json
$envelope=[ordered]@{script=$remote;request=$BootstrapRequestJson}|ConvertTo-Json -Compress
[ordered]@{parsed=($errors.Count -eq 0);status=$reply.status;
 bounded=([Text.Encoding]::UTF8.GetByteCount($envelope) -le 65536)}|ConvertTo-Json -Compress
""",
    )
    assert result == {"parsed": True, "status": "unknown", "bounded": True}


def test_loader_processes_line_frame_before_ssh_eof(shell: str) -> None:
    result = invoke(
        shell,
        f"""
$start=New-Object Diagnostics.ProcessStartInfo
$start.FileName={ps_literal(shell)}
$start.Arguments='-NoLogo -NoProfile -NonInteractive -OutputFormat Text -EncodedCommand '+(ConvertTo-BootstrapRemoteCommand)
$start.UseShellExecute=$false;$start.CreateNoWindow=$true
$start.RedirectStandardInput=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
$child=[Diagnostics.Process]::Start($start)
try {{
  $stdout=$child.StandardOutput.ReadLineAsync();$stderr=$child.StandardError.ReadToEndAsync()
  $envelope=@{{script='[Console]::Out.WriteLine(''{{"framed":true}}'')';request='bounded request'}}
  $bytes=[Text.Encoding]::UTF8.GetBytes(($envelope|ConvertTo-Json -Compress)+"`n")
  $child.StandardInput.BaseStream.Write($bytes,0,5);$child.StandardInput.BaseStream.Flush()
  $child.StandardInput.BaseStream.Write($bytes,5,$bytes.Length-5);$child.StandardInput.BaseStream.Flush()
  $processed=$stdout.Wait(8000)
  $child.StandardInput.Close()
  $completed=$child.WaitForExit(8000)
  if(-not $completed){{$child.Kill();$child.WaitForExit()}}
  @{{processed_before_eof=$processed;completed=$completed;exit_code=$child.ExitCode;output=$stdout.GetAwaiter().GetResult();
    stderr=$stderr.GetAwaiter().GetResult()}}|ConvertTo-Json -Compress
}}
finally {{if(-not $child.HasExited){{$child.Kill()}};$child.Dispose()}}
""",
    )
    assert result == {
        "processed_before_eof": True,
        "completed": True,
        "exit_code": 0,
        "output": '{"framed":true}',
        "stderr": "",
    }


def test_static_public_key_and_no_hidden_privilege_paths() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for guard in [
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        "IdentitiesOnly=yes",
        "PreferredAuthentications=publickey",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "release-allowed-signers",
        "release-key-fingerprint",
        "ruisheng-candidate-v1",
        "remote-support",
        ".remote-maintenance.lock",
    ]:
        assert guard in source
    for forbidden in [
        "Set-Clipboard",
        "MessageBox",
        "Invoke-Expression",
        "docker cp",
        "seed",
        "-EncodedArguments",
    ]:
        assert forbidden not in source
    assert "[IO.File]::Open($path, 'CreateNew'".lower() in source.lower()


def test_signature_command_rejects_shell_metacharacters_before_dispatch(shell: str) -> None:
    result = invoke(
        shell,
        r"""
$ast=[Management.Automation.Language.Parser]::ParseInput((Get-BootstrapRemoteScript),[ref]$null,[ref]$null)
$definition=$ast.Find({param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Invoke-BootstrapSignatureCheck'
},$true)
. ([scriptblock]::Create($definition.Extent.Text))
$script:dispatched=$false
function Invoke-Fixed {$script:dispatched=$true;throw 'must_not_dispatch'}
$rejected=0
foreach($suffix in @('with space','%PATH%','a&b','a|b','a<b','a>b','a!b','a"b',"a`nb")){
  try{Invoke-BootstrapSignatureCheck 'C:\Windows\System32\OpenSSH\ssh-keygen.exe' `
    'C:\ProgramData\Ruisheng\trust\release-allowed-signers' "C:\Ruisheng\$suffix" 'C:\Ruisheng\SHA256SUMS.sig'}
  catch{$rejected++}
}
@{rejected=$rejected;dispatched=$script:dispatched}|ConvertTo-Json -Compress
""",
    )
    assert result == {"rejected": 9, "dispatched": False}


def test_receipt_action_schema_and_types_are_closed(shell: str) -> None:
    result = invoke(
        shell,
        r"""
$operation=[guid]::NewGuid().ToString('D')
$rejected=0
foreach($case in @('wrong-action','extra','wrong-operation','wrong-site','wrong-type')){
  $receipt=[ordered]@{operation_id=$operation;site_id='site-test';user_name='rs_admin';status='created'}
  if($case -eq 'extra'){$receipt.password='never-forward'}
  if($case -eq 'wrong-operation'){$receipt.operation_id=[guid]::NewGuid().ToString('D')}
  if($case -eq 'wrong-site'){$receipt.site_id='site-other'}
  if($case -eq 'wrong-type'){$receipt.status=@('created')}
  $requested=if($case -eq 'wrong-action'){'Status'}else{'Create'}
  try{Assert-BootstrapReceipt ([pscustomobject]$receipt) $requested $operation 'site-test' 'rs_admin'}
  catch{$rejected++}
}
@{rejected=$rejected}|ConvertTo-Json -Compress
""",
    )
    assert result == {"rejected": 5}


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("planned", 0),
        ("created", 0),
        ("confirmed", 0),
        ("empty", 0),
        ("rejected", 2),
        ("conflict", 2),
        ("unknown", 3),
    ],
)
def test_entrypoint_returns_observable_status_exit_code(
    shell: str, status: str, exit_code: int
) -> None:
    receipt = {
        "operation_id": str(uuid4()),
        "site_id": "site-test",
        "user_name": "rs_admin",
        "status": status,
    }
    result = invoke(
        shell,
        f"function Invoke-RemoteAdminBootstrap {{ {ps_literal(json.dumps(receipt))} }}\n"
        "exit (Invoke-BootstrapEntryPoint)",
        expected_exit=exit_code,
    )
    assert result == receipt


def test_unapproved_create_and_credential_identity_conflict_do_not_dispatch(
    shell: str, vault_parent: Path
) -> None:
    result = invoke(
        shell,
        f"$CredentialDirectory={ps_literal(vault_parent / 'vault')}\n"
        + r"""
$Action='Create';$ExpectedCandidateId='deploy-test';$Approved=$false
$script:calls=0
function Invoke-BootstrapNative {$script:calls++;throw 'disconnect'}
$denied=$false;try{Invoke-RemoteAdminBootstrap}catch{$denied=$true}
$Approved=$true
[void](Invoke-RemoteAdminBootstrap)
$UserName='other_admin';$conflict=$false
try{Invoke-RemoteAdminBootstrap}catch{$conflict=$true}
@{unapproved=$denied;conflict=$conflict;dispatches=$script:calls}|ConvertTo-Json -Compress
""",
    )
    assert result == {"unapproved": True, "conflict": True, "dispatches": 1}


def test_real_entrypoint_rejects_unapproved_create_with_exit_two(shell: str) -> None:
    result = invoke(
        shell,
        f"""
$reply=Invoke-BootstrapNative {ps_literal(shell)} @('-NoLogo','-NoProfile','-NonInteractive',
  '-ExecutionPolicy','Bypass','-File',{ps_literal(SCRIPT)},'-Action','Create') @() 10000
@{{exit_code=$reply.ExitCode;stderr=$reply.HasError;stdout=$reply.Output}}|ConvertTo-Json -Compress
""",
    )
    assert result == {"exit_code": 2, "stderr": True, "stdout": ""}


def test_signed_remote_guard_with_real_test_key_and_isolated_docker_double(
    shell: str, vault_parent: Path
) -> None:
    """Only the OS administrator token and Docker are doubles; signature bytes are real."""
    result = invoke(
        shell,
        f"$fixture={ps_literal(vault_parent)}\n"
        + r"""
$candidate=Join-Path $fixture 'candidate';Initialize-BootstrapCredentialDirectory $candidate
$trustFixture=Join-Path $fixture 'trust';Initialize-BootstrapCredentialDirectory $trustFixture
$siteFixture=Join-Path $fixture 'site';Initialize-BootstrapCredentialDirectory $siteFixture
$stateFixture=Join-Path $siteFixture '.remote-maintenance-state';Initialize-BootstrapCredentialDirectory $stateFixture
$key=Join-Path $fixture 'throwaway-key'
$keygen='C:\Windows\System32\OpenSSH\ssh-keygen.exe'
$generated=Invoke-BootstrapNative $keygen @('-q','-t','ed25519','-N','','-f',$key) @() 10000
if($generated.ExitCode -ne 0){throw 'test_key_generation_failed'}
$pub=([IO.File]::ReadAllText("$key.pub").Trim() -split ' ')[1]
$utf8=New-Object Text.UTF8Encoding($false)
$signers=Join-Path $trustFixture 'release-allowed-signers'
[IO.File]::WriteAllText($signers,"ruisheng-release ssh-ed25519 $pub`n",$utf8)
$fp=Invoke-BootstrapNative $keygen @('-l','-E','sha256','-f',$signers) @() 10000
if($fp.ExitCode -ne 0 -or $fp.Output -notmatch '^256 (SHA256:[A-Za-z0-9+/]{43}) '){throw 'test_fingerprint_failed'}
[IO.File]::WriteAllText((Join-Path $trustFixture 'release-key-fingerprint'),"$($Matches[1])`n",$utf8)
[IO.File]::WriteAllText((Join-Path $trustFixture 'entitlement-site-id'),"site-test`n",$utf8)
[IO.File]::WriteAllText((Join-Path $fixture 'verifier.ps1'),'fixture-only',$utf8)
$manifest=[ordered]@{schema_version=2;candidate_id='deploy-test';source_commit=('b'*40);logical_identity=('sha256:'+('c'*64));
images=@(@{component='api';image_id=('sha256:'+('a'*64));candidate_reference='ruisheng-candidate/api:deploy-test'},
@{component='postgres';image_id=('sha256:'+('d'*64));candidate_reference='ruisheng-candidate/postgres:deploy-test'})}
$manifestPath=Join-Path $candidate 'MANIFEST.json'
[IO.File]::WriteAllText($manifestPath,($manifest|ConvertTo-Json -Depth 5 -Compress),$utf8)
$hash=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$sumsPath=Join-Path $candidate 'SHA256SUMS'
[IO.File]::WriteAllText($sumsPath,"$hash  MANIFEST.json`n",$utf8)
$signed=Invoke-BootstrapNative $keygen @('-Y','sign','-f',$key,'-n','ruisheng-candidate-v1',$sumsPath) @() 10000
if($signed.ExitCode -ne 0){throw 'test_signing_failed'}
$signatureCopy=Join-Path $candidate 'signed-copy.sig'
[IO.File]::WriteAllBytes($signatureCopy,[IO.File]::ReadAllBytes("$sumsPath.sig"))
$active=@{schema_version=1;site_root=$siteFixture;candidate_id='deploy-test';candidate_root='C:\Ruisheng\candidates\deploy-test';
source_commit=$manifest.source_commit;logical_identity=$manifest.logical_identity}
[IO.File]::WriteAllText((Join-Path $stateFixture 'active-release.json'),($active|ConvertTo-Json -Compress),$utf8)
$env:SSH_CONNECTION='127.0.0.1 2000 100.1.2.3 22'
$injection=@'
$script:realRead=${function:Read-ProtectedBytes}
$script:realFixed=${function:Invoke-Fixed}
function Map-FixturePath {
  param([string]$Value)
  if($Value -eq 'C:\Ruisheng\candidates\deploy-test\SHA256SUMS.sig'){return $signatureCopy}
  if($Value.StartsWith('C:\Ruisheng\candidates\deploy-test')){return $Value.Replace('C:\Ruisheng\candidates\deploy-test',$candidate)}
  if($Value.StartsWith('C:\ProgramData\Ruisheng\trust')){return $Value.Replace('C:\ProgramData\Ruisheng\trust',$trustFixture)}
  if($Value -eq 'C:\ProgramData\Ruisheng\bin\target_entitlement_verifier.ps1'){return (Join-Path $fixture 'verifier.ps1')}
  return $Value
}
function Read-ProtectedBytes {
  param([string]$Path,[int]$Limit=65536)
  return & $script:realRead (Map-FixturePath $Path) $Limit
}
function Invoke-Fixed {
  param([string]$File,[string[]]$Arguments,[byte[]]$Bytes=@())
  if($File -eq 'C:\Windows\System32\cmd.exe'){
    if(($Arguments[0..3] -join ' ') -cne '/d /q /v:off /c'){throw 'test_unsafe_cmd_flags'}
    $mapped=$Arguments[-1].Replace('C:\Ruisheng\candidates\deploy-test\SHA256SUMS.sig',$signatureCopy).
      Replace('C:\Ruisheng\candidates\deploy-test',$candidate).
      Replace('C:\ProgramData\Ruisheng\trust',$trustFixture)
    return & $script:realFixed $File @('/d','/q','/v:off','/c',$mapped) $Bytes
  }
  if($File -eq 'C:\Windows\System32\OpenSSH\ssh-keygen.exe'){
    $mapped=@($Arguments|ForEach-Object {Map-FixturePath $_})
    return & $script:realFixed $File $mapped $Bytes
  }
  if($File.EndsWith('powershell.exe')){
    $status=if($case -eq 'entitlement'){'denied'}else{'authorized'}
    return [pscustomobject]@{ExitCode=0;HasError=$false;Output=(@{ok=($status -eq 'authorized');status=$status;
      feature='remote-support';valid_until=[DateTimeOffset]::UtcNow.AddHours(1).ToString('o')}|ConvertTo-Json -Compress)}
  }
  throw 'test_unexpected_native'
}
function Get-SafeContainer {
  param([string]$Name)
  if($Name -eq 'ruisheng-postgres'){
    return [pscustomobject]@{Image=('sha256:'+('d'*64));State=@{Running=$true};
      NetworkSettings=@{Networks=@{app=@{Aliases=@('postgres');IPAddress='172.23.0.2'}}}}
  }
  return [pscustomobject]@{Id=('e'*64);Image=if($case -eq 'image'){'sha256:'+('f'*64)}else{'sha256:'+('a'*64)};
    State=@{Running=$true};Config=@{Image='ruisheng-candidate/api:deploy-test';
      Env=@('API_ENV=prod','API_DB_URL=postgresql+asyncpg://ruisheng_api:test-only@postgres:5432/ruisheng')};
    Mounts=@();HostConfig=@{Privileged=$false;
      Devices=if($case -eq 'device-mapping'){@(@{PathOnHost='/dev/test'})}else{$null};
      ExtraHosts=if($case -eq 'extra-hosts'){@('postgres:172.23.0.9')}else{$null}};
    NetworkSettings=@{Networks=@{app=@{}}}}
}
function Invoke-FixedDocker {
  param([string[]]$Arguments,[byte[]]$Bytes=@())
  if($Arguments -contains '/usr/bin/getent'){
    $address=if($case -eq 'db-address'){'172.23.0.9'}else{'172.23.0.2'}
    $output="$address STREAM postgres`n$address DGRAM`n$address RAW`n"
    if($case -eq 'db-multiple'){$output+="172.23.0.9 STREAM postgres`n"}
    return [pscustomobject]@{ExitCode=0;HasError=$false;Output=$output}
  }
  if($Arguments[-1] -eq 'ruisheng_api.healthcheck'){
    $ready=if($case -eq 'health'){'not_ready'}else{'ready'}
    return [pscustomobject]@{ExitCode=if($case -eq 'health'){1}else{0};HasError=$false;
      Output=(@{status=$ready;database='ready';redis='ready';service='ready'}|ConvertTo-Json -Compress)}
  }
  if($Arguments[-1] -cne 'ruisheng_api.admin_bootstrap'){throw 'test_unexpected_docker_command'}
  $script:cliCalled=$true
  try{$stream=[IO.File]::Open($lockPath,'Open','Read','None');$stream.Dispose();$script:locked=$false}
  catch{$script:locked=$true}
  $cli=[Text.Encoding]::UTF8.GetString($Bytes)|ConvertFrom-Json
  $status=if($case -eq 'wrong-receipt'){'confirmed'}elseif($case -eq 'unknown-receipt'){'unknown'}else{'created'}
  return [pscustomobject]@{ExitCode=if($case -eq 'unknown-receipt'){3}else{0};HasError=$false;Output=(@{operation_id=$cli.operation_id;
    site_id=$cli.site_id;user_name=$cli.user_name;status=$status}|ConvertTo-Json -Compress)}
}
'@
$remote=Get-BootstrapRemoteScript
$adminCheck="if (-not `$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'bootstrap_target_not_admin' }"
if(-not $remote.Contains($adminCheck)){throw 'test_admin_hook_not_found'}
$remote=$remote.Replace($adminCheck,'# Administrator token doubled only in this isolated test.')
$makeMarker={
  $operation=[string]$request.operation_id
  $maintenance=[ordered]@{schema_version=1;operation_id=$operation;site_root=$siteFixture;status='active';
    source_identity=('sha256:'+('a'*64));candidate_identity=('sha256:'+('b'*64));
    source_head='0012_alarm_notification_runtime';target_head='0013_serial_polling_profile';
    journal_path=(Join-Path $stateFixture "full-upgrade-$operation.json");updated_at='2000-01-01T00:00:00.0000000+00:00'}
  [IO.File]::WriteAllText((Join-Path $stateFixture 'full-upgrade-maintenance.json'),($maintenance|ConvertTo-Json -Compress))
}
$remote=$remote.Replace('$heldLock.Flush($true)', '$heldLock.Flush($true); if($case -eq ''maintenance-race''){ & $makeMarker }')
$marker='try {'+"`n"+'  if ([Text.Encoding]::UTF8.GetByteCount($BootstrapRequestJson)'
if(-not $remote.Contains($marker)){throw 'test_runtime_hook_not_found'}
$remote=$remote.Replace($marker,$injection+"`n"+$marker)
$remote=$remote.Replace('# No raw exception, Docker environment, SQL parameters or secret input may escape.', '$script:testFailure = $_.Exception.Message')
$results=@()
foreach($case in @('success','candidate','site','image','signature','manifest','entitlement',
  'device-mapping','extra-hosts','db-address','db-multiple','health','wrong-receipt','unknown-receipt','maintenance','maintenance-race')){
  $script:cliCalled=$false;$script:locked=$false;$script:testFailure=''
  $request=[ordered]@{schema_version=1;action='Create';operation_id=[guid]::NewGuid().ToString('D');
    site_id=if($case -eq 'site'){'site-other'}else{'site-test'};user_name='rs_admin';target="$($env:USERNAME)@100.1.2.3";
    expected_computer=$env:COMPUTERNAME;site_root=$siteFixture;
    expected_candidate_id=if($case -eq 'candidate'){'deploy-wrong'}else{'deploy-test'};password='T'*32}
  $BootstrapRequestJson=$request|ConvertTo-Json -Compress
  if($case -eq 'maintenance'){ & $makeMarker }
  if($case -eq 'signature'){[IO.File]::AppendAllText($sumsPath,"x`n",$utf8)}
  if($case -eq 'manifest'){[IO.File]::AppendAllText($manifestPath,' ',$utf8)}
  $reply=& ([scriptblock]::Create($remote))|ConvertFrom-Json
  $results+=,[ordered]@{case=$case;status=$reply.status;cli=$script:cliCalled;locked=$script:locked;failure=$script:testFailure;
    released=(-not (Test-Path -LiteralPath (Join-Path $stateFixture '.remote-maintenance.lock')))}
  if($case -eq 'signature'){[IO.File]::WriteAllText($sumsPath,"$hash  MANIFEST.json`n",$utf8)}
  if($case -eq 'manifest'){[IO.File]::WriteAllText($manifestPath,($manifest|ConvertTo-Json -Depth 5 -Compress),$utf8)}
  if($case -in @('wrong-receipt','unknown-receipt')){
    Remove-Item -LiteralPath (Join-Path $stateFixture '.remote-maintenance.lock') -Force
  }
  if($case -in @('maintenance','maintenance-race')){
    Remove-Item -LiteralPath (Join-Path $stateFixture 'full-upgrade-maintenance.json')
  }
}
@{results=$results}|ConvertTo-Json -Depth 6 -Compress
""",
        timeout=90,
    )
    assert result["results"][0]["failure"] == "", result["results"][0]["failure"]
    cases = {
        case["case"]: {key: value for key, value in case.items() if key != "failure"}
        for case in result["results"]
    }
    assert cases["success"] == {
        "case": "success",
        "status": "created",
        "cli": True,
        "locked": True,
        "released": True,
    }
    for name in [
        "candidate",
        "site",
        "image",
        "signature",
        "manifest",
        "entitlement",
        "extra-hosts",
        "device-mapping",
        "db-address",
        "db-multiple",
        "health",
        "maintenance",
        "maintenance-race",
    ]:
        assert cases[name] == {
            "case": name,
            "status": "unknown",
            "cli": False,
            "locked": False,
            "released": True,
        }
    for name in ["wrong-receipt", "unknown-receipt"]:
        assert cases[name] == {
            "case": name,
            "status": "unknown",
            "cli": True,
            "locked": True,
            "released": False,
        }
