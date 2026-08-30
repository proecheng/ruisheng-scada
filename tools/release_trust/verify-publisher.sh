#!/bin/bash
set -euo pipefail
PATH="/usr/bin:/bin"
export PATH
HOME="/root"
export HOME
unset BASH_ENV ENV CDPATH PYTHONHOME PYTHONPATH TMP TMPDIR TEMP DOCKER_CONFIG \
  DOCKER_CLI_PLUGIN_EXTRA_DIRS DOCKER_HOST DOCKER_CONTEXT XDG_CONFIG_HOME \
  PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE

usage() {
  cat >&2 <<'EOF'
usage: verify-publisher.sh <candidate-directory> [site-env-file]
       verify-publisher.sh <candidate-directory> --qualification-mode ValidatorSchema
       verify-publisher.sh <candidate-directory> --qualification-mode ValidatorProfile \
         --qualification-profile-path <path> --qualification-root-path <path> \
         --qualification-trust-policy-path <path>
       verify-publisher.sh <candidate-directory> --qualification-mode ValidatorLegacy \
         --qualification-evidence-path <path> --qualification-root-path <path>
       verify-publisher.sh <candidate-directory> --qualification-mode Receipt \
         --qualification-output-directory <path> --qualification-signing-identity <path> \
         --qualification-verifier-id <id> --qualification-verifier-key-id <id>
EOF
}

if [[ "$#" -lt 1 ]]; then
  usage
  exit 1
fi
PACKAGE_INPUT="$1"
shift
SITE_ENV_INPUT=""
if [[ "$#" -gt 0 && "$1" != --* ]]; then
  SITE_ENV_INPUT="$1"
  shift
fi

QUALIFICATION_MODE="None"
QUALIFICATION_PROFILE_PATH=""
QUALIFICATION_EVIDENCE_PATH=""
QUALIFICATION_ROOT_PATH=""
QUALIFICATION_TRUST_POLICY_PATH=""
QUALIFICATION_OUTPUT_DIRECTORY=""
QUALIFICATION_SIGNING_IDENTITY=""
QUALIFICATION_VERIFIER_ID=""
QUALIFICATION_VERIFIER_KEY_ID=""
QUALIFICATION_MODE_SEEN=0
QUALIFICATION_PROFILE_PATH_SEEN=0
QUALIFICATION_EVIDENCE_PATH_SEEN=0
QUALIFICATION_ROOT_PATH_SEEN=0
QUALIFICATION_TRUST_POLICY_PATH_SEEN=0
QUALIFICATION_OUTPUT_DIRECTORY_SEEN=0
QUALIFICATION_SIGNING_IDENTITY_SEEN=0
QUALIFICATION_VERIFIER_ID_SEEN=0
QUALIFICATION_VERIFIER_KEY_ID_SEEN=0

while [[ "$#" -gt 0 ]]; do
  option="$1"
  if [[ "$#" -lt 2 || -z "$2" ]]; then
    echo "[publisher] authenticity FAILED: missing value for ${option}" >&2
    usage
    exit 1
  fi
  value="$2"
  shift 2
  case "$option" in
    --qualification-mode)
      [[ "$QUALIFICATION_MODE_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification mode" >&2
        exit 1
      }
      QUALIFICATION_MODE_SEEN=1
      QUALIFICATION_MODE="$value"
      ;;
    --qualification-profile-path)
      [[ "$QUALIFICATION_PROFILE_PATH_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification profile path" >&2
        exit 1
      }
      QUALIFICATION_PROFILE_PATH_SEEN=1
      QUALIFICATION_PROFILE_PATH="$value"
      ;;
    --qualification-evidence-path)
      [[ "$QUALIFICATION_EVIDENCE_PATH_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification evidence path" >&2
        exit 1
      }
      QUALIFICATION_EVIDENCE_PATH_SEEN=1
      QUALIFICATION_EVIDENCE_PATH="$value"
      ;;
    --qualification-root-path)
      [[ "$QUALIFICATION_ROOT_PATH_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification root path" >&2
        exit 1
      }
      QUALIFICATION_ROOT_PATH_SEEN=1
      QUALIFICATION_ROOT_PATH="$value"
      ;;
    --qualification-trust-policy-path)
      [[ "$QUALIFICATION_TRUST_POLICY_PATH_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification trust-policy path" >&2
        exit 1
      }
      QUALIFICATION_TRUST_POLICY_PATH_SEEN=1
      QUALIFICATION_TRUST_POLICY_PATH="$value"
      ;;
    --qualification-output-directory)
      [[ "$QUALIFICATION_OUTPUT_DIRECTORY_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification output directory" >&2
        exit 1
      }
      QUALIFICATION_OUTPUT_DIRECTORY_SEEN=1
      QUALIFICATION_OUTPUT_DIRECTORY="$value"
      ;;
    --qualification-signing-identity)
      [[ "$QUALIFICATION_SIGNING_IDENTITY_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification signing identity" >&2
        exit 1
      }
      QUALIFICATION_SIGNING_IDENTITY_SEEN=1
      QUALIFICATION_SIGNING_IDENTITY="$value"
      ;;
    --qualification-verifier-id)
      [[ "$QUALIFICATION_VERIFIER_ID_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification verifier ID" >&2
        exit 1
      }
      QUALIFICATION_VERIFIER_ID_SEEN=1
      QUALIFICATION_VERIFIER_ID="$value"
      ;;
    --qualification-verifier-key-id)
      [[ "$QUALIFICATION_VERIFIER_KEY_ID_SEEN" -eq 0 ]] || {
        echo "[publisher] authenticity FAILED: duplicate qualification verifier key ID" >&2
        exit 1
      }
      QUALIFICATION_VERIFIER_KEY_ID_SEEN=1
      QUALIFICATION_VERIFIER_KEY_ID="$value"
      ;;
    *)
      echo "[publisher] authenticity FAILED: unsupported option: ${option}" >&2
      usage
      exit 1
      ;;
  esac
done

case "$QUALIFICATION_MODE" in
  None)
    [[ "$QUALIFICATION_PROFILE_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_EVIDENCE_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_ROOT_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_TRUST_POLICY_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_OUTPUT_DIRECTORY_SEEN" -eq 0 &&
       "$QUALIFICATION_SIGNING_IDENTITY_SEEN" -eq 0 &&
       "$QUALIFICATION_VERIFIER_ID_SEEN" -eq 0 &&
       "$QUALIFICATION_VERIFIER_KEY_ID_SEEN" -eq 0 ]] || {
      echo "[publisher] authenticity FAILED: qualification-only parameters require an explicit qualification mode" >&2
      exit 1
    }
    ;;
  ValidatorSchema)
    [[ -z "$SITE_ENV_INPUT" &&
       "$QUALIFICATION_PROFILE_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_EVIDENCE_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_ROOT_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_TRUST_POLICY_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_OUTPUT_DIRECTORY_SEEN" -eq 0 &&
       "$QUALIFICATION_SIGNING_IDENTITY_SEEN" -eq 0 &&
       "$QUALIFICATION_VERIFIER_ID_SEEN" -eq 0 &&
       "$QUALIFICATION_VERIFIER_KEY_ID_SEEN" -eq 0 ]] || {
      echo "[publisher] authenticity FAILED: ValidatorSchema does not accept additional qualification parameters or a site environment" >&2
      exit 1
    }
    ;;
  ValidatorProfile)
    [[ -z "$SITE_ENV_INPUT" &&
       "$QUALIFICATION_PROFILE_PATH_SEEN" -eq 1 &&
       "$QUALIFICATION_ROOT_PATH_SEEN" -eq 1 &&
       "$QUALIFICATION_TRUST_POLICY_PATH_SEEN" -eq 1 &&
       "$QUALIFICATION_EVIDENCE_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_OUTPUT_DIRECTORY_SEEN" -eq 0 &&
       "$QUALIFICATION_SIGNING_IDENTITY_SEEN" -eq 0 &&
       "$QUALIFICATION_VERIFIER_ID_SEEN" -eq 0 &&
       "$QUALIFICATION_VERIFIER_KEY_ID_SEEN" -eq 0 ]] || {
      echo "[publisher] authenticity FAILED: ValidatorProfile requires only profile, root, and trust-policy paths" >&2
      exit 1
    }
    ;;
  ValidatorLegacy)
    [[ -z "$SITE_ENV_INPUT" &&
       "$QUALIFICATION_EVIDENCE_PATH_SEEN" -eq 1 &&
       "$QUALIFICATION_ROOT_PATH_SEEN" -eq 1 &&
       "$QUALIFICATION_PROFILE_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_TRUST_POLICY_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_OUTPUT_DIRECTORY_SEEN" -eq 0 &&
       "$QUALIFICATION_SIGNING_IDENTITY_SEEN" -eq 0 &&
       "$QUALIFICATION_VERIFIER_ID_SEEN" -eq 0 &&
       "$QUALIFICATION_VERIFIER_KEY_ID_SEEN" -eq 0 ]] || {
      echo "[publisher] authenticity FAILED: ValidatorLegacy requires only evidence and root paths" >&2
      exit 1
    }
    ;;
  Receipt)
    [[ -z "$SITE_ENV_INPUT" &&
       "$QUALIFICATION_OUTPUT_DIRECTORY_SEEN" -eq 1 &&
       "$QUALIFICATION_SIGNING_IDENTITY_SEEN" -eq 1 &&
       "$QUALIFICATION_VERIFIER_ID_SEEN" -eq 1 &&
       "$QUALIFICATION_VERIFIER_KEY_ID_SEEN" -eq 1 &&
       "$QUALIFICATION_PROFILE_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_EVIDENCE_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_ROOT_PATH_SEEN" -eq 0 &&
       "$QUALIFICATION_TRUST_POLICY_PATH_SEEN" -eq 0 ]] || {
      echo "[publisher] authenticity FAILED: Receipt requires only output, signing identity, verifier ID, and verifier key ID" >&2
      exit 1
    }
    [[ "$QUALIFICATION_VERIFIER_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ &&
       "$QUALIFICATION_VERIFIER_KEY_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
      echo "[publisher] authenticity FAILED: receipt verifier identifiers are invalid" >&2
      exit 1
    }
    ;;
  *)
    echo "[publisher] authenticity FAILED: unsupported qualification mode" >&2
    exit 1
    ;;
esac

TRUST_INPUT="/etc/ruisheng/trust"
PYTHON="/usr/bin/python3"
SSH_KEYGEN="/usr/bin/ssh-keygen"
BASH="/bin/bash"
[[ -x "$PYTHON" ]] || { echo "[publisher] authenticity FAILED: /usr/bin/python3 is required" >&2; exit 1; }
[[ -x "$SSH_KEYGEN" ]] || { echo "[publisher] authenticity FAILED: /usr/bin/ssh-keygen is required" >&2; exit 1; }
[[ -x "$BASH" ]] || { echo "[publisher] authenticity FAILED: /bin/bash is required" >&2; exit 1; }

"$PYTHON" -I -S - "$PACKAGE_INPUT" "$TRUST_INPUT" "$0" "$PYTHON" "$SSH_KEYGEN" "$BASH" \
  "$SITE_ENV_INPUT" "$QUALIFICATION_MODE" "$QUALIFICATION_PROFILE_PATH" \
  "$QUALIFICATION_EVIDENCE_PATH" "$QUALIFICATION_ROOT_PATH" \
  "$QUALIFICATION_TRUST_POLICY_PATH" "$QUALIFICATION_OUTPUT_DIRECTORY" \
  "$QUALIFICATION_SIGNING_IDENTITY" "$QUALIFICATION_VERIFIER_ID" \
  "$QUALIFICATION_VERIFIER_KEY_ID" <<'PY'
import atexit
import base64
import ctypes
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
import pathlib
import re
import secrets
import signal
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib

MAX_RELEASE_JSON_BYTES = 4 * 1024 * 1024
MAX_QUALIFICATION_MEMBER_BYTES = 64 * 1024 * 1024
MAX_QUALIFICATION_RUNTIME_FILES = 32_768
MAX_QUALIFICATION_RUNTIME_DIRECTORIES = 32_768
MAX_QUALIFICATION_RUNTIME_FILE_BYTES = 512 * 1024 * 1024
MAX_QUALIFICATION_RUNTIME_TOTAL_BYTES = 32 * 1024 * 1024 * 1024
MAX_QUALIFICATION_RUNTIME_PATH_BYTES = 4096
USTAR_BLOCK_BYTES = 512
USTAR_RECORD_BYTES = 20 * USTAR_BLOCK_BYTES
MIN_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS = 2
MAX_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS = USTAR_RECORD_BYTES // USTAR_BLOCK_BYTES + 1
QUALIFICATION_RUNTIME_ROOT = pathlib.Path("/opt/ruisheng/qualification-runtime")
QUALIFICATION_RECEIPT_AGENT_SOCKET = pathlib.Path(
    "/run/ruisheng/receipt-signing-agent.sock"
)
QUALIFICATION_RUNTIME_MANIFEST = "qualification-runtime-manifest.json"
QUALIFICATION_RUNTIME_PYTHON = "bin/python3.11"
QUALIFICATION_RUNTIME_DEPENDENCIES = "lib/python3.11/site-packages"
FRESHNESS_PROVIDER = pathlib.Path(
    "/usr/local/libexec/ruisheng/trust-root-freshness-provider"
)
FRESHNESS_PROVIDER_CONFIG = pathlib.Path(
    "/etc/ruisheng/trust/point-profile-freshness-provider.json"
)
FRESHNESS_TRUST_ROOT = pathlib.Path(
    "/etc/ruisheng/trust/point-profile-policy-root.json"
)
FRESHNESS_PROVIDER_TIMEOUT_SECONDS = 30
FRESHNESS_VERIFIER_ID = "ruisheng.protected-release-publisher.posix.v1"
QUALIFICATION_MEMBER_NAMES = (
    "tools/validate_device_point_profile.py",
    "tools/trust_root_freshness.py",
    "schemas/point-profile/point-profile-v1.schema.json",
    "tools/release_artifacts.py",
    "tools/release_verification_receipt.py",
    "pyproject.toml",
    "uv.lock",
)
QUALIFICATION_TOOLCHAIN_MANIFEST = "qualification-toolchain-manifest.json"
QUALIFICATION_EXPECTED_MEMBERS = (*QUALIFICATION_MEMBER_NAMES, QUALIFICATION_TOOLCHAIN_MANIFEST)
MAX_QUALIFICATION_TAR_BYTES = (
    len(QUALIFICATION_EXPECTED_MEMBERS) * USTAR_BLOCK_BYTES
    + len(QUALIFICATION_MEMBER_NAMES) * MAX_QUALIFICATION_MEMBER_BYTES
    + MAX_RELEASE_JSON_BYTES
    + MAX_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS * USTAR_BLOCK_BYTES
)
MAX_QUALIFICATION_GZIP_BYTES = (
    MAX_QUALIFICATION_TAR_BYTES + MAX_QUALIFICATION_TAR_BYTES // 100 + 64 * 1024
)

package_input, trust_input, verifier_input, python_input, ssh_keygen_input, bash_input = map(
    pathlib.Path, sys.argv[1:7]
)
(
    site_env_input,
    qualification_mode,
    qualification_profile_path,
    qualification_evidence_path,
    qualification_root_path,
    qualification_trust_policy_path,
    qualification_output_directory,
    qualification_signing_identity,
    qualification_verifier_id,
    qualification_verifier_key_id,
) = sys.argv[7:17]

def fail(message):
    raise SystemExit("[publisher] authenticity FAILED: " + message)

qualification_values = (
    qualification_profile_path,
    qualification_evidence_path,
    qualification_root_path,
    qualification_trust_policy_path,
    qualification_output_directory,
    qualification_signing_identity,
    qualification_verifier_id,
    qualification_verifier_key_id,
)
if qualification_mode == "None":
    if any(qualification_values):
        fail("qualification-only parameters require an explicit qualification mode")
elif qualification_mode == "ValidatorSchema":
    if site_env_input or any(qualification_values):
        fail("ValidatorSchema does not accept additional qualification parameters")
elif qualification_mode == "ValidatorProfile":
    if (
        site_env_input
        or not qualification_profile_path
        or not qualification_root_path
        or not qualification_trust_policy_path
        or any((
            qualification_evidence_path,
            qualification_output_directory,
            qualification_signing_identity,
            qualification_verifier_id,
            qualification_verifier_key_id,
        ))
    ):
        fail("ValidatorProfile requires only profile, root, and trust-policy paths")
elif qualification_mode == "ValidatorLegacy":
    if (
        site_env_input
        or not qualification_evidence_path
        or not qualification_root_path
        or any((
            qualification_profile_path,
            qualification_trust_policy_path,
            qualification_output_directory,
            qualification_signing_identity,
            qualification_verifier_id,
            qualification_verifier_key_id,
        ))
    ):
        fail("ValidatorLegacy requires only evidence and root paths")
elif qualification_mode == "Receipt":
    if (
        site_env_input
        or not qualification_output_directory
        or not qualification_signing_identity
        or not qualification_verifier_id
        or not qualification_verifier_key_id
        or any((
            qualification_profile_path,
            qualification_evidence_path,
            qualification_root_path,
            qualification_trust_policy_path,
        ))
    ):
        fail("Receipt requires only output, signing identity, verifier ID, and verifier key ID")
    identifier = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
    if (
        identifier.fullmatch(qualification_verifier_id) is None
        or identifier.fullmatch(qualification_verifier_key_id) is None
    ):
        fail("receipt verifier identifiers are invalid")
else:
    fail("unsupported qualification mode")

def strict_json_loads(contents):
    def reject_duplicate_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON object key: " + key)
            value[key] = item
        return value
    return json.loads(contents, object_pairs_hook=reject_duplicate_keys)

def file_identity(metadata):
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_nlink, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )

def protected(path, label):
    if path.is_symlink() or not path.exists():
        fail(label + " is missing or linked")
    value = path.stat()
    if value.st_uid != 0 or value.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        fail(label + " has unsafe ownership or write permissions")

def protected_with_ancestors(path, label):
    absolute = path.absolute()
    protected(absolute, label)
    for ancestor in absolute.parents:
        protected(ancestor, label + " ancestor")

if os.geteuid() != 0:
    fail("bootstrap must run as root to create an authenticated protected snapshot")
protected_with_ancestors(verifier_input, "external verifier")
protected_with_ancestors(trust_input, "trust directory")
protected_with_ancestors(python_input.resolve(strict=True), "system python3")
protected_with_ancestors(ssh_keygen_input.resolve(strict=True), "system ssh-keygen")
protected_with_ancestors(bash_input.resolve(strict=True), "system bash")
package = package_input.resolve()
trust = trust_input.resolve()
if not package.is_dir() or package_input.is_symlink():
    fail("candidate directory is missing or linked")
if trust == package or package in trust.parents:
    fail("trust directory must be outside the candidate package")
allowed = trust / "release-allowed-signers"
fingerprint_path = trust / "release-key-fingerprint"
protected_with_ancestors(allowed, "allowed-signers")
protected_with_ancestors(fingerprint_path, "fingerprint")
try:
    allowed_text = allowed.read_bytes().decode("ascii")
    fingerprint_text = fingerprint_path.read_bytes().decode("ascii")
except (OSError, UnicodeDecodeError) as error:
    fail("cannot read ASCII trust anchor: {}".format(error))
match = re.fullmatch(r"ruisheng-release ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n", allowed_text)
if match is None:
    fail("allowed-signers is not the approved single identity")
try:
    blob = base64.b64decode(match.group(1), validate=True)
    offset = 0
    fields = []
    for _ in range(2):
        if len(blob) - offset < 4:
            fail("public key blob is truncated")
        length = struct.unpack(">I", blob[offset:offset + 4])[0]
        offset += 4
        if len(blob) - offset < length:
            fail("public key blob is truncated")
        fields.append(blob[offset:offset + length])
        offset += length
except (ValueError, struct.error):
    fail("public key blob is invalid")
if fields[0] != b"ssh-ed25519" or len(fields[1]) != 32 or offset != len(blob):
    fail("public key is not canonical ssh-ed25519")
fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
if fingerprint_text != fingerprint + "\n":
    fail("fingerprint does not match allowed-signers")

fixed_v2 = {
    ".env.prod.example", "MANIFEST.json", "MANIFEST.md", "SHA256SUMS",
    "SHA256SUMS.sig", "docker-compose.prod.yml", "nginx.conf",
    "site-acceptance-profile.md.example", "site-health-acl.conf.example",
    "site-network.override.yml", "site-modbus-probe.json.example", "site-serial-hardware.json.example",
    "site-serial.env.example", "site-serial.override.yml", "setup-customer.md",
    "install_serial_hardware_task.ps1", "serial_hardware_attach.ps1",
    "probe_modbus_rtu.py", "run_modbus_probe.ps1",
    "validate-network-boundary.py", "validate_serial_hardware.py",
    "verify-candidate.ps1", "verify-candidate.sh",
}
components = ("postgres", "redis", "api", "gw", "web")
expected_v2 = fixed_v2 | {"images/{}.tar.gz".format(value) for value in components}
expected_v3 = expected_v2 | {"qualification-toolchain.tar.gz"}
actual = set()
for current, directories, files in os.walk(package, followlinks=False):
    current_path = pathlib.Path(current)
    for name in directories:
        path = current_path / name
        relative = path.relative_to(package).as_posix()
        if path.is_symlink() or relative != "images":
            fail("candidate contains an unsafe directory: " + relative)
    for name in files:
        path = current_path / name
        relative = path.relative_to(package).as_posix()
        if path.is_symlink() or not path.is_file():
            fail("candidate contains a linked or non-regular file: " + relative)
        actual.add(relative)
matches = [
    (version, expected)
    for version, expected in ((2, expected_v2), (3, expected_v3))
    if actual == expected
]
if len(matches) != 1:
    fail("candidate file allowlist mismatch: does not match complete v2 or v3")
expected_schema_version, expected = matches[0]

work = pathlib.Path("/var/lib/ruisheng/work")
for directory in (pathlib.Path("/var"), pathlib.Path("/var/lib"), work.parent, work):
    if not directory.exists():
        directory.mkdir(mode=0o700)
    protected_with_ancestors(directory, "fixed work directory")
protected_with_ancestors(work, "fixed work directory")
run_root = pathlib.Path(tempfile.mkdtemp(prefix="publisher-snapshot-", dir=work))
run_root.chmod(0o700)
def cleanup():
    try:
        shutil.rmtree(run_root)
    except FileNotFoundError:
        return None
    except OSError as error:
        return error
    return None

def cleanup_at_exit():
    error = cleanup()
    if error is not None:
        print("[publisher] protected work cleanup failed: {}: {}".format(run_root, error), file=sys.stderr)

atexit.register(cleanup_at_exit)
snapshot = run_root / "candidate"
snapshot.mkdir(mode=0o700)
(run_root / "docker-config").mkdir(mode=0o700)
(run_root / "docker-config" / "config.json").write_text("{}\n", encoding="ascii")
(run_root / "docker-config" / "config.json").chmod(0o600)
(snapshot / "images").mkdir(mode=0o700)
initial_identities = {}
initial_digests = {}
for relative in sorted(expected):
    source_path = package / relative
    path_before = source_path.stat(follow_symlinks=False)
    if not stat.S_ISREG(path_before.st_mode) or path_before.st_nlink != 1:
        fail("candidate file is linked or not a unique regular file: " + relative)
    expected_identity = file_identity(path_before)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source_path, flags)
    try:
        before = os.fstat(descriptor)
        if file_identity(before) != expected_identity or not os.path.samestat(before, path_before):
            fail("candidate file changed before snapshot: " + relative)
        digest = hashlib.sha256()
        read_size = 0
        with os.fdopen(descriptor, "rb", closefd=False) as source_stream:
            for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                digest.update(chunk)
                read_size += len(chunk)
        after = os.fstat(descriptor)
        path_after = source_path.stat(follow_symlinks=False)
        if (
            read_size != before.st_size
            or file_identity(after) != expected_identity
            or file_identity(path_after) != expected_identity
            or not os.path.samestat(after, path_after)
        ):
            fail("candidate file changed during snapshot initial scan: " + relative)
        initial_identities[relative] = expected_identity
        initial_digests[relative] = digest.hexdigest()
    finally:
        os.close(descriptor)
total_size = sum(identity[3] for identity in initial_identities.values())
reserve = max(64 * 1024 * 1024, total_size // 10)
if shutil.disk_usage(work).free < total_size + reserve:
    fail("insufficient free space for protected candidate snapshot")
try:
    for relative in sorted(expected):
        source_path = package / relative
        destination = snapshot / relative
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source_path, flags)
        try:
            opened = os.fstat(descriptor)
            expected_identity = initial_identities[relative]
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or file_identity(opened) != expected_identity
            ):
                fail("candidate file changed before snapshot: " + relative)
            expected_size = expected_identity[3]
            with os.fdopen(descriptor, "rb", closefd=False) as input_stream:
                with destination.open("xb") as output_stream:
                    copied = 0
                    copied_digest = hashlib.sha256()
                    while copied < expected_size:
                        chunk = input_stream.read(min(1024 * 1024, expected_size - copied))
                        if not chunk:
                            break
                        output_stream.write(chunk)
                        copied_digest.update(chunk)
                        copied += len(chunk)
                    if copied != expected_size or input_stream.read(1):
                        fail("candidate file size changed during snapshot: " + relative)
            if copied_digest.hexdigest() != initial_digests[relative]:
                fail("candidate file content changed during snapshot: " + relative)
            after = os.fstat(descriptor)
            path_after = source_path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(after.st_mode)
                or after.st_nlink != 1
                or file_identity(after) != expected_identity
                or file_identity(path_after) != expected_identity
                or not os.path.samestat(after, path_after)
            ):
                fail("candidate file changed during snapshot: " + relative)
        finally:
            os.close(descriptor)
        destination.chmod(0o600)
except OSError as error:
    fail("cannot create complete candidate snapshot: {}".format(error))
package = snapshot

sums_path = package / "SHA256SUMS"
signature = package / "SHA256SUMS.sig"
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
    sums_bytes = sums_path.read_bytes()
except OSError as error:
    fail("cannot read SHA256SUMS: {}".format(error))
result = subprocess.run(
    [str(ssh_keygen_input), "-Y", "verify", "-f", str(allowed), "-I", "ruisheng-release",
     "-n", "ruisheng-candidate-v1", "-s", str(signature)],
    input=sums_bytes, capture_output=True, timeout=30, check=False,
)
if result.returncode != 0:
    fail("OpenSSH signature verification failed")
sums = {}
try:
    sums_text = sums_bytes.decode("utf-8")
except UnicodeDecodeError:
    fail("SHA256SUMS is not valid UTF-8")
if not sums_text.endswith("\n") or "\r" in sums_text:
    fail("SHA256SUMS must use canonical LF line endings")
for number, line in enumerate(sums_text.removesuffix("\n").split("\n"), 1):
    match = re.fullmatch(r"([0-9a-f]{64})  ([^\\\x00]+)", line)
    if match is None:
        fail("invalid SHA256SUMS entry at line {}".format(number))
    digest, relative = match.groups()
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() != relative or any(part in ("", ".", "..") for part in path.parts):
        fail("unsafe SHA256SUMS path: " + relative)
    if relative in sums:
        fail("duplicate SHA256SUMS path: " + relative)
    sums[relative] = digest
expected_sums = expected - {"SHA256SUMS", "SHA256SUMS.sig"}
if set(sums) != expected_sums:
    fail("SHA256SUMS allowlist mismatch")
manifest_bytes = None
for relative, digest in sums.items():
    path = package / relative
    hasher = hashlib.sha256()
    cached = bytearray() if relative == "MANIFEST.json" else None
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
                if cached is not None:
                    if len(cached) + len(chunk) > MAX_RELEASE_JSON_BYTES:
                        fail("MANIFEST.json exceeds the 4 MiB JSON byte limit")
                    cached.extend(chunk)
    except OSError as error:
        fail("cannot hash candidate file {}: {}".format(relative, error))
    if hasher.hexdigest() != digest:
        fail("candidate hash mismatch: " + relative)
    if relative == "MANIFEST.json" and cached is not None:
        manifest_bytes = bytes(cached)
if manifest_bytes is None:
    fail("MANIFEST.json is missing from authenticated hashes")

def read_exact_gzip(stream, size, label):
    value = bytearray()
    while len(value) < size:
        chunk = stream.read(size - len(value))
        if not chunk:
            fail("qualification toolchain archive is truncated while reading " + label)
        value.extend(chunk)
    return bytes(value)

def consume_exact_gzip(stream, size, label, require_zero=False):
    remaining = size
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            fail("qualification toolchain archive is truncated while reading " + label)
        if require_zero and any(chunk):
            fail("qualification toolchain archive contains non-zero USTAR padding")
        remaining -= len(chunk)

def parse_ustar_octal(value, label):
    encoded = value.rstrip(b"\0 ").lstrip(b" ")
    if not encoded or any(byte < ord("0") or byte > ord("7") for byte in encoded):
        fail("qualification toolchain archive has an invalid USTAR " + label)
    return int(encoded, 8)

def validate_single_qualification_gzip_member(raw_archive):
    initial_position = raw_archive.tell()
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    expanded_bytes = 0
    try:
        while not decompressor.eof:
            compressed = raw_archive.read(64 * 1024)
            if not compressed:
                fail("qualification toolchain gzip member is truncated")
            pending = compressed
            while pending and not decompressor.eof:
                maximum_output = min(
                    64 * 1024, MAX_QUALIFICATION_TAR_BYTES - expanded_bytes + 1
                )
                expanded = decompressor.decompress(pending, maximum_output)
                expanded_bytes += len(expanded)
                if expanded_bytes > MAX_QUALIFICATION_TAR_BYTES:
                    fail("qualification toolchain expanded archive exceeds its byte budget")
                next_pending = decompressor.unconsumed_tail
                if next_pending == pending and not expanded:
                    fail("qualification toolchain gzip member made no progress")
                pending = next_pending
        if decompressor.unused_data or raw_archive.read(1):
            fail("qualification toolchain archive must contain exactly one gzip member")
    finally:
        raw_archive.seek(initial_position)

def preflight_qualification_ustar(archive_path):
    sizes = {}
    expanded_bytes = 0
    try:
        with archive_path.open("rb") as raw_archive:
            initial_position = raw_archive.tell()
            raw_archive.seek(0, os.SEEK_END)
            archive_size = raw_archive.tell()
            raw_archive.seek(initial_position)
            if initial_position != 0 or archive_size > MAX_QUALIFICATION_GZIP_BYTES:
                fail("qualification toolchain gzip archive exceeds its byte budget")
            gzip_header = raw_archive.read(10)
            raw_archive.seek(initial_position)
            if gzip_header != b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff":
                fail("qualification toolchain gzip header is not canonical")
            validate_single_qualification_gzip_member(raw_archive)
        with gzip.open(archive_path, "rb") as stream:
            for expected_name in QUALIFICATION_EXPECTED_MEMBERS:
                header = read_exact_gzip(stream, USTAR_BLOCK_BYTES, "USTAR header")
                encoded_name = expected_name.encode("ascii")
                expected_name_field = encoded_name + b"\0" * (100 - len(encoded_name))
                size = parse_ustar_octal(header[124:136], "member size")
                expected_size_field = ("{:011o}\0".format(size)).encode("ascii")
                expected_checksum = parse_ustar_octal(header[148:156], "checksum")
                actual_checksum = sum(header[:148]) + sum(b" " * 8) + sum(header[156:])
                if (
                    header[:100] != expected_name_field
                    or header[100:124] != b"0000644\0" + b"0000000\0" * 2
                    or header[124:136] != expected_size_field
                    or header[136:148] != b"00000000000\0"
                    or header[148:156]
                    != ("{:06o}\0 ".format(expected_checksum)).encode("ascii")
                    or header[156:157] != b"0"
                    or any(header[157:257])
                    or header[257:263] != b"ustar\0"
                    or header[263:265] != b"00"
                    or any(header[265:512])
                ):
                    fail(
                        "qualification toolchain archive must match the fixed deterministic "
                        "regular USTAR member contract"
                    )
                if expected_checksum != actual_checksum:
                    fail("qualification toolchain archive USTAR checksum mismatch")
                member_limit = (
                    MAX_RELEASE_JSON_BYTES
                    if expected_name == QUALIFICATION_TOOLCHAIN_MANIFEST
                    else MAX_QUALIFICATION_MEMBER_BYTES
                )
                if size > member_limit:
                    fail(
                        "qualification toolchain member is not an allowed regular file: "
                        + expected_name
                    )
                sizes[expected_name] = size
                consume_exact_gzip(stream, size, "member data")
                padding = (-size) % USTAR_BLOCK_BYTES
                consume_exact_gzip(stream, padding, "member padding", require_zero=True)
                expanded_bytes += USTAR_BLOCK_BYTES + size + padding

            terminator_bytes = 2 * USTAR_BLOCK_BYTES
            trailing_bytes = terminator_bytes + (
                -(expanded_bytes + terminator_bytes)
            ) % USTAR_RECORD_BYTES
            trailing_blocks = trailing_bytes // USTAR_BLOCK_BYTES
            if not (
                MIN_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS
                <= trailing_blocks
                <= MAX_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS
            ):
                fail("qualification toolchain USTAR trailer exceeds its zero-block budget")
            consume_exact_gzip(
                stream, trailing_bytes, "USTAR terminator and record padding", require_zero=True
            )
            if stream.read(1):
                fail("qualification toolchain archive contains trailing data")
    except (
        OSError,
        EOFError,
        gzip.BadGzipFile,
        zlib.error,
        ValueError,
        MemoryError,
    ) as error:
        fail("invalid qualification toolchain archive: {}".format(error))
    return sizes

def scan_qualification_archive(archive_path):
    observed = {}
    contents = {}
    internal_name = QUALIFICATION_TOOLCHAIN_MANIFEST
    preflight_sizes = preflight_qualification_ustar(archive_path)
    try:
        with tarfile.open(str(archive_path), "r:gz") as archive:
            count = 0
            for index, member in enumerate(archive):
                if (
                    index >= len(QUALIFICATION_EXPECTED_MEMBERS)
                    or member.name != QUALIFICATION_EXPECTED_MEMBERS[index]
                ):
                    fail("qualification toolchain archive member allowlist mismatch")
                count += 1
                member_limit = (
                    MAX_RELEASE_JSON_BYTES
                    if member.name == internal_name
                    else MAX_QUALIFICATION_MEMBER_BYTES
                )
                if (
                    not member.isfile()
                    or member.size != preflight_sizes[member.name]
                    or member.size < 0
                    or member.size > member_limit
                ):
                    fail(
                        "qualification toolchain member is not an allowed regular file: "
                        + member.name
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    fail("qualification toolchain member cannot be read: " + member.name)
                digest = hashlib.sha256()
                if member.name == internal_name:
                    contents[member.name] = stream.read(member_limit + 1)
                    if len(contents[member.name]) != member.size or stream.read(1):
                        fail("qualification toolchain member size mismatch: " + member.name)
                    digest.update(contents[member.name])
                else:
                    remaining = member.size
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            fail("qualification toolchain member size mismatch: " + member.name)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        fail("qualification toolchain member size mismatch: " + member.name)
                observed[member.name] = digest.hexdigest()
            if count != len(QUALIFICATION_EXPECTED_MEMBERS):
                fail("qualification toolchain archive member allowlist mismatch")
    except (OSError, tarfile.TarError, EOFError, ValueError) as error:
        fail("invalid qualification toolchain archive: {}".format(error))
    internal_bytes = contents.get(internal_name)
    if internal_bytes is None:
        fail("qualification toolchain manifest is missing")
    return observed, internal_bytes

def validate_qualification_toolchain(manifest):
    base_keys = {
        "schema_version", "candidate_id", "source_commit", "generated_at", "target_os",
        "target_architecture", "alembic_head", "logical_identity", "tools",
        "authenticity", "images",
    }
    expected_keys = base_keys if expected_schema_version == 2 else base_keys | {
        "qualification_toolchain"
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        fail("MANIFEST.json keys mismatch for v{}".format(expected_schema_version))
    if expected_schema_version == 2:
        return None
    descriptor = manifest["qualification_toolchain"]
    descriptor_keys = {
        "path", "sha256", "format", "semantic_validator", "schema", "validator",
        "producer", "receipt_producer", "toolchain_manifest",
    }
    archive_name = "qualification-toolchain.tar.gz"
    semantic_validator = "ruisheng.device-point-profile-validator/v5"
    if not isinstance(descriptor, dict) or set(descriptor) != descriptor_keys:
        fail("qualification toolchain descriptor keys mismatch")
    if (
        descriptor.get("path") != archive_name
        or descriptor.get("format") != "tar+gzip"
        or descriptor.get("semantic_validator") != semantic_validator
        or not isinstance(descriptor.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", descriptor["sha256"]) is None
        or sums.get(archive_name) != descriptor["sha256"]
    ):
        fail("qualification toolchain descriptor contract is invalid")
    identity_paths = {
        "schema": QUALIFICATION_MEMBER_NAMES[2],
        "validator": QUALIFICATION_MEMBER_NAMES[0],
        "producer": QUALIFICATION_MEMBER_NAMES[3],
        "receipt_producer": QUALIFICATION_MEMBER_NAMES[4],
        "toolchain_manifest": QUALIFICATION_TOOLCHAIN_MANIFEST,
    }
    for name, path in identity_paths.items():
        identity = descriptor.get(name)
        if (
            not isinstance(identity, dict)
            or set(identity) != {"path", "sha256"}
            or identity.get("path") != path
            or not isinstance(identity.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]) is None
        ):
            fail("qualification toolchain identity is invalid for " + path)
    resolved, internal_bytes = scan_qualification_archive(package / archive_name)
    if resolved[QUALIFICATION_TOOLCHAIN_MANIFEST] != descriptor["toolchain_manifest"]["sha256"]:
        fail("qualification toolchain manifest SHA-256 mismatch")
    try:
        internal = strict_json_loads(internal_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        fail("qualification toolchain manifest is invalid JSON")
    if not isinstance(internal, dict) or set(internal) != {
        "artifact_type", "members", "schema_version", "semantic_validator"
    }:
        fail("qualification toolchain manifest keys mismatch")
    if (
        internal.get("artifact_type") != "ruisheng.qualification-toolchain"
        or type(internal.get("schema_version")) is not int
        or internal.get("schema_version") != 1
        or internal.get("semantic_validator") != semantic_validator
    ):
        fail("qualification toolchain manifest contract is invalid")
    identities = internal.get("members")
    if not isinstance(identities, list) or len(identities) != len(QUALIFICATION_MEMBER_NAMES):
        fail("qualification toolchain manifest members are invalid")
    for path, identity in zip(QUALIFICATION_MEMBER_NAMES, identities):
        if not isinstance(identity, dict) or set(identity) != {"path", "sha256"} or identity.get("path") != path:
            fail("qualification toolchain member identity is invalid")
        if identity.get("sha256") != resolved[path]:
            fail("qualification toolchain member SHA-256 mismatch: " + path)
    for name, path in identity_paths.items():
        if descriptor[name]["sha256"] != resolved[path]:
            fail("qualification toolchain descriptor identity mismatch: " + path)
    return resolved

def canonical_runtime_path(value, label):
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        fail(label + " is not a canonical relative path")
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        fail(label + " is not valid UTF-8")
    path = pathlib.PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in ("", ".", "..") for part in path.parts)
        or encoded_length > MAX_QUALIFICATION_RUNTIME_PATH_BYTES
        or len(path.parts) > 128
    ):
        fail(label + " is not a canonical relative path")
    for part in path.parts:
        try:
            if len(part.encode("utf-8")) > 255:
                fail(label + " contains an oversized path segment")
        except UnicodeEncodeError:
            fail(label + " is not valid UTF-8")
    return value

def root_protected_metadata(path, label):
    try:
        metadata = path.lstat()
    except OSError as error:
        fail("{} is unavailable: {}: {}".format(label, path, error))
    if stat.S_ISLNK(metadata.st_mode):
        fail("{} is linked: {}".format(label, path))
    if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        fail("{} is not root protected: {}".format(label, path))
    return metadata

def read_stable_root_file(path, label, maximum_bytes, capture=False):
    path_before = root_protected_metadata(path, label)
    if (
        not stat.S_ISREG(path_before.st_mode)
        or path_before.st_nlink != 1
        or path_before.st_size > maximum_bytes
    ):
        fail("{} is not an allowed regular file: {}".format(label, path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail("cannot open {}: {}: {}".format(label, path, error))
    try:
        before = os.fstat(descriptor)
        if (
            file_identity(before) != file_identity(path_before)
            or not os.path.samestat(before, path_before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != 0
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_size > maximum_bytes
        ):
            fail("{} changed before it could be read: {}".format(label, path))
        digest = hashlib.sha256()
        contents = bytearray() if capture else None
        read_size = 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                read_size += len(chunk)
                if read_size > maximum_bytes:
                    fail("{} exceeds its byte limit: {}".format(label, path))
                digest.update(chunk)
                if contents is not None:
                    contents.extend(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = path.lstat()
        except OSError:
            fail("{} changed while being read: {}".format(label, path))
        if (
            read_size != before.st_size
            or file_identity(after) != file_identity(before)
            or file_identity(path_after) != file_identity(before)
            or not os.path.samestat(after, path_after)
        ):
            fail("{} changed while being read: {}".format(label, path))
        return digest.hexdigest(), file_identity(after), bytes(contents) if contents is not None else None
    finally:
        os.close(descriptor)

def expected_directories(relative_files):
    directories = set()
    for relative in relative_files:
        parent = pathlib.PurePosixPath(relative).parent
        while parent != pathlib.PurePosixPath("."):
            directory = parent.as_posix()
            if directory not in directories:
                if len(directories) >= MAX_QUALIFICATION_RUNTIME_DIRECTORIES:
                    fail("qualification runtime contains too many directories")
                directories.add(directory)
            parent = parent.parent
    return directories

def validate_runtime_layout(root, expected_files, expected_directory_names):
    actual_files = set()
    actual_directories = set()
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError as error:
            fail("cannot enumerate qualification runtime: {}: {}".format(current, error))
        with entries:
            for entry in entries:
                path = pathlib.Path(entry.path)
                relative = path.relative_to(root).as_posix()
                metadata = root_protected_metadata(
                    path, "qualification runtime member " + relative
                )
                if stat.S_ISDIR(metadata.st_mode):
                    if relative not in expected_directory_names:
                        fail("qualification runtime file allowlist mismatch")
                    actual_directories.add(relative)
                    if len(actual_directories) > MAX_QUALIFICATION_RUNTIME_DIRECTORIES:
                        fail("qualification runtime contains too many directories")
                    pending.append(path)
                elif stat.S_ISREG(metadata.st_mode):
                    if relative not in expected_files:
                        fail("qualification runtime file allowlist mismatch")
                    actual_files.add(relative)
                    if len(actual_files) > MAX_QUALIFICATION_RUNTIME_FILES:
                        fail("qualification runtime contains too many files")
                else:
                    fail(
                        "qualification runtime member is not a file or directory: "
                        + relative
                    )
    if actual_files != expected_files or actual_directories != expected_directory_names:
        fail("qualification runtime file allowlist mismatch")

def validate_posix_qualification_runtime(authenticated_uv_lock_sha256):
    if re.fullmatch(r"[0-9a-f]{64}", authenticated_uv_lock_sha256) is None:
        fail("authenticated qualification uv.lock SHA-256 is invalid")
    root = QUALIFICATION_RUNTIME_ROOT.absolute()
    for current in (root, *root.parents):
        metadata = root_protected_metadata(current, "qualification runtime path")
        if not stat.S_ISDIR(metadata.st_mode):
            fail("qualification runtime path is not a directory: " + str(current))

    manifest_path = root / QUALIFICATION_RUNTIME_MANIFEST
    manifest_digest, manifest_identity, manifest_bytes = read_stable_root_file(
        manifest_path,
        "qualification runtime manifest",
        MAX_RELEASE_JSON_BYTES,
        capture=True,
    )
    try:
        runtime_manifest = strict_json_loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        fail("qualification runtime manifest is invalid JSON")
    if not isinstance(runtime_manifest, dict) or set(runtime_manifest) != {
        "artifact_type",
        "schema_version",
        "python_version",
        "uv_lock_sha256",
        "dependency_root",
        "files",
    }:
        fail("qualification runtime manifest keys mismatch")
    if (
        runtime_manifest.get("artifact_type") != "ruisheng.qualification-runtime"
        or type(runtime_manifest.get("schema_version")) is not int
        or runtime_manifest.get("schema_version") != 1
        or runtime_manifest.get("python_version") != "3.11"
        or runtime_manifest.get("uv_lock_sha256") != authenticated_uv_lock_sha256
        or runtime_manifest.get("dependency_root") != QUALIFICATION_RUNTIME_DEPENDENCIES
    ):
        fail("qualification runtime manifest contract is invalid")
    file_values = runtime_manifest.get("files")
    if (
        not isinstance(file_values, list)
        or not file_values
        or len(file_values) >= MAX_QUALIFICATION_RUNTIME_FILES
    ):
        fail("qualification runtime manifest files are invalid")

    expected_files = {QUALIFICATION_RUNTIME_MANIFEST}
    expected_identities = []
    previous_path = None
    folded_paths = {QUALIFICATION_RUNTIME_MANIFEST.casefold()}
    for identity in file_values:
        if (
            not isinstance(identity, dict)
            or set(identity) != {"path", "sha256"}
            or not isinstance(identity.get("path"), str)
            or not isinstance(identity.get("sha256"), str)
        ):
            fail("qualification runtime file identity is invalid")
        relative = canonical_runtime_path(
            identity["path"], "qualification runtime file path"
        )
        digest = identity["sha256"]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            fail("qualification runtime file identity is invalid")
        if previous_path is not None and previous_path >= relative:
            fail("qualification runtime files are not in strict ordinal path order")
        folded = relative.casefold()
        basename = pathlib.PurePosixPath(folded).name
        if (
            relative == QUALIFICATION_RUNTIME_MANIFEST
            or folded.endswith(".pth")
            or basename in {"pyvenv.cfg", "sitecustomize.py", "usercustomize.py"}
        ):
            fail("qualification runtime contains a forbidden file: " + relative)
        if relative in expected_files or folded in folded_paths:
            fail("qualification runtime contains a case-insensitive path collision")
        expected_files.add(relative)
        folded_paths.add(folded)
        expected_identities.append((relative, digest))
        previous_path = relative

    required_files = {
        QUALIFICATION_RUNTIME_PYTHON,
        "lib/python3.11/encodings/__init__.py",
    }
    if not required_files.issubset(expected_files) or not any(
        relative.startswith(QUALIFICATION_RUNTIME_DEPENDENCIES + "/")
        for relative, _digest in expected_identities
    ):
        fail("qualification runtime is not a self-contained Python 3.11 dependency closure")
    expected_directory_names = expected_directories(expected_files)
    if expected_files & expected_directory_names:
        fail("qualification runtime contains a file/directory path collision")
    for directory in expected_directory_names:
        folded = directory.casefold()
        if folded in folded_paths:
            fail("qualification runtime contains a case-insensitive path collision")
        folded_paths.add(folded)
    validate_runtime_layout(root, expected_files, expected_directory_names)

    observed = []
    total_bytes = manifest_identity[3]
    python_mode = None
    for relative, expected_digest in expected_identities:
        actual_digest, identity, _contents = read_stable_root_file(
            root / relative,
            "qualification runtime file " + relative,
            MAX_QUALIFICATION_RUNTIME_FILE_BYTES,
        )
        total_bytes += identity[3]
        if total_bytes > MAX_QUALIFICATION_RUNTIME_TOTAL_BYTES:
            fail("qualification runtime exceeds its aggregate byte limit")
        if actual_digest != expected_digest:
            fail("qualification runtime file SHA-256 mismatch: " + relative)
        if relative == QUALIFICATION_RUNTIME_PYTHON:
            python_mode = (root / relative).lstat().st_mode
        observed.append((relative, actual_digest, identity))
    if python_mode is None or python_mode & 0o111 == 0:
        fail("qualification runtime Python is not executable")
    dependency_root = root / QUALIFICATION_RUNTIME_DEPENDENCIES
    dependency_metadata = root_protected_metadata(
        dependency_root, "qualification runtime dependency_root"
    )
    if not stat.S_ISDIR(dependency_metadata.st_mode):
        fail("qualification runtime dependency_root is missing")
    return (
        str(root),
        str(root / QUALIFICATION_RUNTIME_PYTHON),
        str(dependency_root),
        authenticated_uv_lock_sha256,
        ((QUALIFICATION_RUNTIME_MANIFEST, manifest_digest, manifest_identity), *observed),
    )

def validate_extracted_qualification(root, identities):
    protected_with_ancestors(root, "extracted qualification root")
    expected_files = set(QUALIFICATION_EXPECTED_MEMBERS)
    expected_directory_names = expected_directories(expected_files)
    actual_files = set()
    actual_directories = set()
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError as error:
            fail("cannot enumerate extracted qualification toolchain: {}".format(error))
        with entries:
            for entry in entries:
                path = pathlib.Path(entry.path)
                relative = path.relative_to(root).as_posix()
                metadata = root_protected_metadata(
                    path, "extracted qualification member " + relative
                )
                if stat.S_ISDIR(metadata.st_mode):
                    if relative not in expected_directory_names:
                        fail("extracted qualification toolchain allowlist mismatch")
                    actual_directories.add(relative)
                    pending.append(path)
                elif stat.S_ISREG(metadata.st_mode):
                    if relative not in expected_files:
                        fail("extracted qualification toolchain allowlist mismatch")
                    actual_files.add(relative)
                else:
                    fail("extracted qualification member is not a file or directory: " + relative)
    if actual_files != expected_files or actual_directories != expected_directory_names:
        fail("extracted qualification toolchain allowlist mismatch")
    observed = []
    for relative in QUALIFICATION_EXPECTED_MEMBERS:
        maximum = (
            MAX_RELEASE_JSON_BYTES
            if relative == QUALIFICATION_TOOLCHAIN_MANIFEST
            else MAX_QUALIFICATION_MEMBER_BYTES
        )
        digest, identity, _contents = read_stable_root_file(
            root / relative,
            "extracted qualification member " + relative,
            maximum,
        )
        if digest != identities[relative]:
            fail("extracted qualification member SHA-256 mismatch: " + relative)
        observed.append((relative, digest, identity))
    return tuple(observed)

def extract_authenticated_qualification_toolchain(identities):
    extraction = run_root / "qualification"
    extraction.mkdir(mode=0o700)
    directory_names = sorted(
        expected_directories(QUALIFICATION_EXPECTED_MEMBERS),
        key=lambda value: (len(pathlib.PurePosixPath(value).parts), value),
    )
    for relative in directory_names:
        (extraction / relative).mkdir(mode=0o700)
    archive_path = package / "qualification-toolchain.tar.gz"
    preflight_sizes = preflight_qualification_ustar(archive_path)
    try:
        with tarfile.open(str(archive_path), "r:gz") as archive:
            count = 0
            for index, member in enumerate(archive):
                if (
                    index >= len(QUALIFICATION_EXPECTED_MEMBERS)
                    or member.name != QUALIFICATION_EXPECTED_MEMBERS[index]
                ):
                    fail("qualification toolchain archive member allowlist mismatch")
                count += 1
                maximum = (
                    MAX_RELEASE_JSON_BYTES
                    if member.name == QUALIFICATION_TOOLCHAIN_MANIFEST
                    else MAX_QUALIFICATION_MEMBER_BYTES
                )
                if (
                    not member.isfile()
                    or member.size != preflight_sizes[member.name]
                    or member.size < 0
                    or member.size > maximum
                ):
                    fail(
                        "qualification toolchain member is not an allowed regular file: "
                        + member.name
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    fail("qualification toolchain member cannot be read: " + member.name)
                destination = extraction / member.name
                digest = hashlib.sha256()
                remaining = member.size
                with destination.open("xb") as output:
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            fail("qualification toolchain member size mismatch: " + member.name)
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        fail("qualification toolchain member size mismatch: " + member.name)
                destination.chmod(0o600)
                if digest.hexdigest() != identities[member.name]:
                    fail("extracted qualification member SHA-256 mismatch: " + member.name)
            if count != len(QUALIFICATION_EXPECTED_MEMBERS):
                fail("qualification toolchain archive member allowlist mismatch")
    except (OSError, tarfile.TarError, EOFError, ValueError) as error:
        fail("cannot extract authenticated qualification toolchain: {}".format(error))
    return extraction

def absolute_qualification_argument(value, label):
    if not value or "\x00" in value:
        fail(label + " is invalid")
    try:
        return str(pathlib.Path(value).absolute())
    except (OSError, ValueError) as error:
        fail("{} is invalid: {}".format(label, error))

def qualification_invocation(manifest, extraction, freshness=None):
    if qualification_mode == "ValidatorSchema":
        return (
            extraction / "tools/validate_device_point_profile.py",
            ["schema"],
            {0, 2, 3},
        )
    if qualification_mode == "ValidatorProfile":
        if freshness is None:
            fail("ValidatorProfile freshness context is missing")
        return (
            extraction / "tools/trust_root_freshness.py",
            ["qualify", *_freshness_qualification_arguments(manifest, freshness),
                "--evidence-root",
                absolute_qualification_argument(
                    qualification_root_path, "qualification root path"
                ),
            ],
            {0, 2, 3},
        )
    if qualification_mode == "ValidatorLegacy":
        return (
            extraction / "tools/validate_device_point_profile.py",
            [
                "validate-legacy",
                absolute_qualification_argument(
                    qualification_evidence_path, "qualification evidence path"
                ),
                "--root",
                absolute_qualification_argument(
                    qualification_root_path, "qualification root path"
                ),
            ],
            {0, 2, 3},
        )
    if qualification_mode == "Receipt":
        descriptor = manifest["qualification_toolchain"]
        return (
            extraction / "tools/release_verification_receipt.py",
            [
                str(package),
                "--output-directory",
                absolute_qualification_argument(
                    qualification_output_directory, "qualification output directory"
                ),
                "--signing-identity",
                absolute_qualification_argument(
                    qualification_signing_identity, "qualification signing identity"
                ),
                "--verifier-id",
                qualification_verifier_id,
                "--verifier-key-id",
                qualification_verifier_key_id,
                "--verifier-tool-sha256",
                "sha256:" + descriptor["receipt_producer"]["sha256"],
            ],
            {0},
        )
    fail("unsupported qualification mode")

def _freshness_qualification_arguments(manifest, freshness):
    return [
        str(freshness["profile_snapshot"]),
        "--trust-policy", str(freshness["policy_snapshot"]),
        "--trust-root-snapshot", str(freshness["trust_root_snapshot"]),
        "--provider-config-snapshot", str(freshness["config_snapshot"]),
        "--attestation", str(freshness["attestation_snapshot"]),
        "--challenge", freshness["challenge"],
        "--requested-at", freshness["requested_at"],
        "--candidate-logical-identity", manifest["logical_identity"],
        "--expected-trust-root-snapshot-sha256",
        freshness["trust_root_snapshot_sha256"],
        "--expected-provider-config-snapshot-sha256",
        freshness["config_snapshot_sha256"],
        "--expected-attestation-sha256", freshness["attestation_sha256"],
    ]

def freshness_preflight_invocation(manifest, extraction, freshness):
    return (
        extraction / "tools/trust_root_freshness.py",
        ["preflight", *_freshness_qualification_arguments(manifest, freshness)],
    )

def _read_locked_file(descriptor, maximum_bytes, label):
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError(label + " is not a unique regular file")
    if metadata.st_size < 0 or metadata.st_size > maximum_bytes:
        raise ValueError(label + " exceeds its byte limit")
    os.lseek(descriptor, 0, os.SEEK_SET)
    contents = bytearray()
    while len(contents) <= maximum_bytes:
        chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - len(contents)))
        if not chunk:
            break
        contents.extend(chunk)
    if len(contents) != metadata.st_size or len(contents) > maximum_bytes:
        raise ValueError(label + " changed or exceeds its byte limit")
    os.lseek(descriptor, 0, os.SEEK_SET)
    return bytes(contents), metadata

def _open_freshness_regular(path, label, maximum_bytes):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size < 0
            or metadata.st_size > maximum_bytes
        ):
            raise ValueError(label + " is not a unique bounded regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise

def _lock_snapshot_source(
    source, destination, label, locks, maximum_bytes, executable_snapshot=False
):
    source_descriptor = _open_freshness_regular(source, label, maximum_bytes)
    try:
        contents, source_metadata = _read_locked_file(
            source_descriptor, maximum_bytes, label
        )
        source_path_metadata = source.stat(follow_symlinks=False)
        if (
            file_identity(source_path_metadata) != file_identity(source_metadata)
            or not os.path.samestat(source_path_metadata, source_metadata)
        ):
            raise ValueError(label + " changed before snapshot")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            written = 0
            while written < len(contents):
                written += os.write(destination_descriptor, contents[written:])
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        if executable_snapshot:
            destination.chmod(0o500)
        snapshot_descriptor = _open_freshness_regular(
            destination, label + " snapshot", maximum_bytes
        )
        snapshot_contents, snapshot_metadata = _read_locked_file(
            snapshot_descriptor, maximum_bytes, label + " snapshot"
        )
        if snapshot_contents != contents:
            os.close(snapshot_descriptor)
            raise ValueError(label + " snapshot content mismatch")
        locks.extend((
            {
                "descriptor": source_descriptor,
                "path": source,
                "identity": file_identity(source_metadata),
                "sha256": hashlib.sha256(contents).hexdigest(),
                "maximum_bytes": maximum_bytes,
                "label": label,
            },
            {
                "descriptor": snapshot_descriptor,
                "path": destination,
                "identity": file_identity(snapshot_metadata),
                "sha256": hashlib.sha256(snapshot_contents).hexdigest(),
                "maximum_bytes": maximum_bytes,
                "label": label + " snapshot",
            },
        ))
        source_descriptor = None
        return contents
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)

def _lock_existing_output(path, label, locks, maximum_bytes):
    descriptor = _open_freshness_regular(path, label, maximum_bytes)
    try:
        contents, metadata = _read_locked_file(descriptor, maximum_bytes, label)
        path_metadata = path.stat(follow_symlinks=False)
        if (
            file_identity(path_metadata) != file_identity(metadata)
            or not os.path.samestat(path_metadata, metadata)
        ):
            raise ValueError(label + " path identity mismatch")
        locks.append({
            "descriptor": descriptor,
            "path": path,
            "identity": file_identity(metadata),
            "sha256": hashlib.sha256(contents).hexdigest(),
            "maximum_bytes": maximum_bytes,
            "label": label,
        })
        descriptor = None
        return contents
    finally:
        if descriptor is not None:
            os.close(descriptor)

def _validate_freshness_locks(locks):
    for lock in locks:
        contents, metadata = _read_locked_file(
            lock["descriptor"], lock["maximum_bytes"], lock["label"]
        )
        path_metadata = lock["path"].stat(follow_symlinks=False)
        if (
            file_identity(metadata) != lock["identity"]
            or file_identity(path_metadata) != lock["identity"]
            or not os.path.samestat(metadata, path_metadata)
            or hashlib.sha256(contents).hexdigest() != lock["sha256"]
        ):
            fail(lock["label"] + " identity or content changed during freshness validation")

def _close_freshness_locks(locks):
    for lock in reversed(locks):
        try:
            os.close(lock["descriptor"])
        except OSError:
            pass
    locks.clear()

def _linux_process_starttime(contents):
    _prefix, separator, suffix = contents.rpartition(")")
    if not separator:
        return None
    fields = suffix.split()
    try:
        return int(fields[19]) if len(fields) > 19 else None
    except ValueError:
        return None

def _linux_process_identity(pid):
    try:
        contents = (pathlib.Path("/proc") / str(pid) / "stat").read_text(
            encoding="ascii"
        )
        return _linux_process_starttime(contents)
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None

def _linux_direct_children(pid):
    children = set()
    task_root = pathlib.Path("/proc") / str(pid) / "task"
    try:
        tasks = tuple(task_root.iterdir())
    except OSError:
        return children
    for task in tasks:
        try:
            values = (task / "children").read_text(encoding="ascii").split()
            children.update(int(value) for value in values)
        except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
            continue
    return children

def _capture_linux_descendants(roots):
    pending = list(roots)
    captured = {}
    while pending:
        parent = pending.pop()
        for pid in _linux_direct_children(parent):
            if pid in captured:
                continue
            identity = _linux_process_identity(pid)
            if identity is not None:
                captured[pid] = identity
                pending.append(pid)
    return captured

def _terminate_and_reap_linux_descendants(
    root_pid, root_identity, initial_descendants, baseline_children, timeout_seconds=30
):
    deadline = time.monotonic() + timeout_seconds
    known = {root_pid: root_identity, **initial_descendants}
    while True:
        known.update(_capture_linux_descendants(tuple(known)))
        for pid in _linux_direct_children(os.getpid()) - baseline_children:
            identity = _linux_process_identity(pid)
            if identity is not None:
                known[pid] = identity
        alive = {
            pid: identity
            for pid, identity in known.items()
            if _linux_process_identity(pid) == identity
        }
        if not alive:
            break
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                reaped, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if reaped <= 0:
                break
        if time.monotonic() >= deadline:
            raise TimeoutError("freshness provider descendants could not be reaped")
        time.sleep(0.01)

def _enable_linux_child_subreaper():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot enable freshness provider child subreaper")

def _run_freshness_provider(provider_snapshot, arguments):
    outcome = None
    timed_out = False
    root_identity = None
    baseline_children = set()
    try:
        _enable_linux_child_subreaper()
        baseline_children = _linux_direct_children(os.getpid())
        outcome = subprocess.Popen(
            [str(provider_snapshot), *arguments],
            cwd=run_root,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/root"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        root_identity = _linux_process_identity(outcome.pid)
        if root_identity is None:
            return 2
        try:
            outcome.wait(timeout=FRESHNESS_PROVIDER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
    except (OSError, subprocess.SubprocessError):
        return 2
    finally:
        if outcome is not None:
            initial_descendants = _capture_linux_descendants((outcome.pid,))
            try:
                os.killpg(outcome.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    outcome.kill()
                except OSError:
                    pass
            try:
                _terminate_and_reap_linux_descendants(
                    outcome.pid,
                    root_identity,
                    initial_descendants,
                    baseline_children,
                    timeout_seconds=30,
                )
                outcome.wait(timeout=30)
            except (subprocess.TimeoutExpired, TimeoutError):
                return 2
    if timed_out:
        return 2
    if outcome.returncode in (0, 2, 3):
        return outcome.returncode
    return 2

def prepare_freshness_context(manifest):
    locks = []
    context = {"locks": locks}
    if not FRESHNESS_PROVIDER.exists() or FRESHNESS_PROVIDER.is_symlink():
        return 2, context
    if not FRESHNESS_PROVIDER_CONFIG.exists() or FRESHNESS_PROVIDER_CONFIG.is_symlink():
        return 2, context
    if not FRESHNESS_TRUST_ROOT.exists() or FRESHNESS_TRUST_ROOT.is_symlink():
        return 3, context
    try:
        freshness_root = run_root / "freshness"
        freshness_root.mkdir(mode=0o700)
        trust_root_snapshot = freshness_root / "trust-root.json"
        config_snapshot = freshness_root / "provider-config.json"
        policy_snapshot = freshness_root / "trust-policy.json"
        profile_snapshot = freshness_root / "profile.json"
        attestation_snapshot = freshness_root / "attestation.json"
        provider_snapshot = freshness_root / "provider"
        verifier_snapshot = freshness_root / "verify-publisher.sh"
        try:
            protected_with_ancestors(FRESHNESS_PROVIDER, "fixed freshness provider")
            _lock_snapshot_source(
                FRESHNESS_PROVIDER,
                provider_snapshot,
                "fixed freshness provider",
                locks,
                512 * 1024 * 1024,
                executable_snapshot=True,
            )
        except (OSError, ValueError, SystemExit):
            return 2, context
        protected_with_ancestors(FRESHNESS_PROVIDER_CONFIG, "fixed freshness provider config")
        protected_with_ancestors(FRESHNESS_TRUST_ROOT, "fixed point-profile trust root")
        verifier_contents = _lock_snapshot_source(
            verifier_input,
            verifier_snapshot,
            "protected publisher verifier",
            locks,
            64 * 1024 * 1024,
        )
        trust_root_contents = _lock_snapshot_source(
            FRESHNESS_TRUST_ROOT,
            trust_root_snapshot,
            "fixed point-profile trust root",
            locks,
            MAX_RELEASE_JSON_BYTES,
        )
        config_contents = _lock_snapshot_source(
            FRESHNESS_PROVIDER_CONFIG,
            config_snapshot,
            "fixed freshness provider config",
            locks,
            MAX_RELEASE_JSON_BYTES,
        )
        profile_contents = _lock_snapshot_source(
            pathlib.Path(absolute_qualification_argument(
                qualification_profile_path, "qualification profile path"
            )),
            profile_snapshot,
            "qualification profile",
            locks,
            MAX_RELEASE_JSON_BYTES,
        )
        _lock_snapshot_source(
            pathlib.Path(absolute_qualification_argument(
                qualification_trust_policy_path, "qualification trust-policy path"
            )),
            policy_snapshot,
            "qualification trust policy",
            locks,
            MAX_RELEASE_JSON_BYTES,
        )
        profile = strict_json_loads(profile_contents.decode("utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("qualification profile is not a JSON object")
        profile_id = profile.get("profile_id")
        payload_sha256 = profile.get("payload_sha256")
        if not isinstance(profile_id, str) or not isinstance(payload_sha256, str):
            raise ValueError("qualification profile binding is invalid")
        challenge = secrets.token_urlsafe(32)
        if len(challenge) != 43 or "=" in challenge:
            raise ValueError("freshness challenge generation failed")
        requested_at = datetime.now(timezone.utc).isoformat()
        verifier_tool_sha256 = "sha256:" + hashlib.sha256(verifier_contents).hexdigest()
        arguments = [
            "attest",
            "--config", str(config_snapshot),
            "--trust-root", str(trust_root_snapshot),
            "--trust-policy", str(policy_snapshot),
            "--profile", str(profile_snapshot),
            "--candidate-logical-identity", manifest["logical_identity"],
            "--verifier-id", FRESHNESS_VERIFIER_ID,
            "--verifier-tool-sha256", verifier_tool_sha256,
            "--challenge", challenge,
            "--requested-at", requested_at,
            "--output", str(attestation_snapshot),
        ]
        _validate_freshness_locks(locks)
        provider_exit_code = _run_freshness_provider(provider_snapshot, arguments)
        if provider_exit_code != 0:
            return provider_exit_code, context
        attestation_contents = _lock_existing_output(
            attestation_snapshot,
            "freshness attestation",
            locks,
            MAX_RELEASE_JSON_BYTES,
        )
        attestation = strict_json_loads(attestation_contents.decode("utf-8"))
        request = attestation.get("request") if isinstance(attestation, dict) else None
        if (
            not isinstance(request, dict)
            or request.get("challenge") != challenge
            or request.get("candidate_logical_identity") != manifest["logical_identity"]
            or request.get("profile_id") != profile_id
            or request.get("payload_sha256") != payload_sha256
            or request.get("verifier_id") != FRESHNESS_VERIFIER_ID
            or request.get("verifier_tool_sha256") != verifier_tool_sha256
        ):
            raise ValueError("freshness attestation request binding is invalid")
        context.update({
            "trust_root_snapshot": trust_root_snapshot,
            "trust_root_snapshot_sha256": (
                "sha256:" + hashlib.sha256(trust_root_contents).hexdigest()
            ),
            "config_snapshot": config_snapshot,
            "config_snapshot_sha256": (
                "sha256:" + hashlib.sha256(config_contents).hexdigest()
            ),
            "policy_snapshot": policy_snapshot,
            "profile_snapshot": profile_snapshot,
            "attestation_snapshot": attestation_snapshot,
            "attestation_sha256": (
                "sha256:" + hashlib.sha256(attestation_contents).hexdigest()
            ),
            "challenge": challenge,
            "requested_at": requested_at,
            "verifier_tool_sha256": verifier_tool_sha256,
        })
        return 0, context
    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        RecursionError,
        MemoryError,
        SystemExit,
    ):
        return 3, context

QUALIFICATION_BOOTSTRAP = r'''
import os
import sys

runtime = os.path.realpath(sys.argv.pop(1))
python = os.path.realpath(sys.argv.pop(1))
dependency_root = os.path.realpath(sys.argv.pop(1))
root = os.path.realpath(sys.argv.pop(1))
script = os.path.realpath(sys.argv.pop(1))
if sys.version_info[:2] != (3, 11):
    raise SystemExit("qualification runtime must be Python 3.11")
if not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode:
    raise SystemExit("qualification runtime isolation flags are incomplete")
if {"site", "sitecustomize", "usercustomize"} & set(sys.modules):
    raise SystemExit("qualification runtime imported site before bootstrap")

def inside(path, parent):
    return path == parent or path.startswith(parent + os.sep)

if os.path.realpath(sys.executable) != python:
    raise SystemExit("qualification executable escaped the fixed runtime")
if any(os.path.realpath(value) != runtime for value in (
    sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix
)):
    raise SystemExit("qualification Python prefix escaped the fixed runtime")
if any(not value or not inside(os.path.realpath(value), runtime) for value in sys.path):
    raise SystemExit("qualification startup search path escaped the fixed runtime")
if not inside(dependency_root, runtime) or dependency_root in {
    os.path.realpath(value) for value in sys.path
}:
    raise SystemExit("qualification dependency_root was not isolated for bootstrap")
if not inside(script, root):
    raise SystemExit("unsupported qualification entrypoint")

sys.modules["site"] = None
sys.modules["sitecustomize"] = None
sys.modules["usercustomize"] = None
sys.path.insert(0, dependency_root)
sys.path.insert(0, root)

import pathlib
import runpy
import types

root_path = pathlib.Path(root).resolve(strict=True)
script_path = pathlib.Path(script).resolve(strict=True)
allowed = {
    (root_path / "tools" / "validate_device_point_profile.py").resolve(strict=True),
    (root_path / "tools" / "trust_root_freshness.py").resolve(strict=True),
    (root_path / "tools" / "release_verification_receipt.py").resolve(strict=True),
}
if script_path not in allowed or root_path not in script_path.parents:
    raise SystemExit("unsupported qualification entrypoint")
package = types.ModuleType("tools")
package.__path__ = [str(root_path / "tools")]
sys.modules["tools"] = package
sys.argv = [str(script_path), *sys.argv[1:]]
runpy.run_path(str(script_path), run_name="__main__")
'''

def execute_authenticated_qualification(manifest, identities):
    extraction = extract_authenticated_qualification_toolchain(identities)
    extracted_before = validate_extracted_qualification(extraction, identities)
    authenticated_uv_lock_sha256 = identities["uv.lock"]
    runtime_before = validate_posix_qualification_runtime(authenticated_uv_lock_sha256)
    runtime_root, runtime_python, dependency_root = runtime_before[:3]
    freshness = None
    if qualification_mode == "ValidatorProfile":
        freshness_exit_code, freshness = prepare_freshness_context(manifest)
        if freshness_exit_code != 0:
            _close_freshness_locks(freshness["locks"])
            return freshness_exit_code
    temporary_root = run_root / "qualification-tmp"
    temporary_root.mkdir(mode=0o700)
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "HOME": "/root",
        "TMPDIR": str(temporary_root),
        "TMP": str(temporary_root),
        "TEMP": str(temporary_root),
        "DOCKER_CONFIG": str(run_root / "docker-config"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if qualification_mode == "Receipt":
        protected_with_ancestors(
            QUALIFICATION_RECEIPT_AGENT_SOCKET,
            "fixed receipt signing agent socket",
        )
        agent_metadata = root_protected_metadata(
            QUALIFICATION_RECEIPT_AGENT_SOCKET,
            "fixed receipt signing agent socket",
        )
        if not stat.S_ISSOCK(agent_metadata.st_mode):
            fail("fixed receipt signing agent path is not a socket")
        environment["SSH_AUTH_SOCK"] = str(QUALIFICATION_RECEIPT_AGENT_SOCKET)
    if freshness is not None:
        preflight_entrypoint, preflight_arguments = freshness_preflight_invocation(
            manifest, extraction, freshness
        )
        preflight, preflight_stdout, _preflight_stderr = _run_authenticated_qualification_process(
            runtime_root,
            runtime_python,
            dependency_root,
            extraction,
            preflight_entrypoint,
            preflight_arguments,
            environment,
            120,
            capture_output=True,
        )
        _validate_freshness_locks(freshness["locks"])
        try:
            preflight_report = strict_json_loads(preflight_stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
            preflight_report = None
        expected_preflight_decision = {0: "EXACT", 2: "BLOCKED", 3: "INVALID"}.get(preflight)
        if (
            not isinstance(preflight_report, dict)
            or set(preflight_report) != {"decision", "reason_code"}
            or preflight_report.get("decision") != expected_preflight_decision
        ):
            _close_freshness_locks(freshness["locks"])
            return 3
        if preflight != 0:
            _close_freshness_locks(freshness["locks"])
            return preflight if preflight in (2, 3) else 3
    entrypoint, arguments, allowed_exit_codes = qualification_invocation(
        manifest, extraction, freshness
    )
    outcome_returncode, _stdout, _stderr = _run_authenticated_qualification_process(
        runtime_root,
        runtime_python,
        dependency_root,
        extraction,
        entrypoint,
        arguments,
        environment,
        900,
        capture_output=False,
    )
    runtime_after = validate_posix_qualification_runtime(authenticated_uv_lock_sha256)
    if runtime_after != runtime_before:
        fail("qualification runtime identity changed during execution")
    extracted_after = validate_extracted_qualification(extraction, identities)
    if extracted_after != extracted_before:
        fail("extracted qualification toolchain changed during execution")
    if freshness is not None:
        _validate_freshness_locks(freshness["locks"])
        _close_freshness_locks(freshness["locks"])
    if outcome_returncode not in allowed_exit_codes:
        fail("qualification command failed ({})".format(outcome_returncode))
    return outcome_returncode

def _run_authenticated_qualification_process(
    runtime_root,
    runtime_python,
    dependency_root,
    extraction,
    entrypoint,
    arguments,
    environment,
    timeout_seconds,
    capture_output,
):
    outcome = None
    timed_out = False
    try:
        outcome = subprocess.Popen(
            [
                runtime_python,
                "-I",
                "-B",
                "-S",
                "-X",
                "utf8",
                "-c",
                QUALIFICATION_BOOTSTRAP,
                runtime_root,
                runtime_python,
                dependency_root,
                str(extraction),
                str(entrypoint),
                *arguments,
            ],
            cwd=extraction,
            env=environment,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            start_new_session=True,
        )
        try:
            outcome.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
    except (OSError, subprocess.SubprocessError) as error:
        fail("cannot execute authenticated qualification tool: {}".format(error))
    finally:
        if outcome is not None:
            group_kill_error = None
            try:
                os.killpg(outcome.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as error:
                group_kill_error = error
                try:
                    outcome.kill()
                except OSError:
                    pass
            try:
                outcome.wait(timeout=30)
            except subprocess.TimeoutExpired:
                fail("authenticated qualification process group could not be reaped")
            if group_kill_error is not None:
                fail(
                    "cannot terminate authenticated qualification process group: "
                    "{}".format(group_kill_error)
                )
    if timed_out:
        fail("authenticated qualification tool timed out")
    stdout, stderr = outcome.communicate()
    return outcome.returncode, stdout or b"", stderr or b""

def expected_logical_identity(manifest):
    value = {
        "alembic_head": manifest["alembic_head"],
        "candidate_id": manifest["candidate_id"],
        "images": [
            {
                "candidate_reference": image["candidate_reference"],
                "component": image["component"],
                "image_id": image["image_id"],
                "repo_digest": image.get("repo_digest"),
                "source_reference": image["source_reference"],
            }
            for image in manifest["images"]
        ],
        "source_commit": manifest["source_commit"],
        "target_architecture": manifest["target_architecture"],
        "target_os": manifest["target_os"],
    }
    if manifest.get("schema_version") == 3:
        value["qualification_toolchain"] = manifest["qualification_toolchain"]
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()

try:
    manifest = strict_json_loads(manifest_bytes.decode("utf-8"))
except (UnicodeDecodeError, ValueError, RecursionError, MemoryError) as error:
    fail("cannot parse authenticated MANIFEST.json: {}".format(error))
authenticity = manifest.get("authenticity")
expected_authenticity = {
    "status": "SIGNED", "scheme": "openssh-sshsig", "publisher": "ruisheng-release",
    "namespace": "ruisheng-candidate-v1", "key_type": "ssh-ed25519",
    "key_fingerprint": fingerprint, "signed_object": "SHA256SUMS",
    "signature_file": "SHA256SUMS.sig",
}
if (
    type(manifest.get("schema_version")) is not int
    or manifest.get("schema_version") != expected_schema_version
    or authenticity != expected_authenticity
):
    fail("signed manifest authenticity contract is invalid")
qualification_identities = validate_qualification_toolchain(manifest)
if manifest.get("logical_identity") != expected_logical_identity(manifest):
    fail("manifest logical_identity does not match its immutable inputs")
print("[publisher] VERIFIED: publisher signature and complete candidate hashes passed")
sys.stdout.flush()
if qualification_mode != "None":
    if expected_schema_version != 3 or qualification_identities is None:
        fail("qualification mode requires an authenticated v3 qualification toolchain")
    qualification_exit_code = execute_authenticated_qualification(
        manifest, qualification_identities
    )
    cleanup_error = cleanup()
    if cleanup_error is not None:
        fail("protected work cleanup failed: {}: {}".format(run_root, cleanup_error))
    raise SystemExit(qualification_exit_code)
try:
    verifier_command = [str(bash_input), str(package / "verify-candidate.sh"), str(package)]
    if site_env_input:
        verifier_command.append(site_env_input)
    verifier_result = subprocess.run(
        verifier_command,
        check=False,
        env={
            "PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/root",
            "DOCKER_CONFIG": str(run_root / "docker-config"),
        },
    )
except (OSError, subprocess.SubprocessError) as error:
    fail("cannot execute authenticated candidate verifier: {}".format(error))
cleanup_error = cleanup()
if cleanup_error is not None:
    fail("protected work cleanup failed: {}: {}".format(run_root, cleanup_error))
raise SystemExit(verifier_result.returncode)
PY
