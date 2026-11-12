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
  status: "completed", result: "Nested JSON: {\"path\":\"C:\\\\tmp\"}\nSecond line", evidence: [],
};

function control({ present = () => true, text = () => "", attribute = () => null,
  click, fill, wait } = {}) {
  const locator = {
    count: async () => Number(present()),
    innerText: async () => text(),
    getAttribute: async name => attribute(name),
    evaluate: async (read, arg) => read({
      value: text(), innerText: text(), textContent: text(),
      getAttribute: key => key === "aria-label" ? locator.name : attribute(key),
    }, arg),
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
  menuNotice = null,
  switches = null, temporaryUrl = "https://chatgpt.com/?temporary-chat=true",
  newMarkup = false, badge = true, oldHeading = false, wrongReplyHash = false, wrongUserHash = false,
  conversationUrl = "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc?temporary-chat=true",
  timeoutMs = 1_000, waitTimeouts = 0, stopWaitTimeouts = 0, waitDenied = false,
  github = false, otherTool = false, legacy = false, mode = "personalized", selectionFails = false, revert = false,
  ambiguousMode = false, nav = "normal", receiveNav = nav, hideTemporaryAfterSend = false,
  sentUrl = "https://chatgpt.com/c/fixture", changedDestination = false,
  loseTemporaryAtPicker = false, transitionDuringWait = false, codeBlocks = 0, longUser = false } = {}) {
  let now = 0;
  class FixtureDate extends Date {
    static now() { return now; }
  }
  const makeRunner = () => vm.runInNewContext(source + "\nrunProHandoff;", { URL, Date: FixtureDate });
  let run = makeRunner();
  const state = {
    temporary: false, menu: false, power: 3, composer: "", sent: 0, ready: false,
    waits: 0, saved: null, handoff: false, phases: [], quotas: [], opened: [], closed: [],
    replyWaits: [], stopWaits: [], generationEnded: false,
    mode, modeMenu: false, modeClicks: 0, prepared: 0, modeObservedAfterDispatch: false,
    pickerWaits: 0, marks: 0, gotos: [], forceUrl: null,
  };
  const model = () => state.power === 4 && !(changedModel && state.sent) ? "Pro" : "Extra High";
  const quotaVisible = () => (quotaAfterReply && state.ready) || (quotaBeforeReply && state.sent > 0);
  const navigation = () => state.sent ? receiveNav : nav;
  const switchState = () => switches ? switches(state) : !state.temporary ? "chat" :
    ({ hidden: "absent", partial: "chat-only", work: "work" })[navigation()] ?? "chat";
  const chatButtons = {
    Chat: control({ present: () => !["absent", "work-only"].includes(switchState()),
      attribute: () => switchState() === "work" ? "false" : "true" }),
    Work: control({ present: () => !["absent", "chat-only"].includes(switchState()),
      attribute: () => switchState() === "work" ? "true" : "false" }),
    "一時チャット": control({ present: () => !state.temporary, click: () => { state.temporary = true; } }),
    "一時チャットをオフにする": control({ present: () => state.temporary && !(state.sent && (hideTemporaryAfterSend || newMarkup)) }),
    "パーソナライズ": control({ present: () => state.mode === "personalized" || ambiguousMode }),
    "パーソナライズなし": control({ present: () => state.mode === "unpersonalized", click: () => { state.modeMenu = true; } }),
    "ChatGPT モデルを選択": control({
      text: model, attribute: () => String(state.menu), click: () => { state.menu = true; },
      wait: () => { state.pickerWaits++; if (loseTemporaryAtPicker && state.pickerWaits === 2) state.temporary = false; },
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
  const waitForReply = ({ timeoutMs }) => {
    assert.equal(state.sent, 1);
    state.waits++;
    if (transitionDuringWait) state.forceUrl = sentUrl;
    state.replyWaits.push(timeoutMs);
    if (waitDenied) throw new Error("Browser permission denied");
    if (state.waits <= waitTimeouts) {
      now += timeoutMs;
      throw new Error("Playwright selector deadline exceeded");
    }
    state.ready = true;
  };
  const final = control({ present: () => state.ready && !newMarkup, wait: waitForReply });
  // The union wait observes the eventual matching locator in either layout.
  final.waitFor = async options => { await waitForReply(options); };
  const newFinal = control({ present: () => state.ready && newMarkup, wait: waitForReply });
  const message = control({ present: () => state.ready, text: () => JSON.stringify(reply) });
  message.locator = () => ({
    filter: () => ({ count: async () => codeBlocks,
      evaluate: async read => read({ textContent: JSON.stringify(reply) }) }),
  });
  const article = control({ present: () => state.ready && !newMarkup });
  article.getByRole = () => final;
  article.locator = () => message;
  const chat = {
    evaluate: async read => menuNotice ? evaluateMenu(read, state, model(), menuNotice) : ({
      model: model(), current: state.power, maximum: 4,
      powerFocused: state.menu, limited: (limited || quotaVisible()) && model() === "Pro",
      locked: quotaVisible() && model() === "Pro",
    }),
    locator: selector => {
      if (selector === '[data-message-author-role="user"]') {
        const user = control({ present: () => state.sent > 0 && !newMarkup,
          text: () => `${longUser ? "x".repeat(131000) : ""}Set request_id to ${listening.request_id} and input_sha256 to ${wrongUserHash ? "0".repeat(64) : listening.input_sha256}.`,
        });
        if (longUser) user.innerText = async () => { throw Error("user text must remain in browser"); };
        return user;
      }
      if (selector === "main article") return article;
      if (selector === ".turn-action-controls") return { getByRole: () => newFinal };
      const absent = control({ present: () => false });
      absent.getByRole = () => absent;
      return absent;
    },
    getByRole(role, { name } = {}) {
      if (role === "menu" && name) {
        const menu = control({ present: () => state.modeMenu && name.test("パーソナライズなし") });
        menu.getByRole = (_role, opts) => control({
          present: () => state.modeMenu && opts.name.test("パーソナライズ このチャット"),
          attribute: () => "false",
          click: () => { state.modeClicks++; state.modeMenu = false; if (!selectionFails) state.mode = "personalized"; },
        });
        return menu;
      }
      if (role === "menu") return control({ present: () => state.menu });
      if (role === "heading") {
        const heading = control({ present: () => oldHeading || (newMarkup && state.sent > 0) });
        heading.evaluate = async (read, arg) => read({ nextElementSibling: {
          tagName: "DIV", getClientRects: () => [1], querySelectorAll: () => Array.from({ length: codeBlocks }, () => ({
            getClientRects: () => [1], textContent: JSON.stringify(reply),
          })), innerText: name === "あなたの発言:"
            ? `Set request_id to ${listening.request_id} and input_sha256 to ${wrongUserHash ? "0".repeat(64) : listening.input_sha256}.`
            : JSON.stringify(wrongReplyHash ? { ...reply, input_sha256: "0".repeat(64) } : reply),
        } }, arg);
        return heading;
      }
      if (role === "alert") return { filter: ({ hasText }) => control({
        present: () => quotaVisible() && hasText.test("Pro usage limit reached"),
      }) };
      if (role === "textbox") return control({
        text: () => state.composer, fill: text => { state.composer = text; },
      });
      const label = Object.keys(chatButtons).find(key => typeof name === "string" ? name === key : name?.test(key));
      if (state.phases.includes("dispatch") && ["パーソナライズ", "パーソナライズなし"].includes(label)) {
        state.modeObservedAfterDispatch = true;
      }
      if (label) chatButtons[label].name = label;
      return chatButtons[label] ?? control({ present: () => false });
    },
    getByText: () => control({ present: () => newMarkup && state.sent > 0 && badge }),
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
        ...(dispatch && github ? { checks: [{ tool: "GitHub" }], temporary_chat_mode: live.temporary_chat_mode } : {}),
      };
      envelope = { phase, request_id: reply.request_id, input_sha256: reply.input_sha256, plan };
    } }),
  };
  const tabs = new Map();
  const cua = {
    async getTab(id, options) {
      assert.equal(options.browser, "iab");
      if (!tabs.has(id) || state.closed.includes(id)) throw new Error("tab unavailable");
      return tabs.get(id);
    },
    async createBrowserTab(browser, target, options) {
      assert.equal(browser, "iab");
      assert.equal(target, "about:blank");
      assert.equal(options.visible, false);
      const id = String(state.opened.length + 1);
      state.opened.push(id);
      let url = target;
      const tab = {
        id, playwright: id === "1" ? chat : local,
        url: async () => id === "1" && state.temporary
          ? (state.forceUrl ?? (state.sent && !transitionDuringWait ? (changedDestination && state.ready ? "https://chatgpt.com/c/other" :
            newMarkup ? conversationUrl : sentUrl) : temporaryUrl))
          : url,
        goto: async next => { state.gotos.push(next); url = next; },
        async pressKey(_, key) {
          assert.equal(state.menu, true);
          if (key === "Escape") state.menu = false;
          else state.power += key === "Right" ? 1 : -1;
        },
        close: async () => { state.closed.push(id); },
        markHandoff: async () => { state.handoff = true; state.marks++; },
      };
      tabs.set(id, tab);
      return tab;
    },
    listTabs: async () => state.opened.filter(id => !state.closed.includes(id)).map(id => ({ id })),
  };
  const agent = { browsers: { get: async browser => {
    assert.equal(browser, "iab");
    return { tabs: { get: async id => {
      if (!tabs.has(id) || state.closed.includes(id)) throw new Error("tab unavailable");
      return tabs.get(id);
    } } };
  } } };
  const input = { ...listening, needs_tools: github || otherTool };
  if (!legacy) input.needs_github = github;
  const prepareTools = github ? async () => {
    state.prepared++;
    if (revert) state.mode = "unpersonalized";
    return { temporaryChatMode: "personalized", verifyGitHubSelection: async () => true };
  } : otherTool ? async () => { state.prepared++; return {}; } : undefined;
  return { run: (resume, override = {}) => run({ cua, agent, listening: { ...input, ...override }, timeoutMs, prepareTools, resume }),
    restart: () => { run = makeRunner(); }, state };
}

test("temporary mode lost before model selection blocks selection and send", async () => {
  const { run, state } = fixture({ loseTemporaryAtPicker: true });
  const result = await run();
  assert.equal(result.reason, "temporary_chat_unconfirmed");
  assert.equal(state.power, 3);
  assert.equal(state.sent, 0);
  assert.equal(state.menu, false);
});

test("both switches may disappear only after explicit Chat and temporary selection", async () => {
  const { run, state } = fixture({ switches: s => s.temporary ? "absent" : "chat" });
  assert.equal((await run()).event, "saved");
  assert.equal(state.sent, 1);
  assert.deepEqual(state.phases, ["dispatch", "receive"]);
});

test("heading layout receives the correlated reply from the same temporary chat", async () => {
  const { run, state } = fixture({ newMarkup: true, switches: s => s.temporary ? "absent" : "chat" });
  const result = await run();
  assert.equal(result.event, "saved");
  assert.equal(result.reply.result, reply.result);
  assert.equal(state.sent, 1);
  assert.deepEqual(state.phases, ["dispatch", "receive"]);
});

test("GitHub selection and heading-layout collection work in the same run", async () => {
  const { run, state } = fixture({ github: true, mode: "unpersonalized", newMarkup: true,
    switches: s => s.temporary ? "absent" : "chat" });
  assert.equal((await run()).event, "saved");
  assert.equal(state.modeClicks, 1);
  assert.equal(state.sent, 1);
  assert.deepEqual(state.phases, ["dispatch", "receive"]);
});

test("heading-layout conversation cannot change after being bound", async () => {
  const { run, state } = fixture({ newMarkup: true, changedDestination: true });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(state.sent, 1);
  assert.equal(state.saved, null);
});

test("heading-layout reply with a different hash is never saved", async () => {
  const { run, state } = fixture({ newMarkup: true, wrongReplyHash: true });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(result.reason, "reply_mismatch");
  assert.equal(state.saved, null);
  assert.equal(state.sent, 1);
});

test("heading-layout conversation with a different user request is never saved", async () => {
  const { run, state } = fixture({ newMarkup: true, wrongUserHash: true });
  const result = await run();
  assert.equal(result.event, "pending");
  assert.equal(result.reason, "request_turn_unconfirmed");
  assert.equal(state.saved, null);
});

for (const [label, options] of [
  ["missing temporary badge", { badge: false }],
  ["unbound conversation URL", { conversationUrl: "https://chatgpt.com/c/not-a-uuid?temporary-chat=true" }],
  ["extra URL parameter", { conversationUrl: "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc?temporary-chat=true&extra=1" }],
  ["Work selected during receive", { switches: s => s.sent ? "work" : "chat" }],
]) {
  test(`${label} blocks heading-layout collection`, async () => {
    const { run, state } = fixture({ newMarkup: true, ...options });
    const result = await run();
    assert.equal(result.event, "pending");
    assert.equal(result.reason, "temporary_chat_unconfirmed");
    assert.equal(state.sent, 1);
    assert.deepEqual(state.phases, ["dispatch"]);
  });
}

test("existing conversation headings prevent sending into a nonempty chat", async () => {
  const { run, state } = fixture({ oldHeading: true });
  assert.equal((await run()).event, "blocked");
  assert.equal(state.sent, 0);
});

for (const [label, options] of [
  ["initially absent", { switches: () => "absent" }],
  ["only Chat remains", { switches: s => s.temporary ? "chat-only" : "chat" }],
  ["only Work remains", { switches: s => s.temporary ? "work-only" : "chat" }],
  ["Work selected", { switches: s => s.temporary ? "work" : "chat" }],
  ["non-temporary URL", { switches: s => s.temporary ? "absent" : "chat", temporaryUrl: "https://chatgpt.com/" }],
]) {
  test(`${label} does not allow dispatch`, async () => {
    const { run, state } = fixture(options);
    assert.equal((await run()).event, "blocked");
    assert.equal(state.sent, 0);
    assert.deepEqual(state.phases, []);
  });
}

// Execute the actual browser callback against a small DOM with the same
// rendered and screen-reader-only geometry as the observed model menu.
function evaluateMenu(read, state, model, notice) {
  const normalStyle = { display: "block", visibility: "visible", opacity: "1", clip: "auto", clipPath: "none" };
  const element = (parent, hidden = false) => ({
    parentElement: parent, style: { ...normalStyle, clip: hidden ? "rect(0px, 0px, 0px, 0px)" : "auto" },
    getBoundingClientRect: () => hidden ? { width: 1, height: 1 } : { width: 100, height: 20 },
    getClientRects: () => [1], getAttribute: () => null,
  });
  const menu = element(null);
  const power = element(menu);
  const slider = element(power);
  slider.closest = () => power;
  slider.getAttribute = name => ({ "aria-valuenow": String(state.power), "aria-valuemax": "4",
    "aria-describedby": notice.referenced ? "current-notice" : null })[name] ?? null;
  const label = element(menu);
  const message = element(menu, notice.hidden);
  if (notice.tiny) message.getBoundingClientRect = () => ({ width: 0, height: 0 });
  if (notice.display) message.style.display = notice.display;
  if (notice.overflow) message.style.overflow = notice.overflow;
  message.textContent = notice.text;
  menu.innerText = `${model}\n${notice.text}`;
  menu.querySelector = () => slider;
  menu.contains = target => target === message;
  label.nodeType = message.nodeType = 1;
  label.childNodes = [{ nodeType: 3, parentElement: label, textContent: model }];
  if (notice.split) {
    const inner = element(message);
    inner.nodeType = 1;
    inner.style.display = "inline";
    inner.childNodes = [{ nodeType: 3, parentElement: inner, textContent: notice.split[1] }];
    const hidden = element(message, true);
    hidden.nodeType = 1;
    hidden.childNodes = [{ nodeType: 3, parentElement: hidden, textContent: "unrelated" }];
    message.childNodes = [{ nodeType: 3, parentElement: message, textContent: notice.split[0] },
      ...(notice.hiddenMiddle ? [hidden] : []), inner];
  } else message.childNodes = [{ nodeType: 3, parentElement: message, textContent: notice.text }];
  menu.childNodes = [label, message];
  const document = {
    activeElement: power,
    querySelectorAll: () => [menu],
    getElementById: id => id === "current-notice" ? message : null,
  };
  return vm.runInNewContext(`(${read.toString()})()`, {
    document, getComputedStyle: node => node.style,
  });
}

test("unreferenced visually hidden quota and lock text does not block Pro", async () => {
  const { run, state } = fixture({ menuNotice: { hidden: true,
    text: "ロック中、アクセスオプションを開きます 利用上限に達しました" } });
  assert.equal((await run()).event, "saved");
  assert.equal(state.sent, 1);
  assert.deepEqual(state.quotas, ["no_limit_notice", "no_limit_notice"]);
});

for (const [label, notice, reason] of [
  ["visible quota", { text: "利用上限に達しました" }, "quota_exhausted"],
  ["visible lock", { text: "ロック中、アクセスオプションを開きます" }, "pro_locked"],
  ["current control description", { hidden: true, referenced: true, text: "利用上限に達しました" }, "quota_exhausted"],
  ["display contents quota", { text: "利用上限", tiny: true, display: "contents" }, "quota_exhausted"],
  ["tiny visible overflow quota", { text: "利用上限", tiny: true, overflow: "visible" }, "quota_exhausted"],
  ["split inline quota", { text: "利用上限", split: ["利用", "上限"] }, "quota_exhausted"],
  ["split inline lock", { text: "ロック中", split: ["ロック", "中"] }, "pro_locked"],
  ["hidden block within inline quota", { text: "利用上限", split: ["利用", "上限"], hiddenMiddle: true }, "quota_exhausted"],
]) {
  test(`${label} blocks dispatch without sending`, async () => {
    const { run, state } = fixture({ menuNotice: notice });
    const result = await run();
    assert.equal(result.reason, reason);
    assert.equal(state.sent, 0);
  });
}

test("tiny clipped overflow does not expose the hidden notice", async () => {
  const { run } = fixture({ menuNotice: { text: "利用上限", tiny: true, overflow: "hidden" } });
  assert.equal((await run()).event, "saved");
});

test("GitHub run selects Personalized only when needed and uses the prepared mode for send", async () => {
  for (const mode of ["personalized", "unpersonalized"]) {
    const { run, state } = fixture({ github: true, mode });
    assert.equal((await run()).event, "saved");
    assert.equal(state.modeClicks, Number(mode === "unpersonalized"));
    assert.equal(state.prepared, 1);
    assert.equal(state.modeObservedAfterDispatch, false);
    assert.equal(state.sent, 1);
  }
});

test("GitHub run stops for failed, ambiguous or reverted mode", async () => {
  for (const options of [
    { mode: "unpersonalized", selectionFails: true },
    { mode: "unpersonalized", ambiguousMode: true },
    { mode: "personalized", revert: true },
  ]) {
    const { run, state } = fixture({ github: true, ...options });
    assert.equal((await run()).event, "blocked");
    assert.equal(state.sent, 0);
  }
});

test("non-GitHub and old metadata do not automatically change mode", async () => {
  const noTools = fixture({ mode: "unpersonalized" });
  assert.equal((await noTools.run()).event, "saved");
  assert.equal(noTools.state.modeClicks, 0);
  const other = fixture({ otherTool: true, mode: "unpersonalized" });
  assert.equal((await other.run()).event, "saved");
  assert.equal(other.state.modeClicks, 0);
  const old = fixture({ github: true, legacy: true, mode: "unpersonalized" });
  assert.equal((await old.run()).event, "blocked");
  assert.equal(old.state.modeClicks, 0);
  assert.equal(old.state.sent, 0);
  const oldReady = fixture({ github: true, legacy: true, mode: "personalized" });
  assert.equal((await oldReady.run()).event, "saved");
  assert.equal(oldReady.state.modeClicks, 0);
});

test("new temporary Chat accepts both navigation buttons hidden but rejects partial or Work selection", async () => {
  const hidden = fixture({ github: true, mode: "unpersonalized", nav: "hidden" });
  const result = await hidden.run();
  assert.equal(hidden.state.modeClicks, 1);
  assert.equal(hidden.state.sent, 1);
  assert.equal(result.event, "saved");
  assert.equal(result.reply.result, reply.result);
  assert.equal(hidden.state.modeObservedAfterDispatch, false);
  for (const nav of ["partial", "work"]) {
    const run = fixture({ github: true, nav });
    assert.equal((await run.run()).event, "blocked");
    assert.equal(run.state.sent, 0);
    assert.equal(run.state.modeClicks, 0);
  }
});

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

test("receive accepts hidden navigation without rechecking Personalized, including old metadata", async () => {
  for (const sentUrl of ["https://chatgpt.com/c/fixture", "https://chatgpt.com/?temporary-chat=true"]) {
    for (const options of [{}, { github: true }, { github: true, legacy: true }]) {
      const { run, state } = fixture({ ...options, receiveNav: "hidden", sentUrl, quotaAfterReply: true });
      const result = await run();
      assert.equal(result.event, "saved");
      assert.equal(result.quota_state, "exhausted");
      assert.equal(state.sent, 1);
      assert.equal(state.modeClicks, 0);
      assert.equal(state.modeObservedAfterDispatch, Boolean(options.legacy));
    }
  }
});

test("receive retains the original request when its context or destination changes", async () => {
  for (const options of [
    { receiveNav: "partial" }, { receiveNav: "work" }, { hideTemporaryAfterSend: true },
    { changedModel: true }, { changedDestination: true },
    { sentUrl: "https://chatgpt.com/" }, { sentUrl: "https://chatgpt.com/work/fixture" },
    { sentUrl: "https://other.example/c/fixture" },
    { sentUrl: "https://chatgpt.com/c/fixture?temporary-chat=false" },
    { sentUrl: "https://chatgpt.com/c/fixture?temporary-chat=true&temporary-chat=true" },
  ]) {
    const { run, state } = fixture({ github: true, receiveNav: "hidden", ...options });
    const result = await run();
    assert.equal(result.event, "pending", JSON.stringify(options));
    assert.equal(result.tab_id, "1");
    assert.equal(result.reply, undefined);
    assert.equal(state.saved, null);
    assert.equal(state.sent, 1);
    assert.equal(state.modeObservedAfterDispatch, false);
    assert.equal((await run()).reason, "already_started");
  }
});


test("pending timeout resumes the same tab without a second dispatch", async () => {
  const { run, state } = fixture({ timeoutMs: 1000, waitTimeouts: 1 });
  const pending = await run();
  assert.equal(pending.event, "pending");
  assert.equal(pending.tab_id, "1");
  const saved = await run(pending);
  assert.equal(saved.event, "saved");
  assert.equal(state.sent, 1);
  assert.deepEqual(state.phases, ["dispatch", "receive"]);
});

test("resume rejects a different request hash without opening or sending", async () => {
  const { run, state } = fixture({ timeoutMs: 1000, waitTimeouts: 1 });
  const pending = await run();
  assert.equal((await run({ ...pending, input_sha256: "0".repeat(64) })).reason, "invalid_resume");
  assert.equal(state.sent, 1);
  assert.deepEqual(state.opened, ["1", "2"]);
});


test("invalid transfer URL is rejected before any tab or navigation for both paths", async () => {
  const { run, state } = fixture({ timeoutMs: 1000, waitTimeouts: 1 });
  assert.equal((await run(undefined, { url: "https://example.com/transfer/x" })).reason, "invalid_workflow_input");
  assert.deepEqual(state.opened, []);
  const pending = await run();
  const opened = state.opened.length, gotos = state.gotos.length;
  assert.equal((await run(pending, { url: "http://evil.example/transfer/x" })).reason, "invalid_workflow_input");
  assert.equal(state.opened.length, opened);
  assert.equal(state.gotos.length, gotos);
});

test("resume rejects changed tab or URL before opening the form or reading the reply", async () => {
  const { run, state } = fixture({ timeoutMs: 1000, waitTimeouts: 1 });
  const pending = await run();
  const opens = state.opened.length;
  assert.equal((await run({ ...pending, tab_id: "2" })).reason, "resume_binding_mismatch");
  state.forceUrl = "https://chatgpt.com/c/different";
  assert.equal((await run(pending)).reason, "resume_binding_mismatch");
  assert.equal(state.opened.length, opens);
  assert.equal(state.saved, null);
});

test("restart rebinds without cua.getTab and marks each pending turn", async () => {
  const { run, restart, state } = fixture({ timeoutMs: 1000, waitTimeouts: 2 });
  const first = await run();
  assert.equal(state.marks, 1);
  restart();
  const second = await run(first);
  assert.equal(second.event, "pending");
  assert.equal(state.marks, 2);
  assert.equal((await run(second)).event, "saved");
  assert.equal(state.marks, 3);
  assert.equal(state.sent, 1);
});


test("ambiguous successful click resumes the same matching conversation without resending", async () => {
  const { run, state } = fixture({ ambiguousClick: true });
  const pending = await run();
  assert.equal(pending.event, "pending");
  assert.equal(pending.chat_url, "https://chatgpt.com/?temporary-chat=true");
  assert.equal((await run(pending)).event, "saved");
  assert.equal(state.sent, 1);
});

test("ambiguous click cannot bind a conversation with a different request marker", async () => {
  const { run, state } = fixture({ ambiguousClick: true, wrongUserHash: true });
  const pending = await run();
  assert.equal((await run(pending)).reason, "resume_binding_mismatch");
  assert.equal(state.saved, null);
  assert.equal(state.sent, 1);
});


test("wait binds a matching root-to-conversation transition in one call", async () => {
  const { run, state } = fixture({ transitionDuringWait: true });
  assert.equal((await run()).event, "saved");
  assert.equal(state.sent, 1);
});

test("both response layouts read a single code block and reject multiple", async () => {
  for (const newMarkup of [false, true]) {
    for (const codeBlocks of [1, 2]) {
      const { run, state } = fixture({ newMarkup, codeBlocks });
      const result = await run();
      assert.equal(result.event, codeBlocks === 1 ? "saved" : "pending");
      assert.equal(Boolean(state.saved), codeBlocks === 1);
    }
  }
});


test("long user marker is checked inside the browser as a boolean", async () => {
  const { run, state } = fixture({ transitionDuringWait: true, longUser: true });
  assert.equal((await run()).event, "saved");
  assert.equal(state.sent, 1);
});


test("legacy layout rejects a different request marker before receive preflight", async () => {
  const { run, state } = fixture({ wrongUserHash: true });
  const result = await run();
  assert.equal(result.reason, "request_turn_unconfirmed");
  assert.deepEqual(state.phases, ["dispatch"]);
  assert.equal(state.saved, null);
});
