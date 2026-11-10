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
  const state = { composer: "", submitted: "", saveClicks: 0 };
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
    chatTab: { url: async () => "https://chatgpt.com/" }, transferTab, listening,
  });
  return { newHandoff, composer, state };
}

test("requires preflight and exact input, and does not retry an ambiguous click", async () => {
  const { newHandoff, composer, state } = fixture();
  const handoff = newHandoff();
  let clicks = 0;
  const sendButton = { click: async () => {
    clicks++;
    throw new Error("browser did not acknowledge the click");
  } };
  const args = { composer, sendButton, plan: dispatch };
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
  const args = { composer, sendButton: { click: async () => { clicks++; } },
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

test("one locator wait signals the observed completion", async () => {
  const { newHandoff } = fixture();
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
