/* The console's client.
 *
 * Two rules govern this whole file, and both are checked rather than trusted.
 *
 * 1. Nothing is ever handed to an HTML parser. Every piece of text that came
 *    from a customer, an agent, a record, or an audit line reaches the page
 *    through textContent and nowhere else, so there is no context in which a
 *    customer's angle bracket could become an element. We type prompt
 *    injections into this box on stage, and inertness by construction beats
 *    inertness by escaping.
 *
 *    The markup-parsing APIs this file may not contain are enumerated in
 *    `injected_markup_is_escaped` in tests.py, which greps for them and
 *    fails if one appears. That list lives there and only there — writing it
 *    out here would trip the very check that enforces it, and one
 *    authoritative copy is the point.
 *
 * 2. No threshold is written here. The return window, the clarify limit and
 *    the dispute trigger are read from policy.py over the API. An interface
 *    allowed to hold its own copy of a number is an interface that will
 *    eventually disagree with the engine.
 */

/* --- the API client ---------------------------------------------------- */

async function api(path, body) {
  const options = { headers: { "Content-Type": "application/json" } };
  if (body !== undefined) {
    options.method = "POST";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch (error) {
    throw new Error(`${path} returned something that was not JSON`);
  }
  if (!response.ok) {
    throw new Error((payload && payload.error) || `${path} failed`);
  }
  return payload;
}

export const Api = {
  customer: () => api("/api/customer"),
  policy: () => api("/api/policy"),
  audit: () => api("/api/audit"),
  scenarios: () => api("/api/scenarios"),
  provider: () => api("/api/provider"),
  setProvider: (name, apiKey) =>
    api("/api/provider", apiKey ? { name, api_key: apiKey } : { name }),
  queue: () => api("/api/queue"),
  resolve: (caseId, body) => api(`/api/queue/${caseId}/resolve`, body),
  turn: (conversationId, text) =>
    api("/api/turn", { conversation_id: conversationId, text }),
  reset: () => api("/api/reset"),

  /* The check suite streams, so this yields lines as they arrive rather
     than resolving once at the end. A button that spins for ten seconds
     proves nothing; one that fills in as the checks pass proves it ran. */
  async *checks() {
    const response = await fetch("/api/checks", { method: "POST" });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) yield line;
    }
    if (buffer) yield buffer;
  },
};

/* --- DOM helpers, the only way this file builds anything ---------------- */

/* Text always arrives as a property, never as markup. Every element in the
   console is built through this function. */
export function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.class) node.className = options.class;
  if (options.text !== undefined && options.text !== null) {
    node.textContent = String(options.text);
  }
  for (const [name, value] of Object.entries(options.attrs || {})) {
    if (value !== null && value !== undefined) node.setAttribute(name, value);
  }
  for (const [name, value] of Object.entries(options.on || {})) {
    node.addEventListener(name, value);
  }
  for (const child of children) {
    if (child) node.appendChild(child);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/* --- state -------------------------------------------------------------- */

const CONVERSATION_ID = "conv-console";

const state = {
  view: "customer",
  tab: "trace",
  region: "conversation",
  customer: null,
  provider: null,
  messages: [],
  trace: [],
  envelopes: [],
  busy: false,
};

const dom = {};

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

/* --- formatting --------------------------------------------------------- */

const money = (amount) =>
  amount === null || amount === undefined
    ? "—"
    : `$${Number(amount).toFixed(2)}`;

function day(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  const months = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  return `${months[m - 1]} ${d}, ${y}`;
}

const shortKey = (key) => (key ? `${key.slice(0, 8)}…` : "—");

const initials = (name) =>
  name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0] || "")
    .join("")
    .toUpperCase();

/* --- record column ------------------------------------------------------ */

function fact(label, value, wide) {
  return el("div", { class: wide ? "fact wide" : "fact" }, [
    el("dt", { text: label }),
    el("dd", { text: value }),
  ]);
}

function renderRecord() {
  const body = dom.recordBody;
  clear(body);
  if (!state.customer) return;
  const { customer, orders, policy, brand } = state.customer;
  const operator = state.view === "operator";

  body.appendChild(
    el("div", { class: "identity" }, [
      el("div", { class: "avatar", text: initials(customer.name) }),
      el("div", {}, [
        el("div", { class: "identity-name", text: customer.name }),
        el("div", {
          class: "identity-sub",
          text: `${customer.customer_id} · ${customer.tier}`,
        }),
      ]),
    ])
  );

  /* Operator-only fields are not rendered at all in customer view, rather
     than hidden with CSS. Lifetime value and CSAT are things support knows
     about you; they are not things you are shown. */
  const facts = el("dl", { class: "facts" }, [
    fact("Member since", day(customer.member_since)),
    fact("Orders", String(customer.orders_placed)),
    operator ? fact("Lifetime value", money(customer.lifetime_value)) : null,
    operator
      ? fact("CSAT", `${customer.csat} / 5 (${customer.csat_responses})`)
      : null,
    fact(
      "Payment",
      `${customer.payment_kind} ···· ${customer.payment_last_four}`,
      true
    ),
  ]);
  body.appendChild(facts);

  body.appendChild(el("h3", { class: "col-head", text: "Orders" }));
  body.appendChild(
    el(
      "ul",
      { class: "orders" },
      orders.map((order) =>
        el("li", { class: "order" }, [
          el("img", {
            attrs: {
              src: order.cover.href,
              alt: `${order.title} by ${order.author}`,
              width: "44",
              height: "66",
              loading: "lazy",
            },
          }),
          el("div", {}, [
            el("div", { class: "order-title", text: order.title }),
            el("div", { class: "order-meta", text: order.author }),
            el("div", {}, [
              el("span", {
                class: "status",
                text: order.status,
                attrs: { "data-status": order.status },
              }),
            ]),
            el("div", {
              class: "order-id",
              text: `${order.order_id} · ${money(order.price_paid)}`,
            }),
          ]),
        ])
      )
    )
  );

  if (!operator) return;

  /* Every number below is read from policy.py over the API. None of them is
     written in this file, which is the point of serving them at all. */
  body.appendChild(el("h3", { class: "col-head", text: "Policy" }));
  const block = el("div", { class: "policy-block" });
  for (const constant of policy.constants) {
    block.appendChild(
      el("div", { class: "policy-row", attrs: { title: constant.why } }, [
        el("span", { class: "policy-name", text: constant.name }),
        el("span", { class: "policy-value", text: String(constant.value) }),
      ])
    );
  }
  block.appendChild(
    el("div", { class: "policy-row" }, [
      el("span", { class: "policy-name", text: "MIN_KEYWORD_MATCHES" }),
      el("span", { class: "policy-value", text: String(policy.retrieval_floor) }),
    ])
  );
  block.appendChild(
    el("p", { class: "policy-source", text: policy.who_can_change_these })
  );
  body.appendChild(block);
  body.appendChild(
    el("p", {
      class: "policy-source",
      text: `Demo clock frozen at ${day(state.customer.today)}. Brand: ${
        brand.display_name
      }.`,
    })
  );
}

/* --- conversation ------------------------------------------------------- */

/* Openers for an empty conversation. Wording only — every one of these goes
   through the same handle_turn as anything typed by hand, and none of them
   carries a hint about what the answer should be. */
const OPENERS = [
  "Where's my Dune order?",
  "I'd like to return a book.",
  "How long does standard shipping take?",
  "I want to return my copy of The Pragmatic Programmer, order BK-0987.",
];

function renderOpeners() {
  const panel = el("div", { class: "opener" }, [
    el("h3", { text: "Start a conversation" }),
    el("p", {
      text: "Type anything, or begin with one of these. Operator view opens the glass box on the right.",
    }),
  ]);
  for (const opener of OPENERS) {
    panel.appendChild(
      el("button", {
        text: opener,
        attrs: { type: "button" },
        on: { click: () => send(opener) },
      })
    );
  }
  return panel;
}

function renderMessages() {
  clear(dom.messages);
  if (!state.messages.length) {
    dom.messages.appendChild(renderOpeners());
    return;
  }
  for (const message of state.messages) {
    dom.messages.appendChild(
      el("div", { class: `bubble ${message.role}` }, [
        el("span", {
          class: "who",
          text: message.role === "customer" ? "Customer" : "Agent · model",
        }),
        el("span", { text: message.text }),
      ])
    );
  }
  dom.messages.scrollTop = dom.messages.scrollHeight;
}

/* --- trace -------------------------------------------------------------- */

/* One line per note, in the vocabulary of the stage that produced it. The
   summary never restates a fact the payload does not already contain. */
function summarize(note) {
  const p = note.payload || {};
  switch (note.stage) {
    case "extract": {
      const intents = (p.requests || [])
        .map((r) => r.intent || "—")
        .join(", ");
      return `${(p.requests || []).length} request(s) · ${intents}`;
    }
    case "route":
      return p.branch;
    case "lookup":
      if (p.kind === "policy_article") {
        return `kb · ${p.article_id || "no match (failed closed)"}`;
      }
      return `${p.rule} → ${p.order_id || (p.matched_ids || []).join(", ") || "nothing"}`;
    case "candidates":
      return `${p.count} candidate(s) · clarify: ${p.should_clarify ? "yes" : "no"}`;
    case "clarify":
      if (p.step === "asked") {
        return `asked · ${(p.option_ids || []).length} options`;
      }
      if (p.attempts !== undefined) {
        return `${p.step} · ${p.attempts} of ${p.limit} attempts used`;
      }
      return p.step;
    case "verdict":
      return `${p.decision} · ${p.reason_code}`;
    case "envelope":
      return `${p.envelope.action} · ${p.envelope.order_id || "—"} · ${money(
        p.envelope.amount
      )}`;
    case "narrate":
      return `${p.event}${p.suppressed_duplicate ? " · duplicate suppressed" : ""}`;
    default:
      return note.stage;
  }
}

function keyValues(payload) {
  const list = el("dl", { class: "kv" });
  for (const [key, value] of Object.entries(payload || {})) {
    list.appendChild(el("dt", { text: key }));
    list.appendChild(
      el("dd", {
        text:
          value === null || typeof value !== "object"
            ? String(value)
            : JSON.stringify(value, null, 1),
      })
    );
  }
  return list;
}

function noteRow(note, streaming) {
  const row = el(
    "li",
    {
      class: streaming ? "note streaming" : "note",
      attrs: { "data-side": note.side, "data-stage": note.stage },
    },
    [
      el("div", { class: "note-head" }, [
        el("span", { class: "note-stage", text: note.stage }),
        el("span", { class: "note-side", text: note.side }),
      ]),
      el("div", { class: "note-summary", text: summarize(note) }),
    ]
  );

  /* A verdict carries the named constants it rests on, so any decision on
     screen walks back to the line of policy that produced it. */
  const constants = (note.payload && note.payload.constants) || [];
  if (constants.length) {
    const why = el("details", {}, [
      el("summary", { text: "why this reason code" }),
    ]);
    for (const constant of constants) {
      why.appendChild(
        el("div", { class: "note-summary", text: `${constant.name} = ${constant.value}` })
      );
      why.appendChild(el("div", { class: "kv" }, [
        el("dt", { text: "because" }),
        el("dd", { text: constant.why }),
      ]));
    }
    row.appendChild(why);
  }

  row.appendChild(
    el("details", {}, [
      el("summary", { text: "payload" }),
      keyValues(note.payload),
    ])
  );
  return row;
}

function envelopeCard(entry, landing) {
  const e = entry.envelope;
  return el(
    "div",
    { class: landing ? "envelope landing" : "envelope" },
    [
      el("div", { class: "envelope-action" }, [
        el("span", { text: e.action.replace(/_/g, " ").toUpperCase() }),
        el("span", { class: "envelope-amount", text: money(e.amount) }),
      ]),
      el("div", { class: "envelope-row", text: e.order_id || "no order yet" }),
      el("div", { class: "envelope-row envelope-reason", text: e.reason_code }),
      el("div", { class: "envelope-row", text: `key ${shortKey(e.idempotency_key)}` }),
      el("span", {
        class: "delivery",
        text: entry.delivery,
        attrs: { "data-state": deliveryState(entry.delivery) },
      }),
    ]
  );
}

function deliveryState(delivery) {
  if (!delivery) return "unknown";
  if (delivery.startsWith("delivered")) return "delivered";
  if (delivery.startsWith("failed")) return "failed";
  if (delivery.startsWith("skipped")) return "skipped";
  return "unknown";
}

function emptyState(title, hint) {
  return el("div", { class: "empty" }, [
    el("strong", { text: title }),
    el("span", { text: hint }),
  ]);
}

function renderTrace(streaming) {
  const panel = dom.panels.trace;
  clear(panel);
  if (!state.trace.length) {
    panel.appendChild(
      emptyState(
        "No turn yet",
        "Send a message and every step the agent takes appears here, coloured by which side of the boundary produced it."
      )
    );
    return;
  }

  const list = el("ul", { class: "trace" });
  state.trace.forEach((note, index) => {
    const row = noteRow(note, streaming && !reducedMotion.matches);
    if (streaming && !reducedMotion.matches) {
      row.style.animationDelay = `${index * 70}ms`;
    }
    list.appendChild(row);
  });
  panel.appendChild(list);

  if (state.envelopes.length) {
    panel.appendChild(el("h3", { class: "col-head", text: "Emitted this session" }));
    state.envelopes
      .slice()
      .reverse()
      .forEach((entry, index) => {
        /* The one card that animates: the newest envelope lands after the
           trace has finished streaming. That is the whole motion budget. */
        const landing = streaming && index === 0 && !reducedMotion.matches;
        const card = envelopeCard(entry, landing);
        if (landing) {
          card.style.animationDelay = `${state.trace.length * 70 + 90}ms`;
        }
        panel.appendChild(card);
      });
  }
}

/* --- audit -------------------------------------------------------------- */

async function renderAudit() {
  const panel = dom.panels.audit;
  clear(panel);
  let entries = [];
  try {
    entries = (await Api.audit()).entries;
  } catch (error) {
    panel.appendChild(emptyState("Could not read the audit trail", String(error)));
    return;
  }
  if (!entries.length) {
    panel.appendChild(
      emptyState(
        "The trail is empty",
        "Every emitted envelope is written to audit.log before the network hop. Take a turn that decides something and it appears here."
      )
    );
    return;
  }
  panel.appendChild(
    el("p", {
      class: "policy-source",
      text: "Newest first. The audit line is written before delivery is attempted, so a failed hop loses the delivery and never the decision.",
    })
  );
  for (const entry of entries) {
    const line = el("div", { class: "audit-line" }, [
      el("div", { class: "audit-event", text: entry.event }),
    ]);
    if (entry.event === "emitted" && entry.envelope) {
      const e = entry.envelope;
      line.appendChild(
        el("div", { text: `${e.action} · ${e.order_id || "—"} · ${money(e.amount)}` })
      );
      line.appendChild(el("div", { text: e.reason_code }));
      line.appendChild(el("div", { text: `key ${shortKey(e.idempotency_key)}` }));
      if (e.customer_note) {
        line.appendChild(
          el("div", { text: `note: ${e.customer_note}` })
        );
      }
    } else if (entry.event === "delivery") {
      line.appendChild(
        el("span", {
          class: "delivery",
          text: entry.delivery,
          attrs: { "data-state": entry.delivery_state || "unknown" },
        })
      );
    } else {
      line.appendChild(el("div", { text: JSON.stringify(entry) }));
    }
    panel.appendChild(line);
  }
}

/* --- placeholders filled in by later steps ------------------------------ */

/* The review queue. The load-bearing part of this rendering is that the
   original verdict and the human's action are drawn as two separate things,
   in that order — because that is what actually happened. Nothing here edits
   a decision; a resolution is appended below the one it reviews. */
async function renderQueue() {
  const panel = dom.panels.queue;
  clear(panel);
  let payload;
  try {
    payload = await Api.queue();
  } catch (error) {
    panel.appendChild(emptyState("Could not read the queue", String(error)));
    return;
  }
  if (!payload.cases.length) {
    panel.appendChild(
      emptyState(
        "No cases yet",
        "Escalations land here for a human to resolve. Ask for a manager, or press an out-of-window return until the agent hands it over."
      )
    );
    return;
  }
  panel.appendChild(
    el("p", {
      class: "policy-source",
      text: `${payload.counts.open} open · ${payload.counts.resolved} resolved. Resolving appends; it never edits the verdict above it.`,
    })
  );
  for (const kase of payload.cases) {
    panel.appendChild(caseCard(kase, payload.actions));
  }
}

function caseCard(kase, actions) {
  const card = el("div", {
    class: "case",
    attrs: { "data-status": kase.status },
  });

  card.appendChild(
    el("div", { class: "case-head" }, [
      el("span", { class: "case-id", text: kase.case_id }),
      el("span", { class: "case-status", text: kase.status }),
    ])
  );

  /* The decision under review, exactly as policy computed it. */
  card.appendChild(
    el("div", { class: "envelope" }, [
      el("div", { class: "envelope-action" }, [
        el("span", { text: "ESCALATED" }),
        el("span", { class: "envelope-amount", text: kase.order_id || "—" }),
      ]),
      el("div", { class: "envelope-row envelope-reason", text: kase.reason_code }),
      el("div", {
        class: "envelope-row",
        text: `key ${shortKey(kase.envelope.idempotency_key)} · ${kase.opened_at}`,
      }),
    ])
  );

  const transcript = el("details", { class: "case-transcript" }, [
    el("summary", { text: `conversation (${kase.conversation.length} turns)` }),
  ]);
  for (const message of kase.conversation) {
    transcript.appendChild(
      el("div", { class: `case-turn ${message.role}` }, [
        el("span", { class: "who", text: message.role }),
        el("span", { text: message.text }),
      ])
    );
  }
  card.appendChild(transcript);

  /* Everything that has happened since, newest last. */
  const history = el("ol", { class: "case-events" });
  for (const event of kase.events) {
    const item = el("li", { attrs: { "data-kind": event.kind } }, [
      el("span", { class: "event-kind", text: event.kind.replace(/_/g, " ") }),
      el("span", { class: "event-actor", text: `${event.actor} · ${event.at}` }),
    ]);
    if (event.kind === "resolution") {
      item.appendChild(el("div", { class: "event-action", text: event.action }));
      item.appendChild(el("div", { text: event.justification }));
      item.appendChild(
        el("div", {
          class: "order-id",
          text: `key ${shortKey(event.envelope.idempotency_key)} · supersedes ${shortKey(
            event.envelope.supersedes
          )}`,
        })
      );
    }
    history.appendChild(item);
  }
  card.appendChild(history);

  if (kase.status === "open") {
    card.appendChild(resolveForm(kase, actions));
  }
  return card;
}

function resolveForm(kase, actions) {
  const actor = el("input", {
    attrs: { type: "text", placeholder: "Your name (required)", required: "" },
  });
  const justification = el("textarea", {
    attrs: {
      rows: "2",
      placeholder: "Why (required) — this is the record an auditor reads",
      required: "",
    },
  });
  const choice = el("select", {}, actions.map((action) =>
    el("option", { text: action, attrs: { value: action } })
  ));
  const error = el("p", { class: "form-error" });

  const form = el("form", { class: "resolve" }, [
    el("h4", { text: "Resolve" }),
    choice,
    actor,
    justification,
    el("button", { text: "Record decision", attrs: { type: "submit" } }),
    error,
  ]);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      await Api.resolve(kase.case_id, {
        action: choice.value,
        actor: actor.value,
        justification: justification.value,
      });
      await renderQueue();
      if (state.tab === "audit") await renderAudit();
    } catch (problem) {
      /* The requirement is enforced once, in queue.py. The browser reports
         what the server said rather than keeping its own copy of the rule. */
      error.textContent = String(problem).replace(/^Error:\s*/, "");
    }
  });
  return form;
}

function renderChecks() {
  const panel = dom.panels.checks;
  clear(panel);
  panel.appendChild(
    emptyState("Checks not run yet", "Run the suite from inside the app.")
  );
}

/* --- provider ----------------------------------------------------------- */

function renderProvider() {
  const badge = dom.providerBadge;
  clear(badge);
  if (!state.provider) return;
  const hosted = state.provider.active !== "rules";
  badge.setAttribute("data-hosted", String(hosted));
  badge.appendChild(el("span", { class: "dot" }));
  badge.appendChild(el("span", { text: state.provider.display_name }));
  if (state.provider.model) {
    badge.appendChild(el("span", { class: "key-badge", text: state.provider.model }));
  }
  if (state.provider.key && state.provider.key.masked) {
    badge.appendChild(
      el("span", { class: "key-badge", text: `key ${state.provider.key.masked}` })
    );
  }
}

function notify(message) {
  if (!message) {
    dom.notice.hidden = true;
    dom.notice.textContent = "";
    return;
  }
  dom.notice.hidden = false;
  dom.notice.textContent = message;
}

/* --- interactions ------------------------------------------------------- */

async function send(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  dom.thinking.textContent = "working…";
  state.messages.push({ role: "customer", text });
  renderMessages();
  try {
    const result = await Api.turn(CONVERSATION_ID, text);
    state.messages.push({ role: "agent", text: result.reply });
    state.trace = result.trace;
    state.envelopes.push(...result.envelopes);
    renderMessages();
    renderTrace(true);
    if (state.tab === "audit") await renderAudit();
    notify(null);
  } catch (error) {
    notify(String(error));
  } finally {
    state.busy = false;
    dom.thinking.textContent = "";
  }
}

function setView(view) {
  state.view = view;
  document.body.setAttribute("data-view", view);
  dom.viewCustomer.setAttribute("aria-pressed", String(view === "customer"));
  dom.viewOperator.setAttribute("aria-pressed", String(view === "operator"));
  renderRecord();
}

function setTab(tab) {
  state.tab = tab;
  for (const button of dom.tabs.querySelectorAll("[data-tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  for (const [name, panel] of Object.entries(dom.panels)) {
    panel.hidden = name !== tab;
  }
  if (tab === "audit") renderAudit();
  if (tab === "queue") renderQueue();
  if (tab === "checks") renderChecks();
}

function setRegion(region) {
  state.region = region;
  document.body.setAttribute("data-region", region);
}

/* --- boot --------------------------------------------------------------- */

async function boot() {
  dom.brand = document.getElementById("brand");
  dom.recordBody = document.getElementById("record-body");
  dom.messages = document.getElementById("messages");
  dom.thinking = document.getElementById("thinking");
  dom.composer = document.getElementById("composer");
  dom.message = document.getElementById("message");
  dom.tabs = document.getElementById("tabs");
  dom.notice = document.getElementById("notice");
  dom.providerBadge = document.getElementById("provider-badge");
  dom.viewCustomer = document.getElementById("view-customer");
  dom.viewOperator = document.getElementById("view-operator");
  dom.panels = {
    trace: document.getElementById("panel-trace"),
    audit: document.getElementById("panel-audit"),
    queue: document.getElementById("panel-queue"),
    checks: document.getElementById("panel-checks"),
  };

  dom.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = dom.message.value;
    dom.message.value = "";
    send(text);
  });
  dom.viewCustomer.addEventListener("click", () => setView("customer"));
  dom.viewOperator.addEventListener("click", () => setView("operator"));
  dom.tabs.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-tab]");
    if (tab) setTab(tab.dataset.tab);
  });
  for (const button of document.querySelectorAll(".mobilebar [data-region]")) {
    button.addEventListener("click", () => setRegion(button.dataset.region));
  }
  document.getElementById("reset").addEventListener("click", async () => {
    await Api.reset();
    state.messages = [];
    state.trace = [];
    state.envelopes = [];
    renderMessages();
    renderTrace(false);
    state.provider = await Api.provider();
    renderProvider();
    if (state.tab === "audit") renderAudit();
    notify(null);
  });
  document.getElementById("replay").addEventListener("click", () => {
    notify("Scripted replay arrives with the demo scenarios.");
  });

  try {
    const [customer, provider] = await Promise.all([
      Api.customer(),
      Api.provider(),
    ]);
    state.customer = customer;
    state.provider = provider;
  } catch (error) {
    notify(`Could not reach the console API: ${error}`);
    return;
  }

  clear(dom.brand);
  dom.brand.appendChild(
    el("span", { text: `${state.customer.brand.display_name} Support` })
  );
  dom.brand.appendChild(
    el("span", { class: "descriptor", text: state.customer.brand.descriptor })
  );
  document.title = `${state.customer.brand.display_name} Support Console`;

  /* Open in customer view so it reads as a finished product, then peel it
     open on stage. It also answers the obvious question — do customers see
     this telemetry — before anyone has to ask it. */
  setView("customer");
  setTab("trace");
  renderRecord();
  renderMessages();
  renderTrace(false);
  renderProvider();
}

boot();
