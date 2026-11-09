// The ORIGINAL chat page (frontend/chat.html, unchanged), driven in jsdom against the real backend.
// The page is not modified. The only test-side change is a shorter request timeout in the timeout
// case, applied to the test's own copy of the HTML, so the case finishes in seconds.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const SERVERS = process.env.SERVERS ? JSON.parse(process.env.SERVERS) : null;
const live = (name, fn) => test(name, { skip: SERVERS ? false : "SERVERS not set; run through the Python suite" }, fn);
const PAGE = readFileSync(new URL("../chat.html", import.meta.url), "utf8");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(check, ms = 8000) {
  const start = Date.now();
  while (true) {
    const value = check();
    if (value) return value;
    if (Date.now() - start > ms) throw new Error("waited too long for the page");
    await sleep(25);
  }
}

async function open(baseUrl, { timeoutMs } = {}) {
  // The page sets the timeout in two places (the API defaults and the config). Both are replaced.
  const html = timeoutMs ? PAGE.split("requestTimeoutMs: 60000,").join(`requestTimeoutMs: ${timeoutMs},`) : PAGE;
  const requests = [];
  const dom = new JSDOM(html, {
    url: `${baseUrl}/app/chat.html`,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(window) {
      window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
      window.HTMLElement.prototype.scrollIntoView = () => {};
      // The page calls fetch() with a jsdom AbortSignal; bridge it to Node's fetch and record each request.
      window.fetch = async (url, init = {}) => {
        const { signal, ...rest } = init;
        const controller = new AbortController();
        if (signal) {
          if (signal.aborted) controller.abort();
          else signal.addEventListener("abort", () => controller.abort(), { once: true });
        }
        const entry = { method: rest.method || "GET", url: String(url), body: typeof rest.body === "string" ? JSON.parse(rest.body) : null, status: null };
        requests.push(entry);
        const response = await fetch(new URL(url, baseUrl), { ...rest, signal: controller.signal });
        entry.status = response.status;
        return response;
      };
    },
  });
  const doc = dom.window.document;
  await waitFor(() => doc.getElementById("sendBtn") && doc.getElementById("composerInput"));
  // Let the page finish its first health check, as a user would have by the time they type.
  await sleep(600);
  return { dom, doc, requests, window: dom.window };
}

function send(ui, text) {
  const input = ui.doc.getElementById("composerInput");
  input.value = text;
  input.dispatchEvent(new ui.window.Event("input", { bubbles: true }));
  ui.doc.getElementById("composer").requestSubmit();
}

// Replies only: an error bubble has the same markup, so it is excluded here.
const assistantTexts = (ui) => [...ui.doc.querySelectorAll(".message-assistant:not(.is-error) .message-content")].map((node) => node.textContent.trim());
const lastAssistant = (ui) => assistantTexts(ui).at(-1) ?? null;

function attach(ui, file) {
  const input = ui.doc.getElementById("fileInput");
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  input.dispatchEvent(new ui.window.Event("change", { bubbles: true }));
}

live("a message is sent as JSON to /chat and the reply is shown", async () => {
  const ui = await open(SERVERS.ok);
  send(ui, "hello there");
  const reply = await waitFor(() => lastAssistant(ui) === "hello there" && lastAssistant(ui));
  assert.equal(reply, "hello there");
  const sent = ui.requests.find((r) => r.method === "POST");
  assert.match(sent.url, /\/chat$/);
  assert.deepEqual(Object.keys(sent.body).sort(), ["attachments", "conversation_id", "message", "mode", "model", "tools"]);
  assert.equal(sent.body.mode, "general");
  assert.equal(sent.body.model, "sonet");
  assert.equal(sent.status, 200);
});

live("a follow-up continues the same conversation on the server", async () => {
  const ui = await open(SERVERS.counting);
  send(ui, "one");
  const first = await waitFor(() => lastAssistant(ui) && /messages in context/.test(lastAssistant(ui)) && lastAssistant(ui));
  send(ui, "two");
  const second = await waitFor(() => assistantTexts(ui).length === 2 && assistantTexts(ui)[1]);
  const countOf = (text) => Number(text.match(/(\d+) messages/)[1]);
  assert.ok(countOf(second) > countOf(first), `${second} should see more than ${first}`);
  const posts = ui.requests.filter((r) => r.method === "POST");
  assert.equal(posts[0].body.conversation_id, posts[1].body.conversation_id);
});

live("a server with no route shows its reason, not an object", async () => {
  const ui = await open(SERVERS.unrouted);
  send(ui, "are you there");
  const text = await waitFor(() => ui.doc.querySelector(".message-assistant.is-error")?.textContent.includes("Error") && ui.doc.querySelector(".message-assistant").textContent);
  // The page shows its own text for a 503. The server's reason is not displayed for that status.
  assert.match(text, /temporarily unavailable/);
  assert.doesNotMatch(text, /\[object Object\]/);
});

live("a validation failure shows the field and the rule", async () => {
  const ui = await open(SERVERS.ok);
  attach(ui, new ui.window.File(["hello from notes"], "notes.txt", { type: "text/plain" }));
  await waitFor(() => ui.doc.getElementById("attachmentTray")?.textContent.includes("notes.txt"));
  send(ui, "summarise");
  const text = await waitFor(() => /attachments\.0\.content|content field/.test(ui.doc.querySelector(".message-assistant")?.textContent ?? "") && ui.doc.querySelector(".message-assistant").textContent);
  assert.match(text, /content/);
  assert.doesNotMatch(text, /\[object Object\]/);
  const sent = ui.requests.find((r) => r.method === "POST");
  assert.equal(sent.status, 422);
});

live("nothing is shown as a reply while the server is still working", async () => {
  const ui = await open(SERVERS.slow);
  send(ui, "wait for me");
  await sleep(300);
  assert.equal(assistantTexts(ui).length, 0, "no reply before the server answers");
  const reply = await waitFor(() => lastAssistant(ui) === "slow reply" && lastAssistant(ui));
  assert.equal(reply, "slow reply");
});

live("a dropped connection is reported and the message can be retried", async () => {
  const ui = await open(SERVERS.closed);
  send(ui, "are you there");
  const bubble = await waitFor(() => {
    const node = ui.doc.querySelector(".message-assistant");
    return node && node.querySelector("[data-action='retry-message']") && node;
  });
  assert.ok(bubble.textContent.trim().length > 0);
  assert.equal(assistantTexts(ui).length, 0, "a failed request is never shown as a reply");
});

live("a slow server is reported as a timeout and can be retried", async () => {
  const ui = await open(SERVERS.slow, { timeoutMs: 300 });
  send(ui, "too slow");
  const bubble = await waitFor(() => {
    const node = ui.doc.querySelector(".message-assistant");
    return node && node.querySelector("[data-action='retry-message']") && node;
  }, 4000);
  assert.ok(bubble, "a retry action is offered");
  assert.equal(assistantTexts(ui).length, 0);
});

live("retry sends the same message again and shows the reply", async () => {
  const ui = await open(SERVERS.flaky);
  send(ui, "retry me");
  const retry = await waitFor(() => ui.doc.querySelector(".message-assistant [data-action='retry-message']"));
  retry.click();
  const reply = await waitFor(() => lastAssistant(ui) === "retry me" && lastAssistant(ui));
  assert.equal(reply, "retry me");
  const posts = ui.requests.filter((r) => r.method === "POST");
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[0].body.message, posts[1].body.message);
});

live("reply text is shown as text, never as markup", async () => {
  const ui = await open(SERVERS.ok);
  const payload = "<img src=x onerror=alert(1)>";
  send(ui, payload);
  // The page renders the reply through its Markdown path. Its text may differ from the input, but
  // it must never become an element, and no event handler may survive.
  await waitFor(() => assistantTexts(ui).some((t) => t.includes("img")) && lastAssistant(ui));
  assert.equal(ui.doc.querySelector("img"), null, "no image element is created from reply text");
  assert.equal(ui.doc.querySelector("[onerror]"), null, "no event handler survives");
});
