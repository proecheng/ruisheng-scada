"""Package-external bootstrap for authenticated qualification tools."""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from tools.release_artifacts import (
    POSIX_QUALIFICATION_RUNTIME_ROOT,
    QUALIFICATION_ALLOWED_EXIT_CODES,
    QUALIFICATION_ENTRYPOINTS,
    QUALIFICATION_TOOLCHAIN_ARCHIVE,
    CommandOutcome,
    ReleaseArtifactError,
    Runner,
    _development_qualification_runtime,
    _extract_qualification_toolchain,
    _load_release_trust,
    _protected_candidate_snapshot,
    _qualification_environment,
    _system_protected_workdir,
    _validate_posix_qualification_runtime,
    _validate_system_trust_permissions,
    _verify_snapshot_contents,
    sha256_file,
)


def execute_authenticated_qualification_tool(
    package: Path,
    runner: Runner,
    *,
    trust_directory: Path,
    tool: str,
    tool_arguments: Sequence[str],
    require_system_trust: bool = False,
) -> CommandOutcome:
    """Run one entrypoint; Windows system qualification uses verify-publisher.ps1."""

    relative = QUALIFICATION_ENTRYPOINTS.get(tool)
    if relative is None:
        raise ReleaseArtifactError(f"unsupported qualification tool: {tool}")
    if require_system_trust and os.name == "nt":
        raise ReleaseArtifactError(
            "Windows system qualification requires the protected PowerShell publisher"
        )
    trust = _load_release_trust(trust_directory)
    snapshot_parent: Path | None = None
    if require_system_trust:
        _validate_system_trust_permissions(trust)
        snapshot_parent = _system_protected_workdir()
    with _protected_candidate_snapshot(package, parent=snapshot_parent) as snapshot:
        if not (snapshot / QUALIFICATION_TOOLCHAIN_ARCHIVE).is_file():
            raise ReleaseArtifactError("candidate has no authenticated qualification toolchain")
        manifest = _verify_snapshot_contents(snapshot, runner, trust=trust, validate_compose=True)
        extraction = _extract_qualification_toolchain(
            snapshot,
            manifest,
            parent=snapshot.parent,
        )
        try:
            temporary_root = extraction / "tmp"
            temporary_root.mkdir(mode=0o700)
            if require_system_trust:
                runtime = _validate_posix_qualification_runtime(
                    POSIX_QUALIFICATION_RUNTIME_ROOT,
                    authenticated_uv_lock_sha256=sha256_file(extraction / "uv.lock"),
                )
            else:
                runtime = _development_qualification_runtime()
            bootstrap = (
                "import os,sys; "
                "gate=os.environ.pop('RUISHENG_PROCESS_JOB_GATE',''); "
                "assert os.name!='nt' or gate, 'qualification process job gate is missing'; "
                "kernel32=__import__('ctypes').WinDLL('kernel32',use_last_error=True) "
                "if gate else None; "
                "gate_handle=kernel32.OpenEventW(0x00100000,False,gate) if gate else None; "
                "assert not gate or gate_handle, 'qualification process job gate cannot open'; "
                "assert not gate or kernel32.WaitForSingleObject(gate_handle,30000)==0, "
                "'qualification process job gate timed out'; "
                "assert not gate or kernel32.CloseHandle(gate_handle), "
                "'qualification process job gate cannot close'; "
                "strict=sys.argv.pop(1)=='1'; "
                "runtime=os.path.realpath(sys.argv.pop(1)); "
                "python=os.path.realpath(sys.argv.pop(1)); "
                "dependency=os.path.realpath(sys.argv.pop(1)); "
                "root=os.path.realpath(sys.argv.pop(1)); script=os.path.realpath(sys.argv.pop(1)); "
                "assert sys.version_info[:2]==(3,11), 'qualification runtime must be Python 3.11'; "
                "assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode, "
                "'qualification runtime isolation flags are incomplete'; "
                "assert not {'site','sitecustomize','usercustomize'}&set(sys.modules), "
                "'qualification runtime imported site before bootstrap'; "
                "inside=lambda value,parent: value==parent or value.startswith(parent+os.sep); "
                "assert not strict or os.path.realpath(sys.executable)==python, "
                "'qualification executable escaped the fixed runtime'; "
                "assert not strict or all(os.path.realpath(value)==runtime for value in "
                "(sys.prefix,sys.exec_prefix,sys.base_prefix,sys.base_exec_prefix)), "
                "'qualification Python prefix escaped the fixed runtime'; "
                "assert not strict or all(value and inside(os.path.realpath(value),runtime) "
                "for value in sys.path), 'qualification startup search path escaped the fixed runtime'; "
                "assert (not strict or inside(dependency,runtime)) and dependency not in "
                "{os.path.realpath(value) for value in sys.path}, "
                "'qualification dependency_root was not isolated for bootstrap'; "
                "assert inside(script,root), 'unsupported qualification entrypoint'; "
                "import pathlib,runpy,types; root_path=pathlib.Path(root).resolve(strict=True); "
                "script_path=pathlib.Path(script).resolve(strict=True); "
                "allowed={(root_path/'tools'/'validate_device_point_profile.py').resolve(strict=True),"
                "(root_path/'tools'/'release_verification_receipt.py').resolve(strict=True)}; "
                "assert script_path in allowed and root_path in script_path.parents, "
                "'unsupported qualification entrypoint'; "
                "sys.modules['site']=sys.modules['sitecustomize']=sys.modules['usercustomize']=None; "
                "sys.path.insert(0,dependency); sys.path.insert(0,root); "
                "pkg=types.ModuleType('tools'); pkg.__path__=[str(root_path/'tools')]; "
                "sys.modules['tools']=pkg; sys.argv=[str(script_path),*sys.argv[1:]]; "
                "runpy.run_path(str(script_path),run_name='__main__')"
            )
            outcome = runner.run_outcome(
                [
                    str(runtime.python),
                    "-I",
                    "-B",
                    "-S",
                    "-X",
                    "utf8",
                    "-c",
                    bootstrap,
                    "1" if runtime.strict else "0",
                    str(runtime.root),
                    str(runtime.python),
                    str(runtime.dependency_root),
                    str(extraction),
                    str(extraction / relative),
                    *tool_arguments,
                ],
                cwd=extraction,
                env=_qualification_environment(temporary_root),
                timeout_seconds=900,
                inherit_environment=False,
                isolate_process_tree=True,
            )
            if runtime.strict:
                runtime_after = _validate_posix_qualification_runtime(
                    runtime.root,
                    authenticated_uv_lock_sha256=runtime.authenticated_uv_lock_sha256 or "",
                )
                if runtime_after != runtime:
                    raise ReleaseArtifactError(
                        "qualification runtime identity changed during execution"
                    )
            if outcome.returncode not in QUALIFICATION_ALLOWED_EXIT_CODES[tool]:
                details = (outcome.stderr or outcome.stdout or "no output").strip()
                raise ReleaseArtifactError(
                    f"qualification command failed ({outcome.returncode}): {details}"
                )
            return outcome
        finally:
            shutil.rmtree(extraction, ignore_errors=True)


def is_package_external() -> bool:
    """Expose a testable invariant without adding an executable candidate entrypoint."""

    return os.path.basename(__file__) not in QUALIFICATION_ENTRYPOINTS.values()
