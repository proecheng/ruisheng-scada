"""Execute updater proofs against owned, retained, network-isolated Timescale assets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from ipaddress import ip_network
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).parents[2]
UPDATER = ROOT / "tools" / "remote_full_upgrade" / "target-updater.ps1"
SOURCE_IMAGE = "sha256:50a2abfa8bad354f4bc1567c6edf7426586fd99ee8cd8982bbaee157a460c6b1"
SOURCE_API = "sha256:33d948f176ddc7d47ec8840d6baff4892bb9b95014444590d3ae7f1dc2989ce3"
REDIS_IMAGE = "sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf"
PREVIOUS = "0012_alarm_notification_runtime"
HEAD = "0013_serial_polling_profile"
LABEL = "com.ruisheng.test=bounded-schema-recovery"
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_test_database_target() -> None:
    """Override the inherited developer database fixture; only owned Docker assets exist here."""


@pytest.fixture(autouse=True)
def require_dev_database() -> None:
    """Never connect to an existing developer or deployed database."""


def docker(*arguments: str, input_text: str | None = None, timeout: int = 180):
    return subprocess.run(
        ["docker", *arguments],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def checked_docker(*arguments: str, input_text: str | None = None, timeout: int = 180) -> str:
    result = docker(*arguments, input_text=input_text, timeout=timeout)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def sql(container: str, statement: str, *, user: str = "ruisheng_admin") -> str:
    return checked_docker(
        "exec",
        "-i",
        container,
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        user,
        "-d",
        "ruisheng",
        input_text=statement,
    )


def wait_ready(container: str, user: str = "ruisheng_admin") -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = docker(
            "exec",
            container,
            "psql",
            "-h",
            "127.0.0.1",
            "-X",
            "-Atqc",
            "SELECT 1",
            "-U",
            user,
            "-d",
            "postgres",
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == "1":
            return
        time.sleep(0.2)
    pytest.fail("owned Timescale container did not become ready")


def ps_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _test_dacl_helpers() -> str:
    # Test-owned ACL preparation/faults only. Core's Set-Acl can request SACL
    # privilege even for an Access-only object; its native ACL API writes the
    # modified DACL directly. Production ACL validation stays unchanged.
    return r"""
function Get-TestAccessAcl {
  param($Item)
  $sections=[Security.AccessControl.AccessControlSections]::Access
  if ($PSVersionTable.PSEdition -eq 'Core') { return [IO.FileSystemAclExtensions]::GetAccessControl($Item,$sections) }
  return $Item.GetAccessControl($sections)
}
function Set-TestAccessAcl {
  param($Item,$Acl)
  if ($PSVersionTable.PSEdition -eq 'Core') { [IO.FileSystemAclExtensions]::SetAccessControl($Item,$Acl) }
  elseif ($Item.PSIsContainer) { Set-DirectoryAccessControl $Item.FullName $Acl }
  else { Set-FileAccessControl $Item.FullName $Acl }
}
function Reset-TestAccessRules {
  param($Item,[string[]]$Sids)
  if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'test_acl_target_linked' }
  $acl=Get-TestAccessAcl $Item
  $acl.SetAccessRuleProtection($true,$false)
  foreach ($oldRule in @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object { $null -ne $_ })) { [void]$acl.RemoveAccessRuleSpecific($oldRule) }
  $inheritance=if ($Item.PSIsContainer) { 'ContainerInherit,ObjectInherit' } else { 'None' }
  foreach ($sid in $Sids) {
    $rule=New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl',$inheritance,'None','Allow')
    [void]$acl.AddAccessRule($rule)
  }
  Set-TestAccessAcl $Item $acl
}
"""


@dataclass
class ProofRun:
    source: str
    operation: str
    directory: Path
    executable: str = "pwsh.exe"
    setup: str = ""
    lock_names: tuple[str, str] = ("shared", "legacy")

    @property
    def clone(self) -> str:
        return f"ruisheng-restore-{self.operation}"

    @property
    def restore_user(self) -> str:
        return "rs_restore_" + self.operation.replace("-", "")

    @property
    def journal_path(self) -> Path:
        return self.directory / "journal.json"

    def powershell(self, body: str, *, timeout: int = 900):
        self.directory.mkdir(parents=True, exist_ok=True)
        source = f"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile({ps_literal(UPDATER)},[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'updater_parse_failed' }}
foreach ($node in $ast.EndBlock.Statements) {{
  if ($node -is [System.Management.Automation.Language.FunctionDefinitionAst]) {{
    . ([ScriptBlock]::Create($node.Extent.Text))
  }}
}}
Set-Item Function:Invoke-RealDockerText ${{function:Invoke-DockerText}}
Set-Item Function:Assert-RealBoundedDatabaseIdentity ${{function:Assert-BoundedDatabaseIdentity}}
function Assert-BoundedDatabaseIdentity {{
  param($Journal,[switch]$AllowStopped)
  if ($null -ne $Journal.migration.dependencies) {{
    return Assert-RealBoundedDatabaseIdentity $Journal -AllowStopped:$AllowStopped
  }}
  Assert-LocksOwned
  $owned=@(Invoke-RealDockerText @('inspect',{ps_literal(self.source)}) | ConvertFrom-Json)[0]
  if ([string]$owned.Image -cne '{SOURCE_IMAGE}' -or
      [string]$owned.Config.Labels.'com.ruisheng.test' -cne 'bounded-schema-recovery') {{ throw 'unowned_test_database' }}
  return $true
}}
function Invoke-DockerText {{
  param([string[]]$Arguments,[int]$TimeoutSeconds=120)
  $mapped=@($Arguments | ForEach-Object {{
    if ($_ -ceq 'ruisheng-postgres') {{ {ps_literal(self.source)} }}
    elseif ($_.StartsWith('ruisheng-postgres:')) {{ {ps_literal(self.source + ":")} + $_.Substring(18) }}
    else {{ $_ }}
  }})
  Invoke-RealDockerText -Arguments $mapped -TimeoutSeconds $TimeoutSeconds
}}
$OperationId={ps_literal(self.operation)}; $LeaseSeconds=1200; $AcquiredLocks=New-Object Collections.ArrayList
$ProcessStartedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
$BoundedSourceHead='{PREVIOUS}'; $BoundedTargetHead='{HEAD}'
$StateDirectory={ps_literal(self.directory)}; $BackupDirectory=Join-Path $StateDirectory 'backup'
$AuditDirectory=Join-Path $StateDirectory 'audit'; $JournalPath=Join-Path $StateDirectory 'journal.json'
if (-not (Test-Path $JournalPath)) {{
  $testDirectory=Get-Item -LiteralPath $StateDirectory
  $testAcl=if ($PSVersionTable.PSEdition -eq 'Core') {{
    [IO.FileSystemAclExtensions]::GetAccessControl($testDirectory,[Security.AccessControl.AccessControlSections]::Access)
  }} else {{ $testDirectory.GetAccessControl([Security.AccessControl.AccessControlSections]::Access) }}
  $testSid=[Security.Principal.WindowsIdentity]::GetCurrent().User
  $testRule=New-Object Security.AccessControl.FileSystemAccessRule($testSid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
  $testAcl.AddAccessRule($testRule)
  Set-DirectoryAccessControl $StateDirectory $testAcl
  Set-RestrictedTree $StateDirectory
}}
try {{
$testLockNames=@{{shared={ps_literal(self.lock_names[0])};legacy={ps_literal(self.lock_names[1])}}}
foreach ($lockFile in @('shared','legacy')) {{
  $lockPath=Join-Path $StateDirectory ($lockFile+'.lock')
  Acquire-LeasedLock $lockPath $testLockNames[$lockFile]
}}
if (Test-Path $JournalPath) {{ $journal=Convert-UpgradeJson (Get-Content -Raw -Encoding UTF8 $JournalPath) }}
else {{
  $journal=@{{ previous_release=@{{logical_identity=('sha256:'+('a'*64))}};
    candidate=@{{logical_identity=('sha256:'+('b'*64))}}; backup=$null;
    migration=@{{phase='writers_fenced';updated_at='';source_images=@{{postgres='{SOURCE_IMAGE}'}}}} }}
}}
{self.setup}
{body}
}}
finally {{ Release-Locks }}
"""
        encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
        env = os.environ.copy()
        if self.executable == "powershell.exe":
            env["PSModulePath"] = str(
                Path(env.get("SystemRoot", r"C:\Windows"))
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "Modules"
            )
        return subprocess.run(
            [
                self.executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )

    def run(self, body: str, *, timeout: int = 900) -> str:
        result = self.powershell(body, timeout=timeout)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    def fingerprint(self, container: str, *, mutation: str = "") -> str:
        statement = self.run("Get-DatabaseFingerprintSql")
        user = self.restore_user if container == self.clone else "ruisheng_admin"
        result = checked_docker(
            "exec",
            "-i",
            container,
            "bash",
            "-c",
            f"set -euo pipefail; psql -X -qAt -v ON_ERROR_STOP=1 -U {user} -d ruisheng | sha256sum",
            input_text="BEGIN;\n" + mutation + "\n" + statement + "\nROLLBACK;\n",
        )
        return result.split()[0]

    def stop_owned_clone(self) -> None:
        result = docker("container", "inspect", self.clone)
        if result.returncode != 0:
            return
        info = json.loads(result.stdout)[0]
        assert info["Config"]["Labels"]["com.ruisheng.upgrade.operation"] == self.operation
        assert info["Image"] == SOURCE_IMAGE
        if info["State"]["Running"]:
            checked_docker("stop", "--time", "30", self.clone)

    def expire_interrupted_locks(self) -> None:
        expected_names = dict(zip(("shared", "legacy"), self.lock_names, strict=True))
        records = []
        for name in ("shared", "legacy"):
            path = self.directory / f"{name}.lock"
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                assert record["operation_id"] == self.operation
                assert record["lock_name"] == expected_names[name]
                records.append((path, record))
        if not records:
            return
        source = f"""
$ErrorActionPreference='Stop';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({ps_literal(UPDATER)},[ref]$tokens,[ref]$errors)
foreach ($function in @($ast.EndBlock.Statements | Where-Object {{ $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -cin @('Test-MatchingLockProcess','Convert-UpgradeJson') }})) {{
  . ([ScriptBlock]::Create($function.Extent.Text))
}}
foreach ($record in (Convert-UpgradeJson {ps_literal(json.dumps([record for _, record in records]))})) {{
  if (Test-MatchingLockProcess $record) {{ throw 'owned_parent_still_running' }}
}}
"""
        result = subprocess.run(
            ["pwsh.exe", "-NoProfile", "-NonInteractive", "-Command", source],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for path, record in records:
            shutil.copyfile(path, path.with_name(path.name + f".before-expiry-{uuid4()}"))
            record["expires_at"] = "2000-01-01T00:00:00.0000000+00:00"
            path.write_text(json.dumps(record), encoding="utf-8")


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize(
    "lock_names", [("shared", "legacy"), ("shared-maintenance", "legacy-hotfix")]
)
def test_proof_helper_reuses_configured_locks_after_parent_death(executable, lock_names):
    # No Docker calls: exercise the exact helper and real lease/process checks
    # in a new directory, preserving the dead process's original lock receipts.
    operation = str(uuid4())
    proof = ProofRun(
        "unused-test-source",
        operation,
        ROOT / "tmp-test-logs" / f"bounded-locks-{operation}",
        executable,
        setup="function Invoke-DockerText { throw 'unexpected_docker_call' }",
        lock_names=lock_names,
    )
    crashed = proof.powershell(
        "Write-JsonAtomic $JournalPath $journal; Stop-Process -Id $PID -Force", timeout=30
    )
    assert crashed.returncode != 0
    for filename, expected in zip(("shared", "legacy"), lock_names, strict=True):
        record = json.loads((proof.directory / f"{filename}.lock").read_text(encoding="utf-8"))
        assert record["operation_id"] == proof.operation
        assert record["lock_name"] == expected
    proof.expire_interrupted_locks()
    observed = proof.run(
        """
Assert-LocksOwned
$names=@(foreach ($file in @('shared','legacy')) {
  (Convert-UpgradeJson (Get-Content -LiteralPath (Join-Path $StateDirectory ($file+'.lock')) -Raw -Encoding UTF8)).lock_name
})
ConvertTo-Json -InputObject $names -Compress
""",
        timeout=30,
    )
    assert json.loads(observed) == list(lock_names)
    assert not (proof.directory / "shared.lock").exists()
    assert not (proof.directory / "legacy.lock").exists()
    assert len(list(proof.directory.glob("*.lock.before-expiry-*"))) == 2


@pytest.fixture(scope="module")
def source_database() -> Iterator[str]:
    if not shutil.which("docker") or not shutil.which("pwsh.exe"):
        pytest.skip("Docker and PowerShell are required")
    if docker("info", "--format", "{{.OSType}}").returncode != 0:
        pytest.skip("Docker is unavailable")
    for image in (SOURCE_IMAGE, SOURCE_API):
        if docker("image", "inspect", image).returncode != 0:
            pytest.skip("the exact reviewed source image is unavailable")
    name = f"ruisheng-test-bounded-{uuid4()}"
    migration = name + "-seed"
    checked_docker("volume", "create", "--label", LABEL, name)
    try:
        checked_docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            LABEL,
            "--network",
            "none",
            "--restart",
            "no",
            "--memory",
            "1g",
            "--mount",
            f"type=volume,src={name},dst=/var/lib/postgresql/data",
            "-e",
            "POSTGRES_USER=ruisheng_admin",
            "-e",
            "POSTGRES_DB=ruisheng",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            SOURCE_IMAGE,
            "postgres",
            "-c",
            "timescaledb.max_background_workers=0",
            "-c",
            "timescaledb.telemetry_level=off",
        )
        wait_ready(name)
        checked_docker(
            "run",
            "--name",
            migration,
            "--label",
            LABEL,
            "--network",
            f"container:{name}",
            "--restart",
            "no",
            "--entrypoint",
            "python",
            "-e",
            "DATABASE_URL=postgresql+asyncpg://ruisheng_admin@127.0.0.1:5432/ruisheng",
            "-e",
            "RUISHENG_API_PASSWORD=bounded-test-api-password",
            "-e",
            "RUISHENG_GW_PASSWORD=bounded-test-gw-password",
            SOURCE_API,
            "-m",
            "alembic",
            "upgrade",
            PREVIOUS,
        )
        sql(
            name,
            """
INSERT INTO wx_groups(usr_group) VALUES ('bounded_test');
INSERT INTO devices(dev_number,dev_ser_number,modbus_addr,transport_type,serial_port,
                    update_interval_decisec,loss_count,is_online,update_flag,usr_group)
VALUES('OLD_DEVICE','OLD_SERIAL',1,'serial','SIMULATED-RS485',10,0,false,0,'bounded_test');
INSERT INTO device_points(dev_number,point_name,point_number,fun_code,dev_addr,value_type,
                          show,point_ratio,point_offset,user_ratio,user_point_offset)
VALUES('OLD_DEVICE','historical_temperature',35,3,1,U&'\\5B57',1,1.0,0.0,1.0,0.0);
INSERT INTO point_data_history(dev_number,point_id,org_value,rt_value,recorded_at)
SELECT 'OLD_DEVICE',id,123,12.3,'2026-09-08T00:00:00Z' FROM device_points;
CREATE ROLE bounded_auditor NOLOGIN;
CREATE ROLE bounded_grantor NOLOGIN;
ALTER DATABASE ruisheng OWNER TO bounded_grantor;
REVOKE ALL ON DATABASE ruisheng FROM PUBLIC;
GRANT CONNECT ON DATABASE ruisheng TO ruisheng_admin,ruisheng_api,ruisheng_gw;
SET ROLE bounded_grantor;
GRANT TEMPORARY ON DATABASE ruisheng TO bounded_auditor WITH GRANT OPTION;
RESET ROLE;
SET ROLE bounded_auditor;
GRANT TEMPORARY ON DATABASE ruisheng TO ruisheng_api;
RESET ROLE;
ALTER DATABASE ruisheng CONNECTION LIMIT 23;
ALTER DATABASE ruisheng SET search_path TO public,pg_catalog;
ALTER DATABASE ruisheng SET timezone TO 'Asia/Shanghai';
ALTER ROLE ruisheng_api IN DATABASE ruisheng SET statement_timeout TO '4s';
ALTER ROLE bounded_auditor IN DATABASE ruisheng SET search_path TO pg_catalog;
GRANT bounded_auditor TO bounded_grantor WITH ADMIN OPTION;
SET ROLE bounded_grantor;
GRANT bounded_auditor TO ruisheng_api;
RESET ROLE;
GRANT SELECT ON devices TO bounded_auditor;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bounded_auditor;
CREATE SEQUENCE public.bounded_uncalled START 73;
ALTER TABLE devices ADD CONSTRAINT bounded_expression_check
  CHECK (transport_type IN ('tcp'::varchar,'serial'::varchar)) NOT VALID;
CREATE INDEX bounded_expression_index ON devices ((lower(dev_number)))
  WHERE transport_type IN ('tcp'::varchar,'serial'::varchar);
CREATE FUNCTION public.bounded_test_value() RETURNS integer LANGUAGE sql IMMUTABLE
  SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT 7';
REVOKE ALL ON FUNCTION public.bounded_test_value() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.bounded_test_value() TO bounded_auditor,ruisheng_api;
CREATE FUNCTION public.bounded_test_trigger() RETURNS trigger LANGUAGE plpgsql
  AS 'BEGIN RETURN NEW; END';
CREATE TRIGGER bounded_test_trigger BEFORE UPDATE ON public.devices
  FOR EACH ROW WHEN (OLD.dev_name IS DISTINCT FROM NEW.dev_name)
  EXECUTE FUNCTION public.bounded_test_trigger();
ALTER ROLE ruisheng_api NOLOGIN;
ALTER ROLE ruisheng_gw NOLOGIN;
""",
        )
        assert sql(name, "SELECT version_num FROM alembic_version") == PREVIOUS
        yield name
    finally:
        for owned_name in (migration, name):
            result = docker("container", "inspect", owned_name)
            if result.returncode == 0:
                info = json.loads(result.stdout)[0]
                assert info["Config"]["Labels"]["com.ruisheng.test"] == "bounded-schema-recovery"
                if info["State"]["Running"]:
                    checked_docker("stop", "--time", "30", owned_name)


@pytest.fixture(scope="module", params=["powershell.exe", "pwsh.exe"])
def restored(source_database: str, request) -> Iterator[ProofRun]:
    operation = str(uuid4())
    proof = ProofRun(
        source_database,
        operation,
        ROOT / "tmp-test-logs" / f"bounded-proof-{operation}",
        request.param,
    )
    try:
        proof.run("""
$journal.backup=New-BoundedDatabaseBackup $journal
Write-JsonAtomic $JournalPath $journal
Test-BoundedBackupRestore $journal
Assert-BoundedBackupReceipt $journal -RequireVerified
Write-Output 'restore_verified'
""")
        info = json.loads(checked_docker("inspect", proof.clone))[0]
        assert info["State"]["Running"] is False
        assert info["HostConfig"]["NetworkMode"] == "none"
        assert not info["HostConfig"]["PortBindings"]
        assert info["HostConfig"]["RestartPolicy"]["Name"] == "no"
        assert info["Image"] == SOURCE_IMAGE
        checked_docker("start", proof.clone)
        wait_ready(proof.clone, proof.restore_user)
        yield proof
    finally:
        proof.stop_owned_clone()


def test_real_snapshot_backup_restores_data_sequences_roles_and_catalog(restored: ProofRun):
    journal = json.loads(restored.journal_path.read_text(encoding="utf-8"))
    receipt = journal["backup"]
    assert journal["migration"]["snapshot_holder"]["stopped"] is True
    assert journal["migration"].get("docker_intent") is None
    assert (
        journal["migration"]["snapshot_holder"]["lifetime_seconds"]
        > 300 + 300 + 600 + 120 + 120 + 660
    )
    assert (
        sql(
            restored.source,
            f"SELECT count(*) FROM pg_stat_activity WHERE application_name='ruisheng-upgrade-snapshot-{restored.operation}'",
        )
        == "0"
    )
    assert receipt["restore_verified"] is True
    assert receipt["postgres_version"] == "150007"
    assert receipt["timescale_version"] == "2.16.1"
    assert receipt["timescale_owner"] == "ruisheng_admin"
    assert restored.fingerprint(restored.source) == receipt["fingerprint_sha256"]
    assert restored.fingerprint(restored.clone) == receipt["fingerprint_sha256"]
    assert (
        sql(restored.clone, "SELECT count(*) FROM wx_groups WHERE usr_group='bounded_test'") == "1"
    )
    assert sql(restored.clone, "SELECT dev_number FROM devices") == "OLD_DEVICE"
    assert sql(restored.clone, "SELECT count(*) FROM device_points") == "1"
    assert (
        sql(restored.clone, "SELECT org_value||'|'||rt_value FROM point_data_history") == "123|12.3"
    )
    assert (
        sql(restored.clone, "SELECT last_value||'|'||is_called FROM bounded_uncalled") == "73|false"
    )
    assert (
        sql(restored.clone, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_api'") == "f"
    )
    assert sql(restored.clone, "SHOW timescaledb.max_background_workers") == "0"
    assert sql(restored.clone, "SELECT public.bounded_test_value()") == "7"
    assert (
        sql(restored.clone, "SELECT tgenabled FROM pg_trigger WHERE tgname='bounded_test_trigger'")
        == "O"
    )
    assert (
        sql(
            restored.clone,
            "SELECT pg_get_userbyid(grantor) FROM pg_auth_members WHERE member='ruisheng_api'::regrole AND roleid='bounded_auditor'::regrole",
        )
        == "bounded_grantor"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE devices SET dev_name='changed' WHERE dev_number='OLD_DEVICE';",
        "REVOKE SELECT ON devices FROM bounded_auditor;",
        "SET ROLE bounded_grantor; REVOKE bounded_auditor FROM ruisheng_api; RESET ROLE; GRANT bounded_auditor TO ruisheng_api;",
        "ALTER ROLE bounded_auditor LOGIN;",
        "REVOKE pg_read_all_stats FROM pg_monitor; SET ROLE ruisheng_admin; GRANT pg_read_all_stats TO pg_monitor; RESET ROLE;",
        "REVOKE ALL ON SEQUENCE bounded_uncalled FROM ruisheng_admin;",
        "ALTER DEFAULT PRIVILEGES FOR ROLE ruisheng_admin IN SCHEMA public REVOKE SELECT ON TABLES FROM bounded_auditor;",
        "ALTER TABLE devices DISABLE ROW LEVEL SECURITY;",
    ],
)
def test_fingerprint_rejects_real_data_security_or_membership_changes(
    restored: ProofRun, mutation: str
):
    receipt = json.loads(restored.journal_path.read_text(encoding="utf-8"))["backup"]
    assert restored.fingerprint(restored.clone, mutation=mutation) != receipt["fingerprint_sha256"]
    assert restored.fingerprint(restored.clone) == receipt["fingerprint_sha256"]


@pytest.mark.parametrize(
    "mutation",
    [
        "CREATE OR REPLACE FUNCTION public.bounded_test_value() RETURNS integer LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT 8';",
        "REVOKE EXECUTE ON FUNCTION public.bounded_test_value() FROM bounded_auditor;",
        "ALTER FUNCTION public.bounded_test_value() SECURITY INVOKER;",
        "ALTER FUNCTION public.bounded_test_value() OWNER TO bounded_auditor;",
        "ALTER TABLE public.devices DISABLE TRIGGER bounded_test_trigger;",
        "DROP TRIGGER bounded_test_trigger ON public.devices; CREATE TRIGGER bounded_test_trigger AFTER UPDATE ON public.devices FOR EACH ROW EXECUTE FUNCTION public.bounded_test_trigger();",
    ],
)
def test_fingerprint_rejects_real_routine_and_trigger_drift(restored: ProofRun, mutation: str):
    receipt = json.loads(restored.journal_path.read_text(encoding="utf-8"))["backup"]
    assert restored.fingerprint(restored.clone, mutation=mutation) != receipt["fingerprint_sha256"]
    assert restored.fingerprint(restored.clone) == receipt["fingerprint_sha256"]


def test_fingerprint_preserves_equivalent_routine_acl_order(restored: ProofRun):
    receipt = json.loads(restored.journal_path.read_text(encoding="utf-8"))["backup"]
    mutation = """
REVOKE EXECUTE ON FUNCTION public.bounded_test_value() FROM bounded_auditor,ruisheng_api;
GRANT EXECUTE ON FUNCTION public.bounded_test_value() TO ruisheng_api,bounded_auditor;
"""
    assert restored.fingerprint(restored.clone, mutation=mutation) == receipt["fingerprint_sha256"]


def test_real_database_properties_restore_owner_acl_limits_and_settings(restored: ProofRun):
    receipt = json.loads(restored.journal_path.read_text(encoding="utf-8"))["backup"]
    assert receipt["schema_version"] == 4
    properties = Path(receipt["database_properties"]["path"])
    assert (
        hashlib.sha256(properties.read_bytes()).hexdigest()
        == receipt["database_properties"]["sha256"]
    )
    assert (
        sql(
            restored.clone,
            "SELECT pg_get_userbyid(datdba)||'|'||datconnlimit FROM pg_database WHERE datname='ruisheng'",
        )
        == "bounded_grantor|23"
    )
    assert (
        sql(restored.clone, "SELECT has_database_privilege('ruisheng_api','ruisheng','TEMPORARY')")
        == "t"
    )
    assert (
        sql(
            restored.clone,
            "SELECT count(*) FROM pg_database d,aclexplode(d.datacl) a WHERE d.datname='ruisheng' AND a.grantee=0",
        )
        == "0"
    )
    assert sql(restored.clone, "SHOW timezone") == "Asia/Shanghai"
    assert sql(restored.clone, "SHOW search_path") == "public, pg_catalog"
    settings = """
SELECT json_build_array(r.rolname,ARRAY(
  SELECT config FROM unnest(s.setconfig) config
  ORDER BY split_part(config,'=',1),substr(config,strpos(config,'=')+1)
))::text
FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid=s.setrole
WHERE s.setdatabase=(SELECT oid FROM pg_database WHERE datname='ruisheng')
ORDER BY r.rolname NULLS FIRST
"""
    assert sql(restored.clone, settings) == sql(restored.source, settings)
    assert restored.fingerprint(restored.clone) == receipt["fingerprint_sha256"]


@pytest.mark.parametrize(
    "mutation",
    [
        "ALTER DATABASE ruisheng OWNER TO ruisheng_admin;",
        "GRANT CONNECT ON DATABASE ruisheng TO PUBLIC;",
        "REVOKE GRANT OPTION FOR TEMPORARY ON DATABASE ruisheng FROM bounded_auditor CASCADE;",
        "ALTER DATABASE ruisheng CONNECTION LIMIT 24;",
        "ALTER DATABASE ruisheng IS_TEMPLATE true;",
        "ALTER DATABASE ruisheng SET search_path TO pg_catalog;",
        "ALTER DATABASE ruisheng SET timezone TO 'UTC';",
        "ALTER ROLE ruisheng_api IN DATABASE ruisheng SET statement_timeout TO '5s';",
        "ALTER ROLE bounded_auditor IN DATABASE ruisheng RESET ALL;",
    ],
)
def test_fingerprint_rejects_real_database_properties_drift(restored: ProofRun, mutation: str):
    receipt = json.loads(restored.journal_path.read_text(encoding="utf-8"))["backup"]
    assert restored.fingerprint(restored.clone, mutation=mutation) != receipt["fingerprint_sha256"]
    assert restored.fingerprint(restored.clone) == receipt["fingerprint_sha256"]


def test_database_properties_export_rejects_unrestorable_creation_attributes(restored: ProofRun):
    statement = restored.run("Get-DatabasePropertiesSql")
    # PostgreSQL forbids ALLOW_CONNECTIONS false for the current database. The
    # second case simulates that catalog state inside this owned clone's
    # transaction; it does not claim the equivalent ALTER DATABASE succeeded.
    for mutation in (
        "ALTER DATABASE ruisheng IS_TEMPLATE true;",
        "UPDATE pg_catalog.pg_database SET datallowconn=false WHERE datname=current_database();",
    ):
        result = docker(
            "exec",
            "-i",
            restored.clone,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            restored.restore_user,
            "-d",
            "ruisheng",
            input_text="BEGIN;\n" + mutation + "\n" + statement + "\nROLLBACK;\n",
        )
        assert result.returncode != 0
        assert "backup_database_properties_unsupported" in result.stderr
        assert sql(restored.clone, "SELECT version_num FROM alembic_version") == PREVIOUS
        assert (
            sql(
                restored.clone,
                "SELECT datallowconn AND NOT datistemplate FROM pg_database WHERE datname='ruisheng'",
            )
            == "t"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "ALTER TABLE devices DROP CONSTRAINT bounded_expression_check; ALTER TABLE devices ADD CONSTRAINT bounded_expression_check CHECK (transport_type IN ('serial'::varchar)) NOT VALID;",
        "DROP INDEX bounded_expression_index; CREATE INDEX bounded_expression_index ON devices ((upper(dev_number))) WHERE transport_type IN ('tcp'::varchar,'serial'::varchar);",
        "DROP INDEX bounded_expression_index; CREATE INDEX bounded_expression_index ON devices ((lower(dev_number))) WHERE transport_type IN ('serial'::varchar);",
    ],
)
def test_postgres_parser_rejects_changed_check_index_expression_or_predicate(
    restored: ProofRun, mutation: str
):
    proof_sql = (restored.directory / "backup" / "expressions.sql").read_text(encoding="utf-8")
    result = docker(
        "exec",
        "-i",
        restored.clone,
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        restored.restore_user,
        "-d",
        "ruisheng",
        input_text="BEGIN;\n" + mutation + "\n" + proof_sql,
    )
    assert result.returncode != 0
    assert "restore_expression_mismatch" in result.stderr
    assert sql(restored.clone, "SELECT version_num FROM alembic_version") == PREVIOUS


def test_existing_owned_restore_assets_are_never_reused(restored: ProofRun):
    before = json.loads(checked_docker("container", "inspect", restored.clone))[0]
    result = restored.run("""
try { Test-BoundedBackupRestore $journal; throw 'unexpected_success' }
catch { $_.Exception.Message }
""")
    assert result == "restore_asset_conflict"
    after = json.loads(checked_docker("container", "inspect", restored.clone))[0]
    assert after["Id"] == before["Id"]
    assert after["State"]["Running"] is True
    assert sql(restored.source, "SELECT version_num FROM alembic_version") == PREVIOUS


def test_real_restore_fingerprint_mismatch_never_approves_or_migrates_source(source_database: str):
    operation = str(uuid4())
    proof = ProofRun(
        source_database, operation, ROOT / "tmp-test-logs" / f"bounded-proof-{operation}"
    )
    before = sql(source_database, "SELECT org_value||'|'||rt_value FROM point_data_history")
    try:
        result = proof.run("""
$journal.backup=New-BoundedDatabaseBackup $journal
$journal.backup.fingerprint_sha256='0'*64
Write-JsonAtomic $JournalPath $journal
Write-JsonAtomic (Join-Path $BackupDirectory 'backup-receipt.json') $journal.backup
try { Test-BoundedBackupRestore $journal; throw 'unexpected_success' }
catch { $_.Exception.Message }
""")
        assert result == "restore_fingerprint_mismatch"
        journal = json.loads(proof.journal_path.read_text(encoding="utf-8"))
        assert journal["backup"]["restore_verified"] is False
        assert journal["migration"]["phase"] == "restore_container_created"
        assert (
            json.loads(checked_docker("container", "inspect", proof.clone))[0]["State"]["Running"]
            is False
        )
        assert sql(source_database, "SELECT version_num FROM alembic_version") == PREVIOUS
        assert (
            sql(source_database, "SELECT org_value||'|'||rt_value FROM point_data_history")
            == before
        )
    finally:
        proof.stop_owned_clone()


@pytest.fixture(scope="module")
def recovery_image(tmp_path_factory) -> str:
    directory = tmp_path_factory.mktemp("bounded-recovery-image")
    source_tag = "ruisheng-test/bounded-source:" + str(uuid4())
    checked_docker("tag", SOURCE_API, source_tag)
    shutil.copyfile(
        ROOT / "alembic" / "versions" / "20260907_0013_serial_polling_profile.py",
        directory / "migration.py",
    )
    (directory / "Dockerfile").write_text(
        f"FROM {source_tag}\nCOPY migration.py /app/alembic/versions/20260907_0013_serial_polling_profile.py\n",
        encoding="utf-8",
    )
    image_file = directory / "image.id"
    checked_docker(
        "build", "--label", LABEL, "--iidfile", str(image_file), str(directory), timeout=300
    )
    return image_file.read_text(encoding="utf-8").strip()


@dataclass
class RecoveryRun:
    proof: ProofRun
    target_image: str
    project: str
    state: Path
    source_root: Path
    target_root: Path

    @property
    def postgres(self) -> str:
        return self.proof.source

    def run(self, body: str, *, timeout: int = 900) -> str:
        return self.proof.run(body, timeout=timeout)

    def migration(self, *, script: str | None = None) -> str:
        name = f"ruisheng-migrate-{self.proof.operation}"
        command = ["-m", "alembic", "upgrade", HEAD] if script is None else ["-c", script]
        checked_docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            LABEL,
            "--label",
            f"com.ruisheng.upgrade.operation={self.proof.operation}",
            "--network",
            f"container:{self.postgres}",
            "--restart",
            "no",
            "--entrypoint",
            "python",
            "-e",
            "DATABASE_URL=postgresql+asyncpg://ruisheng_admin@127.0.0.1:5432/ruisheng",
            self.target_image,
            *command,
        )
        return name

    def recover(self) -> dict:
        result = self.run("""
try { Invoke-BoundedRecovery $journal }
catch { Record-BoundedFailure $journal ([string]$_.Exception.Message) }
@{status=$journal.status;error=$journal.error_code;phase=$journal.migration.phase} | ConvertTo-Json -Compress
""")
        return json.loads(result)

    def outer_recover(
        self, *, setup: str = "", approved: bool = True, operation: str | None = None
    ):
        """Execute the entire dispatcher, ACL checks, real locks and recovery code.

        Rebase only test-owned paths/Docker names and reuse the fixture's external
        publisher, entitlement, network and application health substitutions.
        The test is not elevated: only the shared-audit administrator token/SID
        boundary is rebased to the current test user (including inherited log
        ACL identities). Directory/file ACL algorithms still inspect real files.
        No production site-path grammar, lock or cleanup decision is replaced.
        """
        assert self.state.parent == Path(r"C:\Ruisheng\candidates")
        assert self.state.name == "bounded-test-" + self.proof.operation
        assert self.proof.lock_names == ("shared-maintenance", "legacy-hotfix")
        production = UPDATER.read_text(encoding="utf-8")
        audit = self.state / "outer-audit"
        production = production.replace(r"C:\Ruisheng\audit", str(audit))
        fixture_setup = "\n".join(
            line
            for line in self.proof.setup.splitlines()
            if not line.startswith(("$OperationId=", "$Reason="))
        )
        adapter = f"""
Set-Item Function:Invoke-RealDockerText ${{function:Invoke-DockerText}}
Set-Item Function:Assert-OriginalRestrictedDirectory ${{function:Assert-RestrictedDirectory}}
Set-Item Function:Assert-OriginalRestrictedFile ${{function:Assert-RestrictedFile}}
$StateDirectory={ps_literal(self.state)}
{fixture_setup}
Set-Item Function:Assert-RestrictedDirectory ${{function:Assert-OriginalRestrictedDirectory}}
Set-Item Function:Assert-RestrictedFile ${{function:Assert-OriginalRestrictedFile}}
$BackupDirectory=Join-Path $StateDirectory 'backup'
$SharedLockPath=Join-Path $StateDirectory 'shared.lock'
$LegacyLockPath=Join-Path $StateDirectory 'legacy.lock'
$AuditDirectory={ps_literal(audit)}
$AuditPath=Join-Path $AuditDirectory 'full-upgrade.jsonl'
$AuditLockPath=Join-Path $AuditDirectory '.remote-maintenance-audit.lock'
function Get-SshPosture {{ @{{mutation_allowed=$true}} }}
$testAuditSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach ($testFunction in @('Assert-SharedAuditRoot','Assert-SharedAuditFile')) {{
  $definition=(Get-Item ("Function:"+$testFunction)).ScriptBlock.ToString()
  $definition=$definition.Replace("'S-1-5-32-544'",("'"+$testAuditSid+"'"))
  $definition=$definition.Replace('$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)','$true')
  Set-Item ("Function:"+$testFunction) ([ScriptBlock]::Create($definition))
}}
if ($script:BoundedCleanupAuthority -or $script:BoundedRecoveryMutationStarted -or $script:BoundedRecoveryDatabaseVerified) {{ throw 'fresh_process_required' }}
{setup}
"""
        anchor = "$active = $null\n$locks = $null\n"
        assert production.count(anchor) == 1
        script = self.state / f"outer-recover-{uuid4()}.ps1"
        script.write_text(production.replace(anchor, adapter + "\n" + anchor), encoding="utf-8-sig")
        arguments = [
            self.proof.executable,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Action",
            "Recover",
            "-SiteRoot",
            str(self.state),
            "-OperationId",
            operation or self.proof.operation,
            "-Reason",
            "owned local recovery integration",
        ]
        if approved:
            arguments.append("-Approved")
        env = os.environ.copy()
        if self.proof.executable == "powershell.exe":
            env["PSModulePath"] = str(
                Path(env.get("SystemRoot", r"C:\Windows"))
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "Modules"
            )
        return subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
            env=env,
        )


def _available_recovery_subnet() -> str:
    # Keep retained test networks without exhausting Docker's default address pool.
    ids = checked_docker("network", "ls", "--quiet").splitlines()
    occupied = [
        ip_network(config["Subnet"])
        for network in json.loads(checked_docker("network", "inspect", *ids))
        for config in network["IPAM"].get("Config") or []
        if config.get("Subnet")
    ]
    for subnet in ip_network("10.254.0.0/16").subnets(new_prefix=28):
        if not any(subnet.overlaps(existing) for existing in occupied):
            return str(subnet)
    raise RuntimeError("No unused subnet available for isolated recovery tests")


@pytest.fixture
def recovery(  # noqa: PLR0912, PLR0915
    source_database: str, recovery_image: str, tmp_path: Path, request
) -> Iterator[RecoveryRun]:
    operation = str(uuid4())
    prefix = f"ruisheng-test-recovery-{operation}"
    directory = ROOT / "tmp-test-logs" / f"bounded-recovery-{operation}"
    production_dependencies = (
        request.node.originalname == "test_real_application_start_with_retained_migrator"
    )
    outer_entry = production_dependencies or (
        request.node.originalname == "test_real_outer_recover_preflight_cleanup_after_parent_death"
    )
    if outer_entry:
        directory = Path(r"C:\Ruisheng\candidates") / f"bounded-test-{operation}"
        assert not directory.exists()
        for parent in directory.parents:
            if parent.exists():
                assert parent.is_dir()
                assert not (parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    directory.mkdir(parents=True)
    proof = ProofRun(
        source_database,
        operation,
        directory,
        lock_names=("shared-maintenance", "legacy-hotfix") if outer_entry else ("shared", "legacy"),
    )
    project = f"bounded-{operation}"
    subnet = _available_recovery_subnet() if production_dependencies else None
    owned = []
    try:
        # This receipt is generated and verified by the real backup/restore functions.
        proof.run("""
$journal.backup=New-BoundedDatabaseBackup $journal
Write-JsonAtomic $JournalPath $journal
Test-BoundedBackupRestore $journal
""")
        checked_docker("rename", proof.clone, prefix + "-postgres")
        postgres = prefix + "-postgres"
        owned.append(postgres)
        # Retain the restored database volume, and use the exact source engine as the test site.
        # A stopped retained predecessor allows compose to create the expected test-site container.
        retired = postgres + "-retired"
        checked_docker("rename", postgres, retired)
        owned.append(retired)
        roots = [
            directory / "candidates" / f"{label}-{operation}" for label in ("source", "target")
        ]
        manifest_values = []
        for index, release_root in enumerate(roots):
            release_root.mkdir(parents=True)
            images = []
            services = {}
            image_ids = {
                "postgres": SOURCE_IMAGE,
                "redis": REDIS_IMAGE,
                "api": recovery_image if index else SOURCE_API,
                "gw": recovery_image if index else SOURCE_API,
                "web": recovery_image if index else SOURCE_API,
            }
            for service, image in image_ids.items():
                tag = f"ruisheng-candidate/{service}:{release_root.name}"
                checked_docker("tag", image, tag)
                images.append(
                    {
                        "component": service,
                        "source_reference": image,
                        "repo_digest": "",
                        "candidate_reference": tag,
                        "image_id": image,
                        "os": "linux",
                        "architecture": "amd64",
                        "archive": f"{service}.tar",
                        "sha256": "c" * 64,
                    }
                )
                common = {
                    "image": tag,
                    "container_name": f"{prefix}-{service}",
                    "pull_policy": "never",
                    "restart": "no",
                    "labels": {"com.ruisheng.test": "bounded-schema-recovery"},
                }
                if service == "postgres":
                    common.update(
                        {
                            "restart": "unless-stopped",
                            "environment": {
                                "POSTGRES_USER": "ruisheng_admin",
                                "POSTGRES_HOST_AUTH_METHOD": "trust",
                            },
                            "network_mode": "none",
                            "volumes": ["ruisheng-pgdata:/var/lib/postgresql/data"],
                            "command": [
                                "postgres",
                                "-c",
                                "timescaledb.max_background_workers=0",
                                "-c",
                                "timescaledb.telemetry_level=off",
                            ],
                            "healthcheck": {
                                "test": [
                                    "CMD",
                                    "pg_isready",
                                    "-U",
                                    "ruisheng_admin",
                                    "-d",
                                    "ruisheng",
                                ],
                                "interval": "1s",
                            },
                        }
                    )
                elif service == "redis":
                    common.update(
                        {
                            "restart": "unless-stopped",
                            "network_mode": "none",
                            "volumes": ["ruisheng-redisdata:/data"],
                            "healthcheck": {"test": ["CMD", "redis-cli", "ping"], "interval": "1s"},
                        }
                    )
                else:
                    common.update(
                        {
                            "network_mode": "none",
                            "entrypoint": [
                                "python",
                                "-c",
                                "import signal,time; signal.signal(signal.SIGTERM,lambda *args:exit(0)); time.sleep(3600)",
                            ],
                        }
                    )
                services[service] = common
            services["migrate"] = {
                "image": images[2]["candidate_reference"],
                "pull_policy": "never",
                "network_mode": f"container:{prefix}-postgres",
                "environment": {
                    "DATABASE_URL": "postgresql+asyncpg://ruisheng_admin@127.0.0.1:5432/ruisheng"
                },
            }
            if production_dependencies:
                # Copy the production dependency graph; the prior fixture omitted it.
                # A bridge network preserves service DNS across PostgreSQL recreation.
                # container:<postgres> would leave the old migrator attached to a dead ID.
                services["postgres"].pop("network_mode")
                services["migrate"].pop("network_mode")
                services["migrate"]["environment"]["DATABASE_URL"] = (
                    "postgresql+asyncpg://ruisheng_admin@postgres:5432/ruisheng"
                )
                services["migrate"].update(
                    {
                        "container_name": prefix + "-migrate",
                        "entrypoint": ["python", "-m", "alembic", "upgrade", "head"],
                        "restart": "no",
                        "labels": {"com.ruisheng.test": "bounded-schema-recovery"},
                        "depends_on": {"postgres": {"condition": "service_healthy"}},
                    }
                )
                for application in ("api", "gw"):
                    services[application]["depends_on"] = {
                        "migrate": {"condition": "service_completed_successfully"},
                        "redis": {"condition": "service_healthy"},
                    }
                services["web"]["depends_on"] = ["api"]
            model = {
                "name": project,
                "services": services,
                "volumes": {
                    "ruisheng-pgdata": {"external": True, "name": proof.clone},
                    "ruisheng-redisdata": {"external": True, "name": prefix + "-redis"},
                },
            }
            if production_dependencies:
                model["networks"] = {
                    "default": {"internal": True, "ipam": {"config": [{"subnet": subnet}]}}
                }
            (release_root / "docker-compose.prod.yml").write_text(
                json.dumps(model), encoding="utf-8"
            )
            (release_root / "site-network.override.yml").write_text(
                '{"services":{}}', encoding="utf-8"
            )
            manifest = {
                "schema_version": 2,
                "candidate_id": release_root.name,
                "source_commit": ("d" if index else "e") * 40,
                "generated_at": "2026-09-08T00:00:00.0000000+00:00",
                "target_os": "linux",
                "target_architecture": "amd64",
                "alembic_head": HEAD if index else PREVIOUS,
                "logical_identity": "sha256:" + ("b" if index else "a") * 64,
                "tools": [],
                "authenticity": {},
                "images": images,
            }
            (release_root / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
            manifest_values.append(manifest)
        checked_docker("volume", "create", "--label", LABEL, prefix + "-redis")
        if production_dependencies:
            owned.append(prefix + "-migrate")
        checked_docker(
            "compose",
            "-f",
            str(roots[0] / "docker-compose.prod.yml"),
            "up",
            "-d",
            "postgres",
            "redis",
            "api",
            "gw",
            "web",
        )
        owned.extend(prefix + "-" + service for service in ("redis", "api", "gw", "web"))
        wait_ready(postgres)
        env_path = directory / ".env.prod"
        env_path.write_bytes(
            b"\xef\xbb\xbfSECRET=retained-bytewise\r\n"
            + b"TARGET_PLATFORM=linux/amd64\r\n"
            + b"".join(
                f"{image['component'].upper()}_IMAGE={image['candidate_reference']}\r\n".encode()
                for image in manifest_values[0]["images"]
            )
        )
        proof.source = postgres
        proof.setup = f"""
$OperationId={ps_literal(operation)}
$SiteRoot=$StateDirectory; $StableCandidatesRoot=Join-Path $StateDirectory 'candidates'
$MaintenanceStatePath=Join-Path $StateDirectory 'full-upgrade-maintenance.json'
$JournalPath=Join-Path $StateDirectory "full-upgrade-$OperationId.json"
$EnvFile=Join-Path $StateDirectory '.env.prod'; $ProspectiveEnvPath=Join-Path $StateDirectory '.prospective.env'
$ActiveReleasePath=Join-Path $StateDirectory 'active-release.json'
$AuditPath=Join-Path $StateDirectory 'audit.jsonl'; $AuditLockPath=Join-Path $StateDirectory 'audit.lock'
$Reason='owned local recovery integration'
$BoundedMigrationSha256='df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1'
$PersistentServices=@('postgres','redis','gw','api','web'); $PolicyServices=@('postgres','redis','migrate','gw','api','web')
$AllowedFields=@('TARGET_PLATFORM','POSTGRES_IMAGE','REDIS_IMAGE','API_IMAGE','GW_IMAGE','WEB_IMAGE')
function Invoke-PublisherVerification {{ param($Root,$EnvironmentPath) }}
function Assert-EntitlementFeature {{ param($Feature) }}
function Assert-InstalledMaintenanceGuards {{
  (Get-FileHash -LiteralPath (Join-Path $StateDirectory 'installed-guard-receipt.json')).Hash.ToLowerInvariant()
}}
function Assert-NetworkBoundary {{ param($Model) }}
function Assert-RestrictedDirectory {{ param($Path) }}
function Assert-RestrictedFile {{ param($Path) }}
function Wait-BoundedHealthy {{
  param($ComposeBase)
  $writePath=Join-Path $StateDirectory 'health-write.sql'
  if (Test-Path $writePath) {{
    [void](Invoke-DatabaseSql (Get-Content $writePath -Raw))
    Move-Item $writePath (Join-Path $StateDirectory 'health-write-retained.sql')
  }}
  if (Test-Path (Join-Path $StateDirectory 'fail-health')) {{ throw 'service_health_failed' }}
}}
function Invoke-DockerText {{
  param([string[]]$Arguments,[int]$TimeoutSeconds=120)
  $mapped=@($Arguments | ForEach-Object {{
    $arg=$_
    if ($arg -ceq 'label=com.docker.compose.project=ruisheng-prod') {{ $arg={ps_literal("label=com.docker.compose.project=" + project)} }}
    foreach ($service in @('postgres','redis','api','gw','web')) {{
      $arg=$arg.Replace("ruisheng-$service",{ps_literal(prefix + "-")}+$service)
    }}
    $arg
  }})
  $text=Invoke-RealDockerText $mapped $TimeoutSeconds
  if ($Arguments[0] -eq 'inspect') {{ $text=$text.Replace({ps_literal(project)},'ruisheng-prod') }}
  return $text
}}
if (Test-Path $JournalPath) {{ $journal=Convert-UpgradeJson (Get-Content $JournalPath -Raw -Encoding UTF8) }}
"""
        setup_result = proof.run(f"""
$source=Convert-UpgradeJson (Get-Content {ps_literal(roots[0] / "MANIFEST.json")} -Raw)
$target=Convert-UpgradeJson (Get-Content {ps_literal(roots[1] / "MANIFEST.json")} -Raw)
$sourceImages=Assert-CandidateManifest $source {ps_literal(roots[0])}
$targetImages=Assert-CandidateManifest $target {ps_literal(roots[1])}
$journal=@{{backup=$journal.backup;operation_id=$OperationId;reason_hash=(Get-Sha256Text $Reason)}}
$prior=@{{schema_version=1;candidate_id=$source.candidate_id;logical_identity=$source.logical_identity;source_commit=$source.source_commit;candidate_root={ps_literal(roots[0])};site_root=$SiteRoot;committed_at=[DateTimeOffset]::UtcNow.ToString('o');operation_id='7c250a95-1fd7-4a98-9021-12864349ac97'}}
$journal.previous_release=$prior
$journal.candidate=@{{candidate_id=$target.candidate_id;logical_identity=$target.logical_identity;source_commit=$target.source_commit;candidate_root={ps_literal(roots[1])};alembic_head=$BoundedTargetHead}}
$journal.status='candidate_staged';$journal.error_code='';$journal.completed_at='';$journal.switched=$false
$journal.source_environment_sha256=(Get-FileHash $EnvFile).Hash.ToLowerInvariant()
$journal.environment_backup=New-EnvironmentBackupReceipt $EnvFile (Join-Path $StateDirectory "full-upgrade-$OperationId.env.before")
$journal.migration=@{{kind='bounded_0012_0013';script_sha256=$BoundedMigrationSha256;phase='prepared';updated_at='';application_start_attempted=$false;
  roles=@(@{{name='ruisheng_api';login=$false}},@{{name='ruisheng_gw';login=$true}});applications=@(Get-ManagedApplications);
  dependencies=@(Get-BoundedDependencyEvidence);database_system_identifier=(Invoke-DatabaseSql 'SELECT system_identifier::text FROM pg_control_system()');
  source_images=@{{}};target_images=@{{}};container_name="ruisheng-migrate-$OperationId";container_id=''}}
[IO.File]::WriteAllText((Join-Path $StateDirectory 'installed-guard-receipt.json'),'isolated-test-guard-receipt')
$journal.migration.guard_receipt_sha256=Assert-InstalledMaintenanceGuards
foreach ($service in $PersistentServices) {{$journal.migration.source_images[$service]=$sourceImages[$service].image_id;$journal.migration.target_images[$service]=$targetImages[$service].image_id}}
Write-ProspectiveEnvironment $EnvFile $ProspectiveEnvPath (Get-ReleaseValues $target $targetImages)
Write-JsonAtomic $JournalPath $journal
Write-JsonAtomic $ActiveReleasePath $prior
[IO.File]::WriteAllText($AuditLockPath,'')
Write-MaintenanceState $journal
'prepared'
""")
        assert setup_result == "prepared"
        if production_dependencies:
            legacy = json.loads(checked_docker("inspect", prefix + "-migrate"))[0]
            assert legacy["State"]["ExitCode"] == 0
            proof.run(f"""
$journal.migration | Add-Member -NotePropertyName existing_migrators -NotePropertyValue @(@{{id='{legacy["Id"]}';image_id='{legacy["Image"]}'}}) -Force
Write-JsonAtomic $JournalPath $journal
""")
        yield RecoveryRun(proof, recovery_image, project, directory, roots[0], roots[1])
    finally:
        ids = checked_docker(
            "ps", "-aq", "--filter", f"label=com.ruisheng.upgrade.operation={operation}"
        ).splitlines()
        for name in dict.fromkeys([*owned, *ids]):
            result = docker("inspect", name)
            if result.returncode == 0:
                info = json.loads(result.stdout)[0]
                labels = info["Config"].get("Labels") or {}
                assert (
                    labels.get("com.ruisheng.test") == "bounded-schema-recovery"
                    or labels.get("com.ruisheng.upgrade.operation") == operation
                )
                if info["State"]["Running"]:
                    checked_docker("stop", "--time", "10", name)


@pytest.mark.parametrize(
    "executable,legacy_failure", [("pwsh.exe", False), ("powershell.exe", True)]
)
def test_real_application_start_with_retained_migrator(  # noqa: PLR0915
    recovery: RecoveryRun, executable, legacy_failure
):
    recovery.proof.executable = executable
    journal_path = recovery.state / f"full-upgrade-{recovery.proof.operation}.json"
    prior = json.loads(journal_path.read_text(encoding="utf-8"))
    legacy_id = prior["migration"]["existing_migrators"][0]["id"]
    legacy_before = json.loads(checked_docker("inspect", legacy_id))[0]
    migration = recovery.migration()
    assert checked_docker("wait", migration) == "0"
    migration_id = json.loads(checked_docker("inspect", migration))[0]["Id"]
    recovery.run(f"""
$journal.migration.container_id='{migration_id}'
Save-BoundedPhase $journal 'migration_verified'
""")
    if legacy_failure:
        # Replay only the original Compose start call inside the real recovery
        # flow. The fresh outer Recover below loads the unmodified patched source.
        reproduced = recovery.run(r"""
$definition=(Get-Item Function:Start-BoundedApplications).ScriptBlock.ToString()
$current=[regex]::Match($definition,'(?s)  foreach \(\$applicationId in \$applicationIds\) \{.*?\n  \}')
if (-not $current.Success) { throw 'fault_anchor_missing' }
$definition=$definition.Replace($current.Value,'  [void](Invoke-DockerText ($compose + @("start", "gw", "api", "web")))')
Set-Item Function:Start-BoundedApplications ([ScriptBlock]::Create($definition))
try { Invoke-BoundedRecovery $journal } catch {
  $failure=[string]$_.Exception.Message
  Write-Audit 'upgrade_apply_failed' 'failed' ([string]$journal.candidate.logical_identity) $failure
  Record-BoundedFailure $journal $failure
}
@{status=$journal.status;error=$journal.error_code;phase=$journal.migration.phase} | ConvertTo-Json -Compress
""")
        assert json.loads(reproduced) == {
            "status": "recovery_failed",
            "error": "docker_command_failed",
            "phase": "application_start_intent",
        }
        failed = json.loads(journal_path.read_text(encoding="utf-8"))
        assert failed["migration"]["docker_intent"]["command"] == "compose"
        assert failed["migration"]["docker_intent"]["scope"] == "production"
        assert (
            sql(
                recovery.postgres,
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND NOT rolcanlogin",
            )
            == "2"
        )
        assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == HEAD
        for service in ("gw", "api", "web"):
            info = json.loads(
                checked_docker("inspect", recovery.postgres.removesuffix("postgres") + service)
            )[0]
            assert info["State"]["Status"] == "created"
            assert info["State"]["StartedAt"] == "0001-01-01T00:00:00Z"
            assert info["State"]["Pid"] == 0
        legacy_before = json.loads(checked_docker("inspect", legacy_id))[0]
        assert legacy_before["State"]["Status"] == "exited"
        assert legacy_before["State"]["ExitCode"] != 0
        logs = checked_docker("logs", legacy_id)
        assert "Can't locate revision identified by '0013_serial_polling_profile'" in logs
        shutil.copyfile(journal_path, recovery.state / "legacy-compose-failure-retained.json")
        # A transient recovery gate must not replace the original Apply outcome.
        recovery.run("""
function Assert-BoundedDatabaseIdentity { throw 'dependency_database_identity_invalid' }
try { Invoke-BoundedRecovery $journal } catch { Record-BoundedFailure $journal ([string]$_.Exception.Message) }
if ($journal.error_code -cne 'dependency_database_identity_invalid') { throw 'retry_fault_not_observed' }
""")

    # Real dispatcher, real locks, journal and ACL validation in a fresh process.
    # External publisher/entitlement and sleep-only app health use the existing
    # isolated fixture's substitutions; DB, migrations and Docker are real.
    recovery.run(f"""
{_test_dacl_helpers()}
foreach ($testItem in @((Get-Item -LiteralPath $StateDirectory)) + @(Get-ChildItem -LiteralPath $StateDirectory -Recurse -Force)) {{
  Reset-TestAccessRules $testItem @(Get-AllowedSids)
}}
$audit=Join-Path $StateDirectory 'outer-audit'
New-Item -ItemType Directory -Path $audit | Out-Null
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Reset-TestAccessRules (Get-Item -LiteralPath $audit) @('S-1-5-18',$sid)
$priorAudit=Join-Path $StateDirectory 'audit.jsonl'
if (Test-Path -LiteralPath $priorAudit) {{
  [IO.File]::WriteAllBytes((Join-Path $audit 'full-upgrade.jsonl'),[IO.File]::ReadAllBytes($priorAudit))
}}
$mutex=Join-Path $audit '.remote-maintenance-audit.lock'
[IO.File]::WriteAllText($mutex,'')
Reset-TestAccessRules (Get-Item -LiteralPath $mutex) @(Get-AllowedSids)
""")
    result = recovery.outer_recover()
    (recovery.state / "application-start-recover.stdout.log").write_text(
        result.stdout, encoding="utf-8"
    )
    (recovery.state / "application-start-recover.stderr.log").write_text(
        result.stderr, encoding="utf-8"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["ok"] is True and outcome["status"] == "committed", outcome
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["migration"]["docker_intent"] is None
    assert journal["migration"]["phase"] == "completed"
    if legacy_failure:
        observation = journal["migration"]["application_start_observation"]
        assert observation["prior_intent"] == failed["migration"]["docker_intent"]
        assert observation["legacy_migrator_id"] == legacy_id
        assert observation["migration_id"] == migration_id
        assert observation["legacy_exit_code"] == legacy_before["State"]["ExitCode"]
        records = [
            json.loads(line)
            for line in (recovery.state / "outer-audit" / "full-upgrade.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        audits = [
            record
            for record in records
            if record["event"] == "upgrade_application_start_failure_observed"
        ]
        assert len(audits) == 1 and audits[0]["evidence"] == observation
        origin = [record for record in records if record["event"] == "upgrade_apply_failed"]
        assert (
            len(origin) == 1
            and observation["original_failure_record_hash"] == origin[0]["record_hash"]
        )
    legacy_after = json.loads(checked_docker("inspect", legacy_id))[0]
    assert legacy_after["State"] == legacy_before["State"]
    for service in ("gw", "api", "web"):
        info = json.loads(
            checked_docker("inspect", recovery.postgres.removesuffix("postgres") + service)
        )[0]
        assert info["Image"] == recovery.target_image
        assert info["State"]["Running"] is True
        assert not info["HostConfig"]["PortBindings"]
    assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == HEAD
    assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
    assert (
        sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_api'")
        == "f"
    )
    assert (
        sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'")
        == "t"
    )
    assert not (recovery.state / "shared.lock").exists()
    assert not (recovery.state / "legacy.lock").exists()


def test_real_recovery_stopped_database_missing_application_and_repeat(recovery: RecoveryRun):
    checked_docker("stop", recovery.postgres)
    missing = recovery.postgres.removesuffix("postgres") + "api"
    checked_docker("stop", missing)
    checked_docker("rename", missing, missing + "-retained")
    before = json.loads(
        (recovery.state / f"full-upgrade-{recovery.proof.operation}.json").read_text(
            encoding="utf-8"
        )
    )
    assert before["migration"]["phase"] == "prepared"
    result = recovery.recover()
    assert result["status"] == "rolled_back", result
    assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == PREVIOUS
    assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
    assert (
        sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_api'")
        == "f"
    )
    assert (
        sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'")
        == "t"
    )
    assert recovery.recover()["status"] == "rolled_back"


def test_real_recovery_committed_migration_before_journal_and_new_writes(recovery: RecoveryRun):
    name = recovery.migration()
    assert checked_docker("wait", name) == "0"
    recovery.run("Save-BoundedPhase $journal 'migration_start_intent'")
    (recovery.state / "fail-health").write_text("owned test boundary", encoding="utf-8")
    (recovery.state / "health-write.sql").write_text(
        """
UPDATE devices SET read_profile='zero_origin_38',deleted_at=now() WHERE dev_number='OLD_DEVICE';
INSERT INTO devices(dev_number,dev_ser_number,modbus_addr,transport_type,serial_port,read_profile,
                    update_interval_decisec,loss_count,is_online,update_flag,usr_group)
VALUES('NEW_DEVICE','NEW_SERIAL',1,'serial','SIMULATED-RS485','zero_origin_38',10,0,false,0,'bounded_test');
INSERT INTO point_data_history(dev_number,point_id,org_value,rt_value,recorded_at)
SELECT 'OLD_DEVICE',id,456,45.6,'2026-09-08T00:00:01Z' FROM device_points;
""",
        encoding="utf-8",
    )
    failed = recovery.recover()
    assert failed == {
        "status": "recovery_failed",
        "error": "service_health_failed",
        "phase": "application_start_intent",
    }
    (recovery.state / "fail-health").rename(recovery.state / "health-fault-retained")
    result = recovery.recover()
    assert result["status"] == "committed", result
    assert recovery.recover()["status"] == "committed"
    assert (
        sql(recovery.postgres, "SELECT count(*) FROM devices WHERE read_profile='zero_origin_38'")
        == "2"
    )
    assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "2"
    assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == HEAD


def test_real_recovery_reconstructs_missing_database_container_on_original_volume(
    recovery: RecoveryRun,
):
    original = json.loads(checked_docker("inspect", recovery.postgres))[0]
    checked_docker("stop", recovery.postgres)
    checked_docker("rename", recovery.postgres, recovery.postgres + "-interrupted")
    result = recovery.recover()
    assert result["status"] == "rolled_back", result
    restored = json.loads(checked_docker("inspect", recovery.postgres))[0]
    assert restored["Id"] != original["Id"]
    assert restored["Mounts"] == original["Mounts"]
    assert restored["HostConfig"]["RestartPolicy"] == original["HostConfig"]["RestartPolicy"]
    assert sql(recovery.postgres, "SELECT dev_number FROM devices") == "OLD_DEVICE"


@pytest.mark.parametrize("marker", ["missing", "previous_terminal"])
def test_real_recovery_reconciles_only_prepared_marker_gap(recovery: RecoveryRun, marker: str):
    path = recovery.state / "full-upgrade-maintenance.json"
    if marker == "missing":
        path.rename(recovery.state / "marker-before-interruption.json")
    else:
        state = json.loads(path.read_text(encoding="utf-8"))
        state.update(
            {
                "operation_id": "fdfc5702-60fa-40cf-85b9-93f205909dc6",
                "status": "committed",
                "journal_path": str(
                    recovery.state / "full-upgrade-fdfc5702-60fa-40cf-85b9-93f205909dc6.json"
                ),
            }
        )
        path.write_text(json.dumps(state), encoding="utf-8")
    result = recovery.recover()
    assert result["status"] == "rolled_back", result
    assert json.loads(path.read_text(encoding="utf-8"))["operation_id"] == recovery.proof.operation


def test_real_migration_transaction_failure_recovers_previous(recovery: RecoveryRun):
    script = r"""
import asyncio,asyncpg
from alembic.config import Config
from alembic import command
async def arrange():
    c=await asyncpg.connect('postgresql://ruisheng_admin@127.0.0.1:5432/ruisheng')
    await c.execute('CREATE FUNCTION public.bounded_reject_ddl() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN IF tg_tag = \'CREATE INDEX\' THEN RAISE EXCEPTION \'owned_migration_failure\'; END IF; END $$; CREATE EVENT TRIGGER bounded_reject_index ON ddl_command_start EXECUTE FUNCTION public.bounded_reject_ddl()')
    await c.close()
asyncio.run(arrange())
command.upgrade(Config('alembic.ini'),'0013_serial_polling_profile')
"""
    name = recovery.migration(script=script)
    assert checked_docker("wait", name) != "0"
    sql(
        recovery.postgres,
        "DROP EVENT TRIGGER bounded_reject_index; DROP FUNCTION bounded_reject_ddl();",
    )
    assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == PREVIOUS
    assert (
        sql(
            recovery.postgres,
            "SELECT count(*) FROM information_schema.columns WHERE table_name='devices' AND column_name='read_profile'",
        )
        == "0"
    )
    recovery.run("Save-BoundedPhase $journal 'migration_running'")
    result = recovery.recover()
    assert result["status"] == "rolled_back", result


def test_real_migration_timeout_keeps_fence_then_observes_later_commit(recovery: RecoveryRun):
    script = """
import asyncio,asyncpg,pathlib,time
from alembic.config import Config
from alembic import command
async def hold():
    c=await asyncpg.connect('postgresql://ruisheng_admin@127.0.0.1:5432/ruisheng')
    tr=c.transaction(); await tr.start()
    await c.execute('SELECT 1')
    while not pathlib.Path('/tmp/owned-continue').exists(): await asyncio.sleep(.2)
    await tr.rollback(); await c.close()
asyncio.run(hold())
command.upgrade(Config('alembic.ini'),'0013_serial_polling_profile')
"""
    name = recovery.migration(script=script)
    recovery.run("Save-BoundedPhase $journal 'migration_running'")
    result = recovery.run(f"""
try {{ Invoke-DockerText @('wait',{ps_literal(name)}) 1; throw 'unexpected_completed' }} catch {{ $_.Exception.Message }}
""")
    assert result == "docker_command_timeout"
    assert json.loads(checked_docker("inspect", name))[0]["State"]["Running"]
    result = recovery.recover()
    assert result == {
        "status": "recovery_failed",
        "error": "migration_execution_uncertain",
        "phase": "migration_running",
    }
    assert (
        sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'")
        == "f"
    )
    checked_docker("exec", name, "touch", "/tmp/owned-continue")
    assert checked_docker("wait", name) == "0"
    result = recovery.recover()
    assert result["status"] == "committed", result


def test_real_recovery_audit_failure_after_pointer_commit_preserves_forward_state(
    recovery: RecoveryRun,
):
    name = recovery.migration()
    assert checked_docker("wait", name) == "0"
    failed = recovery.run("""
Set-Item Function:Write-RealAudit ${function:Write-Audit}
function Write-Audit {
  param($Event,$Result,$CandidateIdentity,$ErrorCode='')
  if ($Event -ceq 'upgrade_committed') { throw 'owned_audit_failure' }
  Write-RealAudit $Event $Result $CandidateIdentity $ErrorCode
}
try { Invoke-BoundedRecovery $journal }
catch { Record-BoundedFailure $journal ([string]$_.Exception.Message) }
@{status=$journal.status;error=$journal.error_code;phase=$journal.migration.phase} | ConvertTo-Json -Compress
""")
    assert json.loads(failed)["error"] == "commit_audit_incomplete"
    pointer = json.loads((recovery.state / "active-release.json").read_text(encoding="utf-8"))
    assert pointer["operation_id"] == recovery.proof.operation
    sql(
        recovery.postgres,
        "UPDATE devices SET dev_name='retained-after-pointer' WHERE dev_number='OLD_DEVICE'",
    )
    result = recovery.recover()
    assert result["status"] == "committed", result
    assert (
        sql(recovery.postgres, "SELECT dev_name FROM devices WHERE dev_number='OLD_DEVICE'")
        == "retained-after-pointer"
    )


def test_real_recovery_unknown_or_incomplete_schema_stays_fenced(recovery: RecoveryRun):
    sql(recovery.postgres, "UPDATE alembic_version SET version_num='unknown_revision'")
    result = recovery.recover()
    assert result["error"] == "recovery_database_head_unknown"
    sql(recovery.postgres, f"UPDATE alembic_version SET version_num='{HEAD}'")
    result = recovery.recover()
    assert result["error"] == "recovery_database_structure_invalid"
    assert (
        json.loads((recovery.state / "full-upgrade-maintenance.json").read_text(encoding="utf-8"))[
            "status"
        ]
        == "active"
    )
    assert (
        sql(
            recovery.postgres,
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND rolcanlogin",
        )
        == "0"
    )


def test_real_lost_lock_keeps_marker_and_recovery_materials(recovery: RecoveryRun):
    result = recovery.run("""
Stop-BoundedApplications $journal
Save-BoundedPhase $journal 'writers_fenced'
$record=Convert-UpgradeJson (Get-Content (Join-Path $StateDirectory 'shared.lock') -Raw)
$record.expires_at=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')
Write-JsonAtomic (Join-Path $StateDirectory 'shared.lock') $record
try { Invoke-BoundedRecovery $journal } catch { Record-BoundedFailure $journal ([string]$_.Exception.Message) }
$journal.error_code
""")
    assert result == "upgrade_lock_lost"
    assert (
        json.loads((recovery.state / "full-upgrade-maintenance.json").read_text(encoding="utf-8"))[
            "status"
        ]
        == "active"
    )
    assert (recovery.state / ".prospective.env").exists()
    journal = json.loads(
        (recovery.state / f"full-upgrade-{recovery.proof.operation}.json").read_text(
            encoding="utf-8"
        )
    )
    assert journal["migration"]["phase"] == "writers_fenced"
    assert (
        sql(
            recovery.postgres,
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND rolcanlogin",
        )
        == "0"
    )
    result = recovery.recover()
    assert result["status"] == "rolled_back", result


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_real_bounded_apply_runs_backup_restore_migration_and_commit(
    recovery: RecoveryRun, executable
):
    recovery.proof.executable = executable
    sql(recovery.postgres, "ALTER ROLE ruisheng_gw LOGIN")
    operation = str(uuid4())
    try:
        result = recovery.run(f"""
Release-Locks
$OperationId='{operation}'
$journal.operation_id=$OperationId
foreach ($lockName in @('shared','legacy')) {{ Acquire-LeasedLock (Join-Path $StateDirectory ($lockName+'.lock')) $lockName }}
$BackupDirectory=Join-Path $StateDirectory 'apply-backup-{operation}'
$JournalPath=Join-Path $StateDirectory 'full-upgrade-{operation}.json'
$ProspectiveEnvPath=Join-Path $StateDirectory '.prospective-{operation}.env'
Move-Item $MaintenanceStatePath (Join-Path $StateDirectory 'prepared-marker-retained.json')
$manifest=Convert-UpgradeJson (Get-Content {ps_literal(recovery.target_root / "MANIFEST.json")} -Raw -Encoding UTF8)
$images=Assert-CandidateManifest $manifest {ps_literal(recovery.target_root)}
Write-ProspectiveEnvironment $EnvFile $ProspectiveEnvPath (Get-ReleaseValues $manifest $images)
$journal.migration=$null;$journal.backup=$null;$journal.environment_backup=$null
function Assert-InstalledMaintenanceGuards {{ 'c'*64 }}
Invoke-BoundedApply $journal $manifest $images
@{{status=$journal.status;error=$journal.error_code;phase=$journal.migration.phase;verified=$journal.backup.restore_verified}} | ConvertTo-Json -Compress
""")
        assert json.loads(result) == {
            "status": "committed",
            "error": "",
            "phase": "completed",
            "verified": True,
        }
        assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == HEAD
        assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
        journal = json.loads(
            (recovery.state / f"full-upgrade-{operation}.json").read_text(encoding="utf-8")
        )
        assert journal["migration"]["roles"] == [
            {"name": "ruisheng_api", "login": False},
            {"name": "ruisheng_gw", "login": True},
        ]
        assert all(type(role["login"]) is bool for role in journal["migration"]["roles"])
        assert (
            sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_api'")
            == "f"
        )
        assert (
            sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'")
            == "t"
        )
        for service, image in (("postgres", SOURCE_IMAGE), ("redis", REDIS_IMAGE)):
            info = json.loads(
                checked_docker("inspect", recovery.postgres.removesuffix("postgres") + service)
            )[0]
            assert (
                info["Config"]["Image"]
                == f"ruisheng-candidate/{service}:{recovery.target_root.name}"
            )
            assert info["Image"] == image
            assert info["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
    finally:
        for container in checked_docker(
            "ps", "-q", "--filter", f"label=com.ruisheng.upgrade.operation={operation}"
        ).splitlines():
            checked_docker("stop", "--time", "10", container)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("scenario", ["natural_exit", "persistent", "lost_lock"])
def test_real_unknown_writer_gate_waits_without_whitelisting_or_termination(
    source_database, executable, scenario
):
    operation = str(uuid4())
    proof = ProofRun(
        source_database,
        operation,
        ROOT / "tmp-test-logs" / f"bounded-writer-{operation}",
        executable,
    )
    observed_path = proof.directory / "writer-observed"
    # Even a client bearing the health probe name must actually disappear before release.
    holder = subprocess.Popen(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "PGAPPNAME=pg_isready",
            source_database,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "ruisheng_admin",
            "-d",
            "ruisheng",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert holder.stdin is not None and holder.stdout is not None

    def close_after_observation():
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if observed_path.exists():
                holder.stdin.close()
                return
            time.sleep(0.05)
        raise AssertionError("the actual writer query was never observed")

    try:
        holder.stdin.write("SELECT 1;\n")
        holder.stdin.flush()
        assert holder.stdout.readline().strip() == "1"
        with ThreadPoolExecutor(max_workers=1) as executor:
            closing = (
                executor.submit(close_after_observation) if scenario == "natural_exit" else None
            )
            result = proof.run(
                f"""
Set-Item Function:Invoke-WriterGateSql ${{function:Invoke-DatabaseSql}}
$script:counts=@(); $script:timeouts=@()
function Invoke-DatabaseSql {{
  param($Sql,$Container='ruisheng-postgres',$User='ruisheng_admin',$Database='ruisheng',$TimeoutSeconds=120)
  $script:timeouts+=$TimeoutSeconds
  $count=Invoke-WriterGateSql $Sql $Container $User $Database $TimeoutSeconds
  $script:counts+=$count
  if ($count -cne '0') {{
    [IO.File]::WriteAllText({ps_literal(observed_path)},'observed')
    if ('{scenario}' -ceq 'lost_lock') {{
      $path=Join-Path $StateDirectory 'shared.lock'
      $record=Convert-UpgradeJson (Get-Content $path -Raw)
      $record.expires_at=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')
      Write-JsonAtomic $path $record
    }}
  }}
  return $count
}}
$clock=[Diagnostics.Stopwatch]::StartNew()
try {{ Assert-NoUnknownWriters; $failure='' }} catch {{ $failure=$_.Exception.Message }}
$evidence=@{{error=$failure;counts=$script:counts;timeouts=$script:timeouts;elapsed=$clock.Elapsed.TotalSeconds}}
[IO.File]::WriteAllText((Join-Path $StateDirectory 'writer-gate-result.json'),($evidence | ConvertTo-Json -Compress))
$evidence | ConvertTo-Json -Compress
""",
                timeout=90,
            )
            if closing is not None:
                closing.result(timeout=5)
        observed = json.loads(result)
        assert int(observed["counts"][0]) > 0
        assert all(1 <= timeout <= 14 for timeout in observed["timeouts"])
        if scenario == "natural_exit":
            assert observed["error"] == "", observed
            assert observed["counts"][-1] == "0"
            assert holder.wait(timeout=10) == 0
        else:
            expected_errors = (
                {"upgrade_lock_lost"}
                if scenario == "lost_lock"
                else {
                    "unknown_database_writer_present",
                    "docker_command_timeout",
                }
            )
            assert observed["error"] in expected_errors, observed
            assert all(int(count) > 0 for count in observed["counts"])
            assert holder.poll() is None
            assert (
                sql(
                    source_database,
                    "SELECT count(*) FROM pg_stat_activity WHERE application_name='pg_isready' AND state='idle'",
                )
                == "1"
            )
            if scenario == "persistent":
                # The runner may need additional time to contain a timed-out process tree.
                assert 13 <= observed["elapsed"] < 25
                assert len(observed["counts"]) > 1
            else:
                assert len(observed["counts"]) == 1
    finally:
        if not holder.stdin.closed:
            holder.stdin.close()
        holder.wait(timeout=10)
        holder.stdout.close()
        if holder.stderr is not None:
            holder.stderr.close()


@pytest.mark.parametrize("audit_fails", [False, True])
def test_real_apply_initial_failure_audit_does_not_block_rollback(
    recovery: RecoveryRun, audit_fails
):
    operation = str(uuid4())
    result = recovery.run(f"""
Release-Locks
$OperationId='{operation}'; $journal.operation_id=$OperationId
foreach ($lockName in @('shared','legacy')) {{ Acquire-LeasedLock (Join-Path $StateDirectory ($lockName+'.lock')) $lockName }}
$BackupDirectory=Join-Path $StateDirectory 'apply-backup-{operation}'
$JournalPath=Join-Path $StateDirectory 'full-upgrade-{operation}.json'
$ProspectiveEnvPath=Join-Path $StateDirectory '.prospective-{operation}.env'
Move-Item $MaintenanceStatePath (Join-Path $StateDirectory 'prepared-marker-retained.json')
$manifest=Convert-UpgradeJson (Get-Content {ps_literal(recovery.target_root / "MANIFEST.json")} -Raw -Encoding UTF8)
$images=Assert-CandidateManifest $manifest {ps_literal(recovery.target_root)}
Write-ProspectiveEnvironment $EnvFile $ProspectiveEnvPath (Get-ReleaseValues $manifest $images)
$journal.migration=$null; $journal.backup=$null; $journal.environment_backup=$null
Set-Item Function:Write-ApplyAudit ${{function:Write-Audit}}
$script:auditCalls=@()
function Write-Audit {{
  param($Event,$Result,$CandidateIdentity,$ErrorCode='')
  $script:auditCalls+=@{{event=$Event;error=$ErrorCode}}
  if ($Event -ceq 'upgrade_apply_failed' -and ${str(audit_fails).lower()}) {{ throw 'owned_first_audit_failure' }}
  Write-ApplyAudit $Event $Result $CandidateIdentity $ErrorCode
}}
function New-BoundedDatabaseBackup {{ throw 'owned_backup_failure' }}
Invoke-BoundedApply $journal $manifest $images
@{{status=$journal.status;error=$journal.error_code;phase=$journal.migration.phase;calls=$script:auditCalls}} | ConvertTo-Json -Depth 8 -Compress
""")
    observed = json.loads(result)
    assert observed == {
        "status": "rolled_back",
        "error": "",
        "phase": "completed",
        "calls": [
            {"event": "upgrade_apply_failed", "error": "owned_backup_failure"},
            {"event": "upgrade_rolled_back", "error": ""},
        ],
    }
    records = [
        json.loads(line)
        for line in (recovery.state / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event"] for record in records] == (
        ["upgrade_rolled_back"] if audit_fails else ["upgrade_apply_failed", "upgrade_rolled_back"]
    )
    if not audit_fails:
        assert records[0]["error_code"] == "owned_backup_failure"
    assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == PREVIOUS
    assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
    marker = json.loads(
        (recovery.state / "full-upgrade-maintenance.json").read_text(encoding="utf-8")
    )
    assert marker["status"] == "rolled_back"
    assert marker["operation_id"] == operation
    for service in ("gw", "api", "web"):
        info = json.loads(
            checked_docker("inspect", recovery.postgres.removesuffix("postgres") + service)
        )[0]
        assert info["State"]["Running"] is True


def _device_collation_mutation(column: str, collation: str, *, target: bool) -> str:
    lengths = {"read_profile": 20, "transport_type": 10, "serial_port": 50}
    assert column in lengths and (target or column != "read_profile")
    assert collation in (
        'pg_catalog."C"',
        "public.bounded_equivalent",
        "public.bounded_case_insensitive",
    )
    checks = (
        """
ALTER TABLE public.devices DROP CONSTRAINT ck_devices_read_profile;
ALTER TABLE public.devices ADD CONSTRAINT ck_devices_read_profile CHECK (read_profile IN ('point_groups','zero_origin_38'));
ALTER TABLE public.devices DROP CONSTRAINT ck_devices_read_profile_transport;
ALTER TABLE public.devices ADD CONSTRAINT ck_devices_read_profile_transport CHECK (read_profile <> 'zero_origin_38' OR transport_type='serial');
"""
        if target
        else ""
    )
    predicate = "transport_type='serial'" + (" AND deleted_at IS NULL" if target else "")
    # Prove the counterexample against real catalogs: reparse the reviewed SQL
    # after ALTER, retaining all guard-visible CHECK/predicate text and rebuilding
    # the index with the drifted column's own collation. No shape JSON is mocked.
    return f"""
DO $bounded_collation$
DECLARE prior_checks jsonb; prior_predicate text;
BEGIN
  SELECT jsonb_object_agg(conname,pg_get_expr(conbin,conrelid,false)) INTO prior_checks
    FROM pg_constraint WHERE conrelid='public.devices'::regclass
      AND conname IN ('ck_devices_read_profile','ck_devices_read_profile_transport');
  SELECT pg_get_expr(indpred,indrelid,false) INTO prior_predicate
    FROM pg_index WHERE indexrelid='public.uq_devices_serial_port_modbus_addr'::regclass;
  CREATE COLLATION public.bounded_equivalent FROM pg_catalog."C";
  CREATE COLLATION public.bounded_case_insensitive (provider=icu,locale='und-u-ks-level2',deterministic=false);
  ALTER TABLE public.devices ALTER COLUMN {column} TYPE varchar({lengths[column]}) COLLATE {collation};
  {checks}
  DROP INDEX public.uq_devices_serial_port_modbus_addr;
  CREATE UNIQUE INDEX uq_devices_serial_port_modbus_addr ON public.devices(serial_port,modbus_addr) WHERE {predicate};
  IF prior_checks IS DISTINCT FROM (
    SELECT jsonb_object_agg(conname,pg_get_expr(conbin,conrelid,false))
      FROM pg_constraint WHERE conrelid='public.devices'::regclass
        AND conname IN ('ck_devices_read_profile','ck_devices_read_profile_transport'))
    OR prior_predicate IS DISTINCT FROM (
      SELECT pg_get_expr(indpred,indrelid,false)
        FROM pg_index WHERE indexrelid='public.uq_devices_serial_port_modbus_addr'::regclass)
  THEN RAISE EXCEPTION 'collation_fixture_changed_reviewed_expression'; END IF;
  IF EXISTS (
    SELECT 1 FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey,i.indcollation) k(attnum,collation_id)
      JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
      WHERE i.indexrelid='public.uq_devices_serial_port_modbus_addr'::regclass
        AND a.attcollation<>k.collation_id)
  THEN RAISE EXCEPTION 'collation_fixture_index_mismatch'; END IF;
END $bounded_collation$;
"""


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_real_source_schema_accepts_reviewed_shape_and_rejects_predicate_drift(
    source_database, executable
):
    operation = str(uuid4())
    proof = ProofRun(
        source_database,
        operation,
        ROOT / "tmp-test-logs" / f"bounded-schema-{operation}",
        executable,
    )
    mutations = [
        "DROP INDEX public.uq_devices_serial_port_modbus_addr; CREATE UNIQUE INDEX uq_devices_serial_port_modbus_addr ON public.devices(serial_port,modbus_addr) WHERE transport_type='serial' AND modbus_addr<>7;",
        *(
            _device_collation_mutation(column, collation, target=False)
            for column in ("serial_port", "transport_type")
            for collation in (
                'pg_catalog."C"',
                "public.bounded_equivalent",
                "public.bounded_case_insensitive",
            )
        ),
    ]
    proof.directory.mkdir(parents=True, exist_ok=True)
    mutations_path = proof.directory / "source-schema-mutations.json"
    mutations_path.write_text(json.dumps(mutations), encoding="utf-8")
    result = proof.run(f"""
Assert-BoundedSchema $BoundedSourceHead
Set-Item Function:Invoke-OriginalDatabaseSql ${{function:Invoke-DatabaseSql}}
function Invoke-DatabaseSql {{
  param($Sql,$Container='ruisheng-postgres',$User='ruisheng_admin',$Database='ruisheng',$TimeoutSeconds=120)
  Invoke-OriginalDatabaseSql ("BEGIN;"+$script:mutation+$Sql+"ROLLBACK;") $Container $User $Database $TimeoutSeconds
}}
$failures=@(foreach ($script:mutation in (Get-Content -LiteralPath {ps_literal(mutations_path)} -Raw -Encoding UTF8 | ConvertFrom-Json)) {{
  try {{ Assert-BoundedSchema $BoundedSourceHead; 'unexpected_pass' }} catch {{ $_.Exception.Message }}
}})
ConvertTo-Json -InputObject $failures -Compress
""")
    assert json.loads(result) == ["recovery_database_structure_invalid"] * len(mutations)
    assert sql(source_database, "SELECT version_num FROM alembic_version") == PREVIOUS


def test_real_target_schema_rejects_keyword_preserving_semantic_drift(recovery: RecoveryRun):
    name = recovery.migration()
    assert checked_docker("wait", name) == "0"
    mutations = [
        "ALTER TABLE public.devices DROP CONSTRAINT ck_devices_read_profile; ALTER TABLE public.devices ADD CONSTRAINT ck_devices_read_profile CHECK (read_profile IN ('point_groups','zero_origin_38','unreviewed'));",
        "ALTER TABLE public.devices DROP CONSTRAINT ck_devices_read_profile_transport; ALTER TABLE public.devices ADD CONSTRAINT ck_devices_read_profile_transport CHECK (read_profile <> 'zero_origin_38' OR transport_type IN ('serial','tcp'));",
        "DROP INDEX public.uq_devices_serial_port_modbus_addr; CREATE UNIQUE INDEX uq_devices_serial_port_modbus_addr ON public.devices(serial_port,modbus_addr) WHERE transport_type='serial' AND deleted_at IS NULL AND modbus_addr<>7;",
        "DROP INDEX public.uq_devices_serial_port_modbus_addr; CREATE UNIQUE INDEX uq_devices_serial_port_modbus_addr ON public.devices(serial_port,modbus_addr) INCLUDE(read_profile) WHERE transport_type='serial' AND deleted_at IS NULL;",
        "ALTER TABLE public.devices ALTER COLUMN read_profile TYPE varchar(21);",
        "ALTER TABLE public.devices ALTER COLUMN read_profile DROP NOT NULL;",
        "ALTER TABLE public.devices ALTER COLUMN read_profile SET DEFAULT 'zero_origin_38';",
        "ALTER TABLE public.devices DROP CONSTRAINT ck_devices_read_profile; ALTER TABLE public.devices ADD CONSTRAINT ck_devices_read_profile CHECK (read_profile IN ('point_groups','zero_origin_38')) NOT VALID;",
        *(
            _device_collation_mutation(column, collation, target=True)
            for column in ("read_profile", "serial_port", "transport_type")
            for collation in (
                'pg_catalog."C"',
                "public.bounded_equivalent",
                "public.bounded_case_insensitive",
            )
        ),
    ]
    mutations_path = recovery.state / "target-schema-mutations.json"
    mutations_path.write_text(json.dumps(mutations), encoding="utf-8")
    for executable in ("powershell.exe", "pwsh.exe"):
        recovery.proof.executable = executable
        result = recovery.run(f"""
Assert-BoundedSchema $BoundedTargetHead
Set-Item Function:Invoke-OriginalDatabaseSql ${{function:Invoke-DatabaseSql}}
function Invoke-DatabaseSql {{
  param($Sql,$Container='ruisheng-postgres',$User='ruisheng_admin',$Database='ruisheng',$TimeoutSeconds=120)
  Invoke-OriginalDatabaseSql ("BEGIN;"+$script:mutation+$Sql+"ROLLBACK;") $Container $User $Database $TimeoutSeconds
}}
$failures=@(foreach ($script:mutation in (Get-Content -LiteralPath {ps_literal(mutations_path)} -Raw -Encoding UTF8 | ConvertFrom-Json)) {{
  try {{ Assert-BoundedSchema $BoundedTargetHead; 'unexpected_pass' }} catch {{ $_.Exception.Message }}
}})
ConvertTo-Json -InputObject $failures -Compress
""")
        assert json.loads(result) == ["recovery_database_structure_invalid"] * len(mutations)
    assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == HEAD
    assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_real_recovery_collation_drift_keeps_writers_fenced(recovery: RecoveryRun, executable):
    recovery.proof.executable = executable
    migration = recovery.migration()
    assert checked_docker("wait", migration) == "0"
    assert (
        sql(
            recovery.postgres,
            "SELECT read_profile = 'POINT_GROUPS' FROM devices WHERE dev_number='OLD_DEVICE'",
        )
        == "f"
    )
    # The mutation preserves actual deparsed CHECKs/predicate and changes only
    # implicit comparison semantics. The new data must survive refusal intact.
    sql(
        recovery.postgres,
        _device_collation_mutation("read_profile", "public.bounded_case_insensitive", target=True)
        + "UPDATE public.devices SET read_profile='POINT_GROUPS' WHERE dev_number='OLD_DEVICE';",
    )
    assert (
        sql(recovery.postgres, "SELECT read_profile FROM devices WHERE dev_number='OLD_DEVICE'")
        == "POINT_GROUPS"
    )
    assert (
        sql(
            recovery.postgres,
            "SELECT read_profile = 'point_groups' FROM devices WHERE dev_number='OLD_DEVICE'",
        )
        == "t"
    )
    history_query = "SELECT md5(string_agg(row_to_json(h)::text,E'\\n' ORDER BY recorded_at,dev_number,point_id)) FROM point_data_history h"
    devices_query = (
        "SELECT md5(string_agg(row_to_json(d)::text,E'\\n' ORDER BY dev_number)) FROM devices d"
    )
    before_history = sql(recovery.postgres, history_query)
    before_devices = sql(recovery.postgres, devices_query)
    assert before_history and before_devices
    assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
    # Use independently live inert fixture containers/roles to demonstrate that
    # the real recovery refusal reestablishes both persistent write barriers.
    sql(recovery.postgres, "ALTER ROLE ruisheng_api LOGIN; ALTER ROLE ruisheng_gw LOGIN;")
    applications = [
        recovery.postgres.removesuffix("postgres") + service for service in ("api", "gw", "web")
    ]
    checked_docker("start", *applications)
    result = recovery.recover()
    assert result["status"] == "recovery_failed", result
    assert result["error"] == "recovery_database_structure_invalid", result
    assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == HEAD
    assert sql(recovery.postgres, history_query) == before_history
    assert sql(recovery.postgres, devices_query) == before_devices
    assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
    assert (
        sql(
            recovery.postgres,
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND rolcanlogin",
        )
        == "0"
    )
    for application in applications:
        assert json.loads(checked_docker("inspect", application))[0]["State"]["Running"] is False
    marker = json.loads((recovery.state / "full-upgrade-maintenance.json").read_text())
    assert marker["status"] == "active"
    assert marker["operation_id"] == recovery.proof.operation


def test_real_nested_recovery_refusal_and_persistent_journal_failure_refence_writers(
    recovery: RecoveryRun,
):
    name = recovery.migration()
    assert checked_docker("wait", name) == "0"
    result = recovery.run("""
$script:checks=0
function Assert-EntitlementFeature { $script:checks++; if ($script:checks -gt 1) { throw 'entitlement_denied' } }
Set-Item Function:Write-OriginalJson ${function:Write-JsonAtomic}
$script:diskFailed=$false
function Write-JsonAtomic { param($Path,$Value) if ($script:diskFailed) { throw 'disk_full' }; Write-OriginalJson $Path $Value }
function Wait-BoundedHealthy {
  param($ComposeBase)
  if ((Invoke-DatabaseSql "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'") -cne 't') { throw 'never_unfenced' }
  [void](Invoke-DatabaseSql "UPDATE devices SET dev_name='retained-between-failures' WHERE dev_number='OLD_DEVICE'")
  $script:diskFailed=$true
  throw 'service_health_failed'
}
try { Invoke-BoundedRecovery $journal }
catch {
  $failure=$_.Exception.Message
  try { Invoke-BoundedRecovery $journal } catch { Record-BoundedFailure $journal $failure }
}
@{error=$journal.error_code;checks=$script:checks} | ConvertTo-Json -Compress
""")
    assert json.loads(result) == {"error": "service_health_failed", "checks": 2}
    assert (
        sql(
            recovery.postgres,
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND rolcanlogin",
        )
        == "0"
    )
    assert (
        sql(recovery.postgres, "SELECT dev_name FROM devices WHERE dev_number='OLD_DEVICE'")
        == "retained-between-failures"
    )
    for service in ("gw", "api", "web"):
        info = json.loads(
            checked_docker("inspect", recovery.postgres.removesuffix("postgres") + service)
        )[0]
        assert info["State"]["Running"] is False
        assert info["HostConfig"]["RestartPolicy"]["Name"] == "no"
    assert recovery.recover()["status"] == "committed"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_real_outer_recover_preflight_cleanup_after_parent_death(recovery: RecoveryRun, executable):  # noqa: PLR0912, PLR0915
    # Replay all 14 faults against the same retained crash journal and real assets.
    recovery.proof.executable = executable
    migration = recovery.migration()
    assert checked_docker("wait", migration) == "0"
    audit = recovery.state / "outer-audit"
    env_path = recovery.state / ".env.prod"
    journal_path = recovery.state / f"full-upgrade-{recovery.proof.operation}.json"
    marker_path = recovery.state / "full-upgrade-maintenance.json"
    active_path = recovery.state / "active-release.json"
    applications = [
        recovery.postgres.removesuffix("postgres") + service for service in ("api", "gw", "web")
    ]

    def prepare_acls():
        recovery.run(f"""
{_test_dacl_helpers()}
foreach ($testItem in @((Get-Item -LiteralPath $StateDirectory)) + @(Get-ChildItem -LiteralPath $StateDirectory -Recurse -Force)) {{
  Reset-TestAccessRules $testItem @(Get-AllowedSids)
}}
$testAudit={ps_literal(audit)}
if (-not (Test-Path -LiteralPath $testAudit)) {{ New-Item -ItemType Directory -Path $testAudit | Out-Null }}
$testAuditSid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$testAuditItem=Get-Item -LiteralPath $testAudit
Reset-TestAccessRules $testAuditItem @('S-1-5-18',$testAuditSid.Value)
$log=Join-Path $testAudit 'full-upgrade.jsonl'
if (Test-Path -LiteralPath $log) {{
  $logItem=Get-Item -LiteralPath $log
  $logAcl=Get-TestAccessAcl $logItem
  $logAcl.SetAccessRuleProtection($false,$false)
  foreach ($oldRule in @($logAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object {{ $null -ne $_ -and -not $_.IsInherited }})) {{ [void]$logAcl.RemoveAccessRuleSpecific($oldRule) }}
  Set-TestAccessAcl $logItem $logAcl
}}
$mutex=Join-Path $testAudit '.remote-maintenance-audit.lock'
if (-not (Test-Path -LiteralPath $mutex)) {{ [IO.File]::WriteAllText($mutex,'') }}
Reset-TestAccessRules (Get-Item -LiteralPath $mutex) @(Get-AllowedSids)
""")

    prepare_acls()
    crashed = recovery.outer_recover(
        setup="""
function Wait-BoundedHealthy {
  [void](Invoke-DatabaseSql "UPDATE devices SET dev_name='retained-full-entry-crash' WHERE dev_number='OLD_DEVICE'")
  Stop-Process -Id $PID -Force
}
"""
    )
    assert crashed.returncode != 0, crashed.stdout + crashed.stderr
    assert (
        sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'")
        == "t"
    )
    assert all(
        json.loads(checked_docker("inspect", name))[0]["State"]["Running"] for name in applications
    )
    persisted = json.loads(journal_path.read_text(encoding="utf-8"))
    assert persisted["migration"]["application_start_attempted"] is True
    assert persisted["migration"]["phase"] == "application_start_intent"
    crash_journal = recovery.state / "outer-interrupted-retained.json"
    shutil.copyfile(journal_path, crash_journal)
    crash_marker = marker_path.read_bytes()
    crash_active = active_path.read_bytes()
    crash_env = env_path.read_bytes()
    recovery.proof.expire_interrupted_locks()

    faults = (
        "env_missing",
        "env_acl",
        "audit_root_missing",
        "audit_root_acl",
        "audit_mutex_missing",
        "audit_mutex_acl",
        "unapproved",
        "unknown_operation",
        "unbound",
        "lost_lock",
        "active_drift",
        "database_drift",
        "application_drift",
        "completed",
    )
    for fault in faults:
        env_path.write_bytes(crash_env)
        marker_path.write_bytes(crash_marker)
        active_path.write_bytes(crash_active)
        recovery.run(
            f"$journal=Convert-UpgradeJson (Get-Content -Raw -Encoding UTF8 {ps_literal(crash_journal)}); Write-JsonAtomic $JournalPath $journal"
        )
        prepare_acls()
        sql(recovery.postgres, "ALTER ROLE ruisheng_gw LOGIN;")
        checked_docker("start", *applications)
        setup = ""
        operation = None
        if fault == "unbound":
            recovery.run("$journal.reason_hash='0'*64; Write-JsonAtomic $JournalPath $journal")
        elif fault == "active_drift":
            recovery.run(
                "$pointer=Read-ActiveRelease; $pointer.logical_identity='sha256:'+('f'*64); Write-JsonAtomic $ActiveReleasePath $pointer"
            )
        elif fault == "database_drift":
            recovery.run(
                "$journal.migration.database_system_identifier='0'; Write-JsonAtomic $JournalPath $journal"
            )
        elif fault == "application_drift":
            recovery.run(
                "$journal.migration.source_images.gw='sha256:'+('0'*64); $journal.migration.target_images.gw='sha256:'+('0'*64); Write-JsonAtomic $JournalPath $journal"
            )
        elif fault == "unknown_operation":
            operation = str(uuid4())
        elif fault == "lost_lock":
            foreign_operation = str(uuid4())
            setup = f"""
Set-Item Function:Assert-EnvBeforeLockFault ${{function:Assert-RestrictedFile}}
function Assert-RestrictedFile {{
  param($Path)
  if ($Path -ceq $EnvFile -and $AcquiredLocks.Count -eq 2) {{
    $lease=Convert-UpgradeJson (Get-Content -LiteralPath $SharedLockPath -Raw -Encoding UTF8)
    $lease.operation_id={ps_literal(foreign_operation)}
    [IO.File]::WriteAllText($SharedLockPath,($lease | ConvertTo-Json -Depth 8 -Compress))
  }}
  Assert-EnvBeforeLockFault $Path
}}
"""
        elif fault == "completed":
            assert recovery.recover()["status"] == "committed"

        if fault.endswith("_acl"):
            target = (
                env_path
                if fault == "env_acl"
                else audit
                if fault == "audit_root_acl"
                else audit / ".remote-maintenance-audit.lock"
            )
            recovery.run(f"""
{_test_dacl_helpers()}
$path={ps_literal(target)}
$item=Get-Item -LiteralPath $path
$acl=Get-TestAccessAcl $item
$rule=New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier('S-1-1-0')),'Read','Allow')
[void]$acl.AddAccessRule($rule)
Set-TestAccessAcl $item $acl
""")
        elif fault in ("audit_root_missing", "audit_mutex_missing"):
            target = (
                audit if fault == "audit_root_missing" else audit / ".remote-maintenance-audit.lock"
            )
            retained = target.with_name(target.name + "-retained-" + str(uuid4()))
            assert target.resolve().is_relative_to(recovery.state.resolve())
            assert retained.resolve().is_relative_to(recovery.state.resolve())
            target.rename(retained)
        else:
            env_path.rename(env_path.with_name(".env.prod-retained-" + str(uuid4())))

        result = recovery.outer_recover(
            setup=setup, approved=fault != "unapproved", operation=operation
        )
        assert result.returncode == 0, (fault, result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        assert payload["ok"] is False, (fault, payload)
        expected_code = (
            "restricted_acl_invalid"
            if fault.endswith("_acl")
            else (
                "restricted_directory_missing"
                if fault == "audit_root_missing"
                else "approval_required"
                if fault == "unapproved"
                else "full_upgrade_maintenance_active"
                if fault == "unknown_operation"
                else "restricted_file_missing"
            )
        )
        assert payload["error_code"] == expected_code, (fault, payload)
        no_cleanup = fault in (
            "unapproved",
            "unknown_operation",
            "unbound",
            "lost_lock",
            "active_drift",
            "completed",
        )
        for service, name in zip(("api", "gw", "web"), applications, strict=True):
            assert json.loads(checked_docker("inspect", name))[0]["State"]["Running"] is (
                no_cleanup or fault == "application_drift" and service == "gw"
            ), (fault, service, payload)
        assert sql(
            recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'"
        ) == ("t" if no_cleanup or fault == "database_drift" else "f"), (fault, payload)
        assert sql(
            recovery.postgres,
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND NOT rolcanlogin",
        ) == ("1" if no_cleanup or fault == "database_drift" else "2"), fault
        assert (
            sql(recovery.postgres, "SELECT dev_name FROM devices WHERE dev_number='OLD_DEVICE'")
            == "retained-full-entry-crash"
        )
        assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
        assert json.loads(marker_path.read_text())["status"] == (
            "committed" if fault == "completed" else "active"
        )
        if not no_cleanup:
            assert payload["status"] == "recovery_failed", (fault, payload)
            assert json.loads(journal_path.read_text())["error_code"] == payload["error_code"]
        if fault == "lost_lock":
            lock = recovery.state / "shared.lock"
            assert json.loads(lock.read_text())["operation_id"] == foreign_operation
            lock.rename(lock.with_name("shared.lock-foreign-retained-" + str(uuid4())))
    env_path.write_bytes(crash_env)


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_real_fresh_recovery_preflight_refusal_cleans_up_after_parent_death(
    recovery: RecoveryRun, executable
):
    recovery.proof.executable = executable
    migration = recovery.migration()
    assert checked_docker("wait", migration) == "0"
    interrupted = recovery.proof.powershell("""
function Wait-BoundedHealthy {
  [void](Invoke-DatabaseSql "UPDATE devices SET dev_name='retained-after-parent-death' WHERE dev_number='OLD_DEVICE'")
  Stop-Process -Id $PID -Force
}
Invoke-BoundedRecovery $journal
""")
    assert interrupted.returncode != 0
    assert (
        sql(recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'")
        == "t"
    )
    assert (
        sql(recovery.postgres, "SELECT dev_name FROM devices WHERE dev_number='OLD_DEVICE'")
        == "retained-after-parent-death"
    )
    persisted_path = recovery.state / f"full-upgrade-{recovery.proof.operation}.json"
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    interrupted_receipt = recovery.state / "interrupted-operation-retained.json"
    shutil.copyfile(persisted_path, interrupted_receipt)
    assert persisted["migration"]["application_start_attempted"] is True
    assert persisted["migration"]["phase"] == "application_start_intent"
    recovery.proof.expire_interrupted_locks()
    application_names = [
        recovery.postgres.removesuffix("postgres") + service for service in ("api", "gw", "web")
    ]
    for failure in (
        "entitlement",
        "candidate",
        "journal",
        "database_drift",
        "database_stopped",
        "application_drift",
    ):
        # Replay this exact, retained crash receipt only inside the same owned test site.
        recovery.run(
            f"$journal=Convert-UpgradeJson (Get-Content -Raw -Encoding UTF8 {ps_literal(interrupted_receipt)}); Write-JsonAtomic $JournalPath $journal"
        )
        sql(recovery.postgres, "ALTER ROLE ruisheng_gw LOGIN;")
        checked_docker("start", *application_names)
        if failure == "database_stopped":
            checked_docker("stop", recovery.postgres)
        setup = "function Assert-EntitlementFeature { throw 'entitlement_denied' }"
        if failure == "candidate":
            setup = "function Get-BoundedRelease { throw 'recovery_candidate_identity_drift' }"
        elif failure == "journal":
            setup += "\nfunction Write-JsonAtomic { throw 'disk_full' }\nfunction Write-Audit { throw 'disk_full' }"
        elif failure == "database_drift":
            setup = """
$journal.migration.database_system_identifier='0'
Write-JsonAtomic $JournalPath $journal
function Assert-EntitlementFeature { throw 'entitlement_denied' }
"""
        elif failure == "application_drift":
            setup = """
$journal.migration.source_images.gw='sha256:'+('0'*64)
$journal.migration.target_images.gw='sha256:'+('0'*64)
Write-JsonAtomic $JournalPath $journal
function Assert-EntitlementFeature { throw 'entitlement_denied' }
"""
        result = recovery.run(
            setup
            + """
if ($script:BoundedCleanupAuthority -or $script:BoundedRecoveryMutationStarted -or $script:BoundedRecoveryDatabaseVerified) { throw 'fresh_process_required' }
try { Invoke-BoundedRecovery $journal }
catch { Record-BoundedFailure $journal ([string]$_.Exception.Message) }
@{status=$journal.status;error=$journal.error_code} | ConvertTo-Json -Compress
"""
        )
        assert json.loads(result) == {
            "status": "recovery_failed",
            "error": "recovery_candidate_identity_drift"
            if failure == "candidate"
            else "entitlement_denied",
        }, failure
        for service, application_name in zip(("api", "gw", "web"), application_names, strict=True):
            info = json.loads(checked_docker("inspect", application_name))[0]
            assert info["State"]["Running"] is (
                failure == "application_drift" and service == "gw"
            ), failure
        if failure != "database_stopped":
            assert sql(
                recovery.postgres, "SELECT rolcanlogin FROM pg_roles WHERE rolname='ruisheng_gw'"
            ) == ("t" if failure == "database_drift" else "f"), failure
            assert (
                sql(recovery.postgres, "SELECT dev_name FROM devices WHERE dev_number='OLD_DEVICE'")
                == "retained-after-parent-death"
            )
        else:
            checked_docker("start", recovery.postgres)
            wait_ready(recovery.postgres)
        assert (
            json.loads((recovery.state / "full-upgrade-maintenance.json").read_text())["status"]
            == "active"
        )
    recovery.run(
        f"$journal=Convert-UpgradeJson (Get-Content -Raw -Encoding UTF8 {ps_literal(interrupted_receipt)}); Write-JsonAtomic $JournalPath $journal"
    )
    assert recovery.recover()["status"] == "committed"
    refused = recovery.run("""
function Assert-EntitlementFeature { throw 'entitlement_denied' }
try { Invoke-BoundedRecovery $journal } catch { Record-BoundedFailure $journal ([string]$_.Exception.Message) }
$journal.status
""")
    assert refused == "committed"
    assert (
        json.loads((recovery.state / "full-upgrade-maintenance.json").read_text())["status"]
        == "committed"
    )
    for service in ("api", "gw", "web"):
        assert (
            json.loads(
                checked_docker("inspect", recovery.postgres.removesuffix("postgres") + service)
            )[0]["State"]["Running"]
            is True
        )


def test_real_recovery_guard_drift_prevents_role_release(recovery: RecoveryRun):
    migration = recovery.migration()
    assert checked_docker("wait", migration) == "0"
    for failure in ("receipt", "task", "launcher"):
        setup = "[IO.File]::WriteAllText((Join-Path $StateDirectory 'installed-guard-receipt.json'),'isolated-test-guard-receipt')\n"
        if failure == "receipt":
            setup += "[IO.File]::WriteAllText((Join-Path $StateDirectory 'installed-guard-receipt.json'),'changed-test-guard-receipt')"
            expected = "installed_maintenance_guard_drift"
        else:
            expected = (
                "unprotected_startup_task_present"
                if failure == "task"
                else "installed_maintenance_guards_missing"
            )
            setup += f"function Assert-InstalledMaintenanceGuards {{ throw '{expected}' }}"
        result = recovery.run(
            setup
            + """
try { Invoke-BoundedRecovery $journal } catch { Record-BoundedFailure $journal ([string]$_.Exception.Message) }
@{status=$journal.status;error=$journal.error_code} | ConvertTo-Json -Compress
"""
        )
        assert json.loads(result) == {"status": "recovery_failed", "error": expected}
        assert (
            sql(
                recovery.postgres,
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND rolcanlogin",
            )
            == "0"
        )
        for service in ("gw", "api", "web"):
            assert (
                json.loads(
                    checked_docker("inspect", recovery.postgres.removesuffix("postgres") + service)
                )[0]["State"]["Running"]
                is False
            )
        assert (
            json.loads((recovery.state / "full-upgrade-maintenance.json").read_text())["status"]
            == "active"
        )


def _installed_guard_fixture_script(root: Path) -> str:
    """Rebase the real guards to retained NTFS fixtures, without replacing ACL checks."""
    return rf"""
$guardRoot={ps_literal(root)}
$guardParent=Join-Path $guardRoot 'installation'
$guardLauncherRoot=Join-Path $guardParent 'Launcher'
$guardLauncher=Join-Path $guardLauncherRoot 'start_ruisheng_local.ps1'
$guardReceipt=Join-Path $guardLauncherRoot 'schema-upgrade-guard.json'
$guardSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Protect-InstalledGuardFixture([string]$Path,[string]$Right='') {{
  $item=Get-Item -LiteralPath $Path -Force
  $acl=if ($item.PSIsContainer) {{ New-Object Security.AccessControl.DirectorySecurity }} else {{ New-Object Security.AccessControl.FileSecurity }}
  $acl.SetAccessRuleProtection($true,$false)
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($guardSid),'FullControl','Allow'))
  if ($Right) {{
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),$Right,'Allow'))
  }}
  if ($PSVersionTable.PSEdition -eq 'Core') {{ [IO.FileSystemAclExtensions]::SetAccessControl($item,$acl) }}
  else {{ $item.SetAccessControl($acl) }}
}}
if (-not (Test-Path -LiteralPath $guardRoot)) {{
  foreach ($path in @($guardRoot,$guardParent,$guardLauncherRoot)) {{
    [void](New-Item -ItemType Directory -Path $path); Protect-InstalledGuardFixture $path
  }}
  [IO.File]::WriteAllText($guardLauncher,"Write-Output 'inert fixture; never executed'")
  Protect-InstalledGuardFixture $guardLauncher
  $receipt=[ordered]@{{schema_version=1;guard_version='bounded-schema-v1';launcher=$guardLauncher;
    launcher_sha256=(Get-FileHash -LiteralPath $guardLauncher).Hash.ToLowerInvariant();installed_at='2026-09-10T00:00:00Z'}}
  [IO.File]::WriteAllText($guardReceipt,($receipt | ConvertTo-Json -Compress))
  Protect-InstalledGuardFixture $guardReceipt
}}
# ProofRun loads all production functions before the established external
# fixture boundaries. Restore the actual main guard after those boundaries.
foreach ($name in @('Assert-InstalledMaintenanceGuards','Assert-StartupScriptIdentity')) {{
  $nodes=@($ast.EndBlock.Statements | Where-Object {{ $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $name }})
  if ($nodes.Count -ne 1) {{ throw 'guard_function_missing' }}
  . ([ScriptBlock]::Create($nodes[0].Extent.Text))
}}
$body=${{function:Assert-InstalledMaintenanceGuards}}.ToString().Replace('C:\Program Files\Ruisheng\Launcher',$guardLauncherRoot).Replace('@("S-1-5-18", "S-1-5-32-544")',('@("'+$guardSid+'", "S-1-5-18", "S-1-5-32-544")'))
Set-Item Function:Assert-InstalledMaintenanceGuards ([ScriptBlock]::Create($body))
$body=${{function:Assert-StartupScriptIdentity}}.ToString().Replace("@('S-1-5-18'","@('$guardSid','S-1-5-18'")
Set-Item Function:Assert-StartupScriptIdentity ([ScriptBlock]::Create($body))
function Get-ScheduledTask {{ @() }}
"""


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_guard_recovery_fixture_reopens_real_protection_without_docker(executable):
    operation = str(uuid4())
    root = Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\") / f"rs-r7-recovery-{operation}"
    proof = ProofRun(
        "unused-test-source",
        operation,
        ROOT / "tmp-test-logs" / f"bounded-guard-fixture-{operation}",
        executable,
        setup="function Invoke-DockerText { throw 'unexpected_docker_call' }",
    )
    fixture = _installed_guard_fixture_script(root)
    expected = proof.run(
        fixture
        + "\n$journal.migration.guard_receipt_sha256=Assert-InstalledMaintenanceGuards"
        + "\nWrite-JsonAtomic $JournalPath $journal\n$journal.migration.guard_receipt_sha256"
    )
    assert len(expected) == 64
    result = proof.run(
        fixture
        + rf"""
if ($journal.migration.guard_receipt_sha256 -cne '{expected}') {{ throw 'fixture_receipt_binding_lost' }}
Assert-BoundedMaintenanceGuards $journal
Protect-InstalledGuardFixture $guardParent 'DeleteSubdirectoriesAndFiles'
try {{ Assert-BoundedMaintenanceGuards $journal; 'incorrectly_allowed' }} catch {{ $_.Exception.Message }}
finally {{ Protect-InstalledGuardFixture $guardParent }}
$receipt=Get-Content -LiteralPath $guardReceipt -Raw
try {{
  [IO.File]::AppendAllText($guardReceipt,' ')
  Assert-BoundedMaintenanceGuards $journal
  'incorrectly_allowed'
}} catch {{ $_.Exception.Message }}
finally {{ [IO.File]::WriteAllText($guardReceipt,$receipt) }}
Assert-BoundedMaintenanceGuards $journal
"""
    )
    assert result.splitlines() == [
        "installed_maintenance_guards_acl_invalid",
        "installed_maintenance_guard_drift",
    ]


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_real_recovery_guard_ancestry_drift_keeps_writers_fenced(recovery: RecoveryRun, executable):
    recovery.proof.executable = executable
    root = (
        Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\")
        / f"rs-r7-recovery-{recovery.proof.operation}"
    )
    assert not root.exists()
    # A dot-sourced fixture keeps the real recovery command below Windows' CLI
    # limit. It initializes once, then preserves receipt bytes across processes.
    fixture = recovery.state / "installed-guard-fixture.ps1"
    fixture.write_text(_installed_guard_fixture_script(root), encoding="utf-8-sig")
    adapter = f". {ps_literal(fixture)}\n"
    receipt_hash = recovery.run(
        adapter
        + """
$journal.migration.guard_receipt_sha256=Assert-InstalledMaintenanceGuards
Write-JsonAtomic $JournalPath $journal
$journal.migration.guard_receipt_sha256
"""
    )
    migration = recovery.migration()
    assert checked_docker("wait", migration) == "0"
    history_query = "SELECT md5(string_agg(row_to_json(h)::text,E'\\n' ORDER BY recorded_at,dev_number,point_id)) FROM point_data_history h"
    before = sql(recovery.postgres, history_query)
    assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
    for failure in ("delete_child", "junction"):
        # Each refusal starts with independently live test writers. These are
        # the fixture's inert containers, never actual old application images.
        sql(recovery.postgres, "ALTER ROLE ruisheng_api LOGIN; ALTER ROLE ruisheng_gw LOGIN;")
        applications = [
            recovery.postgres.removesuffix("postgres") + service for service in ("api", "gw", "web")
        ]
        checked_docker("start", *applications)
        result = recovery.run(
            adapter
            + rf"""
$failure='{failure}'
$retained=Join-Path $guardRoot 'retained-installation'
$link=Join-Path $guardRoot 'retained-junction'
if ($failure -eq 'junction') {{
  foreach ($path in @($guardParent,$retained,$link)) {{
    if (-not $path.StartsWith($guardRoot+'\')) {{ throw 'guard_fixture_scope_invalid' }}
  }}
  Move-Item -LiteralPath $guardParent -Destination $retained
  [void](New-Item -ItemType Junction -Path $guardParent -Target $retained)
}} else {{ Protect-InstalledGuardFixture $guardParent 'DeleteSubdirectoriesAndFiles' }}
try {{
  try {{ Invoke-BoundedRecovery $journal }} catch {{ Record-BoundedFailure $journal ([string]$_.Exception.Message) }}
  @{{status=$journal.status;error=$journal.error_code;receipt=$journal.migration.guard_receipt_sha256}} | ConvertTo-Json -Compress
}} finally {{
  if ($failure -eq 'junction') {{
    [IO.Directory]::Move($guardParent,$link)
    Move-Item -LiteralPath $retained -Destination $guardParent
  }} else {{ Protect-InstalledGuardFixture $guardParent }}
}}
"""
        )
        assert json.loads(result) == {
            "status": "recovery_failed",
            "error": "installed_maintenance_guards_acl_invalid",
            "receipt": receipt_hash,
        }
        assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == HEAD
        assert sql(recovery.postgres, history_query) == before
        assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
        assert (
            sql(
                recovery.postgres,
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND rolcanlogin",
            )
            == "0"
        )
        for application in applications:
            assert (
                json.loads(checked_docker("inspect", application))[0]["State"]["Running"] is False
            )
        marker = json.loads((recovery.state / "full-upgrade-maintenance.json").read_text())
        assert marker["status"] == "active"
        assert marker["operation_id"] == recovery.proof.operation


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_real_recovery_wrapper_tasks_prevent_role_release(recovery: RecoveryRun, executable):
    recovery.proof.executable = executable
    migration = recovery.migration()
    assert checked_docker("wait", migration) == "0"
    wrapper = recovery.state / "unreviewed-startup.ps1"
    wrapper.write_text("docker.exe compose up -d", encoding="utf-8")
    history_query = "SELECT md5(string_agg(row_to_json(h)::text,E'\\n' ORDER BY recorded_at,dev_number,point_id)) FROM point_data_history h"
    before = sql(recovery.postgres, history_query)
    for execute, arguments in (
        (
            r"C:\Windows\System32\forfiles.exe",
            '/C "cmd /d /c docker.exe start ruisheng-api"',
        ),
        ("powershell.exe", f'-NoProfile -File "{wrapper}"'),
        (
            "powershell.exe",
            "-NoProfile -Command \"([scriptblock]::Create('docker start ruisheng-api')).Invoke()\"",
        ),
        ("powershell.exe", f"-NoProfile -Command \"& '{wrapper.with_suffix('')}'\""),
        ("cscript.exe", "C:\\Unknown\\startup.vbs"),
    ):
        if execute.endswith("forfiles.exe"):
            # Start only the existing inert test containers. The task payload
            # below is never dispatched, and failed recovery must stop them.
            sql(recovery.postgres, "ALTER ROLE ruisheng_api LOGIN; ALTER ROLE ruisheng_gw LOGIN;")
            checked_docker(
                "start",
                *(
                    recovery.postgres.removesuffix("postgres") + service
                    for service in ("gw", "api", "web")
                ),
            )
        # Replace only the installed-task environment boundary. The real action
        # classifier decides rejection; neither the wrapper nor Docker task runs.
        result = recovery.run(f"""
function Assert-InstalledMaintenanceGuards {{
  if (Test-UnprotectedStartupAction {ps_literal(execute)} {ps_literal(arguments)} 'C:\\Program Files\\Ruisheng\\Launcher\\start_ruisheng_local.ps1') {{
    throw 'unprotected_startup_task_present'
  }}
  (Get-FileHash -LiteralPath (Join-Path $StateDirectory 'installed-guard-receipt.json')).Hash.ToLowerInvariant()
}}
try {{ Invoke-BoundedRecovery $journal }} catch {{ Record-BoundedFailure $journal ([string]$_.Exception.Message) }}
@{{status=$journal.status;error=$journal.error_code}} | ConvertTo-Json -Compress
""")
        assert json.loads(result) == {
            "status": "recovery_failed",
            "error": "unprotected_startup_task_present",
        }
        assert sql(recovery.postgres, "SELECT version_num FROM alembic_version") == HEAD
        assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
        assert sql(recovery.postgres, history_query) == before
        assert (
            sql(
                recovery.postgres,
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND rolcanlogin",
            )
            == "0"
        )
        for service in ("gw", "api", "web"):
            assert (
                json.loads(
                    checked_docker("inspect", recovery.postgres.removesuffix("postgres") + service)
                )[0]["State"]["Running"]
                is False
            )
        assert (
            json.loads((recovery.state / "full-upgrade-maintenance.json").read_text())["status"]
            == "active"
        )


def test_real_first_container_stop_interruption_already_has_durable_role_fence(
    recovery: RecoveryRun,
):
    sql(recovery.postgres, "ALTER ROLE ruisheng_api LOGIN; ALTER ROLE ruisheng_gw LOGIN;")
    result = recovery.proof.powershell("""
Set-Item Function:Invoke-OperationDocker ${function:Invoke-DockerText}
function Invoke-DockerText {
  param($Arguments,$TimeoutSeconds=120)
  if ($Arguments[0] -ceq 'stop' -and $Arguments[-1] -ceq 'ruisheng-gw') { Stop-Process -Id $PID -Force }
  Invoke-OperationDocker $Arguments $TimeoutSeconds
}
Save-BoundedPhase $journal 'fence_intent'
Stop-BoundedApplications $journal
""")
    assert result.returncode != 0
    assert (
        sql(
            recovery.postgres,
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND rolcanlogin",
        )
        == "0"
    )
    assert (
        json.loads(checked_docker("inspect", recovery.postgres.removesuffix("postgres") + "api"))[
            0
        ]["State"]["Running"]
        is True
    )
    assert (
        json.loads((recovery.state / "full-upgrade-maintenance.json").read_text())["status"]
        == "active"
    )
    recovery.proof.expire_interrupted_locks()
    assert recovery.recover()["status"] == "rolled_back"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_real_snapshot_parent_death_is_recoverably_stopped(source_database, executable):
    operation = str(uuid4())
    proof = ProofRun(
        source_database,
        operation,
        ROOT / "tmp-test-logs" / f"bounded-snapshot-death-{operation}",
        executable,
    )
    try:
        result = proof.powershell("""
function Invoke-ContainerScript { Stop-Process -Id $PID -Force }
$journal.backup=New-BoundedDatabaseBackup $journal
""")
        assert result.returncode != 0
        assert (
            sql(
                source_database,
                f"SELECT count(*) FROM pg_stat_activity WHERE application_name='ruisheng-upgrade-snapshot-{operation}'",
            )
            == "1"
        )
        proof.expire_interrupted_locks()
        assert (
            proof.run(
                "Stop-BoundedSnapshot $journal; [string]$journal.migration.snapshot_holder.stopped"
            )
            == "True"
        )
        assert (
            sql(
                source_database,
                f"SELECT count(*) FROM pg_stat_activity WHERE application_name='ruisheng-upgrade-snapshot-{operation}'",
            )
            == "0"
        )
        assert (proof.directory / "backup").is_dir()
    finally:
        if proof.journal_path.exists():
            proof.expire_interrupted_locks()
            proof.run("Stop-BoundedSnapshot $journal")


@pytest.mark.parametrize("stage", ["created", "restored", "verified"])
def test_real_recover_stops_auxiliary_restore_after_parent_death(recovery: RecoveryRun, stage):
    previous_operation = recovery.proof.operation
    operation = str(uuid4())
    original_journal = recovery.state / f"full-upgrade-{previous_operation}.json"
    recovery.proof.operation = operation
    recovery.proof.setup = (
        recovery.proof.setup.replace(
            f"$OperationId='{previous_operation}'",
            f"$OperationId='{operation}'",
        )
        + f"\n$BackupDirectory=Join-Path $StateDirectory 'backup-{operation}'\n"
    )
    try:
        recovery.run(f"""
$journal=Convert-UpgradeJson (Get-Content {ps_literal(original_journal)} -Raw -Encoding UTF8)
$journal.operation_id=$OperationId
$journal.backup=$null
$journal.environment_backup=New-EnvironmentBackupReceipt $EnvFile (Join-Path $StateDirectory "full-upgrade-$OperationId.env.before")
$journal.migration.container_name="ruisheng-migrate-$OperationId"
Move-Item $MaintenanceStatePath (Join-Path $StateDirectory 'original-maintenance-retained.json')
Write-MaintenanceState $journal
$journal.backup=New-BoundedDatabaseBackup $journal
Write-JsonAtomic $JournalPath $journal
""")
        result = recovery.proof.powershell(f"""
Set-Item Function:Invoke-BeforeInterruptionDocker ${{function:Invoke-DockerText}}
Set-Item Function:Save-BeforeInterruptionPhase ${{function:Save-BoundedPhase}}
function Invoke-DockerText {{
  param($Arguments,$TimeoutSeconds=120)
  $output=Invoke-BeforeInterruptionDocker $Arguments $TimeoutSeconds
  if (({ps_literal(stage)} -ceq 'created' -and $Arguments[0] -ceq 'create') -or
      ({ps_literal(stage)} -ceq 'restored' -and $Arguments -ccontains 'pg_restore')) {{ Stop-Process -Id $PID -Force }}
  return $output
}}
function Save-BoundedPhase {{
  param($Journal,$Phase)
  Save-BeforeInterruptionPhase $Journal $Phase
  if ({ps_literal(stage)} -ceq 'verified' -and $Phase -ceq 'backup_restore_verified') {{ Stop-Process -Id $PID -Force }}
}}
Test-BoundedBackupRestore $journal
""")
        assert result.returncode != 0
        before = json.loads(checked_docker("inspect", recovery.proof.clone))[0]
        assert before["State"]["Running"] is (stage != "created")
        stored = json.loads(
            (recovery.state / f"full-upgrade-{operation}.json").read_text(encoding="utf-8")
        )
        assert bool(stored["backup"]["restore_container_id"]) is (stage != "created")
        recovery.proof.expire_interrupted_locks()
        assert recovery.recover()["status"] == "rolled_back"
        after = json.loads(checked_docker("inspect", recovery.proof.clone))[0]
        assert after["State"]["Running"] is False
        assert after["Id"] == before["Id"]
        assert after["Mounts"] == before["Mounts"]
        assert sql(recovery.postgres, "SELECT count(*) FROM point_data_history") == "1"
    finally:
        recovery.proof.stop_owned_clone()
