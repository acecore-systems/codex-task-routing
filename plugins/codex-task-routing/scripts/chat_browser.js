// Load this definition once in the supported Browser REPL. The route owner
// supplies locators observed in the current tabs and the existing CLI checks.
const proHandoff = (() => {
  const attempted = new Set();
  const visible = locator => locator.filter({ visible: true });
  const readEditor = field => field.evaluate(element => element.value ?? element.innerText);

  function requirePlan(plan, route, action) {
    if (plan?.route !== route || plan.next_action !== action ||
        plan.transport !== "browser-temporary" || plan.preflight_complete !== true) {
      throw new Error("The matching temporary Chat preflight did not pass.");
    }
  }

  return function ({ chatTab, transferTab, listening }) {
    const { request_id, input_sha256, url } = listening;
    if (listening.event !== "listening" || !request_id || !/^[a-f0-9]{64}$/.test(input_sha256) ||
        !/^http:\/\/127\.0\.0\.1:[0-9]+\/transfer\/[\w-]+$/.test(url)) {
      throw new Error("Use the local transfer helper's listening event.");
    }
    const signal = event => ({ event, request_id, input_sha256 });
    const form = transferTab.playwright;

    async function checkChat() {
      if (new URL(await chatTab.url()).origin !== "https://chatgpt.com") {
        throw new Error("The original Chat tab is unavailable.");
      }
    }

    async function checkTransfer() {
      if (await transferTab.url() !== url) {
        throw new Error("The bound loopback transfer form is unavailable.");
      }
    }

    return {
      async send({ plan, composer, sendButton, readComposer = readEditor, fillPrompt, verifyGitHubSelection }) {
        requirePlan(plan, "chat_candidate", "send_once");
        if (plan.live_checked !== true || attempted.has(request_id)) {
          throw new Error("Send requires fresh live checks and a request never attempted.");
        }
        const needsGitHub = plan.checks?.some(check => check.tool?.toLowerCase() === "github");
        if (needsGitHub &&
            plan.temporary_chat_mode !== "personalized") {
          throw new Error("GitHub requires a Personalized observation in this new Temporary Chat.");
        }
        await checkChat();
        if (needsGitHub && (typeof verifyGitHubSelection !== "function" || await verifyGitHubSelection() !== true)) {
          return signal("github_selection_unconfirmed");
        }
        await checkTransfer();
        const prompt = await readEditor(form.getByLabel("Request prompt", { exact: true }));
        if (!prompt || await readComposer(composer) !== "") {
          return signal("composer_not_ready");
        }
        if (fillPrompt) {
          await fillPrompt(prompt);
        } else {
          await composer.fill(prompt);
        }
        if (await readComposer(composer) !== prompt) {
          return signal("prompt_mismatch");
        }
        await checkChat();
        if (needsGitHub && await verifyGitHubSelection() !== true) {
          return signal("github_selection_unconfirmed");
        }
        if (attempted.has(request_id)) {
          throw new Error("This request has already been attempted.");
        }
        // Claim before clicking. An ambiguous click must not be retried.
        attempted.add(request_id);
        await sendButton.click();
        return signal("send_clicked");
      },

      async wait({ finished, attention, timeoutMs }) {
        if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 3_600_000) {
          throw new Error("Set a bounded Browser wait of 1 to 3600000 ms.");
        }
        await checkChat();
        const terminal = attention ? finished.or(attention) : finished;
        await visible(terminal).first().waitFor({ state: "visible", timeoutMs });
        await checkChat();
        if (attention && await visible(attention).count()) {
          return signal("attention_required");
        }
        return signal(await visible(finished).count() === 1 ? "reply_ready" : "completion_unconfirmed");
      },

      async collect({ plan, finished, answer }) {
        requirePlan(plan, "chat_collection", "collect_reply");
        if (plan.request_id !== request_id || plan.input_sha256 !== input_sha256) {
          throw new Error("The receive preflight belongs to another request.");
        }
        await checkChat();
        const finalAnswer = visible(answer);
        if (await visible(finished).count() !== 1 || await finalAnswer.count() !== 1) {
          return signal("completion_unconfirmed");
        }
        const displayed = await finalAnswer.innerText();
        const fence = /^```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```$/i.exec(displayed.trim());
        const response = fence ? fence[1] : displayed;
        let identity;
        try {
          identity = JSON.parse(response);
        } catch {
          return signal("invalid_reply");
        }
        if (identity?.request_id !== request_id || identity.input_sha256 !== input_sha256) {
          return signal("reply_mismatch");
        }
        await checkTransfer();
        await form.getByLabel("Response JSON", { exact: true }).fill(response);
        await checkTransfer();
        await form.getByRole("button", { name: "Save verified response", exact: true }).click();
        // The CLI emits the authoritative saved event after validation and write.
        return signal("save_clicked");
      },
    };
  };
})();
