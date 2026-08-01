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

export async function api(path, body) {
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
  restart: (conversationId) =>
    api("/api/conversation/restart", { conversation_id: conversationId }),
  outbox: () => api("/api/outbox"),
  reconcile: () => api("/api/reconcile", {}),

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

const FREE_CONVERSATION_ID = "conv-console";

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
  checksRunning: false,
  providerOpen: false,
  replaying: false,
  scenarios: null,
  conversationId: FREE_CONVERSATION_ID,
};

const dom = {};

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

/* --- formatting --------------------------------------------------------- */

export const money = (amount) =>
  amount === null || amount === undefined
    ? "—"
    : `$${Number(amount).toFixed(2)}`;

export function day(iso) {
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

export function fact(label, value, wide) {
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
        /* Clicking an order writes the question into the composer rather
           than sending it, so it can be edited before it goes — and so the
           demo can show the turn arriving rather than having already
           happened. The order id is used on purpose: it resolves through the
           explicit-reference rule, which is the one visible in the trace. */
        el("li", {}, [
          el("button", {
            class: "order",
            attrs: {
              type: "button",
              title: `Ask about ${order.title}`,
            },
            on: { click: () => ask(`What's the status of my order ${order.order_id}?`) },
          }, [
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

/* The prompts the console offers come from the profile, not from here, for
   the same reason the scenarios do: re-skinning is a data edit. They are
   wording only — every one goes through the same handle_turn as anything
   typed by hand, and none carries a hint about what the answer should be. */

/* The agent is labelled with the name it introduces itself by, read from
   the profile rather than written here — and still tagged "model", because
   what side of the boundary produced a reply does not change with its name. */
function agentLabel() {
  const agent = (state.customer && state.customer.agent) || {};
  return `${agent.name || "Agent"} · model`;
}

function suggestions() {
  return (state.customer && state.customer.suggestions) || {};
}

function promptButton(text, label) {
  return el("button", {
    text: label || text,
    attrs: { type: "button" },
    on: { click: () => send(text) },
  });
}

function renderOpeners() {
  const panel = el("div", { class: "opener" }, [
    el("h3", { text: "Start a conversation" }),
    el("p", {
      text: "Type anything, or begin with one of these. Operator view opens the glass box on the right.",
    }),
  ]);
  for (const opener of suggestions().openers || []) {
    panel.appendChild(promptButton(opener));
  }
  return panel;
}

/* After a reply, offer what to say next. The offer follows the reason code
   the turn actually produced, so it tracks what happened rather than
   guessing — and it is still only wording. The openers come back alongside
   it so the conversation never dead-ends. */
function nextSteps() {
  const all = suggestions();
  const verdicts = state.trace.filter((note) => note.stage === "verdict");
  const lastCode = verdicts.length
    ? verdicts[verdicts.length - 1].payload.reason_code
    : null;
  const followUps =
    (lastCode && (all.after || {})[lastCode]) || all.fallback || [];

  const strip = el("div", { class: "next-steps" });
  strip.appendChild(
    el("p", {
      class: "next-label",
      text: lastCode
        ? `Next, after ${lastCode}`
        : "Next",
    })
  );
  const asked = new Set(
    state.messages.filter((m) => m.role === "customer").map((m) => m.text)
  );
  const offered = [];
  for (const text of [...followUps, ...(all.openers || [])]) {
    if (offered.includes(text) || asked.has(text)) continue;
    offered.push(text);
    strip.appendChild(promptButton(text));
  }
  return offered.length ? strip : null;
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
          text: message.role === "customer" ? "Customer" : agentLabel(),
        }),
        el("span", { text: message.text }),
      ])
    );
  }
  /* Only after the agent has spoken, and never mid-replay — a scripted
     conversation should not grow controls halfway through. */
  const last = state.messages[state.messages.length - 1];
  if (last && last.role === "agent" && !state.replaying) {
    const strip = nextSteps();
    if (strip) dom.messages.appendChild(strip);
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

/* Write a question into the composer and focus it, rather than sending it.
   The turn should be something you watched arrive, not something that had
   already happened by the time you looked. */
function ask(text) {
  dom.message.value = text;
  dom.message.focus();
  dom.message.setSelectionRange(text.length, text.length);
  if (state.region !== "conversation") setRegion("conversation");
}

/* An empty state that says what to do next is better than one that
   apologises — and one that will do it for you is better still. `action`,
   when given, is the turn that fills this surface. */
export function emptyState(title, hint, action) {
  const panel = el("div", { class: "empty" }, [
    el("strong", { text: title }),
    el("span", { text: hint }),
  ]);
  if (action) {
    panel.appendChild(
      el("button", {
        text: action.label,
        attrs: { type: "button" },
        on: { click: () => send(action.text) },
      })
    );
  }
  return panel;
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
        "Only a turn that decides something writes here — a lookup does not. Every emitted envelope is written to audit.log before the network hop.",
        { label: "Ask for a refund", text: "I want to return the Escher book" }
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
        "Only an escalation opens a case: an explicit ask for a person, a disputed denial, or a clarifying question that ran out of attempts.",
        { label: "Ask for a manager", text: "I want to speak to a manager" }
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
    card.appendChild(
      resolveForm(kase, actions, async (caseId, payload) => {
        await Api.resolve(caseId, payload);
        await renderQueue();
        if (state.tab === "audit") await renderAudit();
      })
    );
  }
  return card;
}

/* The resolve form is shared with the back office (imported from here). Only
   the post-submit action differs — the console re-renders its own queue, the
   back office its desk — so that is the one thing passed in. The requirement
   is enforced once, in queue.py; the browser reports what the server said and
   keeps no copy of the rule. */
export function resolveForm(kase, actions, onSubmit) {
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
      await onSubmit(kase.case_id, {
        action: choice.value,
        actor: actor.value,
        justification: justification.value,
      });
    } catch (problem) {
      error.textContent = String(problem).replace(/^Error:\s*/, "");
    }
  });
  return form;
}

/* --- checks: the suite, run from inside the app -------------------------- */

const CHECK_LINE = /^(ok|FAIL)\s+(\S+)/;
const SUMMARY_LINE = /^(\d+) passed, (\d+) failed/;

function renderChecks() {
  const panel = dom.panels.checks;
  clear(panel);

  const output = el("div", { class: "checks-output" });
  const summary = el("p", { class: "checks-summary" });
  const run = el("button", {
    text: state.checksRunning ? "Running…" : "Run the check suite",
    attrs: { type: "button", ...(state.checksRunning ? { disabled: "" } : {}) },
  });

  run.addEventListener("click", async () => {
    if (state.checksRunning) return;
    state.checksRunning = true;
    run.disabled = true;
    run.textContent = "Running…";
    clear(output);
    summary.textContent = "";
    summary.removeAttribute("data-state");
    try {
      /* Streamed, so the first result appears while the rest are still
         running. A button that spins for ten seconds proves nothing. */
      for await (const line of Api.checks()) {
        const match = CHECK_LINE.exec(line);
        const row = el("div", {
          class: "check-line",
          text: line,
          attrs: match ? { "data-result": match[1] } : {},
        });
        output.appendChild(row);
        output.scrollTop = output.scrollHeight;
        const done = SUMMARY_LINE.exec(line);
        if (done) {
          summary.textContent = line;
          summary.setAttribute(
            "data-state",
            Number(done[2]) === 0 ? "pass" : "fail"
          );
        }
      }
    } catch (error) {
      summary.textContent = String(error);
      summary.setAttribute("data-state", "fail");
    } finally {
      state.checksRunning = false;
      run.disabled = false;
      run.textContent = "Run the check suite again";
    }
  });

  panel.appendChild(
    el("p", {
      class: "policy-source",
      text: "tests.py, run in a subprocess with this interpreter, no shell, and an environment with every vendor key stripped out. The same suite the repo ships; nothing here is a separate copy.",
    })
  );
  panel.appendChild(run);
  panel.appendChild(summary);
  panel.appendChild(output);
  panel.appendChild(renderParity());
}

/* --- provider parity ----------------------------------------------------
   The claim is that the provider changes the wording and not the decision.
   This shows it for whatever has actually happened in this session: every
   envelope emitted, tagged with the provider that phrased the reply around
   it. Switch provider mid-conversation, ask the same thing again, and the
   idempotency key is the same value in both rows. */

function renderParity() {
  const block = el("div", { class: "parity" }, [
    el("h3", { class: "col-head", text: "Provider parity" }),
  ]);
  if (!state.envelopes.length) {
    block.appendChild(
      emptyState(
        "Nothing emitted yet",
        "Take a turn that decides something, switch provider from the badge above, and ask again. The wording changes; the idempotency key does not."
      )
    );
    return block;
  }
  const providers = new Set(state.envelopes.map((e) => e.provider));
  block.appendChild(
    el("p", {
      class: "policy-source",
      text:
        providers.size > 1
          ? `${providers.size} providers used this session. Matching keys below were produced by different models.`
          : "One provider so far. Switch from the badge above and repeat a request to compare.",
    })
  );
  for (const entry of state.envelopes) {
    const e = entry.envelope;
    block.appendChild(
      el("div", { class: "parity-row" }, [
        el("span", { class: "parity-provider", text: entry.provider }),
        el("span", {
          class: "parity-decision",
          text: `${e.action} · ${e.order_id || "—"} · ${money(e.amount)} · ${
            e.reason_code
          }`,
        }),
        el("span", { class: "parity-key", text: e.idempotency_key }),
      ])
    );
  }
  return block;
}

/* --- the provider control ------------------------------------------------
   A key is held in memory for the session, shown only as a badge, and never
   written anywhere. Switching mid-conversation is allowed and is itself the
   demonstration. */

function renderProviderPanel() {
  const panel = dom.providerPanel;
  clear(panel);
  if (!state.providerOpen || !state.provider) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const key = el("input", {
    attrs: {
      type: "password",
      placeholder: "Paste an API key here first, then pick a provider below",
      autocomplete: "off",
      spellcheck: "false",
    },
  });
  const message = el("p", { class: "form-error" });
  const choices = el("div", { class: "provider-choices" });
  const buttons = [];

  async function choose(name) {
    /* Switching now makes one real call to the provider before committing,
       so a wrong key or a renamed model surfaces here instead of on the
       first turn of a conversation. That takes a moment, and the button
       says so rather than appearing to have ignored the click. */
    message.textContent = "";
    message.removeAttribute("data-state");
    for (const button of buttons) button.disabled = true;
    const chosen = buttons.find((b) => b.dataset.provider === name);
    const label = chosen.textContent;
    chosen.textContent = "checking…";
    try {
      const result = await Api.setProvider(name, key.value || null);
      state.provider = result;
      if (result.ok) key.value = "";
      renderProvider();
      renderProviderPanel();
      if (!result.ok) {
        const panelMessage = document.querySelector(
          "#provider-panel .form-error"
        );
        if (panelMessage) {
          panelMessage.textContent = result.message;
          panelMessage.setAttribute("data-state", "fail");
        }
      }
    } catch (error) {
      chosen.textContent = label;
      for (const button of buttons) button.disabled = false;
      message.textContent = String(error).replace(/^Error:\s*/, "");
      message.setAttribute("data-state", "fail");
    }
  }

  for (const name of state.provider.available) {
    const active = name === state.provider.active;
    const button = el("button", {
      text: name,
      attrs: {
        type: "button",
        "aria-pressed": String(active),
        "data-provider": name,
      },
      on: { click: () => choose(name) },
    });
    buttons.push(button);
    choices.appendChild(button);
  }

  /* Enter in the key field applies it to the first hosted provider, so a
     paste-and-return works without hunting for the right button. */
  key.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const hosted = state.provider.hosted[0];
    if (hosted) choose(hosted);
  });

  panel.appendChild(el("h4", { text: "Model provider" }));
  /* The key field comes before the buttons because that is the order it has
     to be used in: the key is read at the moment a provider is chosen. It
     used to sit underneath them, which made the first click look ignored. */
  panel.appendChild(key);
  panel.appendChild(choices);
  panel.appendChild(message);
  panel.appendChild(
    el("p", {
      class: "policy-source",
      text: "Picking a hosted provider makes one small call to check the key, the model name and the network before switching, so a broken setup surfaces here rather than mid-conversation. The key is held on one object in the server's memory and shown only as a badge — never written to disk, never logged, never put in a URL, and never exported to the environment, because the check suite runs in a subprocess and an exported key would ride into it.",
    })
  );
  const environment = state.provider.environment_keys;
  if (environment.length) {
    panel.appendChild(
      el("p", {
        class: "policy-source",
        text: `Keys found in the environment: ${environment.join(", ")}. Those are used automatically; you do not need to paste one.`,
      })
    );
  }
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

/* The delivery outbox: envelopes a failed hop deferred, waiting to be retried.
   The Reconcile button appears with a count only when something is pending, so
   in the ordinary demo (no receiver configured, nothing to defer) it stays out
   of the way. Reconcile hands each pending envelope back to the receiver, which
   dedups on the idempotency key — so it posts nothing twice. */
async function refreshOutbox() {
  if (!dom.reconcile) return;
  let counts;
  try {
    counts = (await Api.outbox()).counts;
  } catch (error) {
    return;
  }
  const pending = counts.pending || 0;
  const dead = counts.dead_lettered || 0;
  dom.reconcile.hidden = pending === 0 && dead === 0;
  dom.reconcile.textContent = pending ? `Reconcile (${pending})` : "Reconcile";
  dom.reconcile.disabled = pending === 0;
}

/* --- scripted replay -----------------------------------------------------
   A recording is the leave-behind, and a live demo can always fail. Replay
   makes both survivable: it plays a scripted conversation into the real UI,
   through the real API, at a readable pace.

   Deliberately no randomness in the cadence. A replay that varies is not
   reproducible, and reproducibility is the whole reason this exists. */

const TYPE_TICK_MS = 26;
/* Long turns type in chunks so the whole line lands in a bounded number of
   ticks. Two reasons, and the second is the one that bit: a 110-character
   injection string is dead air at one character a tick, and a browser that
   has backgrounded the tab clamps timers to about a second, which turns that
   dead air into a stall. Bounding the ticks makes the cadence readable and
   the replay robust to a tab that is not in front. */
const MAX_TYPE_TICKS = 34;
const PAUSE_BETWEEN_TURNS_MS = 1100;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function typeInto(input, text) {
  input.value = "";
  if (reducedMotion.matches) {
    input.value = text;
    return;
  }
  const chunk = Math.max(1, Math.ceil(text.length / MAX_TYPE_TICKS));
  for (let at = 0; at < text.length; at += chunk) {
    if (!state.replaying) return;
    input.value = text.slice(0, at + chunk);
    await wait(TYPE_TICK_MS);
  }
}

async function toggleReplay() {
  if (state.replaying) {
    state.replaying = false;
    notify("Replay stopped.");
    return;
  }
  if (!state.scenarios) {
    try {
      state.scenarios = (await Api.scenarios()).scenarios;
    } catch (error) {
      notify(String(error));
      return;
    }
  }
  renderReplayPanel();
}

function renderReplayPanel() {
  const panel = dom.replayPanel;
  clear(panel);
  panel.hidden = false;
  panel.appendChild(el("h4", { text: "Scripted replay" }));
  panel.appendChild(
    el("p", {
      class: "policy-source",
      text: "Played into the real interface through the real API at a fixed cadence — nothing is pre-recorded, and the decisions are computed live. The four numbered scenarios are read from demo.txt, so the CLI script and the console cannot drift.",
    })
  );
  const list = el("div", { class: "replay-list" });
  for (const scenario of state.scenarios) {
    list.appendChild(
      el("button", {
        text: `${scenario.title} · ${scenario.turns.length} turns`,
        attrs: { type: "button" },
        on: {
          click: () => {
            panel.hidden = true;
            runReplay(scenario);
          },
        },
      })
    );
  }
  panel.appendChild(list);
  panel.appendChild(
    el("button", {
      text: "Close",
      attrs: { type: "button" },
      on: { click: () => (panel.hidden = true) },
    })
  );
}

async function runReplay(scenario) {
  state.replaying = true;
  dom.replay.textContent = "Stop replay";
  notify(`Replaying: ${scenario.title}`);
  /* Each scenario is its own conversation, the same way `---` starts a new
     Agent in demo.txt. The id is derived from the scenario rather than being
     random, so replaying it twice produces the same idempotency keys — and
     with the back office running, the second run lands as suppressed
     duplicates rather than a second write. */
  state.conversationId = `conv-${scenario.id}`;
  state.messages = [];
  state.trace = [];
  renderMessages();
  renderTrace(false);
  try {
    await Api.restart(state.conversationId);
    for (const turn of scenario.turns) {
      if (!state.replaying) break;
      await typeInto(dom.message, turn);
      if (!state.replaying) break;
      dom.message.value = "";
      await send(turn);
      await wait(PAUSE_BETWEEN_TURNS_MS);
    }
  } finally {
    state.replaying = false;
    state.conversationId = FREE_CONVERSATION_ID;
    dom.replay.textContent = "Replay";
    dom.message.value = "";
    if (!dom.notice.hidden) notify(null);
  }
}

/* --- interactions ------------------------------------------------------- */

async function send(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  dom.thinking.textContent = "working…";
  state.messages.push({ role: "customer", text });
  renderMessages();
  try {
    const result = await Api.turn(state.conversationId, text);
    state.messages.push({ role: "agent", text: result.reply });
    state.trace = result.trace;
    // Tagged at emission, so the parity view can show that two providers
    // produced the same key.
    state.envelopes.push(
      ...result.envelopes.map((entry) => ({ ...entry, provider: result.provider }))
    );
    renderMessages();
    renderTrace(true);
    if (state.tab === "audit") await renderAudit();
    notify(null);
    // A turn may have emitted an envelope whose delivery failed and is now
    // waiting in the outbox; surface it on the Reconcile button.
    refreshOutbox();
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
  dom.providerPanel = document.getElementById("provider-panel");
  dom.replayPanel = document.getElementById("replay-panel");
  dom.replay = document.getElementById("replay");
  dom.reconcile = document.getElementById("reconcile");
  dom.reconcile.addEventListener("click", async () => {
    dom.reconcile.disabled = true;
    try {
      const result = await Api.reconcile();
      const parts = [`${result.delivered.length} delivered`];
      if (result.dead_lettered.length) {
        parts.push(`${result.dead_lettered.length} dead-lettered`);
      }
      if (result.counts.pending) {
        parts.push(`${result.counts.pending} still pending`);
      }
      notify(`Reconciled: ${parts.join(", ")}.`);
      if (state.tab === "audit") await renderAudit();
    } catch (error) {
      notify(String(error));
    } finally {
      await refreshOutbox();
    }
  });
  dom.providerBadge.addEventListener("click", () => {
    state.providerOpen = !state.providerOpen;
    renderProviderPanel();
  });
  dom.viewCustomer = document.getElementById("view-customer");
  dom.viewOperator = document.getElementById("view-operator");
  dom.panels = {
    trace: document.getElementById("panel-trace"),
    audit: document.getElementById("panel-audit"),
    queue: document.getElementById("panel-queue"),
    checks: document.getElementById("panel-checks"),
  };

  /* Boot is async: it fetches the record and the provider state before any
     of these controls have anything to act on. Clicking during that window
     used to do nothing at all, which reads as a broken button rather than a
     page that is still loading. */
  const gated = [
    dom.providerBadge, dom.replay, dom.viewCustomer, dom.viewOperator,
    document.getElementById("reset"),
  ];
  for (const control of gated) control.disabled = true;

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
    state.replaying = false;
    dom.replayPanel.hidden = true;
    await Api.reset();
    state.messages = [];
    state.trace = [];
    state.envelopes = [];
    state.conversationId = FREE_CONVERSATION_ID;
    renderMessages();
    renderTrace(false);
    state.provider = await Api.provider();
    state.providerOpen = false;
    renderProvider();
    renderProviderPanel();
    if (state.tab === "audit") renderAudit();
    notify(null);
    refreshOutbox();
  });
  document.getElementById("replay").addEventListener("click", toggleReplay);

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
  for (const control of gated) control.disabled = false;

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
  /* The page was just reloaded, so the transcript on screen is empty. The
     server's conversation must agree, or the agent is still holding a
     pending question nobody can see. */
  try {
    await Api.restart(FREE_CONVERSATION_ID);
  } catch (error) {
    notify(String(error));
  }

  setView("customer");
  setTab("trace");
  renderProviderPanel();
  renderRecord();
  renderMessages();
  renderTrace(false);
  renderProvider();
  refreshOutbox();
}

/* The back office imports `el` and `clear` from this module so there is
   exactly one function in the build that puts text on a page — and therefore
   exactly one place a markup sink could ever appear. That import must not
   also start the console, so booting is conditional on the console's own
   shell being the page that loaded us. */
if (document.getElementById("composer")) boot();
