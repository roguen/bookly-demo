"""A stand-in for the orchestration layer's webhook endpoint.

Run this in a second terminal, point BOOKLY_WEBHOOK_URL at it, and watch
envelopes arrive. It also demonstrates the idempotency contract: a repeated
key is acknowledged with 200 but flagged as a duplicate, which is exactly
how a real receiver makes retries safe.
"""
from __future__ import annotations

import json
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "127.0.0.1"
PORT = 8787

_seen_keys = set()


class EnvelopeServer(HTTPServer):
    """HTTPServer's default bind reverse-DNS-resolves the address, which can
    hang for minutes on some DNS setups. A stub doesn't need a hostname."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = HOST
        self.server_port = self.socket.getsockname()[1]


class EnvelopeHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (name fixed by http.server)
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        duplicate = payload.get("idempotency_key") in _seen_keys
        _seen_keys.add(payload.get("idempotency_key"))
        self._print_envelope(payload, duplicate)
        body = json.dumps({"ok": True, "duplicate": duplicate}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _print_envelope(self, payload: dict, duplicate: bool) -> None:
        marker = " (DUPLICATE — would not be re-executed)" if duplicate else ""
        print("--- envelope received%s ---" % marker)
        print(json.dumps(payload, indent=2))

    def log_message(self, fmt: str, *args) -> None:
        pass  # keep the transcript clean; we print envelopes ourselves


def main() -> None:
    server = EnvelopeServer((HOST, PORT), EnvelopeHandler)
    print("stub receiver listening on http://%s:%d/webhook" % (HOST, PORT))
    print("press Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
