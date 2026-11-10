const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "../plugins/codex-task-routing/scripts/chat_browser.js"), "utf8",
);
const listening = {
  event: "listening", workflow: true, needs_tools: false,
  request_id: "fcaa17f0-46c1-4c18-a4bd-96b26e615d57",
  input_sha256: "9".repeat(64),
  url: "http://127.0.0.1:8080/transfer/fixture",
};
const reply = {
  request_id: listening.request_id, input_sha256: listening.input_sha256,
  status: "completed", result: "Verified answer", evidence: [],
};

function control({ present = () => true, text = () => "", attribute = () => null,
  click, fill, wait } = {}) {
  const locator = {
    count: async () => Number(present()),
    innerText: async () => text(),
    getAttribute: async name => attribute(name),
    evaluate: async read => read({
      value: text(), innerText: text(),
      getAttribute: key => key === "aria-label" ? locator.name : attribute(key),
    }),
    async waitFor() {
      if (wait) await wait();
      assert.equal(present(), true);
    },
    async click() {
      assert.equal(present(), true);
      await click();
    },
    fill,
    filter: () => locator,
    first: () => locator,
    or: () => locator,
  };
  return locator;
}

function fixture({ limited = false, ambiguousClick = false, wrongSavedHash = false } = {}) {
  const run = vm.runInNewContext(source + "\nrunProHandoff;", { URL });
  const state = {
    temporary: false, menu: false, power: 3, composer: "", sent: 0, ready: false,
    waits: 0, saved: null, handoff: false, phases: [], opened: [], closed: [],
  };
  const model = () => state.power === 4 ? "Pro" : "Extra High";
  const chatButtons = {
    Chat: control({ attribute: () => "true" }),
    Work: control({ attribute: () => "false" }),
    "一時チャット": control({ present: () => !state.temporary, click: () => { state.temporary = true; } }),
    "一時チャットをオフにする": control({ present: () => state.temporary }),
    "ChatGPT モデルを選択": control({
      text: model, attribute: () => String(state.menu), click: () => { state.menu = true; },
    }),
    送信: control({
      click() {
        assert.equal(state.composer, "approved\nmultiline");
        assert.equal(model(), "Pro");
        assert.equal(state.handoff, true);
        state.sent++;
        if (ambiguousClick) throw new Error("No click acknowledgement");
      },
    }),
  };
  const final = control({ present: () => state.ready, wait: () => {
    assert.equal(state.sent, 1);
    state.waits++;
    state.ready = true;
  } });
  const message = control({ present: () => state.ready, text: () => JSON.stringify(reply) });
  message.locator = () => control({ present: () => false });
  const article = control({ present: () => state.ready });
  article.getByRole = () => final;
  article.locator = () => message;
  const chat = {
    evaluate: async () => ({
      model: model(), current: state.power, maximum: 4,
      powerFocused: state.menu, limited: limited && model() === "Pro", locked: false,
    }),
    locator: selector => selector === "main article" ? article : control({ present: () => false }),
    getByRole(role, { name } = {}) {
      if (role === "menu") return control({ present: () => state.menu });
      if (role === "textbox") return control({
        text: () => state.composer, fill: text => { state.composer = text; },
      });
      const label = Object.keys(chatButtons).find(key => typeof name === "string" ? name === key : name?.test(key));
      if (label) chatButtons[label].name = label;
      return chatButtons[label] ?? control({ present: () => false });
    },
  };
  let live;
  let envelope;
  let submitted;
  const localFields = {
    "Request prompt": control({ text: () => "approved\nmultiline" }),
    "Live observation": control({ fill: text => { live = JSON.parse(text); } }),
    "Preflight result": control({ text: () => JSON.stringify(envelope) }),
    "Response JSON": control({ fill: text => { submitted = JSON.parse(text); } }),
    "Saved result": control({ present: () => !!state.saved, text: () => JSON.stringify(state.saved) }),
  };
  const local = {
    getByLabel: label => localFields[label],
    expectNavigation: action => action(),
    getByRole: (role, { name }) => control({ click() {
      if (name === "Save verified response") {
        assert.deepEqual(submitted, reply);
        state.saved = { ...reply, event: "saved", input_sha256: wrongSavedHash ? "0".repeat(64) : reply.input_sha256 };
        delete state.saved.result;
        delete state.saved.evidence;
        return;
      }
      assert.equal(live.model, "Pro");
      assert.equal(live.surface, "chat-temporary");
      const dispatch = name === "Check dispatch";
      const phase = dispatch ? "dispatch" : "receive";
      state.phases.push(phase);
      const plan = {
        route: dispatch ? "chat_candidate" : "chat_collection",
        next_action: dispatch ? "send_once" : "collect_reply",
        live_checked: true, preflight_complete: true, transport: "browser-temporary",
        request_id: reply.request_id, input_sha256: reply.input_sha256,
      };
      envelope = { phase, request_id: reply.request_id, input_sha256: reply.input_sha256, plan };
    } }),
  };
  const cua = {
    async createBrowserTab(browser, target, options) {
      assert.equal(browser, "iab");
      assert.equal(target, "about:blank");
      assert.equal(options.visible, false);
      const id = String(state.opened.length + 1);
      state.opened.push(id);
      let url = target;
      return {
        id, playwright: id === "1" ? chat : local,
        url: async () => id === "1" && state.temporary
          ? (state.sent ? "https://chatgpt.com/c/fixture" : "https://chatgpt.com/?temporary-chat=true")
          : url,
        goto: async next => { url = next; },
        async pressKey(_, key) {
          assert.equal(state.menu, true);
          if (key === "Escape") state.menu = false;
          else state.power += key === "Right" ? 1 : -1;
        },
        close: async () => { state.closed.push(id); },
        markHandoff: async () => { state.handoff = true; },
      };
    },
    listTabs: async () => state.opened.filter(id => !state.closed.includes(id)).map(id => ({ id })),
  };
  return { run: () => run({ cua, listening, timeoutMs: 1000 }), state };
}

test("one run opens hidden tabs, sends once, waits, and returns the saved reply", async () => {
  const { run, state } = fixture();
  const result = await run();
  assert.equal(result.event, "saved");
  assert.equal(result.completion, "reply_ready");
  assert.equal(result.reply.result, reply.result);
  assert.deepEqual(state.phases, ["dispatch", "receive"]);
  assert.equal(state.sent, 1);
  assert.equal(state.waits, 1);
  assert.equal(result.cleanup.chat_closed && result.cleanup.transfer_closed, true);
});

test("a Pro quota lock stops before opening the transfer form", async () => {
  const { run, state } = fixture({ limited: true });
  const result = await run();
  assert.equal(result.event, "blocked");
  assert.equal(result.reason, "quota_exhausted");
  assert.equal(state.power, 3);
  assert.equal(state.sent, 0);
  assert.deepEqual(state.opened, ["1"]);
  assert.equal(result.cleanup.chat_closed, true);
});

test("an unacknowledged click retains the same Chat and a second run cannot resend", async () => {
  const { run, state } = fixture({ ambiguousClick: true });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(result.tab_id, "1");
  assert.equal(state.handoff, true);
  assert.equal((await run()).reason, "already_started");
  assert.equal(state.sent, 1);
});

test("a mismatched save acknowledgement does not return the answer body", async () => {
  const { run } = fixture({ wrongSavedHash: true });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(result.reason, "save_unconfirmed");
  assert.equal(result.reply, undefined);
});
