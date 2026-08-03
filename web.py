"""The console's server: standard library, one process, no dependencies.

`python3 web.py` on a clean clone. No pip install, no npm, no build step, no
bundler, and no network at runtime except a hosted model call the user opts
into by pasting a key.

This layer decides nothing. It holds the conversation, serves records, and
hands turns to `Agent.handle_turn` — the same entry point the CLI uses, with
a `ListRecorder` passed in so the interface can show the inside of a turn.
`web_layer_emits_identical_envelopes` drives the demo scenarios through this
server and through `Agent` directly and asserts every decision field matches,
which is the answer to "did you just bolt a UI onto it".

Two things it does own, because they are session facts rather than decisions:
which provider is active, and the API key for it. The key is accepted by POST
body, held in a variable on one object, and never written to disk, never
logged, never placed in a URL, and never exported to the environment — the
console spawns a subprocess to run the check suite, and an exported key would
ride along into it.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent
# The runtime-state files. Each is pinned to the repo before anything imports
# the modules that read them, so the console works wherever it was launched.
# The third column is where the check subprocess redirects each, so the suite
# never writes what a demo is about to show; None means it is not redirected —
# a queue check spins up its own console with its own temp queue, so there is
# nothing to point elsewhere here.
DATA_PATHS = (
    ("BOOKLY_AUDIT_PATH", "audit.log", "audit.checks.log"),
    ("BOOKLY_QUEUE_PATH", "queue.json", None),
    ("BOOKLY_POLICY_PATH", "policy.json", "policy.checks.json"),
    ("BOOKLY_OUTBOX_PATH", "outbox.json", "outbox.checks.json"),
    ("BOOKLY_DEADLETTER_PATH", "dead_letter.json", "dead_letter.checks.json"),
    ("BOOKLY_LEDGER_PATH", "ledger.json", "ledger.checks.json"),
)
for _var, _default, _checks_name in DATA_PATHS:
    os.environ.setdefault(_var, str(REPO / _default))

import covers  # noqa: E402  (after the paths are pinned)
import envelope  # noqa: E402
import llm  # noqa: E402
import policy  # noqa: E402
import queue as review  # noqa: E402  (this repo's queue.py, not the stdlib)
import store  # noqa: E402
import tools  # noqa: E402
from agent import Agent  # noqa: E402
from recorder import ListRecorder  # noqa: E402

HOST = "127.0.0.1"  # never 0.0.0.0 — this is a demo console, not a service
PORT = 8000
STATIC_DIR = REPO / "static"
DEMO_SCRIPT = REPO / "demo.txt"

# A local server is still reachable by a page on the open internet through
# DNS rebinding, which resolves an attacker's hostname to 127.0.0.1. The
# defence is to pin the *hostname* the console answers to — the attacker
# controls the name, never the loopback address — so the port is deliberately
# not part of this and the console works on whatever port it was given.
ALLOWED_HOSTS = frozenset(["127.0.0.1", "localhost", "[::1]", "::1"])

# No inline script, no inline style, and no origin but this one. The rule
# "no CDN, no fonts, no analytics" becomes a header a sceptic can read in
# devtools rather than a sentence in a README.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

ORDER_ID_RE = re.compile(r"^BK-\d{4}$")
CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_BODY_BYTES = 64 * 1024
KEY_MASK_TAIL = 4


# ---------------------------------------------------------------------------
# Session state. Everything mutable in the console lives on one object behind
# one lock, because the alternative is module globals and a race on reset.
# ---------------------------------------------------------------------------


class Console:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: Dict[str, Agent] = {}
        # The transcript is kept here, not in the agent, because the only
        # thing that needs it is the case a human will read.
        self._transcripts: Dict[str, List[dict]] = {}
        self.queue = review.ReviewQueue()
        self._provider = llm.RulesProvider()
        self._provider_name = "rules"
        # Held here and nowhere else. Not in os.environ, not on disk, not in
        # any response body.
        self._api_key: Optional[str] = None
        self._key_source: Optional[str] = None

    # -- conversations -----------------------------------------------------

    def turn(self, conversation_id: str, text: str) -> dict:
        with self._lock:
            agent = self._agents.get(conversation_id)
            if agent is None:
                agent = Agent(self._provider, conversation_id)
                self._agents[conversation_id] = agent
            trace = ListRecorder()
            agent.recorder = trace
            result = agent.handle_turn(text)

            transcript = self._transcripts.setdefault(conversation_id, [])
            transcript.append({"role": "customer", "text": text})
            transcript.append({"role": "agent", "text": result.reply})

            # Escalations land in the queue here, in the web layer, by
            # watching what the agent emitted. The agent is not told the queue
            # exists — it emits, and something downstream picks the emission
            # up, which is the same shape as the webhook.
            for emitted, _delivery in result.envelopes:
                if emitted.get("action") == "escalate_to_human":
                    self.queue.open_case(
                        emitted, list(transcript), escalation_context(emitted)
                    )

            return {
                "conversation_id": conversation_id,
                "reply": result.reply,
                "envelopes": [
                    {"envelope": emitted, "delivery": delivery}
                    for emitted, delivery in result.envelopes
                ],
                "trace": trace.as_list(),
                "provider": self._provider.name,
            }

    def restart_conversation(self, conversation_id: str) -> dict:
        """Drop one conversation and nothing else.

        A scripted replay is its own conversation, the same way `---` starts a
        new Agent in demo.txt. Without this, replaying twice in one session
        continues the first run — the agent still holds the pending clarifying
        question, and turn one of the second run reads as a topic change.

        The conversation *id* is deliberately kept, so replaying a scenario
        twice produces the same idempotency keys and the second run shows up
        downstream as suppressed duplicates rather than a second write. What
        is dropped is the conversation memory, not its identity.
        """
        with self._lock:
            self._agents.pop(conversation_id, None)
            self._transcripts.pop(conversation_id, None)
        return {"ok": True, "conversation_id": conversation_id}

    def reset(self) -> dict:
        """Back to a known state, every time: conversations dropped, audit
        trail rotated rather than deleted, provider back to the stand-in."""
        with self._lock:
            self._agents.clear()
            self._transcripts.clear()
            self.queue.clear()
            self._provider = llm.RulesProvider()
            self._provider_name = "rules"
            self._api_key = None
            self._key_source = None
            rotated = _rotate_audit_log()
        return {"ok": True, "rotated_audit_log_to": rotated}

    # -- provider ----------------------------------------------------------

    def provider_state(self) -> dict:
        with self._lock:
            return {
                "active": self._provider_name,
                "display_name": self._provider.name,
                "model": llm.MODELS.get(self._provider_name),
                "available": sorted(llm.PROVIDERS),
                "hosted": sorted(llm.VENDOR_KEY_VARS),
                "key": {
                    # The state of the key, never the key.
                    "present": self._api_key is not None,
                    "masked": _mask(self._api_key),
                    "source": self._key_source,
                },
                "environment_keys": sorted(
                    name
                    for name, var in llm.VENDOR_KEY_VARS.items()
                    if os.environ.get(var)
                ),
            }

    def set_provider(self, name: str, api_key: Optional[str]) -> dict:
        """Switching degrades honestly. Asking for a hosted provider with no
        key available leaves the stand-in running and says so, rather than
        throwing or pretending. Switching mid-conversation rebinds the live
        agents, so conversation memory — and therefore the idempotency key —
        survives the switch."""
        with self._lock:
            if name not in llm.PROVIDERS:
                return _refused(
                    self, name, "%r is not a provider this build knows." % name
                )
            key, source = self._key_for(name, api_key)
            if name in llm.VENDOR_KEY_VARS and not key:
                return _refused(
                    self,
                    name,
                    "No API key is set for %s, so the stand-in is still "
                    "running. Paste a key to switch." % name,
                )
            try:
                provider = llm.build_provider(name, key)
            except ImportError as error:
                return _refused(
                    self,
                    name,
                    "The %s SDK is not installed in this environment (%s). "
                    "The stand-in is still running." % (name, error),
                )
            except Exception as error:  # a bad key, a renamed model, no net
                return _refused(
                    self,
                    name,
                    "%s could not be started: %s. The stand-in is still "
                    "running." % (name, _describe(error)),
                )
            # Constructing a client is offline for both vendors, so a wrong
            # key, a renamed model and a missing network all look like success
            # until the first turn. One tiny real call now moves that
            # discovery off the stage and into this button.
            if isinstance(provider, llm.HostedProvider):
                try:
                    provider.verify()
                except Exception as error:
                    return _refused(
                        self,
                        name,
                        "%s answered with an error, so the stand-in is still "
                        "running: %s" % (name, _describe(error)),
                    )
            self._provider = provider
            self._provider_name = name
            if key:
                self._api_key, self._key_source = key, source
            for agent in self._agents.values():
                # Rebind rather than rebuild: a new Agent would lose the
                # pending question, the denial counts, and the order under
                # discussion, and the demo beat is that only the wording
                # changes.
                agent.provider = provider
            state = self.provider_state()
            state.update({"ok": True, "requested": name, "message": None})
            return state

    def _key_for(
        self, name: str, supplied: Optional[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        if supplied:
            return supplied.strip(), "session"
        variable = llm.VENDOR_KEY_VARS.get(name)
        if variable and os.environ.get(variable):
            return os.environ[variable], "environment"
        if self._api_key and self._provider_name == name:
            return self._api_key, self._key_source
        return None, None


MAX_ERROR_CHARS = 240


def _describe(error: Exception) -> str:
    """A vendor error, short enough to read on a badge.

    SDK exceptions can carry a full response body. The type and the first
    line are what tells you whether it was the key, the model name or the
    network — the rest is noise on a stage.
    """
    text = " ".join(str(error).split())
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS] + "…"
    return "%s: %s" % (type(error).__name__, text) if text else (
        type(error).__name__
    )


def _refused(console: "Console", requested: str, message: str) -> dict:
    state = console.provider_state()
    state.update({"ok": False, "requested": requested, "message": message})
    return state


def _mask(key: Optional[str]) -> Optional[str]:
    """A badge saying a key is set, and nothing a key could be reconstructed
    from. Short keys are hidden entirely rather than mostly revealed."""
    if not key:
        return None
    if len(key) <= KEY_MASK_TAIL:
        return "•" * 8
    return "%s%s" % ("•" * 8, key[-KEY_MASK_TAIL:])


def _rotate_audit_log() -> Optional[str]:
    path = Path(envelope.audit_path())
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rotated = path.with_name("%s.%s" % (path.name, stamp))
    path.rename(rotated)
    return rotated.name


# ---------------------------------------------------------------------------
# Records out. None of this is a decision; it is the store and policy.py
# describing themselves.
# ---------------------------------------------------------------------------


def _order_json(order) -> dict:
    return {
        "order_id": order.order_id,
        "title": order.title,
        "author": order.author,
        "price_paid": order.price_paid,
        "status": order.status,
        "ordered_on": order.ordered_on.isoformat(),
        "delivered_on": (
            order.delivered_on.isoformat() if order.delivered_on else None
        ),
        "returned_on": (
            order.returned_on.isoformat() if order.returned_on else None
        ),
        "eta": order.eta.isoformat() if order.eta else None,
        "carrier": order.carrier,
        "format": order.format,
        "published": order.published,
        "pages": order.pages,
        "cover": {
            "href": "/api/cover/%s.svg" % order.order_id,
            "svg": covers.for_order(order, _cover_palette()),
        },
    }


def _cover_palette() -> str:
    return store.CATALOG.get("cover_palette", covers.DEFAULT_PALETTE)


def _customer_json() -> dict:
    customer = store.CUSTOMER
    return {
        "customer_id": customer.customer_id,
        "name": customer.name,
        "email": customer.email,
        "member_since": customer.member_since.isoformat(),
        "tier": customer.tier,
        "lifetime_value": customer.lifetime_value,
        "orders_placed": customer.orders_placed,
        "payment_kind": customer.payment_kind,
        "payment_last_four": customer.payment_last_four,
        "csat": customer.csat,
        "csat_responses": customer.csat_responses,
        "contact_history": [
            {
                "on": contact.on.isoformat(),
                "channel": contact.channel,
                "subject": contact.subject,
                "outcome": contact.outcome,
            }
            for contact in customer.contact_history
        ],
    }


def policy_json() -> dict:
    """Every threshold and reason code, read from policy.py.

    The interface renders the return window, the clarify limit and the
    dispute trigger from this and never from a number typed into JS. An
    interface allowed to hold its own copy of a threshold is an interface
    that will eventually disagree with the engine.
    """
    active = policy.active_policy()
    return {
        "constants": [
            {"name": c.name, "value": c.value, "why": c.why}
            for c in policy.CONSTANTS
        ],
        # The authorable parameters, with the bounds an edit is validated
        # against and the append-only history behind each value. The back
        # office renders an editor from this; the console renders it read-only.
        "parameters": [
            {
                "name": parameter.name,
                "key": parameter.key,
                "value": active[parameter.name],
                "why": parameter.why,
                "min": parameter.minimum,
                "max": parameter.maximum,
                "history": policy.policy_changes(parameter.key),
            }
            for parameter in policy.PARAMETERS
        ],
        "reason_codes": [
            {
                "code": entry.code,
                "where": entry.where,
                "gloss": entry.gloss,
                "depends_on": list(entry.depends_on),
            }
            for entry in policy.REASON_CODES
        ],
        "retrieval_floor": tools.MIN_KEYWORD_MATCHES,
        "who_can_change_these": (
            "The three CX thresholds are authored in the back office: each "
            "change is validated against its bounds, carries who changed it and "
            "why, and is appended to a log that is never overwritten. The "
            "decision structure and the two floors that stop a confidently "
            "wrong answer — the title-word strength and the retrieval floor — "
            "stay in policy.py and still take an engineer, by design: the whole "
            "point of a floor is that it does not get lowered."
        ),
    }


def customer_json() -> dict:
    return {
        "brand": store.BRAND,
        # Who the agent says it is, so the interface labels it the same way
        # the agent introduces itself rather than keeping its own copy.
        "agent": store.AGENT,
        "service_levels": store.SERVICE_LEVELS,
        "today": store.TODAY.isoformat(),
        "customer": _customer_json(),
        "orders": [
            _order_json(order)
            for order in store.ORDERS.values()
            if order.customer_id == store.CURRENT_CUSTOMER_ID
        ],
        "policy": policy_json(),
        # Prompts the console offers as next things to say. Content, not
        # logic: each one goes through the same handle_turn as anything typed
        # by hand, and none of them carries a hint about the answer.
        "suggestions": store.PROFILE.get("suggestions", {}),
    }


def escalation_context(escalation: dict) -> dict:
    """Everything a human needs to judge a case, snapshotted as it opens.

    Three questions, answered before anyone has to go looking: who is this
    about, what is being escalated, and what does the reason code mean. All
    of it is record and description — nothing here decides anything, and
    `policy.describe` is the same read-only registry the policy viewer uses,
    so the reviewer and the engine cannot disagree about what a code means.
    """
    order = tools.get_order(escalation.get("order_id") or "")
    visible = policy.can_view(order, store.CURRENT_CUSTOMER_ID)
    order_json = _order_json(order) if visible else None
    if order_json:
        # The cover is served by both processes at the same path, so the
        # snapshot keeps the reference and drops the ~2KB of inline SVG. A
        # queue file a human can read in a terminal is worth more than one
        # that carries its own pictures.
        order_json["cover"] = {"href": order_json["cover"]["href"]}
    return {
        "captured_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "today": store.TODAY.isoformat(),
        # From whom.
        "customer": _customer_json(),
        # What is being escalated. An escalation can precede order
        # resolution — an exhausted clarifying question has no order yet —
        # so this is legitimately absent sometimes.
        "order": order_json,
        # What the reason code means, and the named constants behind it.
        "policy": policy.describe(escalation.get("reason_code") or ""),
        "brand": store.BRAND,
        "agent": store.AGENT,
    }


DELIVERY_STATES = ("delivered", "failed", "skipped")


def _delivery_state(delivery: str) -> str:
    """Classify a delivery string by its prefix so a failed hop is legible in
    the audit surface rather than a field nobody reads. The audit line was
    written before the hop, so "failed" here still means the decision survived.
    Kept in sync with deliveryState() in app.js, which recomputes it client-side
    because the turn response does not carry a server-computed state."""
    for prefix in DELIVERY_STATES:
        if delivery.startswith(prefix):
            return prefix
    return "unknown"


def audit_json(limit: int = 500) -> List[dict]:
    """The audit trail, newest first. A line that will not parse is reported
    as a line that will not parse; it is never silently dropped."""
    path = Path(envelope.audit_path())
    if not path.exists():
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as audit_file:
        for number, line in enumerate(audit_file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                entries.append(
                    {"line": number, "event": "unparseable", "raw": line}
                )
                continue
            record["line"] = number
            if record.get("event") == "delivery":
                record["delivery_state"] = _delivery_state(
                    record.get("delivery", "")
                )
            entries.append(record)
    entries.reverse()
    return entries[:limit]


def outbox_json() -> dict:
    """Undelivered envelopes waiting to be retried, and the ones that gave up.
    None of this decides anything; it is the executor's backlog, described."""
    pending = envelope.outbox()
    dead = envelope.dead_letters()
    return {
        "pending": pending,
        "dead_letters": dead,
        "counts": {"pending": len(pending), "dead_lettered": len(dead)},
    }


def reconcile_json() -> dict:
    """Run one reconcile pass and report it. Re-delivers pending envelopes to
    the receiver, which dedups on the idempotency key — so this posts nothing
    twice; it only finishes deliveries a failed hop had deferred."""
    result = envelope.reconcile()
    return dict(result, counts=outbox_json()["counts"])


def scenarios_json() -> List[dict]:
    """The scripted conversations, read from demo.txt so the CLI script and
    the console cannot drift, plus whatever the profile adds."""
    scenarios: List[dict] = []
    current: Optional[dict] = None
    if DEMO_SCRIPT.exists():
        for raw in DEMO_SCRIPT.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line == "---":
                current = None
            elif line.startswith("#"):
                current = {
                    "id": "demo-%d" % (len(scenarios) + 1),
                    "title": line.lstrip("# ").strip(),
                    "source": "demo.txt",
                    "turns": [],
                }
                scenarios.append(current)
            else:
                if current is None:
                    current = {
                        "id": "demo-%d" % (len(scenarios) + 1),
                        "title": "Scenario %d" % (len(scenarios) + 1),
                        "source": "demo.txt",
                        "turns": [],
                    }
                    scenarios.append(current)
                current["turns"].append(line)
    for extra in store.PROFILE.get("scenarios", []):
        scenarios.append(
            {
                "id": extra.get("id", "profile-%d" % len(scenarios)),
                "title": extra.get("title", "Scenario"),
                "source": "profile",
                "note": extra.get("note"),
                "turns": list(extra.get("turns", [])),
            }
        )
    return scenarios


# ---------------------------------------------------------------------------
# The check suite, run from inside the console.
# ---------------------------------------------------------------------------


def checks_command() -> Tuple[List[str], dict]:
    """`sys.executable tests.py`, no shell, and an environment scrubbed of
    every vendor key so a session key cannot ride into the subprocess."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in set(llm.VENDOR_KEY_VARS.values())
    }
    # The suite emits envelopes, authors policy, and posts to a ledger. Each of
    # those writes to its own checks file rather than the one the demo is about
    # to show — the audit trail, the outbox, the dead-letter, the policy
    # document, and the ledger. The queue is redirected per-console instead.
    for var, _default, checks_name in DATA_PATHS:
        if checks_name:
            environment[var] = str(REPO / checks_name)
    environment["PYTHONUNBUFFERED"] = "1"
    return [sys.executable, str(REPO / "tests.py")], environment


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class ConsoleHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "bookly-console"
    sys_version = ""

    console: Console  # set on the server, read through self.server

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        pass  # the console prints what it means to print, and nothing else

    def _headers(self, status: int, content_type: str, length: int,
                 extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _send(self, status: int, content_type: str, body: bytes,
              extra: Optional[dict] = None) -> None:
        self._headers(status, content_type, len(body), extra)
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

    def _read_body(self) -> bool:
        """Take the request body off the socket exactly once, before routing.

        Returns False when it has already answered and the caller should stop.
        Oversized is refused here rather than read and discarded, so a hostile
        Content-Length cannot make the console allocate on demand — the socket
        is left dirty in that one case on purpose, because the answer is to
        close the connection, not to keep talking on it.
        """
        self._payload_bytes = b""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return True
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            self._error(400, "request body too large")
            return False
        self._payload_bytes = self.rfile.read(length)
        return True

    def _body(self) -> dict:
        """The parsed request body. The bytes were already read by _read_body;
        this only decodes them, so a handler that never calls this still leaves
        the socket clean."""
        if not self._payload_bytes:
            return {}
        payload = json.loads(self._payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):  # bracketed IPv6, with or without a port
            host = host.split("]", 1)[0] + "]"
        elif ":" in host:
            host = host.rsplit(":", 1)[0]
        return host in ALLOWED_HOSTS

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (name fixed by http.server)
        self._route("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def _route(self, method: str) -> None:
        # Drain the request body before anything can decide not to want it.
        #
        # This is HTTP/1.1, so the browser keeps the connection open and sends
        # the next request down the same socket. A handler that never reads its
        # body leaves those bytes there, the server parses them as the next
        # request line, and answers 501 to a request that was perfectly valid —
        # so the failure lands on the *following* call and reads as nonsense.
        # /api/reset and /api/reconcile both ignored their bodies (#60).
        #
        # Reading here rather than in each handler means the drain does not
        # depend on handler discipline, and still happens on the 404 and 421
        # paths, where there is no handler to be disciplined.
        if not self._read_body():
            return
        if not self._host_allowed():
            self._error(421, "This console answers on 127.0.0.1 only.")
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            handler = self._match(method, path)
            if handler is None:
                self._error(404, "No route for %s %s" % (method, path))
                return
            handler()
        except ValueError as error:
            self._error(400, str(error))
        except BrokenPipeError:
            pass  # the browser navigated away mid-response; not our problem
        except Exception as error:  # never leak a stack trace to the page
            self._error(500, "%s: %s" % (type(error).__name__, error))

    def _match(self, method: str, path: str) -> Optional[Callable[[], None]]:
        console = self.server.console  # type: ignore[attr-defined]
        if method == "GET":
            if path in ("/", "/index.html"):
                return lambda: self._static("index.html")
            if path == "/api/customer":
                return lambda: self._json(customer_json())
            if path == "/api/policy":
                return lambda: self._json(policy_json())
            if path == "/api/audit":
                return lambda: self._json({"entries": audit_json()})
            if path == "/api/outbox":
                return lambda: self._json(outbox_json())
            if path == "/api/scenarios":
                return lambda: self._json({"scenarios": scenarios_json()})
            if path == "/api/provider":
                return lambda: self._json(console.provider_state())
            if path == "/api/queue":
                return lambda: self._json(
                    {
                        "cases": console.queue.cases(),
                        "counts": console.queue.counts(),
                        "actions": list(review.ACTIONS),
                    }
                )
            case = re.fullmatch(r"/api/queue/(case-[0-9a-f]{1,32})", path)
            if case:
                return lambda: self._case(console, case.group(1))
            cover = re.fullmatch(r"/api/cover/(BK-\d{4})\.svg", path)
            if cover:
                return lambda: self._cover(cover.group(1))
            if path.startswith("/static/"):
                return lambda: self._static(path[len("/static/"):])
        if method == "POST":
            if path == "/api/turn":
                return lambda: self._turn(console)
            if path == "/api/provider":
                return lambda: self._set_provider(console)
            if path == "/api/reset":
                return lambda: self._json(console.reset())
            if path == "/api/reconcile":
                return lambda: self._json(reconcile_json())
            if path == "/api/conversation/restart":
                return lambda: self._restart(console)
            if path == "/api/checks":
                return self._checks
            resolve = re.fullmatch(
                r"/api/queue/(case-[0-9a-f]{1,32})/resolve", path
            )
            if resolve:
                return lambda: self._resolve(console, resolve.group(1))
        return None

    # -- handlers ----------------------------------------------------------

    def _turn(self, console: Console) -> None:
        payload = self._body()
        text = payload.get("text")
        conversation_id = payload.get("conversation_id") or "conv-console"
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text is required")
        if not isinstance(conversation_id, str) or not CONVERSATION_ID_RE.match(
            conversation_id
        ):
            raise ValueError("conversation_id must match %s"
                             % CONVERSATION_ID_RE.pattern)
        self._json(console.turn(conversation_id, text))

    def _set_provider(self, console: Console) -> None:
        payload = self._body()
        name = payload.get("name")
        if not isinstance(name, str):
            raise ValueError("name is required")
        api_key = payload.get("api_key")
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("api_key must be a string")
        self._json(console.set_provider(name, api_key))

    def _restart(self, console: Console) -> None:
        payload = self._body()
        conversation_id = payload.get("conversation_id") or ""
        if not CONVERSATION_ID_RE.match(conversation_id):
            raise ValueError("conversation_id must match %s"
                             % CONVERSATION_ID_RE.pattern)
        self._json(console.restart_conversation(conversation_id))

    def _case(self, console: Console, case_id: str) -> None:
        case = console.queue.get(case_id)
        if case is None:
            self._error(404, "No such case.")
            return
        self._json(case)

    def _resolve(self, console: Console, case_id: str) -> None:
        payload = self._body()
        # Missing actor or justification raises ValueError in queue.py and
        # comes back as a 400. The requirement is enforced there, once, rather
        # than here and again in the browser.
        result = console.queue.resolve(
            case_id,
            action=payload.get("action") or "",
            actor=payload.get("actor") or "",
            justification=payload.get("justification") or "",
        )
        self._json(result)

    def _cover(self, order_id: str) -> None:
        order = tools.get_order(order_id)
        if order is None or not policy.can_view(order, store.CURRENT_CUSTOMER_ID):
            self._error(404, "No such order on this account.")
            return
        self._send(
            200,
            "image/svg+xml; charset=utf-8",
            covers.for_order(order, _cover_palette()).encode("utf-8"),
        )

    def _static(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            self._error(404, "Not found.")
            return
        if not target.is_file():
            self._error(404, "Not found.")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "text/plain"
        if content_type.startswith("text/") or content_type.endswith("script"):
            content_type += "; charset=utf-8"
        extra = {}
        if target.suffix == ".html":
            extra["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        self._send(200, content_type, target.read_bytes(), extra)

    def _checks(self) -> None:
        """Stream the suite as it runs. Chunked so the first result appears
        while the rest are still running, which is the difference between a
        button that proves something and a button that spins."""
        command, environment = checks_command()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        process = subprocess.Popen(
            command,
            cwd=str(REPO),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,  # a list and no shell: nothing here is interpolated
            text=True,
            bufsize=1,
        )
        try:
            for line in process.stdout:
                self._chunk(line.encode("utf-8"))
            process.wait()
            self._chunk(("\nexit %d\n" % process.returncode).encode("utf-8"))
        finally:
            if process.poll() is None:
                process.kill()
            self.wfile.write(b"0\r\n\r\n")

    def _chunk(self, data: bytes) -> None:
        if data:
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, console: Console) -> None:
        self.console = console
        super().__init__(address, handler)

    def server_bind(self) -> None:
        # HTTPServer's bind reverse-DNS-resolves the address, which can hang
        # for minutes on some setups. Same reason stub_receiver.py does this.
        import socketserver

        socketserver.TCPServer.server_bind(self)
        self.server_name = HOST
        self.server_port = self.socket.getsockname()[1]


def serve(port: int = PORT) -> None:
    server = ConsoleServer((HOST, port), ConsoleHandler, Console())
    actual = server.server_address[1]
    print("Bookly console on http://%s:%d" % (HOST, actual))
    print("profile: %s" % store.profile_path().name)
    print("audit trail: %s" % envelope.audit_path())
    print("press Ctrl-C to stop", flush=True)  # visible even when piped
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve(int(os.environ.get("BOOKLY_PORT") or PORT))
