from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import socket
import sqlite3
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(r"C:\ProgramData\RuishengWitness")
AUDIT_PATH = ROOT / "trust" / "witness-audit.sqlite3"
CONFIG_PATH = ROOT / "trust" / "witness-config.json"
HIGH_WATER_PATH = ROOT / "trust" / "high-water.json"
KEY_PATH = ROOT / "trust" / "freshness-witness-ed25519.pem"
SERVER_CERT_PATH = ROOT / "tls" / "server-cert.pem"
SERVER_KEY_PATH = ROOT / "tls" / "server-key.pem"
CLIENT_CERT_PATH = ROOT / "tls" / "client-cert.pem"
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MIN_REQUEST_BYTES = 2
SOCKET_TIMEOUT_SECONDS = 10
MAX_CONCURRENT_REQUESTS = 32
CHALLENGE_BYTES = 32
INVALID_EXIT_CODE = 3
DOMAIN = b"ruisheng.trust-root-freshness-attestation/v1\0"
LOCK = threading.Lock()

REQUEST_FIELDS = {
    "schema_version",
    "artifact_type",
    "site_id",
    "challenge",
    "requested_at",
    "candidate_logical_identity",
    "root_snapshot_sha256",
    "provider_config_sha256",
    "profile_id",
    "profile_sha256",
    "payload_sha256",
    "canonical_gate_sha256",
    "semantic_validator",
    "validator_source_sha256",
    "verifier_id",
    "verifier_tool_sha256",
    "state",
}
STATE_FIELDS = {
    "root_id",
    "root_version",
    "root_revocation_sequence",
    "root_sha256",
    "policy_id",
    "policy_version",
    "policy_revocation_sequence",
    "policy_sha256",
}
HIGH_WATER_FIELDS = STATE_FIELDS | {
    "schema_version",
    "site_id",
    "monotonic_state_id",
    "monotonic_counter",
}
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
CHALLENGE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def canonical_timestamp(value: datetime) -> str:
    utc = value.astimezone(UTC)
    if utc.microsecond:
        return utc.isoformat(timespec="microseconds")
    return utc.isoformat(timespec="seconds")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="ascii"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON value: {item}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def cert_sha256(binary: bytes) -> str:
    return hashlib.sha256(binary).hexdigest()


def is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_identifier(value: object) -> bool:
    return isinstance(value, str) and IDENTIFIER_PATTERN.fullmatch(value) is not None


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def valid_challenge(value: object) -> bool:
    if not isinstance(value, str) or CHALLENGE_PATTERN.fullmatch(value) is None:
        return False
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except ValueError:
        return False
    return (
        len(decoded) == CHALLENGE_BYTES
        and base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") == value
    )


def valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except (OverflowError, ValueError):
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() == timedelta(0)
        and parsed.isoformat() == value
    )


def validate_identity_material(
    config: dict[str, Any],
    private_key: Ed25519PrivateKey,
    server_certificate_pem: str,
    client_certificate_pem: str,
) -> None:
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if base64.b64encode(public_key).decode("ascii") != config["witness_public_key"]:
        raise RuntimeError("freshness witness public key does not match the configured identity")
    try:
        server_der = ssl.PEM_cert_to_DER_cert(server_certificate_pem)
        client_der = ssl.PEM_cert_to_DER_cert(client_certificate_pem)
    except ValueError as error:
        raise RuntimeError("freshness witness certificate material is invalid") from error
    if hashlib.sha256(server_der).hexdigest() != config["server_cert_sha256"]:
        raise RuntimeError(
            "freshness witness server certificate does not match the configured identity"
        )
    if hashlib.sha256(client_der).hexdigest() != config["client_cert_sha256"]:
        raise RuntimeError(
            "freshness witness client certificate does not match the configured identity"
        )


def validate_request(request: dict[str, Any]) -> str | None:  # noqa: PLR0911, PLR0912
    if set(request) != REQUEST_FIELDS:
        return "FRESHNESS_REQUEST_SCHEMA_INVALID"
    if request["schema_version"] != 1 or request["artifact_type"] != (
        "ruisheng.trust-root-freshness-request"
    ):
        return "FRESHNESS_REQUEST_SCHEMA_INVALID"
    if request["semantic_validator"] != "ruisheng.device-point-profile-validator/v5":
        return "FRESHNESS_REQUEST_SCHEMA_INVALID"
    for field in ("site_id", "profile_id", "verifier_id"):
        if not valid_identifier(request[field]):
            return "FRESHNESS_REQUEST_SCHEMA_INVALID"
    for field in (
        "candidate_logical_identity",
        "root_snapshot_sha256",
        "provider_config_sha256",
        "profile_sha256",
        "payload_sha256",
        "canonical_gate_sha256",
        "validator_source_sha256",
        "verifier_tool_sha256",
    ):
        if not valid_sha256(request[field]):
            return "FRESHNESS_REQUEST_SCHEMA_INVALID"
    if not valid_challenge(request["challenge"]) or not valid_timestamp(request["requested_at"]):
        return "FRESHNESS_REQUEST_SCHEMA_INVALID"
    state = request["state"]
    if not isinstance(state, dict) or set(state) != STATE_FIELDS:
        return "FRESHNESS_REQUEST_STATE_INVALID"
    for field in ("root_id", "root_sha256", "policy_id", "policy_sha256"):
        if (field.endswith("_id") and not valid_identifier(state[field])) or (
            field.endswith("_sha256") and not valid_sha256(state[field])
        ):
            return "FRESHNESS_REQUEST_STATE_INVALID"
    for field in (
        "root_version",
        "root_revocation_sequence",
        "policy_version",
        "policy_revocation_sequence",
    ):
        if not is_integer(state[field]) or state[field] < 0:
            return "FRESHNESS_REQUEST_STATE_INVALID"
    return None


def validate_high_water(  # noqa: PLR0911
    high_water: dict[str, Any], config: dict[str, Any]
) -> str | None:
    if set(high_water) != HIGH_WATER_FIELDS:
        return "FRESHNESS_HIGH_WATER_INVALID"
    if high_water["schema_version"] != 1 or high_water["site_id"] != config["site_id"]:
        return "FRESHNESS_HIGH_WATER_INVALID"
    if high_water["monotonic_state_id"] != config["monotonic_state_id"]:
        return "FRESHNESS_HIGH_WATER_INVALID"
    if not is_integer(high_water["monotonic_counter"]) or high_water["monotonic_counter"] < 0:
        return "FRESHNESS_HIGH_WATER_INVALID"
    for field in ("root_id", "root_sha256", "policy_id", "policy_sha256"):
        if (field.endswith("_id") and not valid_identifier(high_water[field])) or (
            field.endswith("_sha256") and not valid_sha256(high_water[field])
        ):
            return "FRESHNESS_HIGH_WATER_INVALID"
    for field in (
        "root_version",
        "root_revocation_sequence",
        "policy_version",
        "policy_revocation_sequence",
    ):
        if not is_integer(high_water[field]) or high_water[field] < 0:
            return "FRESHNESS_HIGH_WATER_INVALID"
    return None


def audit(status: int, value: dict[str, Any], path: str) -> bool:
    try:
        connection = sqlite3.connect(AUDIT_PATH, timeout=2)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, path TEXT NOT NULL, "
                "status INTEGER NOT NULL, decision TEXT, reason_code TEXT)"
            )
            connection.execute(
                "INSERT INTO requests(at,path,status,decision,reason_code) VALUES(?,?,?,?,?)",
                (
                    canonical_timestamp(datetime.now(UTC)),
                    path,
                    status,
                    value.get("decision"),
                    value.get("reason_code"),
                ),
            )
            connection.commit()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return False
    return True


def compare(request: dict[str, Any], high_water: dict[str, Any]) -> tuple[int, str]:
    state = request.get("state")
    if not isinstance(state, dict):
        return 3, "FRESHNESS_REQUEST_STATE_INVALID"
    pairs = (
        ("root_id", "FRESHNESS_ROOT_ID_SWITCH"),
        ("policy_id", "FRESHNESS_POLICY_ID_SWITCH"),
    )
    for field, reason in pairs:
        if state.get(field) != high_water.get(field):
            return 3, reason
    monotonic = (
        ("root_version", "FRESHNESS_ROOT_VERSION_ROLLBACK"),
        ("root_revocation_sequence", "FRESHNESS_ROOT_REVOCATION_ROLLBACK"),
        ("policy_version", "FRESHNESS_POLICY_VERSION_ROLLBACK"),
        ("policy_revocation_sequence", "FRESHNESS_POLICY_REVOCATION_ROLLBACK"),
    )
    for field, reason in monotonic:
        value = state.get(field)
        stored = high_water.get(field)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not isinstance(stored, int)
            or isinstance(stored, bool)
            or value < stored
        ):
            return 3, reason
    for version, digest, reason in (
        ("root_version", "root_sha256", "FRESHNESS_ROOT_HASH_CONFLICT"),
        ("policy_version", "policy_sha256", "FRESHNESS_POLICY_HASH_CONFLICT"),
    ):
        if state.get(version) == high_water.get(version) and state.get(digest) != high_water.get(
            digest
        ):
            return 3, reason
    expected = {
        field: high_water[field]
        for field in (
            "root_id",
            "root_version",
            "root_revocation_sequence",
            "root_sha256",
            "policy_id",
            "policy_version",
            "policy_revocation_sequence",
            "policy_sha256",
        )
    }
    if state != expected:
        return 2, "FRESHNESS_LOCAL_STATE_AHEAD"
    return 0, "FRESHNESS_EXACT"


class WitnessServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        config: dict[str, Any],
        private_key: Ed25519PrivateKey,
        tls_context: ssl.SSLContext,
    ) -> None:
        super().__init__((config["listen_host"], config["listen_port"]), Handler)
        self.config = config
        self.private_key = private_key
        self.tls_context = tls_context
        self.request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)

    def get_request(self) -> tuple[socket.socket, object]:
        raw_socket, address = super().get_request()
        raw_socket.settimeout(SOCKET_TIMEOUT_SECONDS)
        return raw_socket, address

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        try:
            if not isinstance(request, socket.socket):
                self.shutdown_request(request)
                return
            try:
                tls_socket = self.tls_context.wrap_socket(request, server_side=True)
            except (OSError, TimeoutError, ssl.SSLError):
                self.shutdown_request(request)
                return
            super().process_request_thread(tls_socket, client_address)
        finally:
            self.request_slots.release()


class Handler(BaseHTTPRequestHandler):
    server: WitnessServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(SOCKET_TIMEOUT_SECONDS)

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except TimeoutError:
            self.close_connection = True

    def log_message(self, _format: str, *args: object) -> None:
        return

    def response(
        self,
        status: int,
        value: dict[str, Any],
        *,
        require_audit: bool = False,
    ) -> None:
        audited = audit(status, value, self.path)
        if require_audit and not audited:
            status = 503
            value = {"decision": "BLOCKED", "reason_code": "FRESHNESS_AUDIT_UNAVAILABLE"}
        body = canonical_json(value) + b"\n"
        print(
            json.dumps(
                {
                    "at": canonical_timestamp(datetime.now(UTC)),
                    "path": self.path,
                    "status": status,
                    "reason_code": value.get("reason_code"),
                    "decision": value.get("decision"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.response(404, {"error": "not_found"})
            return
        self.response(200, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802, PLR0911
        if self.path != "/v1/attest":
            self.response(404, {"error": "not_found"})
            return
        peer = self.connection.getpeercert(binary_form=True)
        if not peer or cert_sha256(peer) != self.server.config["client_cert_sha256"]:
            self.response(403, {"decision": "BLOCKED", "reason_code": "CLIENT_CERT_REJECTED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < MIN_REQUEST_BYTES or length > MAX_REQUEST_BYTES:
            self.response(400, {"decision": "INVALID", "reason_code": "REQUEST_SIZE_INVALID"})
            return
        try:
            request = json.loads(
                self.rfile.read(length).decode("utf-8"),
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON value: {value}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            self.response(400, {"decision": "INVALID", "reason_code": "REQUEST_JSON_INVALID"})
            return
        if not isinstance(request, dict):
            self.response(400, {"decision": "INVALID", "reason_code": "REQUEST_JSON_INVALID"})
            return
        request_error = validate_request(request)
        if request_error is not None:
            self.response(400, {"decision": "INVALID", "reason_code": request_error})
            return
        if request.get("site_id") != self.server.config["site_id"]:
            self.response(409, {"decision": "INVALID", "reason_code": "FRESHNESS_SITE_MISMATCH"})
            return
        with LOCK:
            try:
                high_water = load_json(HIGH_WATER_PATH)
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
                self.response(
                    503,
                    {"decision": "BLOCKED", "reason_code": "FRESHNESS_HIGH_WATER_UNAVAILABLE"},
                )
                return
            high_water_error = validate_high_water(high_water, self.server.config)
            if high_water_error is not None:
                self.response(503, {"decision": "BLOCKED", "reason_code": high_water_error})
                return
            exit_code, reason = compare(request, high_water)
            if exit_code:
                self.response(
                    409 if exit_code == INVALID_EXIT_CODE else 503,
                    {
                        "decision": "INVALID" if exit_code == INVALID_EXIT_CODE else "BLOCKED",
                        "reason_code": reason,
                    },
                )
                return
            now = datetime.now(UTC).replace(microsecond=0)
            attestation: dict[str, Any] = {
                "schema_version": 1,
                "artifact_type": "ruisheng.trust-root-freshness-attestation",
                "provider_id": self.server.config["provider_id"],
                "witness_key_id": self.server.config["witness_key_id"],
                "request": request,
                "high_water": {
                    field: high_water[field]
                    for field in (
                        "root_id",
                        "root_version",
                        "root_revocation_sequence",
                        "root_sha256",
                        "policy_id",
                        "policy_version",
                        "policy_revocation_sequence",
                        "policy_sha256",
                    )
                },
                "monotonic_state_id": high_water["monotonic_state_id"],
                "monotonic_counter": high_water["monotonic_counter"],
                "observed_at": canonical_timestamp(now),
                "expires_at": canonical_timestamp(now + timedelta(seconds=90)),
                "signature": {
                    "algorithm": "Ed25519",
                    "key_id": self.server.config["witness_key_id"],
                    "value": "",
                },
            }
            unsigned = dict(attestation)
            unsigned.pop("signature")
            signature = self.server.private_key.sign(DOMAIN + canonical_json(unsigned))
            attestation["signature"]["value"] = base64.b64encode(signature).decode("ascii")
        self.response(200, attestation, require_audit=True)


def serve() -> None:
    config = load_json(CONFIG_PATH)
    required_config = {
        "client_cert_sha256",
        "listen_host",
        "listen_port",
        "monotonic_state_id",
        "provider_id",
        "server_cert_sha256",
        "site_id",
        "witness_key_id",
        "witness_public_key",
    }
    if set(config) != required_config:
        raise RuntimeError("freshness witness config schema is invalid")
    if not isinstance(config["listen_port"], int) or isinstance(config["listen_port"], bool):
        raise RuntimeError("freshness witness listen port is invalid")
    for field in required_config - {"listen_port"}:
        if not valid_text(config[field]):
            raise RuntimeError("freshness witness config contains invalid text")
    private_key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RuntimeError("freshness witness key is not Ed25519")
    try:
        validate_identity_material(
            config,
            private_key,
            SERVER_CERT_PATH.read_text(encoding="ascii"),
            CLIENT_CERT_PATH.read_text(encoding="ascii"),
        )
    except (OSError, UnicodeError) as error:
        raise RuntimeError("freshness witness certificate material is unavailable") from error
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(SERVER_CERT_PATH, SERVER_KEY_PATH)
    context.load_verify_locations(CLIENT_CERT_PATH)
    context.verify_mode = ssl.CERT_REQUIRED
    if hasattr(ssl, "VERIFY_X509_PARTIAL_CHAIN"):
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    context.check_hostname = False
    server = WitnessServer(config, private_key, context)
    server.serve_forever(poll_interval=0.2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve"])
    args = parser.parse_args()
    if args.command == "serve":
        serve()


if __name__ == "__main__":
    main()
