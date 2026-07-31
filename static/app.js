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
