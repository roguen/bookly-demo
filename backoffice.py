"""The other side of the boundary: a second process, on a second port.

`python3 backoffice.py`, started separately from the console. The separation
is the architectural argument, not packaging. The agent claims to *emit*
actions rather than *execute* them; if the thing that receives those actions
ran inside the agent's process, that claim would be a diagram rather than a
demonstrable fact. Here you can kill this server mid-conversation and watch
the agent keep deciding, keep auditing, and record the delivery as failed.

Three surfaces, one screen each:

  Refund ledger   receives envelopes at /webhook and renders them as ledger
                  lines. A repeated idempotency key is recorded as a
                  suppressed duplicate against the existing line rather than
                  a second line. Deduplication is durable now (v3.4.0): the
                  ledger is persisted and reloaded on start, so a replayed or
                  retried key is suppressed across a restart. stub_receiver.py
                  stays in-memory; this is the real receiver doing its job.

  Agent desk      the human review queue, rendered as a support console. The
                  queue file is the shared state between the two processes.

  Policy editor   the CX policy the agent enforces, and — new in v3.2.0 —
                  authorable. The three thresholds are authored here: each
                  change is validated against its bounds, carries who changed it
                  and why, is appended to a log that is never overwritten, and
                  is read live by the console. The decision structure and the
                  two floors that stop a confidently wrong answer stay in
                  policy.py and still take an engineer. This is the surface
                  earlier builds deliberately refused to mock; it is now real.

Nothing here flows back. These systems receive and display; nothing returns a
value that reaches a verdict. `stub_receiver.py` is untouched and still works
exactly as evidence/duplicate_receipt.txt documents — this is an addition, and
the two bind the same port, so you run one or the other.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional

REPO = Path(__file__).resolve().parent
os.environ.setdefault("BOOKLY_AUDIT_PATH", str(REPO / "audit.log"))
os.environ.setdefault("BOOKLY_QUEUE_PATH", str(REPO / "queue.json"))
os.environ.setdefault("BOOKLY_POLICY_PATH", str(REPO / "policy.json"))
os.environ.setdefault("BOOKLY_OUTBOX_PATH", str(REPO / "outbox.json"))
os.environ.setdefault("BOOKLY_DEADLETTER_PATH", str(REPO / "dead_letter.json"))
os.environ.setdefault("BOOKLY_LEDGER_PATH", str(REPO / "ledger.json"))

import covers  # noqa: E402
import policy  # noqa: E402
import queue as review  # noqa: E402  (this repo's queue.py, not the stdlib)
import store  # noqa: E402
import tools  # noqa: E402
import web  # noqa: E402  (for the shared JSON shapes and header discipline)

HOST = "127.0.0.1"
# The port the webhook is already documented on. This is a drop-in receiver:
# run this or stub_receiver.py, never both.
PORT = 8787
STATIC_DIR = REPO / "static"

ALLOWED_HOSTS = frozenset(["127.0.0.1", "localhost", "[::1]", "::1"])
MAX_BODY_BYTES = 64 * 1024

# Every surface says what it is. These are honest mocks and they carry a chip
# that says so, on screen, permanently.
STAND_IN_NOTICE = (
    "Stand-in. These screens are part of the demo, not a product: the ledger "
    "records and displays rather than actually posting a refund to a bank, and "
    "nothing here returns a value that reaches a verdict. Its deduplication is "
    "durable now — it survives a restart, suppressing a replayed key on the "
    "idempotency key rather than executing it twice."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ledger_path() -> str:
    """Where the durable ledger persists. Read per call so a check can point it
    at its own store, exactly like the audit trail and the queue."""
    return os.environ.get("BOOKLY_LEDGER_PATH") or str(REPO / "ledger.json")


class Ledger:
    """Refund lines, deduplicated by idempotency key — durably (v3.4.0).

    Persisted to disk and reloaded on start, so a replayed or retried envelope
    is suppressed on its key across a restart of this process, rather than
    executed a second time. This is what makes reconcile() safe: the sender may
    re-deliver an envelope it is unsure landed, and the receiver posts it once.
    It still records and displays rather than actually posting a refund to a
    bank — that is the part still simulated — but delivery here is real, not a
    diagram: re-delivery is at-least-once, and durable dedup on the idempotency
    key is what makes the posting happen exactly once regardless.
    """

    def __init__(self, now: Callable[[], str] = _utc_now) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._lines: List[dict] = self._load()
        self._by_key: Dict[str, dict] = {
            line["idempotency_key"]: line for line in self._lines
        }

    def _load(self) -> List[dict]:
        try:
            data = json.loads(Path(ledger_path()).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _persist(self) -> None:
        path = Path(ledger_path())
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self._lines, indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic on the same filesystem

    def receive(self, envelope: dict) -> dict:
        key = envelope.get("idempotency_key") or ""
        with self._lock:
            existing = self._by_key.get(key)
            if existing is not None:
                # One line, one write. The repeat is recorded against the
                # line it duplicates, which is what "would not be executed
                # again" looks like when you draw it.
                existing["duplicates"].append(
                    {
                        "at": self._now(),
                        "envelope_id": envelope.get("envelope_id"),
                    }
                )
                self._persist()
                return {"duplicate": True, "line": dict(existing)}
            line = {
                "received_at": self._now(),
                "idempotency_key": key,
                "action": envelope.get("action"),
                "resolution": envelope.get("resolution"),
                "order_id": envelope.get("order_id"),
                "amount": envelope.get("amount"),
                "currency": envelope.get("currency"),
                "reason_code": envelope.get("reason_code"),
                "conversation_id": envelope.get("conversation_id"),
                "actor": envelope.get("actor"),
                "justification": envelope.get("justification"),
                "envelope_id": envelope.get("envelope_id"),
                "duplicates": [],
            }
            self._lines.append(line)
            self._by_key[key] = line
            self._persist()
            return {"duplicate": False, "line": dict(line)}

    def lines(self) -> List[dict]:
        with self._lock:
            return [dict(line) for line in reversed(self._lines)]

    def summary(self) -> dict:
        with self._lock:
            executed = len(self._lines)
            suppressed = sum(len(line["duplicates"]) for line in self._lines)
            posted = sum(
                line["amount"] or 0
                for line in self._lines
                if line["action"] == "refund"
            )
        return {
            "lines": executed,
            "suppressed_duplicates": suppressed,
            "amount_posted": round(posted, 2),
            "durability": (
                "durable: persisted to disk and reloaded on start, so a "
                "replayed or retried envelope is suppressed on its idempotency "
                "key across a restart, not executed twice. stub_receiver.py "
                "stays in-memory; this is the real receiver's job, now done."
            ),
        }


class BackOffice:
    def __init__(self, verbose: bool = False) -> None:
        self.ledger = Ledger()
        # The same queue file the console writes. Two processes, one shared
        # record — which is the point of running this separately at all.
        self.queue = review.ReviewQueue()
        # Printing each arriving envelope is a property of running this as a
        # program — it is how you watch deliveries land in a terminal, the
        # same way stub_receiver.py does. It is not a property of the object,
        # so a check that embeds one gets silence and the console's Checks
        # panel shows check results rather than envelope dumps.
        self.verbose = verbose


class BackOfficeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "bookly-backoffice"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        pass

    # -- plumbing (same discipline as the console) -------------------------

    def _send(self, status: int, content_type: str, body: bytes,
              extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload).encode("utf-8"),
        )

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host.split("]", 1)[0] + "]"
        elif ":" in host:
            host = host.rsplit(":", 1)[0]
        return host in ALLOWED_HOSTS

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def _route(self, method: str) -> None:
        office = self.server.office  # type: ignore[attr-defined]
        # The webhook is exempt from the Host pin: it is a machine-to-machine
        # endpoint that the agent posts to, not a page a browser is tricked
        # into loading, and it returns nothing an attacker could read.
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if method == "POST" and path == "/webhook":
            try:
                self._webhook(office)
            except Exception as error:
                self._error(400, "%s: %s" % (type(error).__name__, error))
            return
        if not self._host_allowed():
            self._error(421, "This back office answers on 127.0.0.1 only.")
            return
        try:
            handler = self._match(method, path, office)
            if handler is None:
                self._error(404, "No route for %s %s" % (method, path))
                return
            handler()
        except ValueError as error:
            self._error(400, str(error))
        except BrokenPipeError:
            pass
        except Exception as error:
            self._error(500, "%s: %s" % (type(error).__name__, error))

    def _match(self, method: str, path: str, office: BackOffice):
        if method == "GET":
            if path in ("/", "/index.html", "/backoffice.html"):
                return lambda: self._static("backoffice.html")
            if path == "/api/ledger":
                return lambda: self._json(
                    {
                        "lines": office.ledger.lines(),
                        "summary": office.ledger.summary(),
                        "stand_in": STAND_IN_NOTICE,
                    }
                )
            if path == "/api/queue":
                return lambda: self._json(
                    {
                        "cases": office.queue.cases(),
                        "counts": office.queue.counts(),
                        "actions": list(review.ACTIONS),
                        "stand_in": STAND_IN_NOTICE,
                    }
                )
            if path == "/api/policy":
                # Straight from policy.py, over the same shape the console
                # serves, so the two surfaces cannot disagree.
                return lambda: self._json(
                    dict(web.policy_json(), stand_in=STAND_IN_NOTICE)
                )
            cover = re.fullmatch(r"/api/cover/(BK-\d{4})\.svg", path)
            if cover:
                # Served at the same path the console serves it, so a case
                # snapshot can carry one reference that works in either
                # process rather than a copy of the picture.
                return lambda: self._cover(cover.group(1))
            if path.startswith("/static/"):
                return lambda: self._static(path[len("/static/"):])
        if method == "POST":
            resolve = re.fullmatch(
                r"/api/queue/(case-[0-9a-f]{1,32})/resolve", path
            )
            if resolve:
                return lambda: self._resolve(office, resolve.group(1))
            if path == "/api/policy/change":
                return lambda: self._policy_change()
        return None

    # -- handlers ----------------------------------------------------------

    def _webhook(self, office: BackOffice) -> None:
        """Receive an envelope, exactly as stub_receiver.py does.

        It records and displays. It never executes anything, and it returns
        nothing the agent reads back — the response is an acknowledgement, and
        the agent does not branch on it.
        """
        payload = self._body()
        result = office.ledger.receive(payload)
        if office.verbose:
            marker = " (DUPLICATE — would not be re-executed)" if result[
                "duplicate"
            ] else ""
            print("--- envelope received%s ---" % marker, flush=True)
            print(json.dumps(payload, indent=2), flush=True)
        self._json({"ok": True, "duplicate": result["duplicate"]})

    def _resolve(self, office: BackOffice, case_id: str) -> None:
        payload = self._body()
        result = office.queue.resolve(
            case_id,
            action=payload.get("action") or "",
            actor=payload.get("actor") or "",
            justification=payload.get("justification") or "",
        )
        self._json(result)

    def _policy_change(self) -> None:
        """Author one policy parameter change. Validation — bounds, type, and a
        required actor and justification — is enforced in policy.change_parameter
        and comes back as a 400, the same discipline queue.resolve uses. Only
        this operator surface writes policy: the customer console has no such
        route. The response is the fresh policy surface, so the editor re-renders
        with the new value and its history."""
        payload = self._body()
        change = policy.change_parameter(
            payload.get("field") or "",
            payload.get("value"),
            payload.get("actor") or "",
            payload.get("justification") or "",
        )
        self._json(
            dict(web.policy_json(), stand_in=STAND_IN_NOTICE, change=change)
        )

    def _cover(self, order_id: str) -> None:
        order = tools.get_order(order_id)
        if order is None or not policy.can_view(order, store.CURRENT_CUSTOMER_ID):
            self._error(404, "No such order on this account.")
            return
        self._send(
            200,
            "image/svg+xml; charset=utf-8",
            covers.for_order(
                order, store.CATALOG.get("cover_palette", covers.DEFAULT_PALETTE)
            ).encode("utf-8"),
        )

    def _static(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            self._error(404, "Not found.")
            return
        if not target.is_file():
            self._error(404, "Not found.")
            return
        import mimetypes

        content_type = mimetypes.guess_type(target.name)[0] or "text/plain"
        if content_type.startswith("text/") or content_type.endswith("script"):
            content_type += "; charset=utf-8"
        extra = {}
        if target.suffix == ".html":
            extra["Content-Security-Policy"] = web.CONTENT_SECURITY_POLICY
        self._send(200, content_type, target.read_bytes(), extra)


class BackOfficeServer(ThreadingHTTPServer):
    daemon_threads = True
    # So a restart during the failure demo never hits "address already in
    # use" while the previous socket is still in TIME_WAIT.
    allow_reuse_address = True

    def __init__(self, address, handler, office: BackOffice) -> None:
        self.office = office
        super().__init__(address, handler)

    def server_bind(self) -> None:
        import socketserver

        socketserver.TCPServer.server_bind(self)
        self.server_name = HOST
        self.server_port = self.socket.getsockname()[1]


def serve(port: int = PORT) -> None:
    server = BackOfficeServer(
        (HOST, port), BackOfficeHandler, BackOffice(verbose=True)
    )
    actual = server.server_address[1]
    print("Bookly back office on http://%s:%d" % (HOST, actual))
    print("webhook: http://%s:%d/webhook" % (HOST, actual))
    print("run the console with:")
    print(
        "  BOOKLY_WEBHOOK_URL=http://%s:%d/webhook python3 web.py"
        % (HOST, actual)
    )
    print("press Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve(int(os.environ.get("BOOKLY_BACKOFFICE_PORT") or PORT))
