#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="$(cd "${1:-.}" && pwd)"
SITE_ENV_INPUT="${2:-$PACKAGE_DIR/.env.prod.example}"
if [[ ! -f "$SITE_ENV_INPUT" ]]; then
  echo "[verify] Compose environment file is missing: $SITE_ENV_INPUT" >&2
  exit 1
fi
SITE_ENV_FILE="$(cd "$(dirname "$SITE_ENV_INPUT")" && pwd)/$(basename "$SITE_ENV_INPUT")"
LOCK_FILE="$(mktemp)"
CONFIG_FILE="$(mktemp)"
EXPECTED_FILE="$(mktemp)"
ACTUAL_FILE="$(mktemp)"
trap 'rm -f "$LOCK_FILE" "$CONFIG_FILE" "$EXPECTED_FILE" "$ACTUAL_FILE"' EXIT

command -v python3 >/dev/null || { echo "[verify] python3 is required" >&2; exit 1; }
command -v docker >/dev/null || { echo "[verify] Docker CLI is required" >&2; exit 1; }

python3 - "$PACKAGE_DIR" > "$LOCK_FILE" <<'PY'
import gzip
import hashlib
import json
import os
import pathlib
import re
import sys
import tarfile

root = pathlib.Path(sys.argv[1]).resolve()
fixed = {
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

try:
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
except Exception as error:
    fail("cannot parse MANIFEST.json: {}".format(error))

candidate_id = manifest.get("candidate_id")
if not isinstance(candidate_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", candidate_id) is None:
    fail("invalid candidate ID")
if manifest.get("authenticity", {}).get("status") != "BLOCKED":
    fail("manifest removed the publisher-authenticity BLOCKED gate")
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
    fail("file allowlist mismatch: missing={}, extra={}".format(
        sorted(expected_files - actual_files), sorted(actual_files - expected_files)
    ))

sums = {}
for number, line in enumerate((root / "SHA256SUMS").read_text(encoding="utf-8").splitlines(), 1):
    match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
    if match is None:
        fail("invalid SHA256SUMS entry at line {}".format(number))
    digest, relative = match.groups()
    safe_path(relative)
    if relative in sums:
        fail("duplicate SHA256SUMS path: " + relative)
    sums[relative] = digest
expected_sums = expected_files - {"SHA256SUMS"}
if set(sums) != expected_sums:
    fail("SHA256SUMS allowlist mismatch: missing={}, extra={}".format(
        sorted(expected_sums - set(sums)), sorted(set(sums) - expected_sums)
    ))
for relative, expected_digest in sums.items():
    actual_digest = sha256(root / relative)
    if actual_digest != expected_digest:
        fail("SHA-256 mismatch for {}: expected {}, got {}".format(
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
                if not isinstance(descriptors, list) or len(descriptors) != 1:
                    fail("archive index must contain one descriptor for " + image["component"])
                descriptor_digest = descriptors[0].get("digest")
                if not isinstance(descriptor_digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", descriptor_digest) is None:
                    fail("invalid archive descriptor digest for " + image["component"])
                descriptor_name = "blobs/sha256/" + descriptor_digest.split(":", 1)[1]
                descriptor_stream = archive.extractfile(descriptor_name)
                if descriptor_stream is None:
                    fail("missing archive descriptor for " + image["component"])
                descriptor_bytes = descriptor_stream.read()
                if "sha256:" + hashlib.sha256(descriptor_bytes).hexdigest() != descriptor_digest:
                    fail("archive descriptor digest mismatch for " + image["component"])
                descriptor = json.loads(descriptor_bytes)
                if descriptor.get("config", {}).get("digest") != config_digest:
                    nested_descriptors = descriptor.get("manifests")
                    if not isinstance(nested_descriptors, list):
                        fail("archive descriptor/config mismatch for " + image["component"])
                    matching_nested = []
                    for nested in nested_descriptors:
                        nested_digest = nested.get("digest") if isinstance(nested, dict) else None
                        if not isinstance(nested_digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", nested_digest) is None:
                            fail("invalid archive nested descriptor digest for " + image["component"])
                        nested_name = "blobs/sha256/" + nested_digest.split(":", 1)[1]
                        try:
                            nested_stream = archive.extractfile(nested_name)
                        except KeyError:
                            # Docker 29 can retain the source index while exporting only
                            # the manifest blob for the selected local platform.
                            continue
                        if nested_stream is None:
                            fail("invalid archive nested descriptor for " + image["component"])
                        nested_bytes = nested_stream.read()
                        if "sha256:" + hashlib.sha256(nested_bytes).hexdigest() != nested_digest:
                            fail("archive nested descriptor digest mismatch for " + image["component"])
                        nested_value = json.loads(nested_bytes)
                        if nested_value.get("config", {}).get("digest") != config_digest:
                            continue
                        platform = nested.get("platform")
                        if isinstance(platform, dict) and (
                            platform.get("os") != config.get("os")
                            or platform.get("architecture") != config.get("architecture")
                        ):
                            fail("archive nested descriptor platform mismatch for " + image["component"])
                        matching_nested.append(nested_digest)
                    if len(matching_nested) != 1:
                        fail("archive descriptor/config mismatch for " + image["component"])
                archive_id = descriptor_digest
    except (OSError, EOFError, gzip.BadGzipFile, tarfile.TarError, ValueError, KeyError) as error:
        fail("invalid Docker archive {}: {}".format(image["archive"], error))
    if (archive_id, config.get("os"), config.get("architecture")) != (
        image["image_id"], image["os"], image["architecture"]
    ):
        fail("archive identity mismatch for " + image["component"])
    fields = (
        image["component"], image["candidate_reference"], image["image_id"],
        image["os"], image["architecture"], image["archive"]
    )
    if any(not isinstance(field, str) or "\t" in field or "\n" in field for field in fields):
        fail("unsafe image lock field")
    print("\t".join(fields))
PY

echo "[verify] File allowlist, SHA-256, and archive identities passed."
while IFS=$'\t' read -r component reference image_id image_os architecture archive; do
  echo "[verify] Loading $component from $archive"
  docker image load --input "$PACKAGE_DIR/$archive" >/dev/null
done < "$LOCK_FILE"

while IFS=$'\t' read -r component reference image_id image_os architecture archive; do
  mapfile -t metadata < <(
    docker image inspect "$reference" \
      --format '{{.Id}}{{println}}{{.Os}}{{println}}{{.Architecture}}{{println}}{{range .RepoTags}}{{println .}}{{end}}'
  )
  if [[ "${metadata[0]:-}" != "$image_id" || "${metadata[1]:-}" != "$image_os" || "${metadata[2]:-}" != "$architecture" ]]; then
    echo "[verify] Loaded identity mismatch for $component" >&2
    exit 1
  fi
  if ! printf '%s\n' "${metadata[@]:3}" | grep -Fqx -- "$reference"; then
    echo "[verify] Loaded candidate tag missing for $component: $reference" >&2
    exit 1
  fi
done < "$LOCK_FILE"

docker compose \
  --env-file "$SITE_ENV_FILE" \
  -f "$PACKAGE_DIR/docker-compose.prod.yml" \
  config --images > "$ACTUAL_FILE"
cut -f2 "$LOCK_FILE" | sort -u > "$EXPECTED_FILE"
sort -u "$ACTUAL_FILE" -o "$ACTUAL_FILE"
if ! diff -u "$EXPECTED_FILE" "$ACTUAL_FILE"; then
  echo "[verify] Compose image set does not match the manifest" >&2
  exit 1
fi
if [[ "$(grep -Fxc "ruisheng-candidate/api:$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["candidate_id"])' "$PACKAGE_DIR/MANIFEST.json")" < <(
  docker compose --env-file "$SITE_ENV_FILE" \
    -f "$PACKAGE_DIR/docker-compose.prod.yml" config --images
))" -ne 2 ]]; then
  echo "[verify] Compose migrate/api do not share exactly one API image" >&2
  exit 1
fi

docker compose \
  --env-file "$SITE_ENV_FILE" \
  -f "$PACKAGE_DIR/docker-compose.prod.yml" \
  config --format json > "$CONFIG_FILE"
python3 - "$CONFIG_FILE" "$PACKAGE_DIR/MANIFEST.json" <<'PY'
import json
import sys

services = json.load(open(sys.argv[1], encoding="utf-8")).get("services", {})
manifest = json.load(open(sys.argv[2], encoding="utf-8"))
candidate_id = manifest["candidate_id"]
expected_images = {
    "postgres": f"ruisheng-candidate/postgres:{candidate_id}",
    "redis": f"ruisheng-candidate/redis:{candidate_id}",
    "migrate": f"ruisheng-candidate/api:{candidate_id}",
    "api": f"ruisheng-candidate/api:{candidate_id}",
    "gw": f"ruisheng-candidate/gw:{candidate_id}",
    "web": f"ruisheng-candidate/web:{candidate_id}",
}
if set(services) != set(expected_images):
    raise SystemExit("[verify] Compose service set mismatch")
expected_platform = f'{manifest["target_os"]}/{manifest["target_architecture"]}'
for name, service in services.items():
    if service.get("image") != expected_images[name]:
        raise SystemExit("[verify] Compose image mismatch for service: " + name)
    if service.get("platform") != expected_platform:
        raise SystemExit("[verify] Compose platform mismatch for service: " + name)
    if "build" in service or service.get("pull_policy") != "never":
        raise SystemExit("[verify] Compose service may build or pull: " + name)
PY

echo "[verify] Integrity and loaded image identity passed."
SITE_DIR="$(cd "$(dirname "$SITE_ENV_FILE")" && pwd)"
if [[ -f "$SITE_DIR/site-health-acl.conf" && -f "$SITE_DIR/site-acceptance-profile.md" ]]; then
  python3 "$PACKAGE_DIR/validate-network-boundary.py" \
    --compose "$PACKAGE_DIR/docker-compose.prod.yml" \
    --compose "$PACKAGE_DIR/site-network.override.yml" \
    --env-file "$SITE_ENV_FILE" \
    --profile "$SITE_DIR/site-acceptance-profile.md" \
    --nginx-config "$PACKAGE_DIR/nginx.conf" \
    --acl-file "$SITE_DIR/site-health-acl.conf"
else
  echo "[verify] B-04 network validation remains BLOCKED until site ACL and Profile are supplied." >&2
  exit 2
fi
echo "[verify] Publisher authenticity is not configured; CAP-1/G0-03 remain BLOCKED."
