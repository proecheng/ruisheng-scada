"""Exercise the launcher's ID start path against owned Docker assets and real SQL.

Filesystem/lease policy is covered by tool tests and target preflight. Here those
guards and application readiness probes are replaced; service inspection, ID
starts, SQL version gating and preserved-container observations are real.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "tools/start_ruisheng_local.ps1"
POSTGRES = "sha256:50a2abfa8bad354f4bc1567c6edf7426586fd99ee8cd8982bbaee157a460c6b1"
REDIS = "sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf"
HEAD = "0013_serial_polling_profile"
SERVICES = ("postgres", "redis", "gw", "api", "web")
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_test_database_target() -> None:
    """This test only connects to its uniquely owned, network-none database."""


@pytest.fixture(autouse=True)
def require_dev_database() -> None:
    """Do not connect to an existing developer database."""


def docker(*args: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["docker", "--context", "desktop-linux", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def sql(container: str, statement: str) -> str:
    return docker(
        "exec",
        container,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "ruisheng_admin",
        "-d",
        "ruisheng",
        "-Atqc",
        statement,
    )


def inspect(container: str) -> dict:
    return json.loads(docker("inspect", container))[0]


def preserved(container: str) -> dict:
    item = inspect(container)
    return {key: item[key] for key in ("Id", "Name", "Image", "State", "RestartCount", "Mounts")}


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def fixture_model(ids: dict[str, str]) -> dict:
    model = {"services": {}, "volumes": {}, "networks": {"fixture": {"name": "none"}}}
    for service, identifier in ids.items():
        service_model = {"networks": {"fixture": {}}, "volumes": []}
        if service == "postgres":
            service_model["environment"] = {
                "POSTGRES_HOST_AUTH_METHOD": "trust",
                "POSTGRES_USER": "ruisheng_admin",
                "POSTGRES_DB": "ruisheng",
            }
        else:
            service_model.update(entrypoint=["sleep"], command=["3600"])
        for index, mount in enumerate(inspect(identifier)["Mounts"]):
            assert mount["Type"] == "volume"
            key = f"{service}-{index}"
            model["volumes"][key] = {"name": mount["Name"]}
            service_model["volumes"].append(
                {
                    "type": "volume",
                    "source": key,
                    "target": mount["Destination"],
                    "read_only": not mount["RW"],
                }
            )
        model["services"][service] = service_model
    return model


@pytest.mark.parametrize("engine", ["powershell.exe", "pwsh.exe"])
def test_real_id_start_preserves_migrators_and_gates_database(engine: str) -> None:  # noqa: PLR0915
    if os.environ.get("RUISHENG_RUN_LAUNCHER_DOCKER") != "1":
        pytest.skip("set RUISHENG_RUN_LAUNCHER_DOCKER=1 for retained Docker assets")
    executable = shutil.which(engine)
    assert executable, f"required engine missing: {engine}"
    owner = uuid4().hex
    project = f"ruisheng-launcher-test-{owner}"
    directory = Path(os.environ.get("RUISHENG_TEST_EVIDENCE", ROOT / "tmp-test-logs")) / (
        "desktop-retained-start-" + owner
    )
    directory.mkdir(parents=True, exist_ok=False)
    names = {service: f"rs-launcher-{owner}-{service}" for service in SERVICES}
    ids: dict[str, str] = {}
    artifacts: list[str] = []
    owned: list[str] = []
    expected = {}
    source_hash = hashlib.sha256(LAUNCHER.read_bytes()).hexdigest()
    report: dict = {"passed": False, "engine": engine, "owner": owner, "source_sha256": source_hash}
    try:
        for service in SERVICES:
            image = POSTGRES if service == "postgres" else REDIS
            arguments = [
                "create",
                "--name",
                names[service],
                "--network",
                "none",
                "--restart",
                "no",
                "--label",
                f"com.ruisheng.test.owner={owner}",
                "--label",
                f"com.docker.compose.project={project}",
                "--label",
                f"com.docker.compose.service={service}",
            ]
            if service == "postgres":
                arguments += [
                    "-e",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "-e",
                    "POSTGRES_USER=ruisheng_admin",
                    "-e",
                    "POSTGRES_DB=ruisheng",
                ]
            else:
                arguments += ["--entrypoint", "sleep"]
            arguments += [image]
            if service != "postgres":
                arguments += ["3600"]
            ids[service] = docker(*arguments)
            owned.append(ids[service])
            expected[service] = {"image_id": image, "candidate_reference": image}
        docker("start", *ids.values())
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    "docker",
                    "--context",
                    "desktop-linux",
                    "exec",
                    ids["postgres"],
                    "psql",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "ruisheng_admin",
                    "-d",
                    "ruisheng",
                    "-Atqc",
                    "SELECT 1",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                break
            time.sleep(1)
        else:
            pytest.fail("owned database TCP startup failed")
        sql(
            ids["postgres"],
            f"CREATE TABLE alembic_version(version_num text PRIMARY KEY); INSERT INTO alembic_version VALUES ('{HEAD}')",
        )
        for suffix in ("old-migrate", "new-migrate", "proof"):
            name = f"rs-launcher-{owner}-{suffix}"
            artifact = docker(
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--restart",
                "no",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--label",
                f"com.ruisheng.test.owner={owner}",
                "--label",
                f"com.docker.compose.project={project}",
                "--label",
                "com.docker.compose.service=api"
                if suffix == "proof"
                else "com.docker.compose.service=migrate",
                "--entrypoint",
                "true",
                REDIS,
            )
            artifacts.append(artifact)
            owned.append(artifact)
            docker("start", "--attach", artifact)
        artifact_before = [preserved(item) for item in artifacts]
        docker("stop", "--time", "10", *ids.values())
        model = fixture_model(ids)
        data = base64.b64encode(
            json.dumps({"names": names, "images": expected, "ids": ids, "model": model}).encode()
        ).decode()
        harness = (
            r"""
$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'
$t=$null; $e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(__SOURCE__,[ref]$t,[ref]$e)
if ($e.Count) { throw 'source_parse_failed' }
foreach ($f in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
  # Relocate the project expectation to the test namespace; retained test assets
  # must never carry the real production project label.
  $definition=$f.Extent.Text.Replace('"ruisheng-prod"','"__PROJECT__"')
  . ([ScriptBlock]::Create($definition))
}
$data=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__DATA__')) | ConvertFrom-Json
$ContainerNames=@{}; $ExpectedImages=@{}
foreach ($p in $data.names.PSObject.Properties) {$ContainerNames[$p.Name]=$p.Value}
foreach ($p in $data.images.PSObject.Properties) {$ExpectedImages[$p.Name]=$p.Value}
$PersistentServices=@('postgres','redis','gw','api','web')
$DockerPath=(Get-Command docker.exe -ErrorAction Stop).Source
$DockerEndpoint='npipe:////./pipe/dockerDesktopLinuxEngine'
$SiteRoot='owned-test'; $script:events=New-Object Collections.ArrayList
# The test substitutes machine/lease readiness policy; real Docker operations
# below are constrained to its fixed IDs and cannot touch other containers.
function Assert-RetainedStartGuards {param($ExpectedHashes,$OriginalActive,$Deadline)}
function Assert-NoFullUpgradeMaintenance {param($SiteRoot)}
function Write-LauncherAudit {param($Event,$Result)}
$script:realDocker=${function:Invoke-DockerText}
function Invoke-DockerText {
  param([string[]]$Arguments,[int]$TimeoutSeconds=30,[switch]$Mutation)
  if ($Arguments[0] -eq 'start') {
    if (-not $Mutation -or $Arguments.Count -ne 2 -or $Arguments[1] -notin @($data.ids.PSObject.Properties.Value)) {throw 'test_start_not_owned'}
    [void]$script:events.Add($Arguments[1])
  } elseif ($Arguments[0] -eq 'exec') {
    if ($Arguments[1] -cne $data.ids.postgres -or $Arguments[-1] -cne 'SELECT version_num FROM alembic_version') {throw 'test_exec_not_readonly_owned'}
  } elseif ($Arguments[0] -eq 'image') {
    if ($Arguments.Count -ne 3 -or $Arguments[1] -cne 'inspect' -or $Arguments[2] -cnotin @($ExpectedImages.Values.image_id)) {throw 'test_image_not_owned'}
  } elseif ($Arguments[0] -ne 'container' -or $Arguments[1] -ne 'inspect' -or
      $Arguments[2] -notin (@($data.ids.PSObject.Properties.Value)+@($data.names.PSObject.Properties.Value))) {throw 'test_unexpected_docker_operation'}
  & $script:realDocker -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds -Mutation:$Mutation
}
function Wait-DependenciesHealthy {
  param($ExpectedHashes,$OriginalActive,$Deadline)
  do {
    $result=Invoke-NativeResult -FilePath $DockerPath -Arguments @('--host',$DockerEndpoint,'exec',$data.ids.postgres,'psql','-h','127.0.0.1','-U','ruisheng_admin','-d','ruisheng','-Atqc','SELECT 1') -TimeoutSeconds 10
    if ($result.ExitCode -eq 0 -and $result.Stdout.Trim() -ceq '1') {return}
    Start-Sleep -Milliseconds 500
  } while ([DateTimeOffset]::UtcNow -lt $Deadline)
  throw 'test_dependency_timeout'
}
function Wait-AllHealthy {
  param($ExpectedHashes,$OriginalActive,$Deadline)
  $configs=Get-RetainedStartConfigurations -Model $data.model -Deadline $Deadline
  foreach ($service in $PersistentServices) {
    $info=Get-RetainedStartService -Service $service -ExpectedId $data.ids.$service -Configuration $configs[$service] -Deadline $Deadline
    if (-not $info.State.Running) {throw 'test_service_not_started'}
  }
  return 'all_owned_containers_running'
}
try {
  $health=Invoke-RetainedUpgradeStart -ExpectedHashes @{} -OriginalActive @{} -ComposeModel $data.model -ExpectedHead '0013_serial_polling_profile' -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(300))
  [ordered]@{ok=$true;events=@($script:events);health=$health} | ConvertTo-Json -Compress
} catch {
  [ordered]@{ok=$false;events=@($script:events);error=$_.Exception.Message;line=$_.InvocationInfo.ScriptLineNumber} | ConvertTo-Json -Compress
  exit 1
}
""".replace("__SOURCE__", literal(str(LAUNCHER)))
            .replace("__DATA__", data)
            .replace("__PROJECT__", project)
        )
        script = directory / "run.ps1"
        script.write_text(harness, encoding="utf-8-sig")

        def launch(label: str) -> tuple[int, dict]:
            result = subprocess.run(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=360,
                check=False,
            )
            (directory / f"{label}.stdout").write_text(result.stdout, encoding="utf-8")
            (directory / f"{label}.stderr").write_text(result.stderr, encoding="utf-8")
            return result.returncode, json.loads(result.stdout)

        code, started = launch("all-stopped")
        assert code == 0 and started["ok"], started
        assert started["events"] == list(ids.values()), started
        assert [preserved(item) for item in artifacts] == artifact_before
        running_before = [preserved(item) for item in ids.values()]
        code, repeated = launch("already-running")
        assert code == 0 and repeated["events"] == [], repeated
        assert [preserved(item) for item in ids.values()] == running_before
        application_ids = [ids[service] for service in ("gw", "api", "web")]
        docker("stop", "--time", "10", *application_ids)
        sql(
            ids["postgres"],
            "UPDATE alembic_version SET version_num='0012_alarm_notification_runtime'",
        )
        code, refused = launch("wrong-schema")
        assert code == 1 and refused["error"] == "retained_upgrade_requires_controlled_start", (
            refused
        )
        assert refused["events"] == []
        assert all(not inspect(item)["State"]["Running"] for item in application_ids)
        sql(ids["postgres"], f"UPDATE alembic_version SET version_num='{HEAD}'")
        code, mixed = launch("mixed-running")
        assert code == 0 and mixed["events"] == application_ids, mixed
        assert [preserved(item) for item in artifacts] == artifact_before
        assert sql(ids["postgres"], "SELECT version_num FROM alembic_version") == HEAD
        assert hashlib.sha256(LAUNCHER.read_bytes()).hexdigest() == source_hash
        report.update(
            passed=True,
            all_stopped=started,
            repeated=repeated,
            wrong_schema=refused,
            mixed_running=mixed,
            preserved_artifacts=artifact_before,
        )
    finally:
        for container in owned:
            item = inspect(container)
            assert item["Config"]["Labels"]["com.ruisheng.test.owner"] == owner
            assert item["HostConfig"]["NetworkMode"] == "none"
            assert not item["HostConfig"]["PortBindings"]
            if item["State"]["Running"]:
                docker("stop", "--time", "10", container)
        report["owned_assets"] = [preserved(item) for item in owned]
        report["owned_assets_stopped"] = all(
            not item["State"]["Running"] for item in report["owned_assets"]
        )
        (directory / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
