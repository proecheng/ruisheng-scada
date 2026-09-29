"""Exercise recovery on temporary files only; never touch a local SSH authorization."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "tools/restore_target_ssh_access.ps1"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBh3OfKMf+LJIX3RnTcXS//aYM1Wi/RZEc36Uv334wh2"
CONFIG = """pubkeyauthentication yes
passwordauthentication no
kbdinteractiveauthentication no
authenticationmethods publickey
hostbasedauthentication no
gssapiauthentication no
"""


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def engine(request):
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} unavailable")
    return executable


def run(engine, body):
    # Import only parsed functions, excluding the fixed-target application entry.
    script = f"""
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1')
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('{SCRIPT.as_posix()}',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw ($errors | Out-String) }}
foreach ($node in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]}},$false)) {{
    . ([ScriptBlock]::Create($node.Extent.Text))
}}
{body}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    result = subprocess.run(
        [engine, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        encoding="utf-8",
        timeout=25,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_missing_key_append_is_scoped_and_idempotent(engine):
    result = run(
        engine,
        f"""
$old='# retained comment' + "`n" + 'ssh-ed25519 OTHERKEY unrelated'
$first=Get-RepairedKeyText $old '{KEY}'
$second=Get-RepairedKeyText $first '{KEY}'
@{{first=$first;idempotent=($first -ceq $second)}} | ConvertTo-Json -Compress
""",
    )
    assert result["idempotent"]
    assert result["first"].startswith("# retained comment\nssh-ed25519 OTHERKEY unrelated")
    assert 'from="100.67.229.19" ' + KEY in result["first"]


def test_existing_restricted_key_not_replaced_or_broadened(engine):
    text = f'from="100.67.229.19",no-port-forwarding {KEY} existing'
    result = run(
        engine,
        f"""
$original='{text}'
$value=Get-RepairedKeyText $original '{KEY}'
@{{unchanged=($value -ceq $original)}} | ConvertTo-Json -Compress
""",
    )
    assert result["unchanged"]


def test_encoding_repair_preserves_existing_keys(engine):
    result = run(
        engine,
        f"""
$text='{KEY} existing'
$bytes=[Text.Encoding]::Unicode.GetPreamble() + [Text.Encoding]::Unicode.GetBytes($text)
$decoded=Read-KeyText $bytes
$commented=Get-RepairedKeyText ('  # {KEY}') '{KEY}'
@{{decoded=$decoded;commented=$commented}} | ConvertTo-Json -Compress
""",
    )
    assert result["decoded"] == KEY + " existing"
    assert result["commented"].count(KEY) == 2


@pytest.mark.parametrize(
    "path,expected",
    [
        (
            "__PROGRAMDATA__/ssh/administrators_authorized_keys",
            "C:\\ProgramData\\ssh\\administrators_authorized_keys",
        ),
        (".ssh/authorized_keys", "C:\\Users\\lenovo\\.ssh\\authorized_keys"),
    ],
)
def test_effective_config_selects_only_configured_file(engine, path, expected):
    result = run(
        engine,
        f"""
$config=@'
{CONFIG}authorizedkeysfile {path}
'@
@{{path=(Resolve-KeyFile $config 'C:\\Users\\lenovo')}} | ConvertTo-Json -Compress
""",
    )
    assert result["path"] == expected


@pytest.mark.parametrize(
    "config",
    [
        CONFIG.replace("passwordauthentication no", "passwordauthentication yes")
        + "authorizedkeysfile .ssh/authorized_keys",
        CONFIG.replace("pubkeyauthentication yes", "pubkeyauthentication no")
        + "authorizedkeysfile .ssh/authorized_keys",
        CONFIG + "authorizedkeysfile C:/unrelated/keyfile",
    ],
)
def test_unexpected_policy_or_path_refused_without_changes(engine, config):
    result = run(
        engine,
        f"""
$config=@'
{config}
'@
try {{ Resolve-KeyFile $config 'C:\\Users\\lenovo'; throw 'unexpected_success' }}
catch {{ @{{error=$_.Exception.Message}} | ConvertTo-Json -Compress }}
""",
    )
    assert result["error"] in {
        "ssh_policy_requires_engineer_review",
        "custom_authorized_keys_path_requires_review",
    }


def test_temp_key_permissions_and_backup_restore(engine, tmp_path):
    keyfile = (tmp_path / "authorized_keys").as_posix()
    result = run(
        engine,
        f"""
$path='{keyfile}'
[IO.File]::WriteAllText($path,'original-key-data',[Text.UTF8Encoding]::new($false))
$before=Get-KeySnapshot $path
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Set-KeyAcl $path $sid
$acl=Get-Acl -LiteralPath $path
$permitted=@('S-1-5-18','S-1-5-32-544',$sid)
$unexpected=@($acl.Access | Where-Object {{ $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $permitted }})
[IO.File]::WriteAllText($path,'changed')
Restore-KeySnapshot $before
$after=Get-KeySnapshot $path
@{{protected=$acl.AreAccessRulesProtected;unexpected=$unexpected.Count;bytes_restored=($before.bytes -ceq $after.bytes);acl_restored=($before.sddl -ceq $after.sddl)}} | ConvertTo-Json -Compress
""",
    )
    assert result == {
        "protected": True,
        "unexpected": 0,
        "bytes_restored": True,
        "acl_restored": True,
    }


def test_rollback_removes_only_created_file_and_empty_directory(engine, tmp_path):
    directory = (tmp_path / "created").as_posix()
    result = run(
        engine,
        f"""
$directory='{directory}'; $path=Join-Path $directory 'authorized_keys'
$beforeDir=Get-KeySnapshot $directory; $beforeFile=Get-KeySnapshot $path
[void][IO.Directory]::CreateDirectory($directory)
[IO.File]::WriteAllText($path,'key')
Restore-KeySnapshot $beforeFile; Restore-KeySnapshot $beforeDir
@{{file_exists=(Test-Path -LiteralPath $path);directory_exists=(Test-Path -LiteralPath $directory)}} | ConvertTo-Json -Compress
""",
    )
    assert result == {"file_exists": False, "directory_exists": False}


def test_wrong_target_rejected_before_elevation_or_changes(engine):
    result = run(
        engine,
        """
$env:COMPUTERNAME='TEST-NON-TARGET'
try { Invoke-TargetSshRestore; throw 'unexpected_success' }
catch { @{error=$_.Exception.Message} | ConvertTo-Json -Compress }
""",
    )
    assert result["error"] == "wrong_computer_run_on_WIN-OAUCM8UQUGH"
