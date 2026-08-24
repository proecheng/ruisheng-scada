#!/bin/bash
set -euo pipefail
PATH="/usr/bin:/bin"
export PATH
HOME="/root"
export HOME
unset BASH_ENV ENV CDPATH PYTHONHOME PYTHONPATH TMP TMPDIR TEMP DOCKER_CONFIG \
  DOCKER_CLI_PLUGIN_EXTRA_DIRS DOCKER_HOST DOCKER_CONTEXT XDG_CONFIG_HOME

if [[ "$#" -lt 1 || "$#" -gt 2 ]]; then
  echo "usage: verify-publisher.sh <candidate-directory> [site-env-file]" >&2
  exit 1
fi
PACKAGE_INPUT="$1"
SITE_ENV_INPUT="${2:-}"
TRUST_INPUT="/etc/ruisheng/trust"
PYTHON="/usr/bin/python3"
SSH_KEYGEN="/usr/bin/ssh-keygen"
BASH="/bin/bash"
[[ -x "$PYTHON" ]] || { echo "[publisher] authenticity FAILED: /usr/bin/python3 is required" >&2; exit 1; }
[[ -x "$SSH_KEYGEN" ]] || { echo "[publisher] authenticity FAILED: /usr/bin/ssh-keygen is required" >&2; exit 1; }
[[ -x "$BASH" ]] || { echo "[publisher] authenticity FAILED: /bin/bash is required" >&2; exit 1; }

"$PYTHON" -I -S - "$PACKAGE_INPUT" "$TRUST_INPUT" "$0" "$PYTHON" "$SSH_KEYGEN" "$BASH" "$SITE_ENV_INPUT" <<'PY'
import atexit
import base64
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

package_input, trust_input, verifier_input, python_input, ssh_keygen_input, bash_input = map(
    pathlib.Path, sys.argv[1:7]
)
site_env_input = sys.argv[7]

def fail(message):
    raise SystemExit("[publisher] authenticity FAILED: " + message)

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

fixed = {
    ".env.prod.example", "MANIFEST.json", "MANIFEST.md", "SHA256SUMS",
    "SHA256SUMS.sig", "docker-compose.prod.yml", "nginx.conf",
    "site-acceptance-profile.md.example", "site-health-acl.conf.example",
    "site-network.override.yml", "site-serial-hardware.json.example",
    "site-serial.env.example", "site-serial.override.yml", "setup-customer.md",
    "install_serial_hardware_task.ps1", "serial_hardware_attach.ps1",
    "validate-network-boundary.py", "validate_serial_hardware.py",
    "verify-candidate.ps1", "verify-candidate.sh",
}
components = ("postgres", "redis", "api", "gw", "web")
expected = fixed | {"images/{}.tar.gz".format(value) for value in components}
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
if actual != expected:
    fail("candidate file allowlist mismatch: missing={}, extra={}".format(
        sorted(expected - actual), sorted(actual - expected)))

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
initial_sizes = {}
for relative in sorted(expected):
    source_path = package / relative
    metadata = source_path.stat()
    if source_path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        fail("candidate file is linked or non-regular: " + relative)
    initial_sizes[relative] = metadata.st_size
total_size = sum(initial_sizes.values())
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
                    cached.extend(chunk)
    except OSError as error:
        fail("cannot hash candidate file {}: {}".format(relative, error))
    if hasher.hexdigest() != digest:
        fail("candidate hash mismatch: " + relative)
    if relative == "MANIFEST.json" and cached is not None:
        manifest_bytes = bytes(cached)
if manifest_bytes is None:
    fail("MANIFEST.json is missing from authenticated hashes")
try:
    manifest = json.loads(manifest_bytes.decode("utf-8"))
except (UnicodeDecodeError, ValueError) as error:
    fail("cannot parse authenticated MANIFEST.json: {}".format(error))
authenticity = manifest.get("authenticity")
expected_authenticity = {
    "status": "SIGNED", "scheme": "openssh-sshsig", "publisher": "ruisheng-release",
    "namespace": "ruisheng-candidate-v1", "key_type": "ssh-ed25519",
    "key_fingerprint": fingerprint, "signed_object": "SHA256SUMS",
    "signature_file": "SHA256SUMS.sig",
}
if manifest.get("schema_version") != 2 or authenticity != expected_authenticity:
    fail("signed manifest authenticity contract is invalid")
print("[publisher] VERIFIED: publisher signature and complete candidate hashes passed")
sys.stdout.flush()
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
