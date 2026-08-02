/* The back office client.
 *
 * Same two rules as the console, for the same reasons: nothing is handed to
 * an HTML parser, and no threshold is written here. The policy viewer renders
 * whatever policy.py serves and holds no copy of any number.
 *
 * It imports its DOM and fetch helpers — `el`, `clear`, `api`, `money`, `day`,
 * `fact`, `emptyState`, `resolveForm` — from the console's client rather than
 * reimplementing them, so there is exactly one function in this build that
 * puts text on a page and exactly one place a markup sink could appear, and
 * the two clients cannot drift on how they format money, dates, or a resolve.
 */
import {
  el,
  clear,
  api,
  money,
  day,
  fact,
  emptyState,
  resolveForm,
} from "/static/app.js";

// The back office is not space-constrained the way the console's cards are, so
// it shows a longer key tail. That difference is why shortKey is not shared.
const shortKey = (key) => (key ? `${key.slice(0, 12)}…` : "—");

const panels = {};
let surface = "ledger";

/* --- surface 1: the refund ledger --------------------------------------- */

async function renderLedger() {
  const panel = panels.ledger;
  clear(panel);
  let payload;
  try {
    payload = await api("/api/ledger");
  } catch (error) {
    panel.appendChild(emptyState("Could not read the ledger", String(error)));
    return;
  }
  standInNotice(payload.stand_in);

  const summary = payload.summary;
  panel.appendChild(
    el("dl", { class: "facts" }, [
      fact("Lines", String(summary.lines)),
      fact("Suppressed duplicates", String(summary.suppressed_duplicates)),
      fact("Refunds posted", money(summary.amount_posted)),
      fact("Deduplication", summary.durability, true),
    ])
  );

  if (!payload.lines.length) {
    panel.appendChild(
      emptyState(
        "No envelopes received yet",
        "Start the console with BOOKLY_WEBHOOK_URL=http://127.0.0.1:8787/webhook and take a turn that decides something. Replay the same conversation and it arrives as a suppressed duplicate, not a second line."
      )
    );
    return;
  }

  for (const line of payload.lines) {
    const card = el("div", { class: "envelope" }, [
      el("div", { class: "envelope-action" }, [
        el("span", {
          text: (line.resolution || line.action || "")
            .replace(/_/g, " ")
            .toUpperCase(),
        }),
        /* Only a refund has an amount. An escalation or a resolution shows
           the order it concerns rather than a dash where money would be. */
        el("span", {
          class: "envelope-amount",
          text: line.amount === null || line.amount === undefined
            ? line.order_id || ""
            : money(line.amount),
        }),
      ]),
      el("div", { class: "envelope-row", text: line.order_id || "no order" }),
      el("div", {
        class: "envelope-row envelope-reason",
        text: line.reason_code || (line.actor ? `by ${line.actor}` : "—"),
      }),
      el("div", {
        class: "envelope-row",
        text: `key ${shortKey(line.idempotency_key)} · ${line.received_at}`,
      }),
    ]);
    if (line.justification) {
      card.appendChild(el("div", { class: "envelope-row", text: line.justification }));
    }
    /* A repeat is drawn against the line it duplicates, never as a line of
       its own. That is what "posted exactly once" looks like on a screen. */
    if (line.duplicates.length) {
      const list = el("div", { class: "suppressed" }, [
        el("span", {
          class: "suppressed-count",
          text: `${line.duplicates.length} suppressed duplicate${
            line.duplicates.length === 1 ? "" : "s"
          } — same key, not executed again`,
        }),
      ]);
      for (const duplicate of line.duplicates) {
        list.appendChild(
          el("div", {
            class: "order-id",
            text: `${duplicate.at} · envelope ${String(
              duplicate.envelope_id
            ).slice(0, 8)}…`,
          })
        );
      }
      card.appendChild(list);
    }
    panel.appendChild(card);
  }
}

/* --- surface 2: the agent desk ------------------------------------------ */

async function renderDesk() {
  const panel = panels.desk;
  clear(panel);
  let payload;
  try {
    payload = await api("/api/queue");
  } catch (error) {
    panel.appendChild(emptyState("Could not read the queue", String(error)));
    return;
  }
  standInNotice(payload.stand_in);

  if (!payload.cases.length) {
    panel.appendChild(
      emptyState(
        "No cases waiting",
        "Escalations from the console land here. Ask the agent for a manager, or press an out-of-window return until it hands the conversation over."
      )
    );
    return;
  }

  /* A case is addressable: #case-xxxx opens that escalation directly, so
     "here is what the manager receives" is a link you can send someone. */
  const wanted = location.hash.replace(/^#/, "");
  const open = payload.cases.find((c) => c.case_id === wanted);
  if (open) {
    panel.appendChild(caseTicket(open, payload.actions));
    return;
  }

  panel.appendChild(
    el("p", {
      class: "policy-source",
      text: `${payload.counts.open} open · ${payload.counts.resolved} resolved. Open one to see who it is about, what is being escalated, and the conversation up to the handoff.`,
    })
  );
  for (const kase of payload.cases) {
    panel.appendChild(caseRow(kase));
  }
}

/* --- the queue list ------------------------------------------------------ */

/* Reason codes carried by a repeat push, where they differ from the reason
   the case opened for. A push is not always a repeat of the same ask —
   `a_repeated_escalation_is_one_case_not_two` is right that it stays one
   case, but "refund it anyway" and "I want to speak to a manager" landing on
   the same case are two different things a human needs to know happened,
   and the case list is where a reviewer decides which case to open. */
function distinctPushReasons(kase) {
  const opened = kase.reason_code;
  const seen = new Set();
  for (const event of kase.events) {
    if (
      event.kind === "escalation_repeated" &&
      event.reason_code &&
      event.reason_code !== opened
    ) {
      seen.add(event.reason_code);
    }
  }
  return [...seen];
}

function caseRow(kase) {
  const context = kase.context || {};
  const customer = context.customer || {};
  const order = context.order;
  const pushes = kase.events.filter(
    (e) => e.kind === "escalation_repeated"
  ).length;
  const otherReasons = distinctPushReasons(kase);

  return el(
    "button",
    {
      class: "case-row",
      attrs: { type: "button", "data-status": kase.status },
      on: {
        click: () => {
          location.hash = kase.case_id;
          renderDesk();
        },
      },
    },
    [
      el("span", { class: "case-status", text: kase.status }),
      el("span", { class: "case-row-main" }, [
        el("span", { class: "envelope-reason", text: kase.reason_code }),
        el("span", {
          class: "case-row-who",
          text: [
            customer.name || "unknown customer",
            order ? order.title : kase.order_id || "no order named yet",
          ].join(" · "),
        }),
        el("span", {
          class: "order-id",
          text: `${kase.case_id} · opened ${kase.opened_at}${
            pushes ? ` · pushed ${pushes} more time${pushes === 1 ? "" : "s"}` : ""
          }${otherReasons.length ? ` · also: ${otherReasons.join(", ")}` : ""}`,
        }),
      ]),
    ]
  );
}

/* --- one escalation, as the manager receives it --------------------------
   Three questions answered without anyone going looking: who it is about,
   what is being escalated, and what was said. All of it is snapshot and
   record — the page derives nothing and decides nothing. */

function caseTicket(kase, actions) {
  const context = kase.context || {};
  const customer = context.customer || {};
  const order = context.order;
  const policyNote = context.policy;
  const pushes = kase.events.filter(
    (e) => e.kind === "escalation_repeated"
  ).length;
  const otherReasons = distinctPushReasons(kase);

  const ticket = el("article", {
    class: "ticket",
    attrs: { "data-status": kase.status },
  });

  ticket.appendChild(
    el("div", { class: "ticket-bar" }, [
      el("button", {
        text: "← All cases",
        attrs: { type: "button" },
        on: {
          click: () => {
            location.hash = "";
            renderDesk();
          },
        },
      }),
      el("span", { class: "case-id", text: kase.case_id }),
      el("span", { class: "case-status", text: kase.status }),
    ])
  );

  /* What is being escalated, in the policy engine's own words. */
  ticket.appendChild(
    el("header", { class: "ticket-head" }, [
      el("p", { class: "ticket-kicker", text: "Escalated to a human" }),
      el("h2", { class: "ticket-reason", text: kase.reason_code }),
      policyNote
        ? el("p", { class: "ticket-gloss", text: policyNote.gloss })
        : null,
      el("p", {
        class: "order-id",
        text: `opened ${kase.opened_at} · conversation ${
          kase.conversation_id || "—"
        } · key ${shortKey(kase.envelope.idempotency_key)}`,
      }),
      pushes
        ? el("p", {
            class: "ticket-flag",
            text: `The customer pressed this ${pushes} more time${
              pushes === 1 ? "" : "s"
            } after the handoff. One case, not ${pushes + 1}: the escalation carries a single idempotency key.${
              otherReasons.length
                ? ` At least one of those pushes was a different ask: ${otherReasons.join(", ")}.`
                : ""
            }`,
          })
        : null,
    ])
  );

  const grid = el("div", { class: "ticket-grid" });

  /* From whom. */
  const who = el("section", { class: "ticket-panel" }, [
    el("h3", { class: "col-head", text: "From" }),
    el("p", { class: "ticket-name", text: customer.name || "Unknown" }),
    el("p", {
      class: "identity-sub",
      text: [customer.customer_id, customer.tier].filter(Boolean).join(" · "),
    }),
  ]);
  const facts = el("dl", { class: "facts" });
  if (customer.member_since) {
    facts.appendChild(fact("Member since", day(customer.member_since)));
  }
  if (customer.orders_placed !== undefined) {
    facts.appendChild(fact("Orders", String(customer.orders_placed)));
  }
  if (customer.lifetime_value !== undefined) {
    facts.appendChild(fact("Lifetime value", money(customer.lifetime_value)));
  }
  if (customer.csat !== undefined) {
    facts.appendChild(
      fact("CSAT", `${customer.csat} / 5 (${customer.csat_responses})`)
    );
  }
  who.appendChild(facts);
  if (customer.email) {
    who.appendChild(el("p", { class: "order-id", text: customer.email }));
  }
  if ((customer.contact_history || []).length) {
    who.appendChild(el("h4", { class: "ticket-sub", text: "Prior contacts" }));
    for (const contact of customer.contact_history) {
      who.appendChild(
        el("div", { class: "contact" }, [
          el("span", {
            class: "order-id",
            text: `${contact.on} · ${contact.channel}`,
          }),
          el("span", { text: contact.subject }),
          el("span", { class: "identity-sub", text: contact.outcome }),
        ])
      );
    }
  }
  grid.appendChild(who);

  /* What about. */
  const about = el("section", { class: "ticket-panel" }, [
    el("h3", { class: "col-head", text: "About" }),
  ]);
  if (order) {
    about.appendChild(
      el("div", { class: "ticket-order" }, [
        el("img", {
          attrs: {
            src: order.cover.href,
            alt: `${order.title} by ${order.author}`,
            width: "60",
            height: "90",
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
          el("div", {
            class: "order-id",
            text: order.delivered_on
              ? `delivered ${day(order.delivered_on)}`
              : order.eta
              ? `expected ${day(order.eta)}`
              : `ordered ${day(order.ordered_on)}`,
          }),
        ]),
      ])
    );
  } else {
    about.appendChild(
      el("p", {
        class: "identity-sub",
        text: "No order was resolved before the handoff — which is itself why a human is needed.",
      })
    );
  }

  /* The decision under review, and the named constants behind it. */
  about.appendChild(
    el("h4", { class: "ticket-sub", text: "The decision under review" })
  );
  about.appendChild(
    el("div", { class: "envelope" }, [
      el("div", { class: "envelope-action" }, [
        el("span", { text: "ESCALATE TO HUMAN" }),
        el("span", {
          class: "envelope-amount",
          text: order ? money(order.price_paid) : kase.order_id || "",
        }),
      ]),
      el("div", { class: "envelope-row envelope-reason", text: kase.reason_code }),
      el("div", {
        class: "envelope-row",
        text: `computed by ${policyNote ? policyNote.where : "policy.py"}`,
      }),
    ])
  );
  for (const constant of (policyNote && policyNote.constants) || []) {
    about.appendChild(
      el("div", { class: "reason" }, [
        el("div", {
          class: "reason-code",
          text: `${constant.name} = ${constant.value}`,
        }),
        el("div", { text: constant.why }),
      ])
    );
  }
  about.appendChild(
    el("p", {
      class: "policy-source",
      text: "Resolving appends. The verdict above stays exactly as policy.py computed it, whatever you decide.",
    })
  );
  grid.appendChild(about);
  ticket.appendChild(grid);

  /* The background. */
  ticket.appendChild(
    el("h3", { class: "col-head", text: "The conversation up to the handoff" })
  );
  const transcript = el("div", { class: "case-transcript" });
  for (const message of kase.conversation) {
    transcript.appendChild(
      el("div", { class: `case-turn ${message.role}` }, [
        el("span", { class: "who", text: message.role }),
        el("span", { text: message.text }),
      ])
    );
  }
  ticket.appendChild(transcript);

  ticket.appendChild(el("h3", { class: "col-head", text: "Case history" }));
  ticket.appendChild(caseHistory(kase));

  if (kase.status === "open") {
    ticket.appendChild(
      resolveForm(kase, actions, async (caseId, payload) => {
        await api(`/api/queue/${caseId}/resolve`, payload);
        await renderDesk();
      })
    );
  }
  return ticket;
}

/* Every event that carries a reason code shows it, not just `resolution`. A
   repeat push onto an already-open case stays one case, but the reason for
   the push can differ from the reason the case opened for — "refund it
   anyway" and "I want to speak to a manager" both land as
   `escalation_repeated` here, and they are not the same ask. Without this,
   the line read "escalation repeated · agent · <time>" either way, so a
   reviewer had to already suspect a second reason existed to go find it. */
function caseHistory(kase) {
  const history = el("ol", { class: "case-events" });
  for (const event of kase.events) {
    const item = el("li", { attrs: { "data-kind": event.kind } }, [
      el("span", { class: "event-kind", text: event.kind.replace(/_/g, " ") }),
      el("span", { class: "event-actor", text: `${event.actor} · ${event.at}` }),
    ]);
    if (event.reason_code) {
      item.appendChild(
        el("span", { class: "envelope-reason", text: event.reason_code })
      );
    }
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
  return history;
}

/* --- surface 3: the policy editor ----------------------------------------
   The three CX thresholds are authored here — each change validated,
   attributed, and appended to a log the console reads live. The two floors
   that stop a confidently wrong answer are shown but not editable. This is the
   surface earlier builds refused to mock; it is real now. */

async function renderPolicy() {
  const panel = panels.policy;
  clear(panel);
  let payload;
  try {
    payload = await api("/api/policy");
  } catch (error) {
    panel.appendChild(emptyState("Could not read policy", String(error)));
    return;
  }
  standInNotice(payload.stand_in);

  panel.appendChild(
    el("p", { class: "policy-source", text: payload.who_can_change_these })
  );

  panel.appendChild(
    el("h3", { class: "col-head", text: "Authorable thresholds" })
  );
  for (const parameter of payload.parameters || []) {
    panel.appendChild(policyEditor(parameter));
  }

  /* The floors, shown but not editable, and the reason they are not. */
  panel.appendChild(
    el("h3", { class: "col-head", text: "Floors · not authorable" })
  );
  const floor = el("div", { class: "policy-block" });
  floor.appendChild(
    el("div", { class: "policy-row" }, [
      el("span", { class: "policy-name", text: "MIN_KEYWORD_MATCHES" }),
      el("span", {
        class: "policy-value",
        text: String(payload.retrieval_floor),
      }),
    ])
  );
  floor.appendChild(
    el("p", {
      class: "policy-source",
      text: "This floor and the title-word strength stay in policy.py and take an engineer. The point of a floor is that it does not get lowered — lowering it would re-open a confidently wrong answer reaching a customer.",
    })
  );
  panel.appendChild(floor);

  panel.appendChild(el("h3", { class: "col-head", text: "Reason codes" }));
  for (const code of payload.reason_codes) {
    panel.appendChild(
      el("div", { class: "reason" }, [
        el("div", { class: "reason-code", text: code.code }),
        el("div", { text: code.gloss }),
        el("div", {
          class: "order-id",
          text: `computed by ${code.where}${
            code.depends_on.length ? ` · reads ${code.depends_on.join(", ")}` : ""
          }`,
        }),
      ])
    );
  }
}

/* One authorable threshold: its current value (the deterministic side, so
   purple), why it exists, an editor that writes a validated + attributed +
   append-only change, and the history behind it. The value is purple; a
   person changing it is neither side, so the history rows take the neutral
   outline a queue resolution takes. */
function policyEditor(parameter) {
  const card = el("div", { class: "policy-block param" });
  card.appendChild(
    el("div", { class: "policy-row" }, [
      el("span", { class: "policy-name", text: parameter.name }),
      el("span", { class: "policy-value", text: String(parameter.value) }),
    ])
  );
  card.appendChild(el("p", { class: "policy-source", text: parameter.why }));

  const value = el("input", {
    attrs: {
      type: "number",
      value: String(parameter.value),
      min: String(parameter.min),
      max: String(parameter.max),
      step: "1",
      "aria-label": `New value for ${parameter.name}`,
    },
  });
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
  const error = el("p", { class: "form-error" });
  const form = el("form", { class: "resolve" }, [
    el("h4", { text: `Change · allowed ${parameter.min}–${parameter.max}` }),
    value,
    actor,
    justification,
    el("button", { text: "Record change", attrs: { type: "submit" } }),
    error,
  ]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    try {
      await api("/api/policy/change", {
        field: parameter.key,
        value: Number(value.value),
        actor: actor.value,
        justification: justification.value,
      });
      await renderPolicy();
    } catch (problem) {
      /* The rule is enforced once, in policy.change_parameter. The browser
         shows what the server said rather than keeping its own copy. */
      error.textContent = String(problem).replace(/^Error:\s*/, "");
    }
  });
  card.appendChild(form);

  const history = parameter.history || [];
  if (history.length) {
    const details = el("details", { class: "param-history" }, [
      el("summary", {
        text: `history · ${history.length} change${
          history.length === 1 ? "" : "s"
        }`,
      }),
    ]);
    const list = el("ol", { class: "case-events" });
    for (const change of history) {
      list.appendChild(
        el("li", { attrs: { "data-kind": "resolution" } }, [
          el("span", {
            class: "event-action",
            text: `${change.from} → ${change.to}`,
          }),
          el("span", {
            class: "event-actor",
            text: `${change.actor} · ${change.at}`,
          }),
          el("div", { text: change.justification }),
        ])
      );
    }
    details.appendChild(list);
    card.appendChild(details);
  }
  return card;
}

/* --- shell -------------------------------------------------------------- */

function standInNotice(text) {
  const notice = document.getElementById("standin-notice");
  if (text) notice.textContent = text;
}

function setSurface(name) {
  surface = name;
  document.body.setAttribute("data-surface", name);
  for (const button of document.querySelectorAll("#surfaces [data-surface]")) {
    button.setAttribute(
      "aria-selected",
      String(button.dataset.surface === name)
    );
  }
  for (const [key, panel] of Object.entries(panels)) {
    panel.hidden = key !== name;
  }
  render();
}

function render() {
  if (surface === "ledger") return renderLedger();
  if (surface === "desk") return renderDesk();
  return renderPolicy();
}

function boot() {
  /* Browser back and forward move between the queue and a case, because a
     case is a place. */
  window.addEventListener("hashchange", () => {
    if (surface === "desk") renderDesk();
  });

  panels.ledger = document.getElementById("panel-ledger");
  panels.desk = document.getElementById("panel-desk");
  panels.policy = document.getElementById("panel-policy");
  document
    .getElementById("surfaces")
    .addEventListener("click", (event) => {
      const button = event.target.closest("[data-surface]");
      if (button) setSurface(button.dataset.surface);
    });
  document.getElementById("refresh").addEventListener("click", render);
  setSurface(
    location.hash === "#policy"
      ? "policy"
      : location.hash.startsWith("#case-")
      ? "desk"
      : "ledger"
  );
}

boot();
