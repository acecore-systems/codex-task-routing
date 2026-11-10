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
    async waitFor(options = {}) {
      if (wait) await wait(options);
      assert.equal(present(), options.state !== "hidden");
    },
    async click() {
      assert.equal(present(), true);
      await click();
    },
    fill,
    filter: () => locator,
    first: () => locator,
    or(other) {
      const left = this;
      const union = {
        count: async () => await left.count() + await other.count(),
        async waitFor(options) {
          if (!await union.count()) await left.waitFor(options);
        },
        filter: () => union,
        first: () => union,
        or: locator.or,
      };
      return union;
    },
  };
  return locator;
}

function fixture({ limited = false, ambiguousClick = false, wrongSavedHash = false,
  quotaAfterReply = false, quotaBeforeReply = false, runningAtQuota = false, changedModel = false,
  timeoutMs = 1_000, waitTimeouts = 0, stopWaitTimeouts = 0, waitDenied = false } = {}) {
  let now = 0;
  class FixtureDate extends Date {
    static now() { return now; }
  }
  const run = vm.runInNewContext(source + "\nrunProHandoff;", { URL, Date: FixtureDate });
  const state = {
    temporary: false, menu: false, power: 3, composer: "", sent: 0, ready: false,
    waits: 0, saved: null, handoff: false, phases: [], quotas: [], opened: [], closed: [],
    replyWaits: [], stopWaits: [], generationEnded: false,
  };
  const model = () => state.power === 4 && !(changedModel && state.sent) ? "Pro" : "Extra High";
  const quotaVisible = () => (quotaAfterReply && state.ready) || (quotaBeforeReply && state.sent > 0);
  const chatButtons = {
    Chat: control({ attribute: () => "true" }),
    Work: control({ attribute: () => "false" }),
    "一時チャット": control({ present: () => !state.temporary, click: () => { state.temporary = true; } }),
    "一時チャットをオフにする": control({ present: () => state.temporary }),
    "ChatGPT モデルを選択": control({
      text: model, attribute: () => String(state.menu), click: () => { state.menu = true; },
    }),
    Stop: control({
      present: () => (runningAtQuota && state.sent > 0 && !state.ready) ||
        (stopWaitTimeouts > 0 && state.ready && !state.generationEnded),
      wait: ({ state: target, timeoutMs }) => {
        assert.equal(target, "hidden");
        state.stopWaits.push(timeoutMs);
        if (state.stopWaits.length <= stopWaitTimeouts) {
          now += timeoutMs;
          throw new Error(`Timed out after ${timeoutMs}ms waiting for selector Stop`);
        }
        state.generationEnded = true;
      },
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
  const final = control({ present: () => state.ready, wait: ({ timeoutMs }) => {
    assert.equal(state.sent, 1);
    state.waits++;
    state.replyWaits.push(timeoutMs);
    if (waitDenied) throw new Error("Browser permission denied");
    if (state.waits <= waitTimeouts) {
      now += timeoutMs;
      throw new Error("Playwright selector deadline exceeded");
    }
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
      powerFocused: state.menu, limited: (limited || quotaVisible()) && model() === "Pro",
      locked: quotaVisible() && model() === "Pro",
    }),
    locator: selector => selector === "main article" ? article : control({ present: () => false }),
    getByRole(role, { name } = {}) {
      if (role === "menu") return control({ present: () => state.menu });
      if (role === "alert") return { filter: ({ hasText }) => control({
        present: () => quotaVisible() && hasText.test("Pro usage limit reached"),
      }) };
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
      state.quotas.push(live.quota_state);
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
  return { run: () => run({ cua, listening, timeoutMs }), state };
}

test("one run opens hidden tabs, sends once, waits, and returns the saved reply", async () => {
  const { run, state } = fixture();
  const result = await run();
  assert.equal(result.event, "saved");
  assert.equal(result.completion, "reply_ready");
  assert.equal(result.quota_state, "no_limit_notice");
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

test("the last Pro message is still saved when its completion and the quota notice coexist", async () => {
  const { run, state } = fixture({ quotaAfterReply: true });
  const result = await run();
  assert.equal(result.event, "saved");
  assert.equal(result.quota_state, "exhausted");
  assert.equal(result.reply.result, reply.result);
  assert.deepEqual(state.quotas, ["no_limit_notice", "exhausted"]);
  assert.equal(state.sent, 1);
});

test("a quota notice without a response or active generation returns pending promptly", async () => {
  const { run, state } = fixture({ quotaBeforeReply: true });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(result.reason, "quota_exhausted");
  assert.equal(result.tab_id, "1");
  assert.equal(state.waits, 0);
  assert.equal(state.sent, 1);
});

test("a quota notice during active generation waits for the same answer", async () => {
  const { run, state } = fixture({ quotaBeforeReply: true, runningAtQuota: true });
  const result = await run();
  assert.equal(result.event, "saved");
  assert.equal(result.quota_state, "exhausted");
  assert.equal(state.waits, 1);
  assert.equal(state.sent, 1);
});

test("a changed model still blocks collection after sending", async () => {
  const { run, state } = fixture({ changedModel: true, quotaAfterReply: true });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(result.reason, "pro_not_observed");
  assert.deepEqual(state.phases, ["dispatch"]);
  assert.equal(result.reply, undefined);
});

test("response and generation waits retry selector timeouts against one deadline", async () => {
  const { run, state } = fixture({ timeoutMs: 7_000, waitTimeouts: 1, stopWaitTimeouts: 1 });
  const result = await run();
  assert.equal(result.event, "saved");
  assert.deepEqual(state.replyWaits, [2_500, 2_500]);
  assert.deepEqual(state.stopWaits, [2_500, 2_000]);
  assert.equal(state.sent, 1);
});

test("response wait expiry leaves the original request pending", async () => {
  const { run, state } = fixture({ timeoutMs: 5_200, waitTimeouts: 3 });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(result.phase, "wait");
  assert.equal(result.reason, "wait_timeout");
  assert.equal(result.tab_id, "1");
  assert.deepEqual(state.replyWaits, [2_500, 2_500, 200]);
  assert.equal(state.sent, 1);
});

test("a Browser permission denial during waiting stops without retrying", async () => {
  const { run, state } = fixture({ timeoutMs: 7_000, waitDenied: true });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(result.reason, "browser_operation_unconfirmed");
  assert.deepEqual(state.replyWaits, [2_500]);
  assert.equal(state.sent, 1);
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
