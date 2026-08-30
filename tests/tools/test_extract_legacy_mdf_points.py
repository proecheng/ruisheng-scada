from __future__ import annotations

import ctypes
import errno
import hashlib
import io
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

import pytest

import tools.extract_legacy_mdf_points as extractor
from tools.extract_legacy_mdf_points import (
    DEFAULT_PAGE_NUMBER,
    EXPECTED_DATABASE_FILE_ID,
    EXPECTED_PAGE_SHA256,
    EXPECTED_SOURCE_SHA256,
    MAX_SUPPORTING_EVIDENCE_BYTES,
    PAGE_SIZE,
    MdfEvidenceError,
    extract,
    parse_page,
    read_page,
    render_artifact,
    write_artifact_exclusive,
)

ROOT = Path(__file__).resolve().parents[2]
MDF = ROOT / "DataBase" / "DataBase" / "ModBus.mdf"
CANONICAL = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "evidence"
    / "b08-20260827"
    / "legacy-point-candidates.json"
)
WINDOWS_ERROR_SHARING_VIOLATION = 32
REQUIRES_REPOSITORY_MDF = pytest.mark.skipif(
    not MDF.is_file(),
    reason="repository MDF fixture is unavailable",
)


def _assert_windows_sharing_violation(exc: OSError) -> None:
    assert os.name == "nt"
    assert getattr(exc, "winerror", None) == WINDOWS_ERROR_SHARING_VIOLATION


def _rename_directory_for_race(source: Path, destination: Path) -> None:
    if os.name != "nt":
        source.rename(destination)
        return
    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileW
    move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    move_file.restype = ctypes.c_int
    if not move_file(os.fspath(source), os.fspath(destination)):
        raise ctypes.WinError(ctypes.get_last_error())


def _delete_file_for_race(path: Path) -> None:
    delete_file = ctypes.WinDLL("kernel32", use_last_error=True).DeleteFileW
    delete_file.argtypes = [ctypes.c_wchar_p]
    delete_file.restype = ctypes.c_int
    if not delete_file(os.fspath(path)):
        raise ctypes.WinError(ctypes.get_last_error())


def _require_posix_anonymous_publication(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    probe = tmp_path / "anonymous-publication-probe"
    with extractor._bound_directory(tmp_path, label="test output parent", writable=True) as bound:
        try:
            stream, temporary_name, _created_stat = extractor._create_bound_temporary(bound)
        except MdfEvidenceError as exc:
            pytest.skip(str(exc))
        try:
            try:
                extractor._publish_no_replace(bound, stream, temporary_name, probe.name)
            except MdfEvidenceError as exc:
                pytest.skip(str(exc))
        finally:
            stream.close()
    probe.unlink()


@pytest.fixture(scope="module")
def frozen_artifact() -> dict[str, object]:
    if not MDF.is_file():
        pytest.skip("repository MDF fixture is unavailable")
    return extract(MDF)


@pytest.fixture
def repository_source(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    source_parent = tmp_path_factory.mktemp("repository-mdf")
    source = source_parent / "ModBus.mdf"
    source.write_bytes(b"isolated repository MDF fixture")
    parent_identity, source_identity = extractor._capture_expected_source_identity(source)
    assert parent_identity is not None
    assert source_identity is not None
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_PATH", source)
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_PARENT_STAT_AT_LOAD", parent_identity)
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_STAT_AT_LOAD", source_identity)
    return source


def test_read_only_extractor_recovers_frozen_candidates(
    frozen_artifact: dict[str, object],
) -> None:
    artifact = frozen_artifact

    assert artifact["schema_version"] == "1.3"
    assert artifact["source"]["sha256"] == EXPECTED_SOURCE_SHA256
    assert artifact["source"]["page_sha256"] == EXPECTED_PAGE_SHA256
    assert len(artifact["source"]["raw_page_header"]["hex"]) == 96 * 2
    observations = artifact["source"]["raw_page_header"]["observations"]
    assert observations["page_id_u32_le_at_32"] == DEFAULT_PAGE_NUMBER
    assert observations["file_id_u16_le_at_36"] == EXPECTED_DATABASE_FILE_ID
    parser_path = ROOT / artifact["parser_contract"]["tool"]
    assert (
        artifact["parser_contract"]["extractor_source_sha256"]
        == hashlib.sha256(parser_path.read_bytes()).hexdigest().upper()
    )
    assert artifact["classification_summary"] == {
        "total_candidates": 46,
        "model_counts": {"BCMM": 6, "CBMM": 40},
        "source_candidate": 46,
        "resolved": 0,
        "deployment_eligible": 0,
    }
    candidates = artifact["candidates"]
    assert len({point["source_location"]["page_record_offset"] for point in candidates}) == 46
    assert all(point["direct_import_allowed"] is False for point in candidates)
    assert all(point["implementation_supported"] is False for point in candidates)
    assert all(len(point["field_evidence"]) == 17 for point in candidates)
    assert all(
        set(claim) >= {"supports", "refutes", "does_not_test"}
        for point in candidates
        for claim in point["field_evidence"].values()
    )
    assert len({point["source_location"]["record_sha256"] for point in candidates}) == 46
    assert sum(point["fun_code"] == 3 for point in candidates) == 42
    assert sum(point["fun_code"] == 1 for point in candidates) == 4

    missing_units = [
        point
        for point in candidates
        if point["unit_storage_status"]
        == "fifth_variable_field_not_stored_interpretation_unresolved"
    ]
    assert len(missing_units) == 9
    assert all(
        point["field_evidence"]["/unit_original"]["evidence_grade"] == "unresolved"
        and point["field_evidence"]["/unit_original"]["supports"]
        == ["LEGACY_UNIT_FIELD_NOT_STORED"]
        for point in missing_units
    )
    assert all(
        point["field_evidence"][runtime_path]["does_not_test"][:2]
        == ["CURRENT_DEVICE_IDENTITY", "CURRENT_FIRMWARE_POINT_MAP"]
        for point in candidates
        for runtime_path in (
            "/legacy_runtime/show",
            "/legacy_runtime/update_interval_seconds",
        )
    )

    assert candidates[0]["candidate_id"] == "BCMM-000"
    assert candidates[0]["user_point_name"] == "1路温度"
    assert candidates[0]["legacy_scaling"]["point_ratio"] == pytest.approx(0.1)
    assert candidates[5]["user_point_name"] == "1路湿度"
    assert candidates[6]["candidate_id"] == "CBMM-000"
    assert candidates[33]["point_number"] == 27
    assert candidates[33]["user_point_name"] == "2路温度"
    assert candidates[41]["user_point_name"] == "漏电电流"
    assert (
        candidates[42]["implementation_blockers"][-1]
        == "FC1_POINT_NUMBER_RBIT_SEMANTICS_UNRESOLVED"
    )
    assert candidates[42]["encoding_candidate"]["register_width"] is None
    assert candidates[42]["encoding_candidate"]["status"] == "ambiguous"

    covered_offsets: list[int] = []
    for field in artifact["fixed_field_layout_candidate"]:
        covered_offsets.extend(
            range(field["record_offset"], field["record_offset"] + field["byte_length"])
        )
    assert sorted(covered_offsets) == list(range(4, 92))
    assert len(covered_offsets) == len(set(covered_offsets))

    rendered = render_artifact(artifact)
    assert json.loads(CANONICAL.read_text(encoding="utf-8")) == artifact
    assert CANONICAL.read_bytes() == rendered.encode("utf-8")


def test_supporting_evidence_size_limit_rejects_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = tmp_path / "oversized-evidence.bin"
    with oversized.open("wb") as stream:
        stream.truncate(MAX_SUPPORTING_EVIDENCE_BYTES + 1)
    read_called = False
    original_open = extractor._open_bound_regular_leaf

    def tracking_open(*args: object, **kwargs: object) -> tuple[BinaryIO, os.stat_result]:
        nonlocal read_called
        stream, opened = original_open(*args, **kwargs)

        class ReadTrackingStream:
            def __getattr__(self, name: str) -> object:
                return getattr(stream, name)

            def read(self, size: int = -1) -> bytes:
                nonlocal read_called
                read_called = True
                return stream.read(size)

        return ReadTrackingStream(), opened  # type: ignore[return-value]

    monkeypatch.setattr(extractor, "_open_bound_regular_leaf", tracking_open)

    with pytest.raises(MdfEvidenceError, match="supporting evidence limit"):
        extractor._read_bound_regular_file(oversized, label="oversized fixture")

    assert read_called is False


def test_evidence_sources_are_content_addressed(frozen_artifact: dict[str, object]) -> None:
    evidence_sources = frozen_artifact["evidence_sources"]
    assert "legacy_csharp_database" in evidence_sources
    assert evidence_sources["legacy_server_database"] == {
        "path": "ModBusServer20210908/ModBusServer20210908/ModBusServer/DataBase.cs",
        "sha256": "692A06CCDEBB9C059DFCA81AD1A8F5DE2BF9818555C66F309C89CD4890D56FAB",
        "evidence_ids": ["LEGACY_CSHARP_FIELD_MAPPING", "LEGACY_CSHARP_FORMULA_PATHS"],
        "locators": [
            {"purpose": "PointData field declaration", "line_start": 73, "line_end": 100},
            {
                "purpose": "database field mapping and load formula",
                "line_start": 891,
                "line_end": 920,
            },
        ],
    }
    assert evidence_sources["legacy_server_runtime"] == {
        "path": "ModBusServer20210908/ModBusServer20210908/ModBusServer/ModBusServer.cs",
        "sha256": "DE6FA2B3136A5496CA125BC5721FE848B1B890D4D33096007C52C9FE119A68D6",
        "evidence_ids": ["LEGACY_CSHARP_FORMULA_PATHS"],
        "locators": [
            {
                "purpose": "signed 16-bit realtime formula",
                "line_start": 1765,
                "line_end": 1773,
            },
            {
                "purpose": "unsigned 16-bit realtime formula",
                "line_start": 1776,
                "line_end": 1784,
            },
            {
                "purpose": "32-bit realtime formula",
                "line_start": 1788,
                "line_end": 1798,
            },
        ],
    }
    assert "current_point_api_contract" in evidence_sources
    assert "current_gateway_runtime" in evidence_sources
    for source in evidence_sources.values():
        path = ROOT / source["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest().upper() == source["sha256"]
        assert source["locators"]


def test_bound_evidence_read_rejects_same_length_in_place_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "evidence.jsonl"
    original = b"authenticated evidence\n"
    replacement = b"substituted evidence!!\n"
    assert len(original) == len(replacement)
    source.write_bytes(original)
    initial_stat = source.stat()
    actual_fstat = extractor.os.fstat
    regular_file_calls = 0
    mutation_blocked = False

    def rewrite_after_open(descriptor: int) -> os.stat_result:
        nonlocal mutation_blocked, regular_file_calls
        result = actual_fstat(descriptor)
        if stat.S_ISREG(result.st_mode):
            regular_file_calls += 1
        if regular_file_calls == 2:
            try:
                source.write_bytes(replacement)
                os.utime(source, ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns))
            except OSError as exc:
                if os.name != "nt":
                    raise
                assert isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == (
                    WINDOWS_ERROR_SHARING_VIOLATION
                )
                mutation_blocked = True
        return result

    monkeypatch.setattr(extractor.os, "fstat", rewrite_after_open)

    if os.name == "nt":
        assert extractor._read_bound_regular_file(source, label="test evidence")[0] == original
        assert mutation_blocked is True
    else:
        with pytest.raises(MdfEvidenceError, match="identity, size, mtime or ctime changed"):
            extractor._read_bound_regular_file(source, label="test evidence")


def test_bound_evidence_read_rejects_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "evidence.cs"
    source.write_bytes(b"trusted source")
    replacement = tmp_path / "replacement.cs"
    replacement.write_bytes(b"forged source!")
    actual_fstat = extractor.os.fstat
    regular_file_calls = 0
    replacement_blocked = False

    def replace_path_after_open(descriptor: int) -> os.stat_result:
        nonlocal regular_file_calls, replacement_blocked
        result = actual_fstat(descriptor)
        if stat.S_ISREG(result.st_mode):
            regular_file_calls += 1
        if regular_file_calls == 2:
            try:
                source.unlink()
                replacement.rename(source)
            except OSError as exc:
                if os.name != "nt":
                    raise
                assert isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == (
                    WINDOWS_ERROR_SHARING_VIOLATION
                )
                replacement_blocked = True
        return result

    monkeypatch.setattr(extractor.os, "fstat", replace_path_after_open)

    if os.name == "nt":
        assert (
            extractor._read_bound_regular_file(source, label="test evidence")[0]
            == b"trusted source"
        )
        assert replacement_blocked is True
    else:
        with pytest.raises(MdfEvidenceError, match="identity, size, mtime or ctime changed"):
            extractor._read_bound_regular_file(source, label="test evidence")


def test_bound_evidence_read_rejects_hard_link(tmp_path: Path) -> None:
    source = tmp_path / "evidence.cs"
    alias = tmp_path / "alias.cs"
    source.write_bytes(b"trusted source")
    try:
        os.link(source, alias)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises(MdfEvidenceError, match="exactly one hard link"):
        extractor._read_bound_regular_file(source, label="test evidence")


@REQUIRES_REPOSITORY_MDF
def test_page_fixture_rejects_any_byte_change() -> None:
    page, file_offset = read_page(MDF, DEFAULT_PAGE_NUMBER)
    assert hashlib.sha256(page).hexdigest().upper() == EXPECTED_PAGE_SHA256
    changed = bytearray(page)
    changed[96] ^= 0x01

    with pytest.raises(MdfEvidenceError, match="SHA-256 mismatch"):
        parse_page(bytes(changed), page_number=DEFAULT_PAGE_NUMBER, page_file_offset=file_offset)


def test_raw_page_has_independent_header_record_and_slot_oracles(
    frozen_artifact: dict[str, object],
) -> None:
    page, file_offset = read_page(MDF, DEFAULT_PAGE_NUMBER)
    assert file_offset == DEFAULT_PAGE_NUMBER * PAGE_SIZE
    assert struct.unpack_from("<I", page, 32)[0] == DEFAULT_PAGE_NUMBER
    assert struct.unpack_from("<H", page, 36)[0] == EXPECTED_DATABASE_FILE_ID
    assert struct.unpack_from("<H", page, 22)[0] == 46
    free_data = struct.unpack_from("<H", page, 30)[0]
    offsets = [struct.unpack_from("<H", page, PAGE_SIZE - 2 * (slot + 1))[0] for slot in range(46)]
    assert offsets[0] == 96
    assert offsets[-1] == 6311
    assert free_data == 6446
    assert hashlib.sha256(page[offsets[0] : offsets[1]]).hexdigest().upper() == (
        "1ABE2F05E95DAEDC915A3DDCBFC9219D2F1C8852E98EA8B50BEC1342570EE226"
    )
    assert hashlib.sha256(page[offsets[-1] : free_data]).hexdigest().upper() == (
        "47743080E0959EF4D88717C4324C19FF6F17058A01A6A793990DC7FC2E0B4350"
    )

    for candidate, start, end in zip(
        frozen_artifact["candidates"],
        offsets,
        [*offsets[1:], free_data],
        strict=True,
    ):
        location = candidate["source_location"]
        assert location["page_record_offset"] == start
        assert location["record_length"] == end - start
        assert location["file_absolute_offset"] == file_offset + start
        assert location["record_sha256"] == hashlib.sha256(page[start:end]).hexdigest().upper()


@REQUIRES_REPOSITORY_MDF
def test_page_header_identity_is_enforced_after_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page, file_offset = read_page(MDF, DEFAULT_PAGE_NUMBER)
    changed = bytearray(page)
    struct.pack_into("<I", changed, 32, DEFAULT_PAGE_NUMBER + 1)
    changed_page = bytes(changed)
    monkeypatch.setattr(
        extractor, "EXPECTED_PAGE_SHA256", hashlib.sha256(changed_page).hexdigest().upper()
    )

    with pytest.raises(MdfEvidenceError, match="page ID mismatch"):
        parse_page(changed_page, page_number=DEFAULT_PAGE_NUMBER, page_file_offset=file_offset)


def _record_with_variable_ends(end_offsets: list[int], *, length: int = 115) -> bytes:
    record = bytearray(length)
    null_bitmap_end = 92 + 2 + 3
    struct.pack_into("<H", record, null_bitmap_end, len(end_offsets))
    for index, end in enumerate(end_offsets):
        struct.pack_into("<H", record, null_bitmap_end + 2 + 2 * index, end)
    return bytes(record)


@pytest.mark.parametrize(
    ("end_offsets", "match"),
    [
        ([107, 107, 107, 0x8000 | 115], "flagged variable-column offsets"),
        ([109, 107, 113, 115], "end offsets are not ordered"),
        ([107, 107, 107, 114], "record boundary"),
        ([108, 108, 108, 115], "odd byte length"),
    ],
)
def test_variable_record_boundaries_fail_closed(end_offsets: list[int], match: str) -> None:
    with pytest.raises(MdfEvidenceError, match=match):
        extractor._decode_variable_fields(
            _record_with_variable_ends(end_offsets), fixed_length=92, column_count=21
        )


def test_slot_offsets_must_be_unique_and_ordered() -> None:
    page = bytearray(PAGE_SIZE)
    struct.pack_into("<H", page, 22, 46)
    struct.pack_into("<H", page, 30, 200)
    offsets = [96 + slot for slot in range(46)]
    offsets[-1] = offsets[-2]
    for slot, offset in enumerate(offsets):
        struct.pack_into("<H", page, PAGE_SIZE - 2 * (slot + 1), offset)

    with pytest.raises(MdfEvidenceError, match="unique and strictly ordered"):
        extractor._record_offsets(bytes(page))


@REQUIRES_REPOSITORY_MDF
def test_source_is_not_modified() -> None:
    before = (MDF.stat().st_size, MDF.stat().st_mtime_ns)
    extract(MDF)
    after = (MDF.stat().st_size, MDF.stat().st_mtime_ns)
    assert after == before


def test_exclusive_writer_refuses_source_alias_and_existing_output(
    tmp_path: Path, repository_source: Path
) -> None:
    artifact: dict[str, object] = {"schema_version": "test"}

    with pytest.raises(MdfEvidenceError, match="must not alias"):
        write_artifact_exclusive(artifact, source=repository_source, output=repository_source)

    _require_posix_anonymous_publication(tmp_path)
    output = tmp_path / "artifact.json"
    write_artifact_exclusive(artifact, source=repository_source, output=output)
    assert output.read_bytes() == render_artifact(artifact).encode("utf-8")
    with pytest.raises(MdfEvidenceError, match="already exists"):
        write_artifact_exclusive(artifact, source=repository_source, output=output)


def test_source_path_must_be_the_repository_mdf(repository_source: Path) -> None:
    with tempfile.TemporaryDirectory(
        prefix=".b08-test-", dir=repository_source.parent
    ) as temporary:
        alias = Path(temporary) / "ModBus-alias.mdf"
        os.link(repository_source, alias)

        with pytest.raises(MdfEvidenceError, match="repository MDF"):
            extractor._canonical_source_path(alias)


def test_missing_source_has_no_module_load_identity(tmp_path: Path) -> None:
    missing_source = tmp_path / "missing" / "ModBus.mdf"

    assert extractor._capture_expected_source_identity(missing_source) == (None, None)


def test_bound_source_rejects_missing_module_load_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_PARENT_STAT_AT_LOAD", None)
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_STAT_AT_LOAD", None)

    def unexpected_path_access(_source: Path) -> Path:
        raise AssertionError("source path must not be inspected without a module-load identity")

    monkeypatch.setattr(extractor, "_canonical_source_path", unexpected_path_access)

    with (
        pytest.raises(
            MdfEvidenceError,
            match="repository MDF identity was unavailable at module load",
        ),
        extractor._open_bound_source(MDF),
    ):
        pytest.fail("source opened without a module-load identity")


def test_bound_source_rejects_different_module_load_parent_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    different_parent = tmp_path / "different-parent"
    different_parent.mkdir()
    monkeypatch.setattr(
        extractor,
        "_EXPECTED_SOURCE_PARENT_STAT_AT_LOAD",
        different_parent.stat(),
    )

    with (
        pytest.raises(
            MdfEvidenceError,
            match="bound source parent does not match its module-load identity",
        ),
        extractor._open_bound_source(repository_source),
    ):
        pytest.fail("source opened against a different trusted parent identity")


def test_bound_source_rejects_different_module_load_mdf_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    different_source = tmp_path / "different-source.mdf"
    different_source.write_bytes(b"different source")
    monkeypatch.setattr(
        extractor,
        "_EXPECTED_SOURCE_STAT_AT_LOAD",
        different_source.stat(),
    )

    with (
        pytest.raises(
            MdfEvidenceError,
            match="opened source does not match its module-load MDF identity",
        ),
        extractor._open_bound_source(repository_source),
    ):
        pytest.fail("source opened against a different trusted MDF identity")


def _create_directory_reparse(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name != "nt":
            pytest.skip(f"directory symlinks are unavailable: {symlink_error}")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"directory junctions are unavailable: {result.stderr or result.stdout}")


def _remove_directory_reparse(link: Path) -> None:
    if link.is_symlink():
        link.unlink()
    elif os.path.lexists(link):
        link.rmdir()


def test_source_parent_symlink_is_rejected(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    (real_parent / "source.mdf").write_bytes(b"not an MDF")
    linked_parent = tmp_path / "linked"
    _create_directory_reparse(linked_parent, real_parent)
    try:
        with pytest.raises(MdfEvidenceError, match="symlink or reparse point"):
            extractor._reject_reparse_components(linked_parent / "source.mdf", label="source")
    finally:
        _remove_directory_reparse(linked_parent)


def _make_source_exchange_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, bytes, os.stat_result, os.stat_result]:
    container = tmp_path / "container"
    source_parent = container / "source-parent"
    replacement_parent = container / "replacement-parent"
    moved_parent = container / "moved-parent"
    source_parent.mkdir(parents=True)
    replacement_parent.mkdir()
    source = source_parent / "source.mdf"
    original_page = b"A" * PAGE_SIZE
    page_prefix = b"A" * (DEFAULT_PAGE_NUMBER * PAGE_SIZE)
    source.write_bytes(page_prefix + original_page)
    (replacement_parent / source.name).write_bytes(b"B" * ((DEFAULT_PAGE_NUMBER + 1) * PAGE_SIZE))
    source_parent_identity = source_parent.stat()
    source_identity = source.stat()
    return (
        source_parent,
        replacement_parent,
        moved_parent,
        source,
        original_page,
        source_parent_identity,
        source_identity,
    )


def test_bound_source_parent_exchange_is_blocked_or_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        source_parent,
        replacement_parent,
        moved_parent,
        source,
        original_page,
        source_parent_identity,
        source_identity,
    ) = _make_source_exchange_fixture(tmp_path)

    monkeypatch.setattr(extractor, "_canonical_source_path", lambda _source: source)
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_PARENT_STAT_AT_LOAD", source_parent_identity)
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_STAT_AT_LOAD", source_identity)
    actual_open = extractor._open_bound_source_leaf
    observed: list[bytes] = []
    rename_blocked = False
    exchanged = False

    def exchange_parent(
        bound: extractor.BoundDirectory, name: str
    ) -> tuple[BinaryIO, os.stat_result]:
        nonlocal exchanged, rename_blocked
        try:
            _rename_directory_for_race(source_parent, moved_parent)
        except OSError as exc:
            if os.name != "nt":
                raise
            _assert_windows_sharing_violation(exc)
            assert os.path.samestat(source_parent.stat(), source_parent_identity)
            assert os.path.samestat(source.stat(), source_identity)
            rename_blocked = True
        else:
            exchanged = True
            replacement_parent.rename(source_parent)
        stream, opened_stat = actual_open(bound, name)
        observed.append(stream.read(1))
        stream.seek(0)
        return stream, opened_stat

    monkeypatch.setattr(extractor, "_open_bound_source_leaf", exchange_parent)
    try:
        if os.name == "nt":
            page, _offset = read_page(source)
            assert rename_blocked is True
            assert page == original_page
        else:
            with pytest.raises(MdfEvidenceError, match="source parent identity changed"):
                read_page(source)
            assert exchanged is True
        assert observed == [b"A"]
    finally:
        if exchanged:
            source_parent.rename(replacement_parent)
            moved_parent.rename(source_parent)


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics are NT-specific")
def test_windows_bound_source_blocks_write_and_delete_while_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        _source_parent,
        _replacement_parent,
        _moved_parent,
        source,
        _original_page,
        source_parent_identity,
        source_identity,
    ) = _make_source_exchange_fixture(tmp_path)
    monkeypatch.setattr(extractor, "_canonical_source_path", lambda _source: source)
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_PARENT_STAT_AT_LOAD", source_parent_identity)
    monkeypatch.setattr(extractor, "_EXPECTED_SOURCE_STAT_AT_LOAD", source_identity)

    with extractor._open_bound_source(source) as (stream, opened_stat, bound, name):
        with pytest.raises(OSError) as write_error:
            extractor._win_create_handle(
                source,
                desired_access=extractor._WIN_GENERIC_WRITE,
                share_mode=extractor._WIN_FILE_SHARE_READ,
                creation_disposition=extractor._WIN_OPEN_EXISTING,
                flags_and_attributes=extractor._WIN_FILE_ATTRIBUTE_NORMAL,
            )
        _assert_windows_sharing_violation(write_error.value)

        with pytest.raises(OSError) as delete_error:
            _delete_file_for_race(source)
        _assert_windows_sharing_violation(delete_error.value)

        assert not stream.closed
        assert os.path.samestat(os.fstat(stream.fileno()), opened_stat)
        assert os.path.samestat(source.stat(), source_identity)
        extractor._validate_bound_source_after(stream, opened_stat, bound, name)

    assert stream.closed


def test_writer_rejects_reparse_output_parent(tmp_path: Path, repository_source: Path) -> None:
    artifact: dict[str, object] = {"schema_version": "test"}
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    _create_directory_reparse(linked_parent, real_parent)
    try:
        with pytest.raises(MdfEvidenceError, match="symlink or reparse point"):
            write_artifact_exclusive(
                artifact,
                source=repository_source,
                output=linked_parent / "artifact.json",
            )
        assert not (real_parent / "artifact.json").exists()
    finally:
        _remove_directory_reparse(linked_parent)


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate streams are Windows-specific")
def test_writer_rejects_ntfs_alternate_data_stream(repository_source: Path) -> None:
    artifact: dict[str, object] = {"schema_version": "test"}
    output = Path(f"{repository_source}:b08-test-artifact")

    with pytest.raises(MdfEvidenceError, match="alternate data stream"):
        write_artifact_exclusive(artifact, source=repository_source, output=output)
    assert not os.path.exists(output)


def test_windows_device_namespace_is_rejected_without_opening_it() -> None:
    with pytest.raises(MdfEvidenceError, match="device namespace"):
        extractor._reject_windows_special_path(Path(r"\\.\PhysicalDrive0"), label="output")


def test_atomic_publish_failure_leaves_no_final_or_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    _require_posix_anonymous_publication(tmp_path)
    artifact: dict[str, object] = {"schema_version": "test"}
    output = tmp_path / "artifact.json"

    def fail_publish(
        bound: extractor.BoundDirectory,
        stream: BinaryIO,
        temporary_name: str | None,
        output_name: str,
    ) -> None:
        assert not stream.closed
        assert os.fstat(stream.fileno()).st_size == len(render_artifact(artifact).encode("utf-8"))
        assert (
            extractor._sha256_open_stream(stream)
            == hashlib.sha256(render_artifact(artifact).encode("utf-8")).hexdigest().upper()
        )
        if temporary_name is None:
            assert bound.posix_descriptor is not None
            assert os.fstat(stream.fileno()).st_nlink == 0
            assert list(bound.path.iterdir()) == []
        else:
            assert extractor._bound_lexists(bound, temporary_name)
        assert not extractor._bound_lexists(bound, output_name)
        assert bound.path == tmp_path
        raise OSError("injected publish failure")

    monkeypatch.setattr(extractor, "_publish_no_replace", fail_publish)
    with pytest.raises(MdfEvidenceError, match="injected publish failure"):
        write_artifact_exclusive(artifact, source=repository_source, output=output)

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows handle cleanup is NT-specific")
def test_windows_creation_cleanup_closes_descriptor_when_delete_mark_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_descriptors: list[int] = []
    actual_descriptor_handle = extractor._win_descriptor_handle

    def capture_descriptor_handle(descriptor: int) -> int:
        captured_descriptors.append(descriptor)
        return actual_descriptor_handle(descriptor)

    def fail_validation(_bound: extractor.BoundDirectory, _name: str) -> os.stat_result:
        raise MdfEvidenceError("injected temporary validation failure")

    def fail_delete_mark(_handle: int) -> None:
        raise OSError("injected delete-mark failure")

    with extractor._bound_directory(tmp_path, label="test output parent", writable=True) as bound:
        monkeypatch.setattr(extractor, "_bound_lstat", fail_validation)
        monkeypatch.setattr(extractor, "_win_descriptor_handle", capture_descriptor_handle)
        monkeypatch.setattr(extractor, "_win_mark_handle_for_deletion", fail_delete_mark)

        with pytest.raises(OSError, match="injected delete-mark failure"):
            extractor._create_windows_temporary(bound)

    assert len(captured_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(captured_descriptors[0])
    leaked_entries = list(tmp_path.iterdir())
    assert len(leaked_entries) == 1
    leaked_entries[0].unlink()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle cleanup is NT-specific")
def test_windows_writer_cleanup_closes_stream_when_delete_mark_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    output = tmp_path / "artifact.json"
    created: list[tuple[BinaryIO, str | None, os.stat_result]] = []
    actual_create = extractor._create_bound_temporary

    def capture_created(
        bound: extractor.BoundDirectory,
    ) -> tuple[BinaryIO, str | None, os.stat_result]:
        result = actual_create(bound)
        created.append(result)
        return result

    def fail_publish(
        _bound: extractor.BoundDirectory,
        _stream: BinaryIO,
        _temporary_name: str | None,
        _output_name: str,
    ) -> None:
        raise OSError("injected publish failure")

    def fail_delete_mark(_handle: int) -> None:
        raise OSError("injected delete-mark failure")

    monkeypatch.setattr(extractor, "_create_bound_temporary", capture_created)
    monkeypatch.setattr(extractor, "_publish_no_replace", fail_publish)
    monkeypatch.setattr(extractor, "_win_mark_handle_for_deletion", fail_delete_mark)

    with pytest.raises(OSError, match="injected delete-mark failure"):
        write_artifact_exclusive(
            {"schema_version": "test"}, source=repository_source, output=output
        )

    assert len(created) == 1
    stream, temporary_name, _created_stat = created[0]
    assert stream.closed
    assert temporary_name is not None
    assert not output.exists()
    temporary_path = tmp_path / temporary_name
    assert temporary_path.exists()
    temporary_path.unlink()


def test_temporary_mutation_is_blocked_or_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    _require_posix_anonymous_publication(tmp_path)
    artifact: dict[str, object] = {"schema_version": "test"}
    output = tmp_path / "artifact.json"
    actual_publish = extractor._publish_no_replace
    mutation_blocked = False
    mutated = False

    def mutate_before_publish(
        bound: extractor.BoundDirectory,
        stream: BinaryIO,
        temporary_name: str | None,
        output_name: str,
    ) -> None:
        nonlocal mutated, mutation_blocked
        assert not stream.closed
        if temporary_name is None:
            attacker_descriptor = os.dup(stream.fileno())
            with os.fdopen(attacker_descriptor, "r+b") as attacker:
                attacker.write(b"X")
                attacker.flush()
                os.fsync(attacker.fileno())
            mutated = True
        else:
            temporary_path = bound.path / temporary_name
            handle_identity = os.fstat(stream.fileno())
            try:
                attacker_handle = extractor._win_create_handle(
                    temporary_path,
                    desired_access=extractor._WIN_GENERIC_WRITE,
                    share_mode=extractor._WIN_FILE_SHARE_READ,
                    creation_disposition=extractor._WIN_OPEN_EXISTING,
                    flags_and_attributes=extractor._WIN_FILE_ATTRIBUTE_NORMAL,
                )
            except OSError as exc:
                if os.name != "nt":
                    raise
                _assert_windows_sharing_violation(exc)
                assert os.path.samestat(temporary_path.stat(), handle_identity)
                mutation_blocked = True
            else:
                extractor._win_close_handle(attacker_handle)
                pytest.fail("Windows retained handle unexpectedly allowed a write handle")
        actual_publish(bound, stream, temporary_name, output_name)

    monkeypatch.setattr(extractor, "_publish_no_replace", mutate_before_publish)
    if os.name == "nt":
        write_artifact_exclusive(artifact, source=repository_source, output=output)
        assert mutation_blocked is True
        assert output.read_bytes() == render_artifact(artifact).encode("utf-8")
    else:
        with pytest.raises(MdfEvidenceError, match="output retained.*cannot safely unlink"):
            write_artifact_exclusive(artifact, source=repository_source, output=output)
        assert mutated is True
        assert output.read_bytes().endswith(b"X")
        assert list(tmp_path.iterdir()) == [output]


def test_temporary_name_replacement_is_preserved_if_identity_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    _require_posix_anonymous_publication(tmp_path)
    artifact: dict[str, object] = {"schema_version": "test"}
    output = tmp_path / "artifact.json"
    replacement = b"independent replacement"
    actual_create = extractor._create_bound_temporary
    replacement_path: Path | None = None
    replacement_blocked = False

    def replace_created_name(
        bound: extractor.BoundDirectory,
    ) -> tuple[BinaryIO, str | None, os.stat_result]:
        nonlocal replacement_blocked, replacement_path
        stream, name, created_stat = actual_create(bound)
        if name is None:
            assert bound.posix_descriptor is not None
            assert created_stat.st_nlink == 0
            assert list(bound.path.iterdir()) == []
            return stream, name, created_stat
        replacement_path = bound.path / name
        handle_identity = os.fstat(stream.fileno())
        try:
            _delete_file_for_race(replacement_path)
        except OSError as exc:
            if os.name != "nt":
                raise
            _assert_windows_sharing_violation(exc)
            assert os.path.samestat(replacement_path.stat(), handle_identity)
            replacement_blocked = True
        else:
            replacement_path.write_bytes(replacement)
        return stream, name, created_stat

    monkeypatch.setattr(extractor, "_create_bound_temporary", replace_created_name)
    if os.name == "nt":
        write_artifact_exclusive(artifact, source=repository_source, output=output)
        assert replacement_blocked is True
        assert output.read_bytes() == render_artifact(artifact).encode("utf-8")
    else:
        write_artifact_exclusive(artifact, source=repository_source, output=output)
        assert replacement_path is None
        assert output.read_bytes() == render_artifact(artifact).encode("utf-8")


def test_destination_created_at_publish_boundary_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    _require_posix_anonymous_publication(tmp_path)
    artifact: dict[str, object] = {"schema_version": "test"}
    output = tmp_path / "artifact.json"
    incumbent = b"incumbent"
    actual_publish = extractor._publish_no_replace

    def create_destination_first(
        bound: extractor.BoundDirectory,
        stream: BinaryIO,
        temporary_name: str | None,
        output_name: str,
    ) -> None:
        (bound.path / output_name).write_bytes(incumbent)
        actual_publish(bound, stream, temporary_name, output_name)

    monkeypatch.setattr(extractor, "_publish_no_replace", create_destination_first)
    with pytest.raises(MdfEvidenceError, match="output already exists"):
        write_artifact_exclusive(artifact, source=repository_source, output=output)

    assert output.read_bytes() == incumbent
    assert list(tmp_path.iterdir()) == [output]


def test_bound_output_parent_exchange_is_blocked_or_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    _require_posix_anonymous_publication(tmp_path)
    artifact: dict[str, object] = {"schema_version": "test"}
    container = tmp_path / "container"
    output_parent = container / "output-parent"
    replacement_parent = container / "replacement-parent"
    moved_parent = container / "moved-parent"
    output_parent.mkdir(parents=True)
    replacement_parent.mkdir()
    output = output_parent / "artifact.json"
    output_parent_identity = output_parent.stat()
    actual_publish = extractor._publish_no_replace
    rename_blocked = False
    exchanged = False

    def exchange_parent(
        bound: extractor.BoundDirectory,
        stream: BinaryIO,
        temporary_name: str | None,
        output_name: str,
    ) -> None:
        nonlocal exchanged, rename_blocked
        try:
            _rename_directory_for_race(output_parent, moved_parent)
        except OSError as exc:
            if os.name != "nt":
                raise
            _assert_windows_sharing_violation(exc)
            assert os.path.samestat(output_parent.stat(), output_parent_identity)
            rename_blocked = True
        else:
            exchanged = True
            replacement_parent.rename(output_parent)
        actual_publish(bound, stream, temporary_name, output_name)

    monkeypatch.setattr(extractor, "_publish_no_replace", exchange_parent)
    try:
        if os.name == "nt":
            write_artifact_exclusive(artifact, source=repository_source, output=output)
            assert rename_blocked is True
            assert output.read_bytes() == render_artifact(artifact).encode("utf-8")
        else:
            with pytest.raises(MdfEvidenceError, match="output retained.*cannot safely unlink"):
                write_artifact_exclusive(artifact, source=repository_source, output=output)
            assert exchanged is True
            assert (moved_parent / output.name).read_bytes() == render_artifact(artifact).encode(
                "utf-8"
            )
            assert not output.exists()
    finally:
        if exchanged:
            output_parent.rename(replacement_parent)
            moved_parent.rename(output_parent)


def test_post_publish_validation_failure_rolls_back_exact_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    _require_posix_anonymous_publication(tmp_path)
    artifact: dict[str, object] = {"schema_version": "test"}
    output = tmp_path / "artifact.json"
    actual_verify = extractor._verify_parser_source_unchanged
    calls = 0

    def fail_after_publish() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise MdfEvidenceError("injected post-publish validation failure")
        actual_verify()

    monkeypatch.setattr(extractor, "_verify_parser_source_unchanged", fail_after_publish)
    with pytest.raises(MdfEvidenceError, match="post-publish validation failure"):
        write_artifact_exclusive(artifact, source=repository_source, output=output)

    assert calls == 3
    if os.name == "nt":
        assert not output.exists()
        assert list(tmp_path.iterdir()) == []
    else:
        assert output.read_bytes() == render_artifact(artifact).encode("utf-8")
        assert list(tmp_path.iterdir()) == [output]


@pytest.mark.skipif(os.name == "nt", reason="O_TMPFILE is POSIX-specific")
def test_posix_writer_fails_closed_without_anonymous_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_source: Path,
) -> None:
    output = tmp_path / "artifact.json"
    monkeypatch.setattr(extractor, "_OS_O_TMPFILE", 0)

    with pytest.raises(MdfEvidenceError, match="requires O_TMPFILE"):
        write_artifact_exclusive(
            {"schema_version": "test"}, source=repository_source, output=output
        )

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_posix_linkat_eperm_fails_closed_without_directory_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "artifact.json"
    calls: list[tuple[int, bytes, int, bytes, int]] = []

    class FailingLinkAt:
        argtypes: object = None
        restype: object = None

        def __call__(
            self,
            source_descriptor: int,
            source_name: bytes,
            destination_descriptor: int,
            destination_name: bytes,
            flags: int,
        ) -> int:
            calls.append(
                (
                    source_descriptor,
                    source_name,
                    destination_descriptor,
                    destination_name,
                    flags,
                )
            )
            return -1

    class FailingLibc:
        linkat = FailingLinkAt()

    monkeypatch.setattr(extractor, "_IS_WINDOWS", False)
    monkeypatch.setattr(extractor.ctypes, "CDLL", lambda *_args, **_kwargs: FailingLibc())
    monkeypatch.setattr(extractor.ctypes, "get_errno", lambda: errno.EPERM)

    with pytest.raises(MdfEvidenceError, match=r"linkat\(AT_EMPTY_PATH\).*unavailable"):
        extractor._posix_link_anonymous_no_replace(101, 202, output.name)

    assert calls == [(101, b"", 202, b"artifact.json", extractor._POSIX_AT_EMPTY_PATH)]
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_parser_source_change_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extractor, "_PARSER_SOURCE_SHA256_AT_LOAD", "0" * 64)
    with pytest.raises(MdfEvidenceError, match="source bytes changed"):
        extractor._verify_parser_source_unchanged()


def test_parser_source_verification_binds_one_open_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "extractor.py"
    replacement = tmp_path / "replacement.py"
    original = b"trusted parser\n"
    forged = b"forged parser!\n"
    assert len(original) == len(forged)
    source.write_bytes(original)
    replacement.write_bytes(forged)
    _, original_hash, original_stat = extractor._read_bound_regular_file_snapshot(
        source,
        label="extractor source",
    )

    monkeypatch.setattr(extractor, "__file__", os.fspath(source))
    monkeypatch.setattr(extractor, "_PARSER_SOURCE_PATH", source.resolve(strict=True))
    monkeypatch.setattr(extractor, "_PARSER_SOURCE_BYTES_AT_LOAD", original)
    monkeypatch.setattr(extractor, "_PARSER_SOURCE_SHA256_AT_LOAD", original_hash)
    monkeypatch.setattr(extractor, "_PARSER_SOURCE_STAT_AT_LOAD", original_stat)

    actual_fstat = extractor.os.fstat
    regular_file_calls = 0
    replacement_blocked = False

    def replace_path_after_open(descriptor: int) -> os.stat_result:
        nonlocal regular_file_calls, replacement_blocked
        result = actual_fstat(descriptor)
        if stat.S_ISREG(result.st_mode):
            regular_file_calls += 1
        if regular_file_calls == 2:
            try:
                source.unlink()
                replacement.rename(source)
            except OSError as exc:
                if os.name != "nt":
                    raise
                assert isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == (
                    WINDOWS_ERROR_SHARING_VIOLATION
                )
                replacement_blocked = True
        return result

    monkeypatch.setattr(extractor.os, "fstat", replace_path_after_open)

    if os.name == "nt":
        extractor._verify_parser_source_unchanged()
        assert replacement_blocked is True
        assert source.read_bytes() == original
    else:
        with pytest.raises(MdfEvidenceError, match="identity, size, mtime or ctime changed"):
            extractor._verify_parser_source_unchanged()


def test_cli_stdout_is_canonical_utf8_lf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact: dict[str, object] = {"purpose": "只读\n证据"}
    binary_output = io.BytesIO()
    text_output = io.TextIOWrapper(
        binary_output, encoding="cp1252", errors="strict", newline="\r\n"
    )

    monkeypatch.setattr(
        extractor,
        "extract",
        lambda source, *, page_number: artifact,
    )
    monkeypatch.setattr(sys, "stdout", text_output)
    monkeypatch.setattr(sys, "argv", ["extract_legacy_mdf_points.py", "unused.mdf"])

    assert extractor.main() == 0
    assert binary_output.getvalue() == render_artifact(artifact).encode("utf-8")
    assert b"\r\n" not in binary_output.getvalue()


def test_stdout_releases_no_bytes_when_final_parser_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact: dict[str, object] = {"purpose": "must remain buffered"}
    binary_output = io.BytesIO()
    text_output = io.TextIOWrapper(binary_output, encoding="utf-8")

    monkeypatch.setattr(sys, "stdout", text_output)
    monkeypatch.setattr(
        extractor,
        "_verify_parser_source_unchanged",
        lambda: (_ for _ in ()).throw(MdfEvidenceError("parser changed before stdout release")),
    )

    with pytest.raises(MdfEvidenceError, match="parser changed before stdout release"):
        extractor._write_stdout_canonical(artifact)

    assert binary_output.getvalue() == b""
