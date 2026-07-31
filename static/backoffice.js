/* The back office client.
 *
 * Same two rules as the console, for the same reasons: nothing is handed to
 * an HTML parser, and no threshold is written here. The policy viewer renders
 * whatever policy.py serves and holds no copy of any number.
 *
 * It imports `el` and `clear` from the console's client rather than
 * reimplementing them, so there is exactly one function in this build that
 * puts text on a page and exactly one place a markup sink could appear.
 */
import { el, clear } from "/static/app.js";

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

const money = (amount) =>
  amount === null || amount === undefined
    ? "—"
    : `$${Number(amount).toFixed(2)}`;

const shortKey = (key) => (key ? `${key.slice(0, 12)}…` : "—");

const panels = {};
let surface = "ledger";

function emptyState(title, hint) {
  return el("div", { class: "empty" }, [
    el("strong", { text: title }),
    el("span", { text: hint }),
  ]);
}

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
      factOf("Lines", String(summary.lines)),
      factOf("Suppressed duplicates", String(summary.suppressed_duplicates)),
      factOf("Refunds posted", money(summary.amount_posted)),
      factOf("Deduplication", summary.durability, true),
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

function factOf(label, value, wide) {
  return el("div", { class: wide ? "fact wide" : "fact" }, [
    el("dt", { text: label }),
    el("dd", { text: value }),
  ]);
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
        "Escalations from the console land here. Press an out-of-window return in the console until the agent hands it to a human."
      )
    );
    return;
  }
  panel.appendChild(
    el("p", {
      class: "policy-source",
      text: `${payload.counts.open} open · ${payload.counts.resolved} resolved. A resolution is appended below the verdict it reviews; the verdict itself is never edited.`,
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

  const transcript = el("div", { class: "case-transcript" });
  for (const message of kase.conversation) {
    transcript.appendChild(
      el("div", { class: `case-turn ${message.role}` }, [
        el("span", { class: "who", text: message.role }),
        el("span", { text: message.text }),
      ])
    );
  }
  card.appendChild(transcript);

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

  if (kase.status === "open") card.appendChild(resolveForm(kase, actions));
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
      await api(`/api/queue/${kase.case_id}/resolve`, {
        action: choice.value,
        actor: actor.value,
        justification: justification.value,
      });
      await renderDesk();
    } catch (problem) {
      error.textContent = String(problem).replace(/^Error:\s*/, "");
    }
  });
  return form;
}

/* --- surface 3: the policy viewer, read only ----------------------------- */

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

  /* Read only, deliberately. There is no edit control anywhere on this
     screen, and the line below says who can change these and where. */
  panel.appendChild(
    el("p", { class: "policy-source", text: payload.who_can_change_these })
  );

  panel.appendChild(el("h3", { class: "col-head", text: "Thresholds" }));
  const block = el("div", { class: "policy-block" });
  for (const constant of payload.constants) {
    block.appendChild(
      el("div", { class: "policy-row" }, [
        el("span", { class: "policy-name", text: constant.name }),
        el("span", { class: "policy-value", text: String(constant.value) }),
      ])
    );
    block.appendChild(el("p", { class: "policy-source", text: constant.why }));
  }
  block.appendChild(
    el("div", { class: "policy-row" }, [
      el("span", { class: "policy-name", text: "MIN_KEYWORD_MATCHES" }),
      el("span", { class: "policy-value", text: String(payload.retrieval_floor) }),
    ])
  );
  panel.appendChild(block);

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
  setSurface("ledger");
}

boot();
