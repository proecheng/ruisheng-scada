"""Opt-in Docker end-to-end checks using five network-free scratch images."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.release_artifacts import (
    COMPONENTS,
    ReleaseArtifactError,
    SubprocessRunner,
    build_candidate,
    candidate_image_references,
    load_and_verify_images,
    verify_package,
)

ROOT = Path(__file__).parents[2]
RUN_DOCKER_E2E = os.environ.get("RUN_RELEASE_DOCKER_E2E") == "1"
SYSTEM_SIGNING_IDENTITY = os.environ.get("RUN_RELEASE_SYSTEM_SIGNING_IDENTITY")


def _ssh_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _public_key_blob(private_key: Ed25519PrivateKey) -> bytes:
    key_type = b"ssh-ed25519"
    public_key = private_key.public_key().public_bytes_raw()
    return _ssh_string(key_type) + _ssh_string(public_key)


def _write_sshsig(path: Path, payload: bytes, private_key: Ed25519PrivateKey) -> None:
    namespace = b"ruisheng-candidate-v1"
    hash_algorithm = b"sha512"
    signed_payload = (
        b"SSHSIG"
        + _ssh_string(namespace)
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm)
        + _ssh_string(hashlib.sha512(payload).digest())
    )
    signature_blob = _ssh_string(b"ssh-ed25519") + _ssh_string(private_key.sign(signed_payload))
    binary_signature = (
        b"SSHSIG"
        + (1).to_bytes(4, "big")
        + _ssh_string(_public_key_blob(private_key))
        + _ssh_string(namespace)
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm)
        + _ssh_string(signature_blob)
    )
    encoded = base64.b64encode(binary_signature)
    body = b"\n".join(encoded[index : index + 70] for index in range(0, len(encoded), 70))
    path.write_bytes(b"-----BEGIN SSH SIGNATURE-----\n" + body + b"\n-----END SSH SIGNATURE-----\n")


class InMemorySigningRunner(SubprocessRunner):
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self.private_key = private_key

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
    ) -> str:
        command = tuple(str(argument) for argument in args)
        if Path(command[0]).name.casefold() in {"ssh-keygen", "ssh-keygen.exe"} and command[
            1:3
        ] == ("-Y", "sign"):
            signed_object = Path(command[-1])
            _write_sshsig(
                signed_object.with_name(signed_object.name + ".sig"),
                signed_object.read_bytes(),
                self.private_key,
            )
            return ""
        return super().run(args, cwd=cwd, env=env, input_bytes=input_bytes)


def _run(command: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=input_text,
        timeout=120,
    )
    return result.stdout


def _build_provenance_sources(tmp_path: Path, source_images: dict[str, str]) -> None:
    build_context = tmp_path / "provenance-build"
    build_context.mkdir()
    (build_context / "payload.txt").write_text("release-e2e\n", encoding="utf-8")
    (build_context / "Dockerfile").write_text(
        "FROM scratch\nARG COMPONENT\nCOPY payload.txt /payload.txt\n"
        "LABEL b03.component=$COMPONENT\n",
        encoding="utf-8",
    )
    compose_path = build_context / "compose.yml"
    compose_path.write_text(
        json.dumps(
            {
                "services": {
                    component: {
                        "build": {
                            "args": {"COMPONENT": component},
                            "context": ".",
                            "dockerfile": "Dockerfile",
                            "provenance": "mode=max",
                        },
                        "image": reference,
                        "platform": "linux/amd64",
                    }
                    for component, reference in source_images.items()
                }
            }
        ),
        encoding="utf-8",
    )
    _run(["docker", "compose", "--file", str(compose_path), "build"])


def _assert_docker29_provenance_indexes(package: Path) -> None:
    server_version = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    assert int(server_version.strip().split(".", maxsplit=1)[0]) == 29
    top_level_descriptor_counts = []
    nested_attestation_counts = []
    for archive_path in sorted((package / "images").glob("*.tar.gz")):
        with tarfile.open(archive_path, "r:gz") as archive:
            index_stream = archive.extractfile("index.json")
            assert index_stream is not None
            index = json.load(index_stream)
            descriptors = index["manifests"]
            top_level_descriptor_counts.append(len(descriptors))
            nested_attestations = 0
            for descriptor in descriptors:
                digest = descriptor["digest"].split(":", maxsplit=1)[1]
                descriptor_stream = archive.extractfile(f"blobs/sha256/{digest}")
                assert descriptor_stream is not None
                descriptor_value = json.load(descriptor_stream)
                nested_attestations += sum(
                    1
                    for nested in descriptor_value.get("manifests", [])
                    if nested.get("platform") == {"architecture": "unknown", "os": "unknown"}
                )
            nested_attestation_counts.append(nested_attestations)
    assert len(top_level_descriptor_counts) == len(COMPONENTS)
    assert all(count >= 1 for count in top_level_descriptor_counts)
    assert all(count >= 1 for count in nested_attestation_counts)


@pytest.mark.integration
@pytest.mark.skipif(not RUN_DOCKER_E2E, reason="set RUN_RELEASE_DOCKER_E2E=1")
def test_small_scratch_images_close_generation_load_and_tamper_contract(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    token = uuid.uuid4().hex[:12]
    candidate_id = f"b03-e2e-{token}"
    source_images = {
        component: f"ruisheng-b03-source/{component}:{token}" for component in COMPONENTS
    }
    candidate_images = candidate_image_references(candidate_id)
    if SYSTEM_SIGNING_IDENTITY:
        signing_identity = Path(SYSTEM_SIGNING_IDENTITY)
        trust_directory = (
            Path("C:/ProgramData/Ruisheng/trust")
            if os.name == "nt"
            else Path("/etc/ruisheng/trust")
        )
        runner = SubprocessRunner()
    else:
        private_key = Ed25519PrivateKey.generate()
        key_blob = _public_key_blob(private_key)
        encoded_key = base64.b64encode(key_blob).decode("ascii")
        signing_identity = tmp_path / "release-signing-key.pub"
        signing_identity.write_text(
            f"ssh-ed25519 {encoded_key} in-memory-e2e\n", encoding="ascii", newline="\n"
        )
        trust_directory = tmp_path / "release-trust"
        trust_directory.mkdir()
        (trust_directory / "release-allowed-signers").write_text(
            f"ruisheng-release ssh-ed25519 {encoded_key}\n",
            encoding="ascii",
            newline="\n",
        )
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key_blob).digest()).decode(
            "ascii"
        ).rstrip("=")
        (trust_directory / "release-key-fingerprint").write_text(
            fingerprint + "\n", encoding="ascii", newline="\n"
        )
        runner = InMemorySigningRunner(private_key)
    try:
        _build_provenance_sources(tmp_path, source_images)
        package = build_candidate(
            root=ROOT,
            output_root=tmp_path / "dist" / "deploy",
            candidate_id=candidate_id,
            target_platform="linux/amd64",
            env_file=ROOT / ".env.prod.example",
            postgres_source=source_images["postgres"],
            redis_source=source_images["redis"],
            runner=runner,
            signing_identity=signing_identity,
            trust_directory=trust_directory,
            check_clean=False,
            prebuilt_app_sources={
                component: source_images[component] for component in ("api", "gw", "web")
            },
            pull_base_images=False,
        )
        _assert_docker29_provenance_indexes(package)

        load_and_verify_images(package, runner, trust_directory=trust_directory)
        powershell = shutil.which("pwsh")
        if SYSTEM_SIGNING_IDENTITY and os.name != "nt":
            verified = subprocess.run(
                ["/bin/bash", "/usr/local/lib/ruisheng/verify-publisher.sh", str(package)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            assert verified.returncode == 2
            assert "B-04 remains BLOCKED" in (verified.stdout + verified.stderr)
        if SYSTEM_SIGNING_IDENTITY and os.name == "nt" and powershell:
            verified = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-File",
                    "C:\\ProgramData\\Ruisheng\\bin\\verify-publisher.ps1",
                    str(package),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            assert verified.returncode == 2
            assert "File allowlist, SHA-256, and archive identities passed" in verified.stdout
            assert "B-04 remains BLOCKED" in (verified.stdout + verified.stderr)

        setup_path = package / "setup-customer.md"
        setup_contents = setup_path.read_bytes()
        setup_path.write_text("tampered\n", encoding="utf-8")
        with pytest.raises(ReleaseArtifactError, match="publisher authenticity FAILED"):
            verify_package(package, runner, trust_directory=trust_directory)
        setup_path.write_bytes(setup_contents)

        if SYSTEM_SIGNING_IDENTITY and os.name == "nt" and powershell:
            manifest_path = package / "MANIFEST.json"
            markdown_path = package / "MANIFEST.md"
            manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
            web_image = next(
                image for image in manifest_value["images"] if image["component"] == "web"
            )
            old_web_sha = web_image["sha256"]
            shutil.copyfile(package / "images" / "gw.tar.gz", package / "images" / "web.tar.gz")
            new_web_sha = hashlib.sha256(
                (package / "images" / "web.tar.gz").read_bytes()
            ).hexdigest()
            web_image["sha256"] = new_web_sha
            manifest_path.write_text(
                json.dumps(manifest_value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            markdown_path.write_text(
                markdown_path.read_text(encoding="utf-8").replace(old_web_sha, new_web_sha),
                encoding="utf-8",
            )
            sums_path = package / "SHA256SUMS"
            sums = {
                relative: digest
                for line in sums_path.read_text(encoding="utf-8").splitlines()
                for digest, relative in (line.split("  ", maxsplit=1),)
            }
            for relative in ("MANIFEST.json", "MANIFEST.md", "images/web.tar.gz"):
                sums[relative] = hashlib.sha256((package / relative).read_bytes()).hexdigest()
            sums_path.write_text(
                "".join(f"{digest}  {relative}\n" for relative, digest in sorted(sums.items())),
                encoding="utf-8",
            )
            for reference in candidate_images.values():
                subprocess.run(
                    ["docker", "image", "rm", "--force", reference],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
            rejected = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-File",
                    "C:\\ProgramData\\Ruisheng\\bin\\verify-publisher.ps1",
                    str(package),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            assert rejected.returncode != 0
            assert "publisher authenticity FAILED" in rejected.stderr
            for reference in candidate_images.values():
                assert (
                    subprocess.run(
                        ["docker", "image", "inspect", reference],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        timeout=30,
                    ).returncode
                    != 0
                )
    finally:
        for reference in (*candidate_images.values(), *source_images.values()):
            subprocess.run(
                ["docker", "image", "rm", "--force", reference],
                cwd=ROOT,
                check=False,
                capture_output=True,
                timeout=30,
            )
