"""Build and verify immutable offline deployment candidates."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

CANDIDATE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}\Z")
PLATFORM_PATTERN = re.compile(
    r"(?P<os>[a-z0-9][a-z0-9._-]*)/(?P<architecture>[a-z0-9][a-z0-9._-]*)\Z"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")

COMPONENTS = ("postgres", "redis", "api", "gw", "web")
SERVICES = ("postgres", "redis", "migrate", "api", "gw", "web")
APP_COMPONENTS = ("api", "gw", "web")
API_SERVICE_REFERENCE_COUNT = 2
IMAGE_ENV_KEYS = {
    "postgres": "POSTGRES_IMAGE",
    "redis": "REDIS_IMAGE",
    "api": "API_IMAGE",
    "gw": "GW_IMAGE",
    "web": "WEB_IMAGE",
}
FIXED_PACKAGE_FILES = {
    ".env.prod.example",
    "MANIFEST.json",
    "MANIFEST.md",
    "SHA256SUMS",
    "docker-compose.prod.yml",
    "nginx.conf",
    "site-acceptance-profile.md.example",
    "site-health-acl.conf.example",
    "site-network.override.yml",
    "setup-customer.md",
    "validate-network-boundary.py",
    "verify-candidate.ps1",
    "verify-candidate.sh",
}
HASHED_FIXED_FILES = FIXED_PACKAGE_FILES - {"SHA256SUMS"}


class ReleaseArtifactError(RuntimeError):
    """Raised when a candidate violates the release artifact contract."""


class Runner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> str: ...

    def image_exists(self, image: str, *, cwd: Path) -> bool: ...

    def save_image(self, image: str, destination: Path, *, cwd: Path) -> None: ...


class SubprocessRunner:
    """Production command runner; tests inject a deterministic fake."""

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> str:
        command_env = os.environ.copy()
        if env:
            command_env.update(env)
        try:
            result = subprocess.run(
                list(args),
                cwd=cwd,
                env=command_env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,
            )
        except FileNotFoundError as error:
            raise ReleaseArtifactError(f"required command not found: {args[0]}") from error
        except subprocess.TimeoutExpired as error:
            raise ReleaseArtifactError(f"command timed out: {' '.join(args)}") from error
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or "no output").strip()
            raise ReleaseArtifactError(
                f"command failed ({error.returncode}): {' '.join(args)}: {details}"
            ) from error
        return result.stdout.strip()

    def image_exists(self, image: str, *, cwd: Path) -> bool:
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image, "--format", "{{json .Id}}"],
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        except FileNotFoundError as error:
            raise ReleaseArtifactError("required command not found: docker") from error
        except subprocess.TimeoutExpired as error:
            raise ReleaseArtifactError(
                f"candidate image tag preflight timed out: {image}"
            ) from error
        if result.returncode == 0:
            return True
        details = (result.stderr or result.stdout or "no output").strip()
        if re.search(r"\bno such (?:image|object)\b", details, flags=re.IGNORECASE):
            return False
        raise ReleaseArtifactError(
            f"candidate image tag preflight failed ({result.returncode}) for {image}: {details}"
        )

    def save_image(self, image: str, destination: Path, *, cwd: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    ["docker", "image", "save", image],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=stderr_file,
                )
                if process.stdout is None:  # pragma: no cover - subprocess invariant
                    raise ReleaseArtifactError("docker image save did not expose stdout")
                process_stdout = process.stdout
                copy_errors: list[BaseException] = []
                destination_created = threading.Event()

                def stream_archive() -> None:
                    try:
                        with (
                            process_stdout,
                            destination.open("xb") as raw_output,
                            gzip.GzipFile(fileobj=raw_output, mode="wb") as compressed,
                        ):
                            destination_created.set()
                            shutil.copyfileobj(process_stdout, compressed)
                    except BaseException as error:  # surfaced on the calling thread below
                        copy_errors.append(error)

                copy_thread = threading.Thread(target=stream_archive, daemon=True)
                copy_thread.start()
                try:
                    return_code = process.wait(timeout=600)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    copy_thread.join()
                    if destination_created.is_set():
                        destination.unlink(missing_ok=True)
                    raise ReleaseArtifactError(
                        f"docker image save timed out for {image}"
                    ) from error
                copy_thread.join()
                if copy_errors:
                    if destination_created.is_set():
                        destination.unlink(missing_ok=True)
                    if isinstance(copy_errors[0], FileExistsError):
                        raise ReleaseArtifactError(f"archive already exists: {destination}")
                    raise ReleaseArtifactError(
                        f"failed to compress Docker image archive for {image}: {copy_errors[0]}"
                    ) from copy_errors[0]
                if return_code != 0:
                    stderr_file.seek(0)
                    details = stderr_file.read().decode("utf-8", errors="replace").strip()
                    destination.unlink(missing_ok=True)
                    raise ReleaseArtifactError(
                        f"docker image save failed ({return_code}) for {image}: "
                        f"{details or 'no output'}"
                    )
        except FileNotFoundError as error:
            raise ReleaseArtifactError("required command not found: docker") from error


@dataclass(frozen=True)
class ImageArtifact:
    component: str
    source_reference: str
    repo_digest: str | None
    candidate_reference: str
    image_id: str
    os: str
    architecture: str
    archive: str
    sha256: str


@dataclass(frozen=True)
class CandidateManifest:
    schema_version: int
    candidate_id: str
    source_commit: str
    generated_at: str
    target_os: str
    target_architecture: str
    alembic_head: str
    logical_identity: str
    tools: dict[str, str]
    authenticity: dict[str, str]
    images: tuple[ImageArtifact, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InspectedImage:
    image_id: str
    os: str
    architecture: str
    repo_digests: tuple[str, ...]
    repo_tags: tuple[str, ...]


@dataclass(frozen=True)
class ArchiveIdentity:
    image_id: str
    os: str
    architecture: str
    repo_tags: tuple[str, ...]


def validate_candidate_id(candidate_id: str) -> str:
    if CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None:
        raise ReleaseArtifactError(
            "candidate ID must be 1-63 lowercase letters, digits, dots, underscores, or hyphens"
        )
    return candidate_id


def parse_target_platform(value: str) -> tuple[str, str]:
    match = PLATFORM_PATTERN.fullmatch(value)
    if match is None:
        raise ReleaseArtifactError("target platform must use the form os/architecture")
    return match.group("os"), match.group("architecture")


def candidate_image_references(candidate_id: str) -> dict[str, str]:
    validate_candidate_id(candidate_id)
    return {component: f"ruisheng-candidate/{component}:{candidate_id}" for component in COMPONENTS}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ReleaseArtifactError(f"unsafe package path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReleaseArtifactError(f"unsafe package path: {value!r}")
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError(f"invalid JSON file {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"JSON root must be an object: {path}")
    return value


def inspect_docker_archive(  # noqa: PLR0912, PLR0915
    path: Path, expected_reference: str
) -> ArchiveIdentity:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ReleaseArtifactError(f"archive contains duplicate members: {path}")
            for member in members:
                _validate_relative_path(member.name.rstrip("/") or member.name)
                if member.issym() or member.islnk():
                    raise ReleaseArtifactError(
                        f"archive contains a link member: {path}:{member.name}"
                    )
            try:
                manifest_member = archive.getmember("manifest.json")
            except KeyError as error:
                raise ReleaseArtifactError(f"archive is missing manifest.json: {path}") from error
            manifest_stream = archive.extractfile(manifest_member)
            if manifest_stream is None:
                raise ReleaseArtifactError(f"archive manifest.json is not a regular file: {path}")
            manifest_value = json.load(manifest_stream)
            if not isinstance(manifest_value, list) or len(manifest_value) != 1:
                raise ReleaseArtifactError(f"archive must contain exactly one image: {path}")
            entry = manifest_value[0]
            if not isinstance(entry, dict):
                raise ReleaseArtifactError(f"archive manifest entry is invalid: {path}")
            repo_tags = entry.get("RepoTags")
            if repo_tags != [expected_reference]:
                raise ReleaseArtifactError(
                    f"archive RepoTags mismatch for {path}: expected [{expected_reference!r}], "
                    f"got {repo_tags!r}"
                )
            config_name = entry.get("Config")
            if not isinstance(config_name, str):
                raise ReleaseArtifactError(f"archive config path is invalid: {path}")
            _validate_relative_path(config_name)
            try:
                config_member = archive.getmember(config_name)
            except KeyError as error:
                raise ReleaseArtifactError(
                    f"archive config is missing: {path}:{config_name}"
                ) from error
            config_stream = archive.extractfile(config_member)
            if config_stream is None:
                raise ReleaseArtifactError(
                    f"archive config is not a regular file: {path}:{config_name}"
                )
            config_bytes = config_stream.read()
            try:
                config = json.loads(config_bytes)
            except json.JSONDecodeError as error:
                raise ReleaseArtifactError(f"archive config is invalid JSON: {path}") from error
            config_digest = f"sha256:{hashlib.sha256(config_bytes).hexdigest()}"
            image_id = config_digest
            if "index.json" in names:
                index_stream = archive.extractfile("index.json")
                if index_stream is None:
                    raise ReleaseArtifactError(f"archive index.json is not a regular file: {path}")
                index_value = json.load(index_stream)
                descriptors = (
                    index_value.get("manifests") if isinstance(index_value, dict) else None
                )
                if not isinstance(descriptors, list) or len(descriptors) != 1:
                    raise ReleaseArtifactError(
                        f"archive index must contain exactly one image descriptor: {path}"
                    )
                descriptor = descriptors[0]
                descriptor_digest = (
                    descriptor.get("digest") if isinstance(descriptor, dict) else None
                )
                if (
                    not isinstance(descriptor_digest, str)
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", descriptor_digest) is None
                ):
                    raise ReleaseArtifactError(f"archive descriptor digest is invalid: {path}")
                descriptor_blob = f"blobs/sha256/{descriptor_digest.removeprefix('sha256:')}"
                try:
                    descriptor_member = archive.getmember(descriptor_blob)
                except KeyError as error:
                    raise ReleaseArtifactError(
                        f"archive descriptor blob is missing: {path}:{descriptor_blob}"
                    ) from error
                descriptor_stream = archive.extractfile(descriptor_member)
                if descriptor_stream is None:
                    raise ReleaseArtifactError(
                        f"archive descriptor blob is not a regular file: {path}:{descriptor_blob}"
                    )
                descriptor_bytes = descriptor_stream.read()
                actual_descriptor_digest = f"sha256:{hashlib.sha256(descriptor_bytes).hexdigest()}"
                if actual_descriptor_digest != descriptor_digest:
                    raise ReleaseArtifactError(f"archive descriptor digest mismatch: {path}")
                descriptor_value = json.loads(descriptor_bytes)
                descriptor_config = (
                    descriptor_value.get("config") if isinstance(descriptor_value, dict) else None
                )
                if (
                    not isinstance(descriptor_config, dict)
                    or descriptor_config.get("digest") != config_digest
                ):
                    raise ReleaseArtifactError(f"archive descriptor/config digest mismatch: {path}")
                image_id = descriptor_digest
    except (
        tarfile.TarError,
        gzip.BadGzipFile,
        json.JSONDecodeError,
        EOFError,
        OSError,
    ) as error:
        raise ReleaseArtifactError(f"invalid Docker image archive {path}: {error}") from error

    if not isinstance(config, dict):
        raise ReleaseArtifactError(f"archive config root is invalid: {path}")
    image_os = config.get("os")
    architecture = config.get("architecture")
    if not isinstance(image_os, str) or not isinstance(architecture, str):
        raise ReleaseArtifactError(f"archive config omits OS/architecture: {path}")
    return ArchiveIdentity(
        image_id=image_id,
        os=image_os,
        architecture=architecture,
        repo_tags=(expected_reference,),
    )


def inspect_image(reference: str, runner: Runner, *, root: Path) -> InspectedImage:
    raw = runner.run(["docker", "image", "inspect", reference, "--format", "{{json .}}"], cwd=root)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ReleaseArtifactError(
            f"docker returned invalid image metadata for {reference}"
        ) from error
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"docker returned non-object image metadata for {reference}")
    image_id = value.get("Id")
    image_os = value.get("Os")
    architecture = value.get("Architecture")
    repo_digests = value.get("RepoDigests") or []
    repo_tags = value.get("RepoTags") or []
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise ReleaseArtifactError(f"image {reference} has an invalid ID: {image_id!r}")
    if not isinstance(image_os, str) or not isinstance(architecture, str):
        raise ReleaseArtifactError(f"image {reference} omits OS/architecture")
    if not isinstance(repo_digests, list) or not all(
        isinstance(item, str) for item in repo_digests
    ):
        raise ReleaseArtifactError(f"image {reference} has invalid RepoDigests")
    if not isinstance(repo_tags, list) or not all(isinstance(item, str) for item in repo_tags):
        raise ReleaseArtifactError(f"image {reference} has invalid RepoTags")
    return InspectedImage(
        image_id=image_id,
        os=image_os,
        architecture=architecture,
        repo_digests=tuple(sorted(repo_digests)),
        repo_tags=tuple(sorted(repo_tags)),
    )


def _repository_name(reference: str) -> str:
    without_digest = reference.split("@", maxsplit=1)[0]
    last_slash = without_digest.rfind("/")
    last_colon = without_digest.rfind(":")
    if last_colon > last_slash:
        return without_digest[:last_colon]
    return without_digest


def _matching_repo_digest(source_reference: str, inspected: InspectedImage) -> str | None:
    repository = _repository_name(source_reference)
    prefix = f"{repository}@sha256:"
    return next((item for item in inspected.repo_digests if item.startswith(prefix)), None)


def compute_logical_identity(
    *,
    candidate_id: str,
    source_commit: str,
    target_os: str,
    target_architecture: str,
    alembic_head: str,
    images: Sequence[ImageArtifact],
) -> str:
    value = {
        "alembic_head": alembic_head,
        "candidate_id": candidate_id,
        "images": [
            {
                "candidate_reference": image.candidate_reference,
                "component": image.component,
                "image_id": image.image_id,
                "repo_digest": image.repo_digest,
                "source_reference": image.source_reference,
            }
            for image in images
        ],
        "source_commit": source_commit,
        "target_architecture": target_architecture,
        "target_os": target_os,
    }
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def render_manifest_markdown(manifest: CandidateManifest) -> str:
    lines = [
        "# Offline Deployment Candidate",
        "",
        f"- Candidate ID: `{manifest.candidate_id}`",
        f"- Logical identity: `{manifest.logical_identity}`",
        f"- Source commit: `{manifest.source_commit}`",
        f"- Target platform: `{manifest.target_os}/{manifest.target_architecture}`",
        f"- Alembic head: `{manifest.alembic_head}`",
        f"- Generated at: `{manifest.generated_at}`",
        "",
        "## Generation Tools",
        "",
    ]
    lines.extend(f"- {name}: `{version}`" for name, version in sorted(manifest.tools.items()))
    lines.extend(
        [
            "",
            "## Images",
            "",
            "| Component | Source | RepoDigest | Candidate tag | Image ID | Platform | Archive | SHA-256 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for image in manifest.images:
        lines.append(
            "| {component} | `{source}` | `{digest}` | `{candidate}` | `{image_id}` | "
            "`{image_os}/{architecture}` | `{archive}` | `{sha256}` |".format(
                component=image.component,
                source=image.source_reference,
                digest=image.repo_digest or "N/A",
                candidate=image.candidate_reference,
                image_id=image.image_id,
                image_os=image.os,
                architecture=image.architecture,
                archive=image.archive,
                sha256=image.sha256,
            )
        )
    lines.extend(
        [
            "",
            "## Authenticity Gate",
            "",
            "File and image integrity can be verified, but publisher authenticity is not configured.",
            "CAP-1 and G0-03 remain **BLOCKED** until the approved signature or trusted distribution "
            "mechanism is applied.",
            "",
        ]
    )
    return "\n".join(lines)


def _replace_env_values(template: str, replacements: Mapping[str, str]) -> str:
    found: set[str] = set()
    output: list[str] = []
    for line in template.splitlines():
        key, separator, _value = line.partition("=")
        if separator and key in replacements:
            if key in found:
                raise ReleaseArtifactError(f"environment template contains duplicate key: {key}")
            output.append(f"{key}={replacements[key]}")
            found.add(key)
        else:
            output.append(line)
    missing = set(replacements) - found
    if missing:
        raise ReleaseArtifactError(
            f"environment template is missing release keys: {', '.join(sorted(missing))}"
        )
    return "\n".join(output) + "\n"


def _write_sha256sums(package: Path, paths: Sequence[str]) -> None:
    lines = [f"{sha256_file(package / relative)}  {relative}" for relative in sorted(paths)]
    (package / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _parse_sha256sums(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseArtifactError(f"cannot read SHA256SUMS: {error}") from error
    for line_number, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ReleaseArtifactError(f"invalid SHA256SUMS entry at line {line_number}")
        digest, relative = match.groups()
        _validate_relative_path(relative)
        if relative in values:
            raise ReleaseArtifactError(f"duplicate SHA256SUMS path: {relative}")
        values[relative] = digest
    return values


def _manifest_from_dict(value: dict[str, Any]) -> CandidateManifest:
    required_keys = {
        "schema_version",
        "candidate_id",
        "source_commit",
        "generated_at",
        "target_os",
        "target_architecture",
        "alembic_head",
        "logical_identity",
        "tools",
        "authenticity",
        "images",
    }
    if set(value) != required_keys:
        raise ReleaseArtifactError(
            f"MANIFEST.json keys mismatch: expected {sorted(required_keys)}, got {sorted(value)}"
        )
    images_value = value["images"]
    if not isinstance(images_value, list):
        raise ReleaseArtifactError("MANIFEST.json images must be a list")
    image_keys = {field.name for field in ImageArtifact.__dataclass_fields__.values()}
    images: list[ImageArtifact] = []
    for index, image_value in enumerate(images_value):
        if not isinstance(image_value, dict) or set(image_value) != image_keys:
            raise ReleaseArtifactError(f"MANIFEST.json image {index} has invalid keys")
        try:
            images.append(ImageArtifact(**image_value))
        except TypeError as error:
            raise ReleaseArtifactError(f"MANIFEST.json image {index} is invalid") from error
    try:
        return CandidateManifest(
            schema_version=value["schema_version"],
            candidate_id=value["candidate_id"],
            source_commit=value["source_commit"],
            generated_at=value["generated_at"],
            target_os=value["target_os"],
            target_architecture=value["target_architecture"],
            alembic_head=value["alembic_head"],
            logical_identity=value["logical_identity"],
            tools=value["tools"],
            authenticity=value["authenticity"],
            images=tuple(images),
        )
    except TypeError as error:
        raise ReleaseArtifactError("MANIFEST.json has invalid field types") from error


def _validate_manifest(manifest: CandidateManifest) -> None:  # noqa: PLR0912, PLR0915
    string_fields = (
        manifest.candidate_id,
        manifest.source_commit,
        manifest.generated_at,
        manifest.target_os,
        manifest.target_architecture,
        manifest.alembic_head,
        manifest.logical_identity,
    )
    if not all(isinstance(value, str) for value in string_fields):
        raise ReleaseArtifactError("manifest scalar fields have invalid types")
    if (
        not isinstance(manifest.schema_version, int)
        or isinstance(manifest.schema_version, bool)
        or manifest.schema_version != 1
    ):
        raise ReleaseArtifactError("unsupported manifest schema_version")
    try:
        generated_at = datetime.fromisoformat(manifest.generated_at)
    except ValueError as error:
        raise ReleaseArtifactError("manifest generated_at must be an ISO-8601 timestamp") from error
    if generated_at.utcoffset() is None:
        raise ReleaseArtifactError("manifest generated_at must include a timezone offset")
    validate_candidate_id(manifest.candidate_id)
    if SOURCE_COMMIT_PATTERN.fullmatch(manifest.source_commit) is None:
        raise ReleaseArtifactError("manifest source_commit must be a full lowercase Git commit")
    parse_target_platform(f"{manifest.target_os}/{manifest.target_architecture}")
    if not isinstance(manifest.alembic_head, str) or not manifest.alembic_head:
        raise ReleaseArtifactError("manifest alembic_head is missing")
    if (
        not isinstance(manifest.tools, dict)
        or not manifest.tools
        or not all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in manifest.tools.items()
        )
    ):
        raise ReleaseArtifactError("manifest tools are invalid")
    expected_authenticity = {
        "status": "BLOCKED",
        "reason": "No approved publisher signature or trusted distribution mechanism is configured.",
    }
    if manifest.authenticity != expected_authenticity:
        raise ReleaseArtifactError("manifest must preserve the publisher-authenticity BLOCKED gate")
    if tuple(image.component for image in manifest.images) != COMPONENTS:
        raise ReleaseArtifactError(
            "manifest must contain postgres, redis, api, gw, and web in order"
        )
    expected_references = candidate_image_references(manifest.candidate_id)
    seen_references: set[str] = set()
    seen_ids: set[str] = set()
    seen_archives: set[str] = set()
    for image in manifest.images:
        image_string_fields = (
            image.component,
            image.source_reference,
            image.candidate_reference,
            image.image_id,
            image.os,
            image.architecture,
            image.archive,
            image.sha256,
        )
        if not all(isinstance(value, str) for value in image_string_fields) or not (
            image.repo_digest is None or isinstance(image.repo_digest, str)
        ):
            raise ReleaseArtifactError(
                f"manifest image fields have invalid types for {image.component!r}"
            )
        if image.candidate_reference != expected_references[image.component]:
            raise ReleaseArtifactError(f"candidate reference mismatch for {image.component}")
        if image.candidate_reference in seen_references:
            raise ReleaseArtifactError(
                f"duplicate candidate reference: {image.candidate_reference}"
            )
        if image.image_id in seen_ids:
            raise ReleaseArtifactError(f"duplicate image ID: {image.image_id}")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image.image_id) is None:
            raise ReleaseArtifactError(f"invalid image ID for {image.component}")
        expected_archive = f"images/{image.component}.tar.gz"
        if image.archive != expected_archive:
            raise ReleaseArtifactError(f"archive path mismatch for {image.component}")
        _validate_relative_path(image.archive)
        if image.archive in seen_archives:
            raise ReleaseArtifactError(f"duplicate archive path: {image.archive}")
        if SHA256_PATTERN.fullmatch(image.sha256) is None:
            raise ReleaseArtifactError(f"invalid archive SHA-256 for {image.component}")
        if (image.os, image.architecture) != (
            manifest.target_os,
            manifest.target_architecture,
        ):
            raise ReleaseArtifactError(f"platform mismatch for {image.component}")
        if (
            image.repo_digest is not None
            and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image.repo_digest) is None
        ):
            raise ReleaseArtifactError(f"invalid RepoDigest for {image.component}")
        seen_references.add(image.candidate_reference)
        seen_ids.add(image.image_id)
        seen_archives.add(image.archive)
    expected_identity = compute_logical_identity(
        candidate_id=manifest.candidate_id,
        source_commit=manifest.source_commit,
        target_os=manifest.target_os,
        target_architecture=manifest.target_architecture,
        alembic_head=manifest.alembic_head,
        images=manifest.images,
    )
    if manifest.logical_identity != expected_identity:
        raise ReleaseArtifactError("manifest logical_identity does not match its immutable inputs")


def _package_file_set(package: Path) -> set[str]:
    if package.is_symlink() or not package.is_dir():
        raise ReleaseArtifactError(f"candidate package is not a regular directory: {package}")
    files: set[str] = set()
    for entry in package.rglob("*"):
        relative = entry.relative_to(package).as_posix()
        _validate_relative_path(relative)
        if entry.is_symlink():
            raise ReleaseArtifactError(f"candidate package contains a symlink: {relative}")
        if entry.is_file():
            files.add(relative)
        elif entry.is_dir():
            if relative != "images":
                raise ReleaseArtifactError(
                    f"candidate package contains an extra directory: {relative}"
                )
        else:
            raise ReleaseArtifactError(
                f"candidate package contains a non-regular entry: {relative}"
            )
    return files


def _validate_compose(package: Path, manifest: CandidateManifest, runner: Runner) -> None:
    compose = package / "docker-compose.prod.yml"
    env_file = package / ".env.prod.example"
    base_command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose),
    ]
    image_output = runner.run([*base_command, "config", "--images"], cwd=package)
    resolved_images = [line.strip() for line in image_output.splitlines() if line.strip()]
    expected_images = [image.candidate_reference for image in manifest.images]
    if len(resolved_images) != len(SERVICES):
        raise ReleaseArtifactError(
            f"Compose must resolve six service image references, got {len(resolved_images)}"
        )
    if set(resolved_images) != set(expected_images):
        raise ReleaseArtifactError(
            f"Compose image set mismatch: expected {sorted(expected_images)}, "
            f"got {sorted(set(resolved_images))}"
        )
    api_reference = candidate_image_references(manifest.candidate_id)["api"]
    if resolved_images.count(api_reference) != API_SERVICE_REFERENCE_COUNT:
        raise ReleaseArtifactError(
            "Compose migrate/api services must share the API candidate image"
        )
    config_output = runner.run([*base_command, "config", "--format", "json"], cwd=package)
    try:
        config = json.loads(config_output)
    except json.JSONDecodeError as error:
        raise ReleaseArtifactError("Docker Compose returned invalid JSON") from error
    services = config.get("services") if isinstance(config, dict) else None
    if not isinstance(services, dict) or set(services) != set(SERVICES):
        raise ReleaseArtifactError("candidate Compose service set is invalid")
    references = candidate_image_references(manifest.candidate_id)
    expected_service_images = {
        "postgres": references["postgres"],
        "redis": references["redis"],
        "migrate": references["api"],
        "api": references["api"],
        "gw": references["gw"],
        "web": references["web"],
    }
    expected_platform = f"{manifest.target_os}/{manifest.target_architecture}"
    for name, service in services.items():
        if not isinstance(service, dict):
            raise ReleaseArtifactError(f"Compose service is not an object: {name}")
        if service.get("image") != expected_service_images[name]:
            raise ReleaseArtifactError(f"candidate Compose image mismatch for service: {name}")
        if service.get("platform") != expected_platform:
            raise ReleaseArtifactError(f"candidate Compose platform mismatch for service: {name}")
        if "build" in service:
            raise ReleaseArtifactError(f"candidate Compose service contains build: {name}")
        if service.get("pull_policy") != "never":
            raise ReleaseArtifactError(f"candidate Compose service can pull: {name}")


def verify_package(package: Path, runner: Runner) -> CandidateManifest:
    package = package.resolve()
    manifest_path = package / "MANIFEST.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReleaseArtifactError("candidate package is missing a regular MANIFEST.json")
    manifest = _manifest_from_dict(_read_json_object(manifest_path))
    _validate_manifest(manifest)
    expected_files = FIXED_PACKAGE_FILES | {image.archive for image in manifest.images}
    actual_files = _package_file_set(package)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise ReleaseArtifactError(
            f"candidate file allowlist mismatch: missing={missing}, extra={extra}"
        )
    sums = _parse_sha256sums(package / "SHA256SUMS")
    expected_hashed_files = expected_files - {"SHA256SUMS"}
    if set(sums) != expected_hashed_files:
        missing = sorted(expected_hashed_files - set(sums))
        extra = sorted(set(sums) - expected_hashed_files)
        raise ReleaseArtifactError(
            f"SHA256SUMS allowlist mismatch: missing={missing}, extra={extra}"
        )
    for relative, expected_digest in sums.items():
        actual_digest = sha256_file(package / relative)
        if actual_digest != expected_digest:
            raise ReleaseArtifactError(
                f"SHA-256 mismatch for {relative}: expected {expected_digest}, got {actual_digest}"
            )
    expected_markdown = render_manifest_markdown(manifest)
    if (package / "MANIFEST.md").read_text(encoding="utf-8") != expected_markdown:
        raise ReleaseArtifactError("MANIFEST.md does not match MANIFEST.json")
    for image in manifest.images:
        if sums[image.archive] != image.sha256:
            raise ReleaseArtifactError(f"manifest/SHA256SUMS mismatch for {image.archive}")
        archive_identity = inspect_docker_archive(
            package / image.archive, image.candidate_reference
        )
        expected_identity = (image.image_id, image.os, image.architecture)
        actual_identity = (
            archive_identity.image_id,
            archive_identity.os,
            archive_identity.architecture,
        )
        if actual_identity != expected_identity:
            raise ReleaseArtifactError(
                f"archive identity mismatch for {image.component}: "
                f"expected {expected_identity}, got {actual_identity}"
            )
    _validate_compose(package, manifest, runner)
    return manifest


def load_and_verify_images(package: Path, manifest: CandidateManifest, runner: Runner) -> None:
    for image in manifest.images:
        runner.run(
            ["docker", "image", "load", "--input", str(package / image.archive)], cwd=package
        )
    for image in manifest.images:
        inspected = inspect_image(image.candidate_reference, runner, root=package)
        expected = (image.image_id, image.os, image.architecture)
        actual = (inspected.image_id, inspected.os, inspected.architecture)
        if actual != expected:
            raise ReleaseArtifactError(
                f"loaded image identity mismatch for {image.component}: expected {expected}, got {actual}"
            )
        if image.candidate_reference not in inspected.repo_tags:
            raise ReleaseArtifactError(
                f"loaded image tag is missing for {image.component}: {image.candidate_reference}"
            )


def _git_state(root: Path, runner: Runner) -> tuple[str, str]:
    source_commit = runner.run(["git", "rev-parse", "HEAD"], cwd=root)
    if SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ReleaseArtifactError("git rev-parse did not return a full lowercase commit")
    dirty = runner.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    if dirty:
        raise ReleaseArtifactError("tracked release inputs are dirty; commit or revert them first")
    return source_commit, dirty


def _alembic_head(root: Path, runner: Runner) -> str:
    output = runner.run([sys.executable, "-m", "alembic", "heads"], cwd=root)
    heads = [
        match.group(1)
        for line in output.splitlines()
        if (match := re.fullmatch(r"([A-Za-z0-9_]+) \(head\)", line.strip())) is not None
    ]
    if len(heads) != 1:
        raise ReleaseArtifactError(f"expected exactly one Alembic head, got {heads}")
    return heads[0]


def _tool_versions(root: Path, runner: Runner) -> dict[str, str]:
    return {
        "docker": runner.run(
            ["docker", "version", "--format", "{{.Client.Version}}/{{.Server.Version}}"],
            cwd=root,
        ),
        "docker_compose": runner.run(["docker", "compose", "version", "--short"], cwd=root),
        "git": runner.run(["git", "--version"], cwd=root),
        "python": platform.python_version(),
        "release_artifacts": "1",
    }


def _write_manifests(package: Path, manifest: CandidateManifest) -> None:
    serialized = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (package / "MANIFEST.json").write_text(serialized, encoding="utf-8", newline="\n")
    (package / "MANIFEST.md").write_text(
        render_manifest_markdown(manifest), encoding="utf-8", newline="\n"
    )


def _copy_candidate_files(root: Path, package: Path, replacements: Mapping[str, str]) -> None:
    source_deploy = root / "deploy"
    shutil.copyfile(source_deploy / "docker-compose.prod.yml", package / "docker-compose.prod.yml")
    shutil.copyfile(
        source_deploy / "site-health-acl.conf.example", package / "site-health-acl.conf.example"
    )
    shutil.copyfile(
        source_deploy / "site-network.override.yml", package / "site-network.override.yml"
    )
    shutil.copyfile(source_deploy / "setup-customer.md", package / "setup-customer.md")
    shutil.copyfile(source_deploy / "verify-candidate.sh", package / "verify-candidate.sh")
    shutil.copyfile(source_deploy / "verify-candidate.ps1", package / "verify-candidate.ps1")
    shutil.copyfile(root / "ruisheng-web" / "nginx.conf", package / "nginx.conf")
    shutil.copyfile(
        root
        / "docs"
        / "superpowers"
        / "specs"
        / "spec-plan-5-customer-deployment-acceptance"
        / "site-acceptance-profile.md",
        package / "site-acceptance-profile.md.example",
    )
    shutil.copyfile(
        root / "tools" / "validate_network_boundary.py", package / "validate-network-boundary.py"
    )
    template = (source_deploy / ".env.prod.example").read_text(encoding="utf-8")
    candidate_replacements = dict(replacements)
    # The site env is copied outside the immutable candidate; Compose resolves
    # this bind source relative to the candidate's Compose file directory.
    candidate_replacements["WEB_HEALTH_ACL_FILE"] = "../site/site-health-acl.conf"
    (package / ".env.prod.example").write_text(
        _replace_env_values(template, candidate_replacements), encoding="utf-8", newline="\n"
    )


def _ensure_candidate_tags_absent(
    references: Mapping[str, str], runner: Runner, *, root: Path
) -> None:
    for component in COMPONENTS:
        reference = references[component]
        if runner.image_exists(reference, cwd=root):
            raise ReleaseArtifactError(
                f"candidate image tag already exists for {component}: {reference}"
            )


def _remove_candidate_tags(
    references: Mapping[str, str], runner: Runner, *, root: Path
) -> list[str]:
    errors: list[str] = []
    for component in COMPONENTS:
        reference = references[component]
        try:
            if runner.image_exists(reference, cwd=root):
                runner.run(["docker", "image", "rm", "--force", reference], cwd=root)
        except ReleaseArtifactError as error:
            errors.append(f"{reference}: {error}")
    return errors


def build_candidate(  # noqa: PLR0912, PLR0915
    *,
    root: Path,
    output_root: Path,
    candidate_id: str,
    target_platform: str,
    env_file: Path,
    postgres_source: str,
    redis_source: str,
    runner: Runner,
    check_clean: bool = True,
    prebuilt_app_sources: Mapping[str, str] | None = None,
    pull_base_images: bool = True,
    lock_root: Path | None = None,
) -> Path:
    candidate_id = validate_candidate_id(candidate_id)
    target_os, target_architecture = parse_target_platform(target_platform)
    root = root.resolve()
    env_file = env_file.resolve()
    if not env_file.is_file():
        raise ReleaseArtifactError(f"production environment file does not exist: {env_file}")
    if check_clean:
        source_commit, _dirty = _git_state(root, runner)
    else:
        source_commit = runner.run(["git", "rev-parse", "HEAD"], cwd=root)
        if SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
            raise ReleaseArtifactError("git rev-parse did not return a full lowercase commit")
    final_directory = output_root.resolve() / candidate_id
    if final_directory.exists():
        raise ReleaseArtifactError(f"candidate ID already exists: {final_directory}")
    references = candidate_image_references(candidate_id)
    _ensure_candidate_tags_absent(references, runner, root=root)
    final_directory.parent.mkdir(parents=True, exist_ok=True)
    resolved_lock_root = (
        lock_root or Path(tempfile.gettempdir()) / "ruisheng-release-artifact-locks"
    ).resolve()
    try:
        resolved_lock_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot create candidate lock directory: {resolved_lock_root}: {error}"
        ) from error
    lock_path = resolved_lock_root / f"{candidate_id}.lock"
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ReleaseArtifactError(
            f"candidate build already in progress or requires stale-lock cleanup: {lock_path}"
        ) from error
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot create candidate build lock: {lock_path}: {error}"
        ) from error
    try:
        try:
            os.write(lock_descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        finally:
            os.close(lock_descriptor)
    except BaseException as error:
        lock_path.unlink(missing_ok=True)
        if isinstance(error, OSError):
            raise ReleaseArtifactError(
                f"cannot initialize candidate build lock: {lock_path}: {error}"
            ) from error
        raise
    try:
        if final_directory.exists():
            raise ReleaseArtifactError(f"candidate ID already exists: {final_directory}")
        _ensure_candidate_tags_absent(references, runner, root=root)
    except BaseException:
        lock_path.unlink(missing_ok=True)
        raise
    try:
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=f".{candidate_id}.tmp-", dir=final_directory.parent)
        )
    except BaseException:
        lock_path.unlink(missing_ok=True)
        raise
    compose_env = {
        "TARGET_PLATFORM": target_platform,
        **{IMAGE_ENV_KEYS[name]: reference for name, reference in references.items()},
    }
    source_references = {
        "postgres": postgres_source,
        "redis": redis_source,
        "api": f"docker-build://ruisheng-api/Dockerfile@{source_commit}",
        "gw": f"docker-build://ruisheng-gw/Dockerfile@{source_commit}",
        "web": f"docker-build://ruisheng-web/Dockerfile@{source_commit}",
    }
    try:
        (temporary_directory / "images").mkdir()
        for component, source in (("postgres", postgres_source), ("redis", redis_source)):
            if pull_base_images:
                runner.run(
                    ["docker", "image", "pull", "--platform", target_platform, source], cwd=root
                )
            runner.run(["docker", "image", "tag", source, references[component]], cwd=root)
        if prebuilt_app_sources is None:
            runner.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "-f",
                    str(root / "docker-compose.prod.yml"),
                    "build",
                    "--pull",
                    "api",
                    "gw",
                    "web",
                ],
                cwd=root,
                env=compose_env,
            )
        else:
            if set(prebuilt_app_sources) != set(APP_COMPONENTS):
                raise ReleaseArtifactError("prebuilt app source set must be api, gw, and web")
            for component in APP_COMPONENTS:
                source = prebuilt_app_sources[component]
                source_references[component] = source
                runner.run(["docker", "image", "tag", source, references[component]], cwd=root)

        inspected_images: dict[str, InspectedImage] = {}
        for component in COMPONENTS:
            inspected = inspect_image(references[component], runner, root=root)
            if (inspected.os, inspected.architecture) != (target_os, target_architecture):
                raise ReleaseArtifactError(
                    f"platform mismatch for {component}: expected {target_platform}, "
                    f"got {inspected.os}/{inspected.architecture}"
                )
            if references[component] not in inspected.repo_tags:
                raise ReleaseArtifactError(
                    f"candidate tag did not resolve after build/tag: {references[component]}"
                )
            inspected_images[component] = inspected

        partial_images: list[ImageArtifact] = []
        for component in COMPONENTS:
            archive_relative = f"images/{component}.tar.gz"
            archive_path = temporary_directory / archive_relative
            runner.save_image(references[component], archive_path, cwd=root)
            inspected = inspected_images[component]
            archived = inspect_docker_archive(archive_path, references[component])
            expected = (inspected.image_id, inspected.os, inspected.architecture)
            actual = (archived.image_id, archived.os, archived.architecture)
            if actual != expected:
                raise ReleaseArtifactError(
                    f"exported archive identity mismatch for {component}: expected {expected}, got {actual}"
                )
            partial_images.append(
                ImageArtifact(
                    component=component,
                    source_reference=source_references[component],
                    repo_digest=_matching_repo_digest(source_references[component], inspected),
                    candidate_reference=references[component],
                    image_id=inspected.image_id,
                    os=inspected.os,
                    architecture=inspected.architecture,
                    archive=archive_relative,
                    sha256=sha256_file(archive_path),
                )
            )

        replacements = {key: compose_env[key] for key in compose_env}
        _copy_candidate_files(root, temporary_directory, replacements)
        alembic_head = _alembic_head(root, runner)
        images = tuple(partial_images)
        manifest = CandidateManifest(
            schema_version=1,
            candidate_id=candidate_id,
            source_commit=source_commit,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            target_os=target_os,
            target_architecture=target_architecture,
            alembic_head=alembic_head,
            logical_identity=compute_logical_identity(
                candidate_id=candidate_id,
                source_commit=source_commit,
                target_os=target_os,
                target_architecture=target_architecture,
                alembic_head=alembic_head,
                images=images,
            ),
            tools=_tool_versions(root, runner),
            authenticity={
                "status": "BLOCKED",
                "reason": "No approved publisher signature or trusted distribution mechanism is configured.",
            },
            images=images,
        )
        _write_manifests(temporary_directory, manifest)
        hashed_files = HASHED_FIXED_FILES | {image.archive for image in images}
        _write_sha256sums(temporary_directory, tuple(hashed_files))
        verify_package(temporary_directory, runner)
        if check_clean:
            final_commit, _dirty = _git_state(root, runner)
            if final_commit != source_commit:
                raise ReleaseArtifactError(
                    "tracked release inputs changed HEAD while the candidate was being built"
                )
        os.replace(temporary_directory, final_directory)
        return final_directory
    except BaseException as error:
        shutil.rmtree(temporary_directory, ignore_errors=True)
        cleanup_errors = _remove_candidate_tags(references, runner, root=root)
        if cleanup_errors:
            error.add_note("candidate tag cleanup failed: " + "; ".join(cleanup_errors))
        raise
    finally:
        lock_path.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build an offline deployment candidate")
    build.add_argument("--candidate-id", required=True)
    build.add_argument("--target-platform", required=True)
    build.add_argument("--env-file", type=Path, required=True)
    build.add_argument("--output-root", type=Path, default=Path("dist/deploy"))
    build.add_argument("--postgres-source", default="timescale/timescaledb:2.16.1-pg15")
    build.add_argument("--redis-source", default="redis:7-alpine")
    verify = subparsers.add_parser("verify", help="verify a candidate without starting services")
    verify.add_argument("package", type=Path)
    verify.add_argument("--load", action="store_true", help="load and inspect the five images")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    runner = SubprocessRunner()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "build":
            destination = build_candidate(
                root=root,
                output_root=args.output_root,
                candidate_id=args.candidate_id,
                target_platform=args.target_platform,
                env_file=args.env_file,
                postgres_source=args.postgres_source,
                redis_source=args.redis_source,
                runner=runner,
            )
            print(f"Candidate created: {destination}")
        else:
            manifest = verify_package(args.package, runner)
            if args.load:
                load_and_verify_images(args.package.resolve(), manifest, runner)
            print(
                f"Integrity verified for {manifest.candidate_id}; publisher authenticity is not "
                "configured. CAP-1/G0-03 remain BLOCKED."
            )
    except ReleaseArtifactError as error:
        print(f"release artifact error: {error}", file=sys.stderr)
        for note in getattr(error, "__notes__", ()):  # Notes are absent from str(error).
            print(f"release artifact note: {note}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
