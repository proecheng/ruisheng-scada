#!/bin/bash
set -euo pipefail
PATH="/usr/bin:/bin"
export PATH
HOME="/root"
export HOME
unset BASH_ENV ENV CDPATH PYTHONHOME PYTHONPATH TMP TMPDIR TEMP DOCKER_CONFIG \
  DOCKER_CLI_PLUGIN_EXTRA_DIRS DOCKER_HOST DOCKER_CONTEXT XDG_CONFIG_HOME

if [[ "$#" -gt 2 ]]; then
  echo "usage: verify-candidate.sh [candidate-directory] [site-env-file]" >&2
  exit 1
fi
PACKAGE_INPUT="${1:-.}"
if [[ -L "$PACKAGE_INPUT" || ! -d "$PACKAGE_INPUT" ]]; then
  echo "[verify] publisher authenticity FAILED: candidate directory is missing or linked" >&2
  exit 1
fi
SOURCE_PACKAGE_DIR="$(cd "$PACKAGE_INPUT" && pwd)"
TRUST_DIR_INPUT="/etc/ruisheng/trust"
PYTHON="/usr/bin/python3"
SSH_KEYGEN="/usr/bin/ssh-keygen"
SHA256SUM="/usr/bin/sha256sum"
DOCKER="/usr/bin/docker"
RM="/bin/rm"
GREP="/usr/bin/grep"
CUT="/usr/bin/cut"
SORT="/usr/bin/sort"
DIFF="/usr/bin/diff"

[[ -x "$PYTHON" ]] || { echo "[verify] publisher authenticity FAILED: /usr/bin/python3 is required" >&2; exit 1; }
[[ -x "$SSH_KEYGEN" ]] || { echo "[verify] publisher authenticity FAILED: /usr/bin/ssh-keygen is required" >&2; exit 1; }
[[ -x "$SHA256SUM" ]] || { echo "[verify] publisher authenticity FAILED: /usr/bin/sha256sum is required" >&2; exit 1; }
[[ -x "$RM" ]] || { echo "[verify] publisher authenticity FAILED: /bin/rm is required" >&2; exit 1; }
for tool in "$GREP" "$CUT" "$SORT" "$DIFF"; do
  [[ -x "$tool" ]] || { echo "[verify] publisher authenticity FAILED: fixed system tool is required: $tool" >&2; exit 1; }
done

[[ "$EUID" -eq 0 ]] || { echo "[verify] publisher authenticity FAILED: verifier must run as root" >&2; exit 1; }
WORK_ROOT="/var/lib/ruisheng/work"
WORK_DIR="$("$PYTHON" -I -S - "$WORK_ROOT" "$PYTHON" "$SSH_KEYGEN" "$SHA256SUM" "$DOCKER" "$RM" "$GREP" "$CUT" "$SORT" "$DIFF" <<'PY'
import pathlib
import stat
import sys
import tempfile

work = pathlib.Path(sys.argv[1])
tools = [pathlib.Path(value) for value in sys.argv[2:]]

def protected(path, label):
    if path.is_symlink() or not path.exists():
        raise SystemExit("[verify] publisher authenticity FAILED: {} is missing or linked: {}".format(label, path))
    metadata = path.stat()
    if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SystemExit("[verify] publisher authenticity FAILED: {} has unsafe ownership or permissions: {}".format(label, path))

for directory in (pathlib.Path("/var"), pathlib.Path("/var/lib"), work.parent, work):
    if not directory.exists():
        directory.mkdir(mode=0o700)
    protected(directory, "fixed work directory")
protected(pathlib.Path("/"), "fixed work root")
for tool in tools:
    resolved = tool.resolve(strict=True)
    protected(resolved, "fixed system tool")
    for ancestor in resolved.parents:
        protected(ancestor, "fixed system tool ancestor")
print(tempfile.mkdtemp(prefix="verified-candidate-", dir=work))
PY
)"
cleanup() {
  status=$?
  trap - EXIT
  if ! "$RM" -rf -- "$WORK_DIR"; then
    echo "[verify] protected work cleanup failed: $WORK_DIR" >&2
    exit 1
  fi
  exit "$status"
}
trap cleanup EXIT
PACKAGE_DIR="$WORK_DIR/candidate"
DOCKER_CONFIG="$WORK_DIR/docker-config"
export DOCKER_CONFIG
mkdir -m 700 "$DOCKER_CONFIG"
printf '{}\n' > "$DOCKER_CONFIG/config.json"
chmod 600 "$DOCKER_CONFIG/config.json"
LOCK_FILE="$WORK_DIR/image-lock.tsv"
CONFIG_FILE="$WORK_DIR/compose-config.json"
EXPECTED_FILE="$WORK_DIR/expected-images.txt"
ACTUAL_FILE="$WORK_DIR/actual-images.txt"

"$PYTHON" -I -S - "$SOURCE_PACKAGE_DIR" "$PACKAGE_DIR" <<'PY'
import os
import pathlib
import shutil
import stat
import sys

source = pathlib.Path(sys.argv[1])
snapshot = pathlib.Path(sys.argv[2])
fixed = {
    ".env.prod.example",
    "MANIFEST.json",
    "MANIFEST.md",
    "SHA256SUMS",
    "SHA256SUMS.sig",
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
components = ("postgres", "redis", "api", "gw", "web")
expected = fixed | {"images/{}.tar.gz".format(value) for value in components}

def fail(message):
    raise SystemExit("[verify] publisher authenticity FAILED: " + message)

actual = set()
initial_sizes = {}
for current, directories, files in os.walk(source, followlinks=False):
    current_path = pathlib.Path(current)
    for name in directories:
        path = current_path / name
        relative = path.relative_to(source).as_posix()
        if path.is_symlink() or relative != "images":
            fail("candidate contains an unsafe directory: " + relative)
    for name in files:
        path = current_path / name
        relative = path.relative_to(source).as_posix()
        if path.is_symlink() or not path.is_file():
            fail("candidate contains a linked or non-regular file: " + relative)
        actual.add(relative)
if actual != expected:
    fail("candidate file allowlist mismatch: missing={}, extra={}".format(
        sorted(expected - actual), sorted(actual - expected)
    ))

for relative in sorted(expected):
    source_path = source / relative
    metadata = source_path.stat()
    if source_path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        fail("candidate file is linked or non-regular: " + relative)
    initial_sizes[relative] = metadata.st_size
total_size = sum(initial_sizes.values())
reserve = max(64 * 1024 * 1024, total_size // 10)
if shutil.disk_usage(snapshot.parent).free < total_size + reserve:
    fail("insufficient free space for protected candidate snapshot")

snapshot.mkdir(mode=0o700)
(snapshot / "images").mkdir(mode=0o700)
try:
    for relative in sorted(expected):
        source_path = source / relative
        destination = snapshot / relative
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source_path, flags)
        try:
            opened = os.fstat(descriptor)
            expected_size = initial_sizes[relative]
            if not stat.S_ISREG(opened.st_mode):
                fail("candidate file is not regular: " + relative)
            if opened.st_size != expected_size:
                fail("candidate file changed before snapshot: " + relative)
            with os.fdopen(descriptor, "rb", closefd=False) as input_stream:
                with destination.open("xb") as output_stream:
                    copied = 0
                    while copied < expected_size:
                        chunk = input_stream.read(min(1024 * 1024, expected_size - copied))
                        if not chunk:
                            break
                        output_stream.write(chunk)
                        copied += len(chunk)
                    if copied != expected_size or input_stream.read(1):
                        fail("candidate file size changed during snapshot: " + relative)
        finally:
            os.close(descriptor)
        destination.chmod(0o600)
except OSError as error:
    fail("cannot create complete candidate snapshot: {}".format(error))
PY

SITE_ENV_INPUT="${2:-$PACKAGE_DIR/.env.prod.example}"
if [[ ! -f "$SITE_ENV_INPUT" ]]; then
  echo "[verify] Compose environment file is missing: $SITE_ENV_INPUT" >&2
  exit 1
fi
SITE_ENV_FILE="$(cd "$(dirname "$SITE_ENV_INPUT")" && pwd)/$(basename "$SITE_ENV_INPUT")"

"$PYTHON" -I -S - "$PACKAGE_DIR" "$TRUST_DIR_INPUT" "$SSH_KEYGEN" <<'PY'
import base64
import hashlib
import json
import os
import pathlib
import re
import stat
import struct
import subprocess
import sys

root = pathlib.Path(sys.argv[1]).resolve()
trust_input = pathlib.Path(sys.argv[2])
ssh_keygen = pathlib.Path(sys.argv[3])

def fail(message):
    raise SystemExit("[verify] publisher authenticity FAILED: " + message)

if trust_input.is_symlink() or not trust_input.is_dir():
    fail("external trust directory is missing or linked")
trust = trust_input.resolve()
if trust == root or root in trust.parents:
    fail("trust directory must be outside the candidate package")
allowed = trust / "release-allowed-signers"
fingerprint_path = trust / "release-key-fingerprint"
for path in (allowed, fingerprint_path):
    if path.is_symlink() or not path.is_file():
        fail("trust file is missing or linked: " + path.name)
def protected_with_ancestors(path, label):
    absolute = path.absolute()
    for current in (absolute, *absolute.parents):
        if current.is_symlink() or not current.exists():
            fail(label + " is missing or linked: " + str(current))
        metadata = current.stat()
        if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            fail(label + " has unsafe ownership or write permissions: " + str(current))

protected_with_ancestors(trust, "trust anchor")
protected_with_ancestors(allowed, "allowed-signers")
protected_with_ancestors(fingerprint_path, "fingerprint")
protected_with_ancestors(ssh_keygen.resolve(strict=True), "system ssh-keygen")
try:
    allowed_text = allowed.read_bytes().decode("ascii")
    fingerprint_text = fingerprint_path.read_bytes().decode("ascii")
except (OSError, UnicodeDecodeError) as error:
    fail("cannot read ASCII trust anchor: {}".format(error))
match = re.fullmatch(r"ruisheng-release ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n", allowed_text)
if match is None:
    fail("release-allowed-signers is not the approved single identity")
try:
    blob = base64.b64decode(match.group(1), validate=True)
    offset = 0
    fields = []
    for _ in range(2):
        if len(blob) - offset < 4:
            fail("public key blob is truncated")
        length = struct.unpack(">I", blob[offset:offset + 4])[0]
        offset += 4
        fields.append(blob[offset:offset + length])
        offset += length
except (ValueError, struct.error):
    fail("public key blob is invalid")
if fields[0] != b"ssh-ed25519" or len(fields[1]) != 32 or offset != len(blob):
    fail("public key is not canonical ssh-ed25519")
derived = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
fingerprint_match = re.fullmatch(r"(SHA256:[A-Za-z0-9+/]{43})\n", fingerprint_text)
if fingerprint_match is None or fingerprint_match.group(1) != derived:
    fail("fingerprint does not match allowed-signers")
sums = root / "SHA256SUMS"
signature = root / "SHA256SUMS.sig"
if sums.is_symlink() or not sums.is_file() or signature.is_symlink() or not signature.is_file():
    fail("signed object or signature is missing or linked")
try:
    signature_text = signature.read_bytes().decode("ascii")
except (OSError, UnicodeDecodeError):
    fail("SSH signature armor is not canonical")
signature_match = re.fullmatch(
    r"-----BEGIN SSH SIGNATURE-----\n((?:[A-Za-z0-9+/]+={0,2}\n)+)-----END SSH SIGNATURE-----\n",
    signature_text,
)
if signature_match is None:
    fail("SSH signature armor is not canonical")
try:
    decoded_signature = base64.b64decode(signature_match.group(1).replace("\n", ""), validate=True)
except ValueError:
    fail("SSH signature armor is invalid base64")
if not decoded_signature.startswith(b"SSHSIG"):
    fail("SSH signature payload is invalid")
encoded_signature = base64.b64encode(decoded_signature).decode("ascii")
canonical_signature = "-----BEGIN SSH SIGNATURE-----\n" + "\n".join(
    encoded_signature[offset:offset + 70]
    for offset in range(0, len(encoded_signature), 70)
) + "\n-----END SSH SIGNATURE-----\n"
if signature_text != canonical_signature:
    fail("SSH signature armor is not canonical")
try:
    sums_bytes = sums.read_bytes()
except OSError as error:
    fail("cannot read SHA256SUMS: {}".format(error))
try:
    result = subprocess.run(
        [str(ssh_keygen), "-Y", "verify", "-f", str(allowed), "-I", "ruisheng-release",
         "-n", "ruisheng-candidate-v1", "-s", str(signature)],
        input=sums_bytes, capture_output=True, timeout=30, check=False,
    )
except (OSError, subprocess.SubprocessError) as error:
    fail("signature verifier failed: {}".format(error))
if result.returncode != 0:
    fail("OpenSSH signature verification failed")
try:
    sums_text = sums_bytes.decode("utf-8")
except UnicodeDecodeError:
    fail("SHA256SUMS is not valid UTF-8")
if not sums_text.endswith("\n") or "\r" in sums_text:
    fail("SHA256SUMS must use canonical LF line endings")
authenticated_sums = {}
for number, line in enumerate(sums_text.removesuffix("\n").split("\n"), 1):
    entry = re.fullmatch(r"([0-9a-f]{64})  ([^\\\x00]+)", line)
    if entry is None:
        fail("invalid SHA256SUMS entry at line {}".format(number))
    digest, relative = entry.groups()
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() != relative or any(
        part in ("", ".", "..") for part in path.parts
    ):
        fail("unsafe SHA256SUMS path: " + relative)
    if relative in authenticated_sums:
        fail("duplicate SHA256SUMS path: " + relative)
    authenticated_sums[relative] = digest
try:
    manifest_bytes = (root / "MANIFEST.json").read_bytes()
except OSError as error:
    fail("cannot read MANIFEST.json: {}".format(error))
if authenticated_sums.get("MANIFEST.json") != hashlib.sha256(manifest_bytes).hexdigest():
    fail("SHA-256 mismatch for MANIFEST.json")
try:
    manifest = json.loads(manifest_bytes.decode("utf-8"))
except (OSError, UnicodeDecodeError, ValueError) as error:
    fail("cannot parse signed manifest metadata: {}".format(error))
expected = {
    "status": "SIGNED", "scheme": "openssh-sshsig", "publisher": "ruisheng-release",
    "namespace": "ruisheng-candidate-v1", "key_type": "ssh-ed25519",
    "key_fingerprint": derived, "signed_object": "SHA256SUMS",
    "signature_file": "SHA256SUMS.sig",
}
if manifest.get("schema_version") != 2 or manifest.get("authenticity") != expected:
    fail("signed manifest authenticity contract is invalid")
PY

"$PYTHON" -I -S - "$PACKAGE_DIR" "$TRUST_DIR_INPUT" "$SSH_KEYGEN" > "$LOCK_FILE" <<'PY'
import base64
import gzip
import hashlib
import json
import os
import pathlib
import re
import stat
import struct
import subprocess
import sys
import tarfile

root = pathlib.Path(sys.argv[1]).resolve()
trust_input = pathlib.Path(sys.argv[2])
ssh_keygen = pathlib.Path(sys.argv[3])
fixed = {
    ".env.prod.example",
    "MANIFEST.json",
    "MANIFEST.md",
    "SHA256SUMS",
    "SHA256SUMS.sig",
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
components = ("postgres", "redis", "api", "gw", "web")

def fail(message):
    raise SystemExit("[verify] " + message)

def safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        fail("unsafe package path: {!r}".format(value))
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ("", ".", "..") for part in path.parts):
        fail("unsafe package path: {!r}".format(value))
    return value

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def read_archive_sha256_blob(archive, archive_label, digest, label, allow_missing=False):
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        fail("archive {} digest is invalid for {}".format(label, archive_label))
    blob_name = "blobs/sha256/" + digest.split(":", 1)[1]
    try:
        member = archive.getmember(blob_name)
    except KeyError:
        if allow_missing:
            return None
        fail("archive {} blob is missing: {}:{}".format(label, archive_label, blob_name))
    stream = archive.extractfile(member)
    if stream is None:
        fail("archive {} blob is not a regular file: {}:{}".format(
            label, archive_label, blob_name
        ))
    contents = stream.read()
    if "sha256:" + hashlib.sha256(contents).hexdigest() != digest:
        fail("archive {} digest mismatch for {}".format(label, archive_label))
    return contents

def parse_archive_json_object(contents, archive_label, label):
    try:
        value = json.loads(contents)
    except (UnicodeDecodeError, ValueError):
        fail("archive {} is invalid JSON for {}".format(label, archive_label))
    if not isinstance(value, dict):
        fail("archive {} root is invalid for {}".format(label, archive_label))
    return value

def validate_slsa_provenance_statement(statement, archive_label, main_manifest_digest):
    subjects = statement.get("subject")
    if (
        statement.get("_type") != "https://in-toto.io/Statement/v0.1"
        or statement.get("predicateType") != "https://slsa.dev/provenance/v1"
        or not isinstance(statement.get("predicate"), dict)
        or not isinstance(subjects, list)
        or not subjects
    ):
        fail("archive provenance statement is invalid for " + archive_label)
    expected_subject = main_manifest_digest.split(":", 1)[1]
    subject_digests = []
    for subject in subjects:
        digest = subject.get("digest") if isinstance(subject, dict) else None
        name = subject.get("name") if isinstance(subject, dict) else None
        sha256 = digest.get("sha256") if isinstance(digest, dict) else None
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            fail("archive provenance statement is invalid for " + archive_label)
        subject_digests.append(sha256)
    if expected_subject not in subject_digests:
        fail("archive provenance statement subject mismatch for " + archive_label)

def resolve_main_manifest_digest(
    archive, archive_label, descriptor_digest, descriptor_value, config_digest, config
):
    descriptor_config = descriptor_value.get("config")
    if isinstance(descriptor_config, dict) and descriptor_config.get("digest") == config_digest:
        return descriptor_digest
    nested_descriptors = descriptor_value.get("manifests")
    if not isinstance(nested_descriptors, list):
        return None
    matching_nested = []
    for nested in nested_descriptors:
        nested_digest = nested.get("digest") if isinstance(nested, dict) else None
        nested_bytes = read_archive_sha256_blob(
            archive, archive_label, nested_digest, "nested descriptor", allow_missing=True
        )
        if nested_bytes is None:
            # Docker 29 can retain source index entries while exporting only
            # the manifest blob for the selected local platform.
            continue
        nested_value = parse_archive_json_object(
            nested_bytes, archive_label, "nested descriptor"
        )
        nested_config = nested_value.get("config")
        if not isinstance(nested_config, dict):
            continue
        platform = nested.get("platform")
        if platform is not None and not isinstance(platform, dict):
            fail("archive nested descriptor platform is invalid for " + archive_label)
        if nested_config.get("digest") != config_digest:
            nested_config_bytes = read_archive_sha256_blob(
                archive, archive_label, nested_config.get("digest"), "nested config"
            )
            nested_config_value = parse_archive_json_object(
                nested_config_bytes, archive_label, "nested config"
            )
            attachment_platform = (
                nested_config_value.get("os"), nested_config_value.get("architecture")
            )
            descriptor_platform = (
                (platform.get("os"), platform.get("architecture"))
                if isinstance(platform, dict)
                else ("unknown", "unknown")
            )
            if attachment_platform != ("unknown", "unknown") or descriptor_platform != (
                "unknown", "unknown"
            ):
                fail("archive contains an additional runnable descriptor for " + archive_label)
            continue
        if isinstance(platform, dict) and (
            platform.get("os") != config.get("os")
            or platform.get("architecture") != config.get("architecture")
        ):
            fail("archive nested descriptor platform mismatch for " + archive_label)
        matching_nested.append(nested_digest)
    if len(matching_nested) > 1:
        fail("archive main descriptor is not unique for " + archive_label)
    return matching_nested[0] if matching_nested else None

def validate_provenance_attachment(
    archive, archive_label, descriptor, descriptor_value, main_manifest_digest
):
    manifest_media_type = "application/vnd.oci.image.manifest.v1+json"
    if (
        descriptor.get("mediaType") != manifest_media_type
        or descriptor_value.get("schemaVersion") != 2
        or descriptor_value.get("mediaType") != manifest_media_type
    ):
        fail("unsupported archive attachment for " + archive_label)
    annotations = descriptor.get("annotations")
    subject = (
        annotations.get("io.containerd.manifest.subject")
        if isinstance(annotations, dict)
        else None
    )
    if subject != main_manifest_digest:
        fail("archive provenance subject mismatch for " + archive_label)
    descriptor_platform = descriptor.get("platform")
    if descriptor_platform is not None and (
        not isinstance(descriptor_platform, dict)
        or descriptor_platform.get("os") != "unknown"
        or descriptor_platform.get("architecture") != "unknown"
    ):
        fail("archive provenance descriptor platform mismatch for " + archive_label)
    manifest_subject = descriptor_value.get("subject")
    if manifest_subject is not None and (
        not isinstance(manifest_subject, dict)
        or manifest_subject.get("digest") != main_manifest_digest
    ):
        fail("archive provenance subject mismatch for " + archive_label)
    config_descriptor = descriptor_value.get("config")
    if (
        not isinstance(config_descriptor, dict)
        or config_descriptor.get("mediaType")
        != "application/vnd.oci.image.config.v1+json"
    ):
        fail("archive provenance config is invalid for " + archive_label)
    config_bytes = read_archive_sha256_blob(
        archive, archive_label, config_descriptor.get("digest"), "provenance config"
    )
    provenance_config = parse_archive_json_object(
        config_bytes, archive_label, "provenance config"
    )
    if (
        provenance_config.get("os") != "unknown"
        or provenance_config.get("architecture") != "unknown"
    ):
        fail("archive provenance config platform mismatch for " + archive_label)
    layers = descriptor_value.get("layers")
    if not isinstance(layers, list) or len(layers) != 1:
        fail("archive provenance layers are invalid for " + archive_label)
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("mediaType") != "application/vnd.in-toto+json":
            fail("archive provenance layer media type is invalid for " + archive_label)
        layer_annotations = layer.get("annotations")
        if (
            not isinstance(layer_annotations, dict)
            or layer_annotations.get("in-toto.io/predicate-type")
            != "https://slsa.dev/provenance/v1"
        ):
            fail("archive provenance layer is invalid for " + archive_label)
        layer_bytes = read_archive_sha256_blob(
            archive, archive_label, layer.get("digest"), "provenance layer"
        )
        statement = parse_archive_json_object(
            layer_bytes, archive_label, "provenance layer"
        )
        validate_slsa_provenance_statement(
            statement, archive_label, main_manifest_digest
        )

def protected_with_ancestors(path, label):
    absolute = path.absolute()
    for current in (absolute, *absolute.parents):
        if current.is_symlink() or not current.exists():
            fail(label + " is missing or linked: " + str(current))
        metadata = current.stat()
        if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            fail(label + " has unsafe ownership or write permissions: " + str(current))

if trust_input.is_symlink() or not trust_input.is_dir():
    fail("publisher authenticity FAILED: external trust directory is missing or linked")
trust = trust_input.resolve()
if trust == root or root in trust.parents:
    fail("publisher authenticity FAILED: trust directory must be outside the candidate package")
allowed = trust / "release-allowed-signers"
fingerprint_path = trust / "release-key-fingerprint"
protected_with_ancestors(trust, "publisher authenticity FAILED: trust anchor")
protected_with_ancestors(allowed, "publisher authenticity FAILED: allowed-signers")
protected_with_ancestors(fingerprint_path, "publisher authenticity FAILED: fingerprint")
protected_with_ancestors(
    ssh_keygen.resolve(strict=True), "publisher authenticity FAILED: system ssh-keygen"
)
try:
    allowed_text = allowed.read_bytes().decode("ascii")
    fingerprint_text = fingerprint_path.read_bytes().decode("ascii")
except (OSError, UnicodeDecodeError) as error:
    fail("publisher authenticity FAILED: cannot read ASCII trust anchor: {}".format(error))
allowed_match = re.fullmatch(
    r"ruisheng-release ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n", allowed_text
)
if allowed_match is None:
    fail("publisher authenticity FAILED: allowed-signers is not the approved identity")
try:
    key_blob = base64.b64decode(allowed_match.group(1), validate=True)
    offset = 0
    key_fields = []
    for _ in range(2):
        if len(key_blob) - offset < 4:
            fail("publisher authenticity FAILED: public key blob is truncated")
        length = struct.unpack(">I", key_blob[offset:offset + 4])[0]
        offset += 4
        if len(key_blob) - offset < length:
            fail("publisher authenticity FAILED: public key blob is truncated")
        key_fields.append(key_blob[offset:offset + length])
        offset += length
except (ValueError, struct.error):
    fail("publisher authenticity FAILED: public key blob is invalid")
if key_fields[0] != b"ssh-ed25519" or len(key_fields[1]) != 32 or offset != len(key_blob):
    fail("publisher authenticity FAILED: public key is not canonical ssh-ed25519")
fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key_blob).digest()).decode().rstrip("=")
if fingerprint_text != fingerprint + "\n":
    fail("publisher authenticity FAILED: fingerprint does not match allowed-signers")

sums_path = root / "SHA256SUMS"
signature_path = root / "SHA256SUMS.sig"
if sums_path.is_symlink() or not sums_path.is_file() or signature_path.is_symlink() or not signature_path.is_file():
    fail("publisher authenticity FAILED: signed object or signature is missing or linked")
try:
    signature_text = signature_path.read_bytes().decode("ascii")
except (OSError, UnicodeDecodeError):
    fail("publisher authenticity FAILED: SSH signature armor is not canonical")
signature_match = re.fullmatch(
    r"-----BEGIN SSH SIGNATURE-----\n((?:[A-Za-z0-9+/]+={0,2}\n)+)-----END SSH SIGNATURE-----\n",
    signature_text,
)
if signature_match is None:
    fail("publisher authenticity FAILED: SSH signature armor is not canonical")
try:
    decoded_signature = base64.b64decode(
        signature_match.group(1).replace("\n", ""), validate=True
    )
except ValueError:
    fail("publisher authenticity FAILED: SSH signature armor is invalid base64")
encoded_signature = base64.b64encode(decoded_signature).decode("ascii")
canonical_signature = "-----BEGIN SSH SIGNATURE-----\n" + "\n".join(
    encoded_signature[index:index + 70]
    for index in range(0, len(encoded_signature), 70)
) + "\n-----END SSH SIGNATURE-----\n"
if not decoded_signature.startswith(b"SSHSIG") or signature_text != canonical_signature:
    fail("publisher authenticity FAILED: SSH signature armor is not canonical")
try:
    sums_bytes = sums_path.read_bytes()
    signature_result = subprocess.run(
        [str(ssh_keygen), "-Y", "verify", "-f", str(allowed), "-I", "ruisheng-release",
         "-n", "ruisheng-candidate-v1", "-s", str(signature_path)],
        input=sums_bytes, capture_output=True, timeout=30, check=False,
    )
except (OSError, subprocess.SubprocessError) as error:
    fail("publisher authenticity FAILED: signature verifier failed: {}".format(error))
if signature_result.returncode != 0:
    fail("publisher authenticity FAILED: OpenSSH signature verification failed")
try:
    sums_text = sums_bytes.decode("utf-8")
except UnicodeDecodeError:
    fail("publisher authenticity FAILED: SHA256SUMS is not valid UTF-8")
if not sums_text.endswith("\n") or "\r" in sums_text:
    fail("publisher authenticity FAILED: SHA256SUMS must use canonical LF line endings")
sums = {}
for number, line in enumerate(sums_text.removesuffix("\n").split("\n"), 1):
    match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
    if match is None:
        fail("publisher authenticity FAILED: invalid SHA256SUMS entry at line {}".format(number))
    digest, relative = match.groups()
    safe_path(relative)
    if relative in sums:
        fail("publisher authenticity FAILED: duplicate SHA256SUMS path: " + relative)
    sums[relative] = digest

expected_files = fixed | {"images/{}.tar.gz".format(value) for value in components}
actual_files = set()
for current, directories, files in os.walk(str(root), followlinks=False):
    current_path = pathlib.Path(current)
    for name in directories:
        directory = current_path / name
        relative = directory.relative_to(root).as_posix()
        safe_path(relative)
        if directory.is_symlink() or relative != "images":
            fail("publisher authenticity FAILED: extra or unsafe directory: " + relative)
    for name in files:
        path = current_path / name
        relative = path.relative_to(root).as_posix()
        safe_path(relative)
        if path.is_symlink() or not path.is_file():
            fail("publisher authenticity FAILED: non-regular package file: " + relative)
        actual_files.add(relative)
if actual_files != expected_files:
    fail("publisher authenticity FAILED: file allowlist mismatch: missing={}, extra={}".format(
        sorted(expected_files - actual_files), sorted(actual_files - expected_files)
    ))
expected_sums = expected_files - {"SHA256SUMS", "SHA256SUMS.sig"}
if set(sums) != expected_sums:
    fail("publisher authenticity FAILED: SHA256SUMS allowlist mismatch: missing={}, extra={}".format(
        sorted(expected_sums - set(sums)), sorted(set(sums) - expected_sums)
    ))
manifest_bytes = None
for relative, expected_digest in sums.items():
    path = root / relative
    actual_digest = sha256(path)
    if actual_digest != expected_digest:
        fail("publisher authenticity FAILED: SHA-256 mismatch for {}: expected {}, got {}".format(
            relative, expected_digest, actual_digest
        ))
    if relative == "MANIFEST.json":
        manifest_bytes = path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != expected_digest:
            fail("publisher authenticity FAILED: MANIFEST.json changed while being authenticated")

try:
    manifest = json.loads(manifest_bytes.decode("utf-8"))
except Exception as error:
    fail("publisher authenticity FAILED: cannot parse authenticated MANIFEST.json: {}".format(error))

candidate_id = manifest.get("candidate_id")
if not isinstance(candidate_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", candidate_id) is None:
    fail("invalid candidate ID")
if manifest.get("schema_version") != 2 or manifest.get("authenticity", {}).get("status") != "SIGNED":
    fail("manifest authenticity contract is invalid")
target_os = manifest.get("target_os")
target_arch = manifest.get("target_architecture")
images = manifest.get("images")
if not isinstance(images, list) or [item.get("component") for item in images if isinstance(item, dict)] != list(components):
    fail("manifest must contain the five ordered image components")

expected_files = set(fixed)
seen_refs = set()
seen_ids = set()
for image in images:
    component = image["component"]
    expected_ref = "ruisheng-candidate/{}:{}".format(component, candidate_id)
    expected_archive = "images/{}.tar.gz".format(component)
    if image.get("candidate_reference") != expected_ref:
        fail("candidate tag mismatch for " + component)
    if image.get("archive") != expected_archive:
        fail("archive path mismatch for " + component)
    if image.get("os") != target_os or image.get("architecture") != target_arch:
        fail("platform mismatch for " + component)
    image_id = image.get("image_id")
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        fail("invalid image ID for " + component)
    if expected_ref in seen_refs or image_id in seen_ids:
        fail("duplicate image identity in manifest")
    seen_refs.add(expected_ref)
    seen_ids.add(image_id)
    expected_files.add(expected_archive)

actual_files = set()
for current, directories, files in os.walk(str(root), followlinks=False):
    current_path = pathlib.Path(current)
    for name in directories:
        directory = current_path / name
        relative = directory.relative_to(root).as_posix()
        safe_path(relative)
        if directory.is_symlink() or relative != "images":
            fail("extra or unsafe directory: " + relative)
    for name in files:
        path = current_path / name
        relative = path.relative_to(root).as_posix()
        safe_path(relative)
        if path.is_symlink() or not path.is_file():
            fail("non-regular package file: " + relative)
        actual_files.add(relative)
if actual_files != expected_files:
    fail("publisher authenticity FAILED: file allowlist mismatch: missing={}, extra={}".format(
        sorted(expected_files - actual_files), sorted(actual_files - expected_files)
    ))

expected_sums = expected_files - {"SHA256SUMS", "SHA256SUMS.sig"}
if set(sums) != expected_sums:
    fail("publisher authenticity FAILED: SHA256SUMS allowlist mismatch: missing={}, extra={}".format(
        sorted(expected_sums - set(sums)), sorted(set(sums) - expected_sums)
    ))
for relative, expected_digest in sums.items():
    actual_digest = sha256(root / relative)
    if actual_digest != expected_digest:
        fail("publisher authenticity FAILED: SHA-256 mismatch for {}: expected {}, got {}".format(
            relative, expected_digest, actual_digest
        ))

for image in images:
    archive_path = root / image["archive"]
    if sums[image["archive"]] != image.get("sha256"):
        fail("manifest/SHA256SUMS mismatch for " + image["archive"])
    try:
        with tarfile.open(str(archive_path), "r:gz") as archive:
            names = [member.name for member in archive.getmembers()]
            if len(names) != len(set(names)):
                fail("duplicate archive member in " + image["archive"])
            for member in archive.getmembers():
                safe_path(member.name.rstrip("/") or member.name)
                if member.issym() or member.islnk():
                    fail("link member in " + image["archive"])
            stream = archive.extractfile("manifest.json")
            if stream is None:
                fail("missing archive manifest in " + image["archive"])
            archive_manifest = json.load(stream)
            if len(archive_manifest) != 1 or archive_manifest[0].get("RepoTags") != [image["candidate_reference"]]:
                fail("archive tag mismatch for " + image["component"])
            config_name = safe_path(archive_manifest[0].get("Config"))
            config_stream = archive.extractfile(config_name)
            if config_stream is None:
                fail("missing archive config for " + image["component"])
            config_bytes = config_stream.read()
            config = json.loads(config_bytes)
            config_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
            archive_id = config_digest
            if "index.json" in names:
                index_stream = archive.extractfile("index.json")
                if index_stream is None:
                    fail("missing archive index for " + image["component"])
                index = json.load(index_stream)
                descriptors = index.get("manifests") if isinstance(index, dict) else None
                if not isinstance(descriptors, list) or not descriptors:
                    fail("archive index must contain image descriptors for " + image["component"])
                loaded_descriptors = []
                for descriptor in descriptors:
                    if not isinstance(descriptor, dict):
                        fail("archive descriptor is invalid for " + image["component"])
                    descriptor_digest = descriptor.get("digest")
                    descriptor_bytes = read_archive_sha256_blob(
                        archive, image["archive"], descriptor_digest, "descriptor"
                    )
                    descriptor_value = parse_archive_json_object(
                        descriptor_bytes, image["archive"], "descriptor"
                    )
                    resolved = resolve_main_manifest_digest(
                        archive,
                        image["archive"],
                        descriptor_digest,
                        descriptor_value,
                        config_digest,
                        config,
                    )
                    loaded_descriptors.append(
                        (descriptor, descriptor_digest, descriptor_value, resolved)
                    )
                main_descriptors = [
                    loaded for loaded in loaded_descriptors if loaded[3] is not None
                ]
                if len(main_descriptors) != 1:
                    fail("archive main descriptor is not unique for " + image["component"])
                archive_id = main_descriptors[0][1]
                main_manifest_digest = main_descriptors[0][3]
                for descriptor, _digest, descriptor_value, resolved in loaded_descriptors:
                    if resolved is None:
                        validate_provenance_attachment(
                            archive,
                            image["archive"],
                            descriptor,
                            descriptor_value,
                            main_manifest_digest,
                        )
    except (OSError, EOFError, gzip.BadGzipFile, tarfile.TarError, ValueError, KeyError) as error:
        fail("invalid Docker archive {}: {}".format(image["archive"], error))
    if (archive_id, config.get("os"), config.get("architecture")) != (
        image["image_id"], image["os"], image["architecture"]
    ):
        fail("archive identity mismatch for " + image["component"])
    fields = (
        image["component"], image["candidate_reference"], image["image_id"],
        image["os"], image["architecture"], image["archive"], sums[image["archive"]]
    )
    if any(not isinstance(field, str) or "\t" in field or "\n" in field for field in fields):
        fail("unsafe image lock field")
    print("\t".join(fields))
PY

[[ -x "$DOCKER" ]] || { echo "[verify] Docker CLI is required at /usr/bin/docker" >&2; exit 1; }
[[ -d "/proc/$$/fd" ]] || { echo "[verify] publisher authenticity FAILED: /proc file descriptors are required" >&2; exit 1; }

declare -a ARCHIVE_FDS=()
declare -a ARCHIVE_FD_PATHS=()
while IFS=$'\t' read -r component reference image_id image_os architecture archive digest; do
  exec {archive_fd}<"$PACKAGE_DIR/$archive"
  archive_fd_path="/proc/$$/fd/$archive_fd"
  locked_digest="$($SHA256SUM "$archive_fd_path")"
  locked_digest="${locked_digest%% *}"
  if [[ "$locked_digest" != "$digest" ]]; then
    echo "[verify] publisher authenticity FAILED: archive changed before load: $archive" >&2
    exit 1
  fi
  ARCHIVE_FDS+=("$archive_fd")
  ARCHIVE_FD_PATHS+=("$archive_fd_path")
done < "$LOCK_FILE"

echo "[verify] Publisher authenticity VERIFIED; file allowlist, SHA-256, and archive identities passed."
archive_index=0
while IFS=$'\t' read -r component reference image_id image_os architecture archive digest; do
  echo "[verify] Loading $component from $archive"
  "$DOCKER" image load --input "${ARCHIVE_FD_PATHS[$archive_index]}" >/dev/null
  archive_index=$((archive_index + 1))
done < "$LOCK_FILE"
for archive_fd in "${ARCHIVE_FDS[@]}"; do
  exec {archive_fd}<&-
done

while IFS=$'\t' read -r component reference image_id image_os architecture archive digest; do
  mapfile -t metadata < <(
    "$DOCKER" image inspect "$reference" \
      --format '{{.Id}}{{println}}{{.Os}}{{println}}{{.Architecture}}{{println}}{{range .RepoTags}}{{println .}}{{end}}'
  )
  if [[ "${metadata[0]:-}" != "$image_id" || "${metadata[1]:-}" != "$image_os" || "${metadata[2]:-}" != "$architecture" ]]; then
    echo "[verify] Loaded identity mismatch for $component" >&2
    exit 1
  fi
  if ! printf '%s\n' "${metadata[@]:3}" | "$GREP" -Fqx -- "$reference"; then
    echo "[verify] Loaded candidate tag missing for $component: $reference" >&2
    exit 1
  fi
done < "$LOCK_FILE"

"$DOCKER" compose \
  --env-file "$SITE_ENV_FILE" \
  -f "$PACKAGE_DIR/docker-compose.prod.yml" \
  config --images > "$ACTUAL_FILE"
"$CUT" -f2 "$LOCK_FILE" | "$SORT" -u > "$EXPECTED_FILE"
"$SORT" -u "$ACTUAL_FILE" -o "$ACTUAL_FILE"
if ! "$DIFF" -u "$EXPECTED_FILE" "$ACTUAL_FILE"; then
  echo "[verify] Compose image set does not match the manifest" >&2
  exit 1
fi
api_reference=""
while IFS=$'\t' read -r component reference image_id image_os architecture archive digest; do
  if [[ "$component" == "api" ]]; then
    api_reference="$reference"
  fi
done < "$LOCK_FILE"
if [[ -z "$api_reference" ]] || [[ "$("$GREP" -Fxc "$api_reference" < <(
  "$DOCKER" compose --env-file "$SITE_ENV_FILE" \
    -f "$PACKAGE_DIR/docker-compose.prod.yml" config --images
))" -ne 2 ]]; then
  echo "[verify] Compose migrate/api do not share exactly one API image" >&2
  exit 1
fi

"$DOCKER" compose \
  --env-file "$SITE_ENV_FILE" \
  -f "$PACKAGE_DIR/docker-compose.prod.yml" \
  config --format json > "$CONFIG_FILE"
"$PYTHON" -I -S - "$CONFIG_FILE" "$LOCK_FILE" <<'PY'
import json
import sys

services = json.load(open(sys.argv[1], encoding="utf-8")).get("services", {})
rows = [line.rstrip("\n").split("\t") for line in open(sys.argv[2], encoding="utf-8")]
images = {row[0]: row for row in rows}
expected_images = {
    "postgres": images["postgres"][1],
    "redis": images["redis"][1],
    "migrate": images["api"][1],
    "api": images["api"][1],
    "gw": images["gw"][1],
    "web": images["web"][1],
}
if set(services) != set(expected_images):
    raise SystemExit("[verify] Compose service set mismatch")
platforms = {f"{row[3]}/{row[4]}" for row in rows}
if len(platforms) != 1:
    raise SystemExit("[verify] Image lock platform set mismatch")
expected_platform = platforms.pop()
for name, service in services.items():
    if service.get("image") != expected_images[name]:
        raise SystemExit("[verify] Compose image mismatch for service: " + name)
    if service.get("platform") != expected_platform:
        raise SystemExit("[verify] Compose platform mismatch for service: " + name)
    if "build" in service or service.get("pull_policy") != "never":
        raise SystemExit("[verify] Compose service may build or pull: " + name)
PY

echo "[verify] Integrity and loaded image identity passed."
echo "[verify] Publisher authenticity VERIFIED; CAP-1/G0-03 authenticity gate passed."
echo "[verify] B-04 remains BLOCKED; close it only through the independent field acceptance workflow." >&2
exit 2
