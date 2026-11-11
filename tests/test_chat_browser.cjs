const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(
  path.join(__dirname, "../plugins/codex-task-routing/scripts/chat_browser.js"),
  "utf8",
);
const request_id = "7cbd8fe9-c91b-488b-887e-fd2f7927e56c";
const input_sha256 = "a".repeat(64);
const listening = {
  event: "listening",
  request_id,
  input_sha256,
  url: "http://127.0.0.1:12345/transfer/fixture-token",
};
const dispatch = {
  route: "chat_candidate", next_action: "send_once", preflight_complete: true,
  transport: "browser-temporary", live_checked: true,
};
const receive = {
  route: "chat_collection", next_action: "collect_reply", preflight_complete: true,
  transport: "browser-temporary", request_id, input_sha256,
};

function fixture() {
  const createHandoff = vm.runInNewContext(script + "\nproHandoff;", { URL });
  const prompt = "First line.\nSecond line.";
  const state = { composer: "", submitted: "", saveClicks: 0, temporary: true, chatUrl: "https://chatgpt.com/?temporary-chat=true" };
  const composer = {
    evaluate: async read => read({ value: state.composer }),
    fill: async text => { state.composer = text; },
  };
  const transferTab = {
    url: async () => listening.url,
    playwright: {
      getByLabel(label) {
        if (label === "Request prompt") {
          return { evaluate: async read => read({ value: prompt }) };
        }
        assert.equal(label, "Response JSON");
        return { fill: async text => { state.submitted = text; } };
      },
      getByRole: () => ({ click: async () => { state.saveClicks++; } }),
    },
  };
  const newHandoff = () => createHandoff({
    chatTab: { url: async () => state.chatUrl, playwright: { getByRole: (_role, { name }) => ({
      filter: () => ({ count: async () => name.test("一時チャットをオフにする") ? Number(state.temporary) : 0 }),
    }) } }, transferTab, listening,
  });
  return { newHandoff, composer, state };
}

test("temporary indicator lost after filling blocks send even if URL is unchanged", async () => {
  const { newHandoff, composer, state } = fixture();
  let clicks = 0;
  await assert.rejects(newHandoff().send({ plan: dispatch, composer, temporaryChatUrl: state.chatUrl,
    fillPrompt: async text => { await composer.fill(text); state.temporary = false; },
    sendButton: { click: async () => { clicks++; } },
  }), /Temporary Chat destination/);
  assert.equal(clicks, 0);
});

function proseMirror(text, { chip = false, split = false, span = false, decorated = false, marker = false } = {}) {
  const paragraph = value => ({ nodeType: 1, tagName: "P", innerText: value || "\n", textContent: value,
    childNodes: span ? [{ nodeType: 1, tagName: "SPAN", attributes: { length: decorated && marker ? 2 : decorated || marker ? 1 : 0 },
      getAttribute: name => marker && name === "data-prompt-literal-paste" ? "" : null,
      childNodes: value.split("\n").flatMap((part, i) => i ? [{ nodeType: 1, tagName: "BR", attributes: [] },
        { nodeType: 3, textContent: part }] : [{ nodeType: 3, textContent: part }]) }] :
      value ? [{ nodeType: 3, textContent: value }] : [{ nodeType: 1, tagName: "BR", attributes: [] }] });
  const childNodes = (split ? text.split("\n") : [text]).map(paragraph);
  if (chip) childNodes.push({ nodeType: 1, tagName: "SPAN", innerText: "chip" });
  return { tagName: "DIV", value: "", innerText: "", childNodes,
    getAttribute: name => ({ class: "ProseMirror", contenteditable: "true" })[name] ?? null };
}

test("ProseMirror paragraphs preserve exact prompt and reject unexpected content", async () => {
  for (const kind of ["single", "paragraphs", "span", "marked", "marked-extra", "decorated", "mismatch", "chip-before", "chip-after"]) {
    const { newHandoff, state } = fixture();
    let filled = false;
    let clicks = 0;
    let waited = false;
    const composer = {
      evaluate: async read => read(proseMirror(kind === "span" && filled && !waited ? "" : state.composer, {
        split: kind === "paragraphs", chip: kind === "chip-before" || (kind === "chip-after" && filled),
        span: ["span", "marked", "marked-extra", "decorated"].includes(kind) && filled,
        decorated: ["decorated", "marked-extra"].includes(kind), marker: ["marked", "marked-extra"].includes(kind),
      })),
      fill: async text => { filled = true; state.composer = kind === "mismatch" ? text + "!" : text; },
      getByText: (line, options) => ({ filter: () => ({ or: () => ({ first: () => ({ waitFor: async wait => {
        assert.equal(line, "First line.");
        assert.equal(options.exact, false);
        assert.equal(wait.state, "visible");
        waited = true;
      } }) }) }) }),
    };
    const result = await newHandoff().send({ plan: dispatch, composer, temporaryChatUrl: state.chatUrl,
      sendButton: { click: async () => { clicks++; } } });
    assert.equal(result.event, kind === "chip-before" ? "composer_not_ready" :
      ["mismatch", "chip-after", "decorated", "marked-extra"].includes(kind) ? "prompt_mismatch" : "send_clicked");
    assert.equal(clicks, ["single", "paragraphs", "span", "marked"].includes(kind) ? 1 : 0);
    assert.equal(waited, kind !== "chip-before");
  }
});

test("empty trailing-break sentinel accepts localized and cosmetic paragraph variations only", async () => {
  for (const kind of ["valid", "english", "cosmetic-p-class", "missing-empty-flag", "extra-p-attribute",
    "extra-br-attribute", "wrong-br-class", "extra-child", "not-empty", "extra-paragraph"]) {
    const { newHandoff, state } = fixture();
    let filled = false;
    let clicks = 0;
    const br = { nodeType: 1, tagName: "BR",
      attributes: Array(kind === "extra-br-attribute" ? 2 : 1).fill({ name: "class" }),
      getAttribute: name => name === "class" ? (kind === "wrong-br-class" ? "other" : "ProseMirror-trailingBreak") : null };
    const attributes = { "data-empty-paragraph": kind === "missing-empty-flag" ? null : "true",
      "data-placeholder": kind === "english" ? "Ask ChatGPT" : "ChatGPT に聞く",
      class: kind === "cosmetic-p-class" ? "other-style" : "placeholder before:composer-placeholder" };
    const paragraph = { nodeType: 1, tagName: "P", attributes: Array(kind === "extra-p-attribute" ? 4 : 3),
      getAttribute: name => attributes[name] ?? null, textContent: kind === "not-empty" ? "x" : "",
      childNodes: kind === "extra-child" ? [br, { nodeType: 3, textContent: "x" }] : [br] };
    const initial = { tagName: "DIV", childNodes: kind === "extra-paragraph" ? [paragraph, paragraph] : [paragraph],
      getAttribute: name => ({ class: "ProseMirror", contenteditable: "true" })[name] ?? null };
    const composer = { evaluate: async read => read(filled ? proseMirror(state.composer) : initial),
      fill: async text => { filled = true; state.composer = text; },
      getByText: () => ({ filter: () => ({ or: () => ({ first: () => ({ waitFor: async () => {} }) }) }) }) };
    const result = await newHandoff().send({ plan: dispatch, composer, temporaryChatUrl: state.chatUrl,
      sendButton: { click: async () => { clicks++; } } });
    const accepted = ["valid", "english", "cosmetic-p-class", "missing-empty-flag", "extra-p-attribute"].includes(kind);
    assert.equal(result.event, accepted ? "send_clicked" : "composer_not_ready", kind);
    assert.equal(filled, accepted, kind);
    assert.equal(clicks, accepted ? 1 : 0, kind);
  }
});

test("long text attachment expands uniquely and matches only observed literal and autolink nodes", async () => {
  const url = "https://example.org/pr/63\\nBase";
  const prefix = "Long " + "x".repeat(10374) + "\n";
  const prompt = prefix + url + "\nEnd";
  const attrNames = ["rich-link-source-app-id", "class", "data-appearance", "data-layout",
    "data-font-weight", "data-tone", "data-underline-on-hover", "data-breakable-url",
    "text-link-href", "data-rich-text-generated-autolink"];
  for (const kind of ["valid", "href-mismatch", "extra-attribute", "nested", "changed-body",
    "ambiguous", "stale", "delayed", "reappeared", "preexisting", "destination-changed"]) {
    const state = { body: "", attachment: kind === "preexisting" ? 1 : 0, clicks: 0, expansions: 0,
      chatUrl: "https://chatgpt.com/?temporary-chat=true", expanded: false };
    const text = value => ({ nodeType: 3, textContent: value });
    const br = () => ({ nodeType: 1, tagName: "BR", attributes: [] });
    const span = nodes => ({ nodeType: 1, tagName: "SPAN", attributes: [{ name: "data-prompt-literal-paste" }],
      getAttribute: name => name === "data-prompt-literal-paste" ? "" : null, childNodes: nodes });
    const editor = () => {
      if (!state.expanded) return { tagName: "DIV", childNodes: [{ nodeType: 1, tagName: "P", textContent: "",
        childNodes: [br()] }], getAttribute: name => ({ class: "ProseMirror", contenteditable: "true" })[name] ?? null };
      if (kind === "reappeared") state.attachment = 1;
      const inner = { nodeType: 1, tagName: "SPAN", attributes: [{ name: "class" }],
        getAttribute: name => name === "class" ? "link" : null,
        childNodes: kind === "nested" ? [{ nodeType: 1, tagName: "B" }] : [text(url)] };
      const link = { nodeType: 1, tagName: "SPAN", textContent: url,
        attributes: [...attrNames, ...(kind === "extra-attribute" ? ["unexpected"] : [])].map(name => ({ name })),
        getAttribute: name => name === "text-link-href" ? (kind === "href-mismatch" ? url + "!" : url) : "x",
        childNodes: [inner] };
      return { tagName: "DIV", getAttribute: name => ({ class: "ProseMirror", contenteditable: "true" })[name] ?? null,
        childNodes: [{ nodeType: 1, tagName: "P", textContent: prompt, innerText: prompt + "\n",
          childNodes: [span([text(prefix.slice(0, -1)), br()]), link,
            span([br(), text(kind === "changed-body" ? "changed" : "End")])] }] };
    };
    const composer = { evaluate: async read => read(editor()), fill: async value => {
      assert.equal(value, prompt);
      state.attachment = kind === "ambiguous" ? 2 : kind === "delayed" ? 0 : 1;
      if (kind === "destination-changed") state.chatUrl = "https://chatgpt.com/";
    }, getByText: () => ({ filter: () => ({
      or: () => ({ first: () => ({ waitFor: async () => { if (kind === "delayed") state.attachment = 1; } }) }),
      first: () => ({ waitFor: async () => { assert.equal(state.expanded, true); } }),
    }) }) };
    const chatTab = { url: async () => state.chatUrl, playwright: { getByRole: (_role, { name }) => ({ filter: () => ({
      count: async () => name.test("一時チャットをオフにする") ? 1 : state.attachment,
      click: async () => { state.expansions++; if (kind !== "stale" && kind !== "delayed") { state.attachment = 0; state.expanded = true; } },
      waitFor: async ({ state: desired }) => { assert.equal(desired, "hidden");
        if (kind === "stale") throw new Error("expansion timeout");
        if (kind === "delayed") { state.attachment = 0; state.expanded = true; }
      },
    }) }) } };
    const transferTab = { url: async () => listening.url, playwright: { getByLabel: () => ({
      evaluate: async read => read({ value: prompt }),
    }) } };
    const createHandoff = vm.runInNewContext(script + "\nproHandoff;", { URL });
    const handoff = createHandoff({ chatTab, transferTab, listening });
    const args = { plan: dispatch, composer, temporaryChatUrl: "https://chatgpt.com/?temporary-chat=true",
      sendButton: { click: async () => { state.clicks++; } } };
    if (kind === "destination-changed") await assert.rejects(handoff.send(args), /destination/);
    else if (kind === "stale") await assert.rejects(handoff.send(args), /expansion timeout/);
    else {
      const result = await handoff.send(args);
      assert.equal(result.event, ["valid", "delayed"].includes(kind) ? "send_clicked" : kind === "preexisting" ? "composer_not_ready" :
        ["ambiguous", "stale", "reappeared"].includes(kind) ? "attachment_expansion_unconfirmed" : "prompt_mismatch", kind);
    }
    assert.equal(state.clicks, ["valid", "delayed"].includes(kind) ? 1 : 0, kind);
    assert.equal(state.expansions, ["ambiguous", "preexisting", "destination-changed"].includes(kind) ? 0 : 1, kind);
  }
});

test("requires preflight and exact input, and does not retry an ambiguous click", async () => {
  const { newHandoff, composer, state } = fixture();
  const handoff = newHandoff();
  let clicks = 0;
  const sendButton = { click: async () => {
    clicks++;
    throw new Error("browser did not acknowledge the click");
  } };
  const args = { composer, sendButton, plan: dispatch, temporaryChatUrl: state.chatUrl };
  await assert.rejects(
    handoff.send({ ...args, plan: { ...dispatch, transport: "codex-app-tools" } }),
    /temporary Chat preflight/,
  );
  await assert.rejects(handoff.send({ ...args, plan: { ...dispatch, live_checked: false } }), /fresh live/);
  const mismatch = await handoff.send({
    ...args, fillPrompt: text => composer.fill(text.replace("\n", " ")),
  });
  assert.equal(mismatch.event, "prompt_mismatch");
  assert.equal(clicks, 0);
  state.composer = "";
  await assert.rejects(handoff.send(args), /did not acknowledge/);
  assert.equal(state.composer, "First line.\nSecond line.");
  state.composer = "";
  await assert.rejects(newHandoff().send(args), /never attempted/);
  assert.equal(clicks, 1);
});

test("GitHub dispatch rejects missing or non-Personalized mode before touching the composer", async () => {
  const { newHandoff, composer, state } = fixture();
  const handoff = newHandoff();
  let clicks = 0;
  let selected = true;
  const args = { composer, sendButton: { click: async () => { clicks++; } }, temporaryChatUrl: state.chatUrl,
    verifyGitHubSelection: async () => selected };
  const githubPlan = { ...dispatch, checks: [{ tool: "GitHub" }] };
  for (const mode of [undefined, "unknown", "unpersonalized"]) {
    await assert.rejects(handoff.send({ ...args, plan: { ...githubPlan, temporary_chat_mode: mode } }), /Personalized/);
  }
  assert.equal(state.composer, "");
  assert.equal(clicks, 0);
  assert.equal((await handoff.send({ ...args, plan: { ...githubPlan, temporary_chat_mode: "personalized" },
    readComposer: async () => state.composer,
    fillPrompt: async text => { state.composer = text; selected = false; } })).event, "github_selection_unconfirmed");
  assert.equal(clicks, 0);
  state.composer = "";
  selected = true;
  // The UI may expose the chip alongside the body; the caller reads the body
  // separately and preserves the selected chip while filling.
  assert.equal((await handoff.send({ ...args, plan: { ...githubPlan, temporary_chat_mode: "personalized" },
    readComposer: async () => state.composer,
    fillPrompt: async text => { state.composer = text; } })).event, "send_clicked");
  assert.equal(clicks, 1);
});

test("dispatch binds the observed empty temporary destination before fill and click", async () => {
  const { newHandoff, composer, state } = fixture();
  const handoff = newHandoff();
  let clicks = 0;
  const args = { composer, plan: dispatch, temporaryChatUrl: state.chatUrl,
    sendButton: { click: async () => { clicks++; } } };
  for (const url of ["https://chatgpt.com/", "https://chatgpt.com/c/unrelated-existing-chat",
    "https://other.example/?temporary-chat=true", "https://chatgpt.com/?temporary-chat=true&temporary-chat=true"]) {
    state.chatUrl = url;
    await assert.rejects(handoff.send(args), /destination/);
    await assert.rejects(handoff.send({ ...args, temporaryChatUrl: url }), /destination/);
    assert.equal(state.composer, "");
  }
  state.chatUrl = args.temporaryChatUrl;
  await assert.rejects(handoff.send({ ...args, readComposer: async () => {
    state.chatUrl = "https://chatgpt.com/c/unrelated-existing-chat";
    return "";
  } }), /destination/);
  assert.equal(state.composer, "");
  state.chatUrl = args.temporaryChatUrl;
  await assert.rejects(handoff.send({ ...args, fillPrompt: async text => {
    await composer.fill(text);
    state.chatUrl = "https://chatgpt.com/c/unrelated-existing-chat";
  } }), /destination/);
  assert.equal(clicks, 0);
  state.composer = "";
  state.chatUrl = args.temporaryChatUrl;
  const githubPlan = { ...dispatch, checks: [{ tool: "GitHub" }], temporary_chat_mode: "personalized" };
  let checks = 0;
  await assert.rejects(handoff.send({ ...args, plan: githubPlan, verifyGitHubSelection: async () => {
    if (++checks === 2) state.chatUrl = "https://chatgpt.com/c/unrelated-existing-chat";
    return true;
  } }), /destination/);
  assert.equal(clicks, 0);
  state.composer = "";
  state.chatUrl = "https://chatgpt.com/?temporary-chat=true&example=observed";
  assert.equal((await handoff.send({ ...args, temporaryChatUrl: state.chatUrl })).event, "send_clicked");
  assert.equal(clicks, 1);
});

test("one locator wait signals the observed completion", async () => {
  const { newHandoff, state } = fixture();
  state.chatUrl = "https://chatgpt.com/c/sent-temporary-chat";
  let finish;
  let started;
  const waiting = new Promise(resolve => { started = resolve; });
  const completed = new Promise(resolve => { finish = resolve; });
  let calls = 0;
  const finished = { filter: () => ({
    first: () => ({ waitFor: async options => {
      calls++;
      assert.equal(options.state, "visible");
      assert.equal(options.timeoutMs, 1000);
      started();
      await completed;
    } }),
    count: async () => 1,
  }) };
  const result = newHandoff().wait({ finished, timeoutMs: 1000 });
  await waiting;
  finish();
  const signal = await result;
  assert.equal(signal.event, "reply_ready");
  assert.equal(signal.request_id, request_id);
  assert.equal(signal.input_sha256, input_sha256);
  assert.equal(calls, 1);
});

test("collects only a visible, correlated final reply and leaves its fields unchanged", async () => {
  const { newHandoff, state } = fixture();
  state.chatUrl = "https://chatgpt.com/c/sent-temporary-chat";
  const handoff = newHandoff();
  let done = false;
  const finished = { filter: () => ({ count: async () => Number(done) }) };
  const reply = { request_id, input_sha256, status: "completed", result: "Answer", evidence: [] };
  let displayed = JSON.stringify({ ...reply, input_sha256: "b".repeat(64) });
  const answer = {
    filter: () => ({ count: async () => 1, innerText: async () => displayed }),
  };
  const args = { plan: receive, finished, answer };
  assert.equal((await handoff.collect(args)).event, "completion_unconfirmed");
  done = true;
  assert.equal((await handoff.collect(args)).event, "reply_mismatch");
  assert.equal(state.submitted, "");
  assert.equal(state.saveClicks, 0);

  const finalJson = JSON.stringify(reply, null, 2);
  displayed = "```json\n" + finalJson + "\n```";
  assert.equal((await handoff.collect(args)).event, "save_clicked");
  assert.equal(state.submitted, finalJson);
  assert.equal(state.saveClicks, 1);
});

test("wait binds the first conversation URL and collection rejects a later same-origin move", async () => {
  const { newHandoff, state } = fixture();
  const handoff = newHandoff();
  const finished = {
    filter() { return this; }, first() { return this; }, count: async () => 1,
    waitFor: async () => { state.chatUrl = "https://chatgpt.com/c/sent-temporary-chat"; },
  };
  assert.equal((await handoff.wait({ finished, timeoutMs: 1000 })).event, "reply_ready");
  state.chatUrl = "https://chatgpt.com/c/another-chat";
  const answer = { filter() { return this; }, count: async () => 1,
    innerText: async () => JSON.stringify({ request_id, input_sha256, status: "completed", result: "Answer", evidence: [] }),
  };
  await assert.rejects(handoff.collect({ plan: receive, finished, answer }), /original Chat/);
  assert.equal(state.submitted, "");
  assert.equal(state.saveClicks, 0);
});

test("collection rechecks the conversation after reading the displayed reply", async () => {
  const { newHandoff, state } = fixture();
  state.chatUrl = "https://chatgpt.com/c/sent-temporary-chat";
  const handoff = newHandoff();
  const finished = { filter() { return this; }, count: async () => 1 };
  const answer = { filter() { return this; }, count: async () => 1,
    innerText: async () => {
      state.chatUrl = "https://chatgpt.com/c/another-chat";
      return JSON.stringify({ request_id, input_sha256, status: "completed", result: "Answer", evidence: [] });
    },
  };
  await assert.rejects(handoff.collect({ plan: receive, finished, answer }), /original Chat/);
  assert.equal(state.submitted, "");
  assert.equal(state.saveClicks, 0);
});
