"""Local HTTPS test server that mimics Anthropic-shaped endpoints.

Used by the end-to-end integration tests so we can prove the full
client -> mitmproxy -> RecordAddon -> upstream pipeline works without
calling a real LLM provider. The server is a stdlib threading HTTP server
wrapped in a self-signed TLS context. Tests start it in a background
thread, point mitmproxy at it via a custom provider_hosts mapping, and
assert on what mitmproxy captures.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# Canonical-ish Anthropic-shaped response used by tests.
_DEFAULT_JSON_RESPONSE = {
    "id": "msg_test_e2e_01",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "hello from local test server"}],
    "model": "test-model-1",
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 7, "output_tokens": 6},
}

# SSE chunks following Anthropic's event-stream shape closely enough that
# the parse_sse_chunks code path exercises real-world structure.
_DEFAULT_SSE_CHUNKS: list[bytes] = [
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_sse_01"}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0}\n\n',
    b"event: content_block_delta\ndata: "
    b'{"type":"content_block_delta","delta":{"text":"Hello "}}\n\n',
    b"event: content_block_delta\ndata: "
    b'{"type":"content_block_delta","delta":{"text":"world"}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    b"data: [DONE]\n\n",
]


def generate_self_signed_cert(out_dir: Path, host: str = "127.0.0.1") -> tuple[Path, Path]:
    """Generate a short-lived self-signed cert+key for the test server.

    Returns (cert_path, key_path). The cert SubjectAltName includes both
    "127.0.0.1" and "localhost" so the server works under either address.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cert_path = out_dir / "server.crt"
    key_path = out_dir / "server.key"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(_ipaddress(host))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _ipaddress(host: str) -> Any:
    # ipaddress.ip_address typed-as-Any to avoid pulling the module just for type hints.
    import ipaddress

    return ipaddress.ip_address(host)


class _LLMHandler(BaseHTTPRequestHandler):
    json_response: dict[str, Any] = _DEFAULT_JSON_RESPONSE
    sse_chunks: list[bytes] = _DEFAULT_SSE_CHUNKS

    def log_message(self, format: str, *args: Any) -> None:
        # Silence the default request logging so test output stays clean.
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _body = self.rfile.read(length) if length else b""

        if self.path.endswith("/stream"):
            self._send_sse()
        else:
            self._send_json()

    def _send_json(self) -> None:
        payload = json.dumps(type(self).json_response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Test-Marker", "local-llm-server")
        self.end_headers()
        self.wfile.write(payload)

    def _send_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Test-Marker", "local-llm-server-sse")
        self.end_headers()
        for chunk in type(self).sse_chunks:
            self.wfile.write(chunk)
            self.wfile.flush()
            # Tiny delay between chunks so RecordAddon's SSE accumulator
            # exercises the multi-chunk path, not a single-buffer shortcut.
            time.sleep(0.01)


class LocalLLMServer:
    """Threaded HTTPS test server. Use as a context manager.

    with LocalLLMServer(cert_dir=tmp_path) as srv:
        # srv.host_header is "localhost:<port>" — use as the Host header
        # srv.url is "https://127.0.0.1:<port>"
        ...
    """

    def __init__(
        self,
        cert_dir: Path,
        *,
        json_response: dict[str, Any] | None = None,
        sse_chunks: list[bytes] | None = None,
    ) -> None:
        self._cert_dir = cert_dir
        self._json = json_response or _DEFAULT_JSON_RESPONSE
        self._sse = sse_chunks or _DEFAULT_SSE_CHUNKS
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.cert_path: Path | None = None
        self.key_path: Path | None = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.server_address[1])

    @property
    def host_header(self) -> str:
        return f"localhost:{self.port}"

    @property
    def url(self) -> str:
        return f"https://localhost:{self.port}"

    def __enter__(self) -> LocalLLMServer:
        self.cert_path, self.key_path = generate_self_signed_cert(self._cert_dir)

        # Configure the handler class with this instance's payloads. Using a
        # subclass avoids global state when multiple servers run concurrently.
        json_payload = self._json
        sse_payload = self._sse

        class _ConfiguredHandler(_LLMHandler):
            json_response = json_payload
            sse_chunks = sse_payload

        self._server = HTTPServer(("127.0.0.1", 0), _ConfiguredHandler)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(self.cert_path), keyfile=str(self.key_path))
        self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)

        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="LocalLLMServer"
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
