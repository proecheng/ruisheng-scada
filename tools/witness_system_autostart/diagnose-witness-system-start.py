from __future__ import annotations

import faulthandler
import runpy
import socket
import ssl
import sys
from pathlib import Path
from types import FrameType
from typing import Any

FINITE_PROBE_ARG_COUNT = 2
TRACE_SERVE_ARG_COUNT = 3


def checkpoint(name: str) -> None:
    print(name, flush=True)


def load_witness(witness_path: Path) -> dict[str, Any]:
    checkpoint("python_started")
    namespace = runpy.run_path(str(witness_path))
    checkpoint("witness_module_loaded")
    return namespace


def trace_serve(witness_path: Path) -> None:
    namespace = load_witness(witness_path)

    def trace(frame: FrameType, event: str, arg: object) -> Any:
        code = frame.f_code
        if Path(code.co_filename).resolve() != witness_path:
            return None
        if event == "line":
            print(f"witness_line:{frame.f_lineno}", file=sys.stderr, flush=True)
        return trace

    faulthandler.enable(file=sys.stderr, all_threads=True)
    faulthandler.dump_traceback_later(10, repeat=True, file=sys.stderr)
    sys.argv = [str(witness_path), "serve"]
    checkpoint("trace_serve_started")
    sys.settrace(trace)
    namespace["main"]()


def finite_probe(witness_path: Path) -> None:
    namespace = load_witness(witness_path)

    config = namespace["load_json"](namespace["CONFIG_PATH"])
    checkpoint("config_loaded")
    private_key = namespace["serialization"].load_pem_private_key(
        namespace["KEY_PATH"].read_bytes(), password=None
    )
    if not isinstance(private_key, namespace["Ed25519PrivateKey"]):
        raise RuntimeError("freshness witness key is not Ed25519")
    checkpoint("witness_key_loaded")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(namespace["SERVER_CERT_PATH"], namespace["SERVER_KEY_PATH"])
    checkpoint("server_certificate_loaded")
    context.load_verify_locations(namespace["CLIENT_CERT_PATH"])
    context.verify_mode = ssl.CERT_REQUIRED
    checkpoint("client_certificate_loaded")

    server = namespace["WitnessServer"](config, private_key, context)
    checkpoint("listener_bound")
    try:
        if server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1:
            raise RuntimeError("witness socket is not listening")
        checkpoint("socket_accepting")
    finally:
        server.server_close()
    checkpoint("probe_complete")


def main() -> None:
    if len(sys.argv) == FINITE_PROBE_ARG_COUNT:
        finite_probe(Path(sys.argv[1]).resolve(strict=True))
        return
    if len(sys.argv) == TRACE_SERVE_ARG_COUNT and sys.argv[1] == "--trace-serve":
        trace_serve(Path(sys.argv[2]).resolve(strict=True))
        return
    raise RuntimeError("expected [--trace-serve] and the fixed witness script path")


if __name__ == "__main__":
    main()
