// Load this definition once in the supported Browser REPL. The route owner
// supplies locators observed in the current tabs and the existing CLI checks.
function isProTemporaryDestination(url) {
  try {
    const parsed = new URL(url);
    const modes = parsed.searchParams.getAll("temporary-chat");
    return parsed.origin === "https://chatgpt.com" && parsed.pathname === "/" &&
      !parsed.username && !parsed.password && modes.length === 1 && modes[0] === "true";
  } catch {
    return false;
  }
}

function isProConversationDestination(url) {
  try {
    const parsed = new URL(url);
    const modes = parsed.searchParams.getAll("temporary-chat");
    return parsed.origin === "https://chatgpt.com" && /^\/c\/[a-z0-9-]+$/i.test(parsed.pathname) &&
      !parsed.username && !parsed.password &&
      (modes.length === 0 || (modes.length === 1 && modes[0] === "true"));
  } catch {
    return false;
  }
}

const proHandoff = (() => {
  const attempted = new Set();
  const visible = locator => locator.filter({ visible: true });
  const readEditor = field => field.evaluate(element => {
    const editable = element.getAttribute?.("contenteditable");
    const prosemirror = (element.getAttribute?.("class") ?? "").split(/\s+/).includes("ProseMirror");
    if (editable === "true" || editable === "plaintext-only" || element.isContentEditable || prosemirror) {
      if (editable !== "true" || element.tagName !== "DIV" || !prosemirror) return null;
      const paragraphs = Array.from(element.childNodes);
      const textOrBreak = node => node.nodeType === 3 || (node.nodeType === 1 && node.tagName === "BR");
      const plainSpan = node => node.nodeType === 1 && node.tagName === "SPAN" &&
        (node.attributes.length === 0 || (node.attributes.length === 1 &&
          node.getAttribute("data-prompt-literal-paste") !== null)) &&
        Array.from(node.childNodes).every(textOrBreak);
      if (!paragraphs.length || paragraphs.some(paragraph => paragraph.nodeType !== 1 ||
          paragraph.tagName !== "P" || Array.from(paragraph.childNodes).some(node =>
            !textOrBreak(node) && !plainSpan(node)))) return null;
      if (paragraphs.length === 1 && paragraphs[0].childNodes.length === 1 &&
          paragraphs[0].childNodes[0].nodeType === 1 && paragraphs[0].childNodes[0].tagName === "BR" &&
          paragraphs[0].textContent === "") return "";
      return paragraphs.map(paragraph => paragraph.innerText).join("\n");
    }
    return element.value ?? element.innerText;
  });
  const isProseMirrorEditor = field => field.evaluate(element => element.tagName === "DIV" &&
    element.getAttribute?.("contenteditable") === "true" &&
    (element.getAttribute("class") ?? "").split(/\s+/).includes("ProseMirror"));

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
    let conversationUrl;

    async function checkChat() {
      const current = await chatTab.url();
      const conversation = isProConversationDestination(current);
      if ((!isProTemporaryDestination(current) && !conversation) ||
          (conversationUrl && current !== conversationUrl)) {
        throw new Error("The original Chat destination is unavailable or changed.");
      }
      // Bind the first conversation reached from the temporary composer.
      if (conversation) conversationUrl = current;
    }

    async function checkTransfer() {
      if (await transferTab.url() !== url) {
        throw new Error("The bound loopback transfer form is unavailable.");
      }
    }

    async function checkDestination(expected) {
      const temporaryOn = /^(?:一時チャットをオフにする|Turn off temporary chat)$/i;
      if (!isProTemporaryDestination(expected) || await chatTab.url() !== expected ||
          await visible(chatTab.playwright.getByRole("button", { name: temporaryOn })).count() !== 1 ||
          await chatTab.url() !== expected) {
        throw new Error("The observed new Temporary Chat destination is unavailable or changed.");
      }
    }

    return {
      async send({ plan, composer, sendButton, temporaryChatUrl, readComposer = readEditor, fillPrompt, verifyGitHubSelection }) {
        requirePlan(plan, "chat_candidate", "send_once");
        if (plan.live_checked !== true || attempted.has(request_id)) {
          throw new Error("Send requires fresh live checks and a request never attempted.");
        }
        const needsGitHub = plan.checks?.some(check => check.tool?.toLowerCase() === "github");
        if (needsGitHub &&
            plan.temporary_chat_mode !== "personalized") {
          throw new Error("GitHub requires a Personalized observation in this new Temporary Chat.");
        }
        await checkDestination(temporaryChatUrl);
        if (needsGitHub && (typeof verifyGitHubSelection !== "function" || await verifyGitHubSelection() !== true)) {
          return signal("github_selection_unconfirmed");
        }
        await checkTransfer();
        const prompt = await readEditor(form.getByLabel("Request prompt", { exact: true }));
        if (!prompt || await readComposer(composer) !== "") {
          return signal("composer_not_ready");
        }
        await checkDestination(temporaryChatUrl);
        if (fillPrompt) {
          await fillPrompt(prompt);
        } else {
          await composer.fill(prompt);
          if (await isProseMirrorEditor(composer)) {
            const line = prompt.split("\n").find(part => part.length > 0);
            if (line) await composer.getByText(line, { exact: false }).first()
              .waitFor({ state: "visible", timeoutMs: 15_000 });
          }
        }
        if (await readComposer(composer) !== prompt) {
          return signal("prompt_mismatch");
        }
        if (needsGitHub && await verifyGitHubSelection() !== true) {
          return signal("github_selection_unconfirmed");
        }
        await checkDestination(temporaryChatUrl);
        if (attempted.has(request_id)) {
          throw new Error("This request has already been attempted.");
        }
        // Claim before clicking. An ambiguous click must not be retried.
        attempted.add(request_id);
        await sendButton.click();
        return signal("send_clicked");
      },

      async wait({ finished, attention, quotaAttention, timeoutMs,
        waitFor = (locator, options) => locator.waitFor(options) }) {
        if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 3_600_000) {
          throw new Error("Set a bounded Browser wait of 1 to 3600000 ms.");
        }
        await checkChat();
        let terminal = attention ? finished.or(attention) : finished;
        if (quotaAttention) terminal = terminal.or(quotaAttention);
        await waitFor(visible(terminal).first(), { state: "visible", timeoutMs });
        await checkChat();
        if (attention && await visible(attention).count()) {
          return signal("attention_required");
        }
        if (await visible(finished).count() === 1) return signal("reply_ready");
        if (quotaAttention && await visible(quotaAttention).count()) return signal("quota_exhausted");
        return signal("completion_unconfirmed");
      },

      async collect({ plan, finished, answer, includeReply = false }) {
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
        await checkChat();
        await checkTransfer();
        await form.getByLabel("Response JSON", { exact: true }).fill(response);
        await checkChat();
        await checkTransfer();
        await form.getByRole("button", { name: "Save verified response", exact: true }).click();
        // The CLI emits the authoritative saved event after validation and write.
        const submitted = signal("save_clicked");
        return includeReply ? { ...submitted, reply: identity } : submitted;
      },
    };
  };
})();

// One exchange in the documented Browser REPL. The local transfer helper
// evaluates both plans and acknowledges saving.
const runProHandoff = (() => {
  const started = new Set();
  const chatUrl = "https://chatgpt.com/";
  const uiTimeout = 15_000;
  const pro = /^(?:Pro|6 Pro|GPT-6 Pro)$/i;
  const pickerName = /^(?:ChatGPT モデルを選択|Select model|Choose model|Choose a model)$/i;
  const temporaryOn = /^(?:一時チャットをオフにする|Turn off temporary chat)$/i;
  const temporaryOff = /^(?:一時チャット|一時チャットをオンにする|Temporary Chat|Turn on temporary chat)$/i;
  const personalized = /^(?:Personalized|パーソナライズ)$/i;
  const unpersonalized = /^(?:Unpersonalized|パーソナライズなし)$/i;
  const personalizedChoice = /^(?:Personalized|パーソナライズ)(?:\s|$)/i;
  const composerName = /^(?:ChatGPT に聞く|Ask ChatGPT|Message ChatGPT|Prompt)$/i;
  const sendName = /^(?:送信|メッセージを送信|Send|Send message|Send prompt)$/i;
  const stopName = /^(?:停止|生成を停止する?|回答の生成を停止する?|応答を停止する?|Stop|Stop generating|Stop response|Stop streaming)$/i;
  const copyName = /^(?:コピー|コピーする|回答をコピーする?|Copy|Copy response)$/i;
  const userHeading = "あなたの発言:";
  const assistantHeading = "ChatGPT の発言:";
  const quotaText = /利用上限|usage limit|limit reached|reached (?:your|the) limit/i;
  const selectorTimeout = /^(?:Error:\s*)?(?:Playwright selector deadline exceeded\b|Timed out after \d+ms waiting for selector\b)/;
  const answerSelector = '[data-message-author-role="assistant"]';
  class WaitTimeoutError extends Error {}

  const button = (scope, name) => scope.getByRole("button", { name, exact: true });
  const visible = locator => locator.filter({ visible: true });
  const field = (scope, name) => scope.getByLabel(name, { exact: true });
  const readJson = async locator => JSON.parse(await locator.evaluate(element => element.value));
  const quotaAlert = pw => pw.getByRole("alert").filter({ hasText: quotaText });

  async function observedButton(pw, candidate) {
    await candidate.waitFor({ state: "visible", timeoutMs: uiTimeout });
    const name = await candidate.evaluate(element =>
      element.getAttribute("aria-label") ?? element.getAttribute("title") ?? element.innerText.trim(),
    );
    if (!name) throw new Error("The button has no observed name.");
    const exact = button(pw, name);
    if (await exact.count() !== 1) throw new Error("The observed button is ambiguous.");
    return exact;
  }

  async function ordinaryChat(pw, allowMissing = false) {
    const chat = button(pw, "Chat");
    const work = button(pw, "Work");
    const chatCount = await chat.count();
    const workCount = await work.count();
    if (allowMissing && chatCount === 0 && workCount === 0) return true;
    return chatCount === 1 && workCount === 1 &&
      await chat.getAttribute("aria-pressed") === "true" &&
      await work.getAttribute("aria-pressed") === "false";
  }

  async function temporaryChatContext(tab, { phase = "dispatch" } = {}) {
    const pw = tab.playwright;
    const current = await tab.url();
    if (!await ordinaryChat(pw, true) || !(isProTemporaryDestination(current) ||
        (phase === "receive" && isProConversationDestination(current)))) return false;
    if (await visible(button(pw, temporaryOn)).count() === 1) return true;
    if (phase !== "receive") return false;
    const parsed = new URL(current);
    const modes = parsed.searchParams.getAll("temporary-chat");
    return /^\/c\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(parsed.pathname) &&
      modes.length === 1 && modes[0] === "true" && Array.from(parsed.searchParams).length === 1 && !parsed.hash &&
      await visible(pw.getByText("一時チャット", { exact: true })).count() === 1;
  }

  async function modeInChat(tab, expectedUrl, { select = false } = {}) {
    const pw = tab.playwright;
    if (await tab.url() !== expectedUrl || !isProTemporaryDestination(expectedUrl) ||
        !await temporaryChatContext(tab)) return false;
    const yes = visible(button(pw, personalized));
    const no = visible(button(pw, unpersonalized));
    const yesCount = await yes.count();
    const noCount = await no.count();
    if (yesCount === 1 && noCount === 0) return true;
    if (!select || yesCount !== 0 || noCount !== 1) return false;
    await no.click({ timeoutMs: uiTimeout });
    const menu = visible(pw.getByRole("menu", { name: unpersonalized }));
    if (await menu.count() !== 1) return false;
    const choice = visible(menu.getByRole("menuitemradio", { name: personalizedChoice }));
    if (await choice.count() !== 1 || await choice.getAttribute("aria-checked") !== "false" ||
        await tab.url() !== expectedUrl) return false;
    await choice.click({ timeoutMs: uiTimeout });
    return await tab.url() === expectedUrl && await yes.count() === 1 && await no.count() === 0;
  }

  async function openTemporary(tab) {
    await tab.goto(chatUrl);
    const pw = tab.playwright;
    const chat = button(pw, "Chat");
    await chat.waitFor({ state: "visible", timeoutMs: uiTimeout });
    const work = button(pw, "Work");
    if (await chat.getAttribute("aria-pressed") === "false" && await work.count() === 1 &&
        await work.getAttribute("aria-pressed") === "true") {
      await chat.click({ timeoutMs: uiTimeout });
    }
    if (!await ordinaryChat(pw)) return false;
    // The model picker appears after the Chat controls are ready for input.
    await button(pw, pickerName).waitFor({ state: "visible", timeoutMs: uiTimeout });
    const enabled = button(pw, temporaryOn);
    if (await enabled.count() === 0) {
      const toggle = await observedButton(pw, button(pw, temporaryOff));
      await toggle.click({ timeoutMs: uiTimeout });
    }
    await enabled.waitFor({ state: "visible", timeoutMs: uiTimeout });
    return await temporaryChatContext(tab) && await pw.locator("main [data-message-author-role]").count() === 0 &&
      await pw.getByRole("heading", { name: userHeading, exact: true }).count() === 0 &&
      await pw.getByRole("heading", { name: assistantHeading, exact: true }).count() === 0;
  }

  async function menuState(pw) {
    return pw.evaluate(() => {
      const menus = Array.from(document.querySelectorAll('[role="menu"]'))
        .filter(menu => menu.getClientRects().length);
      if (menus.length !== 1) return null;
      const menu = menus[0];
      const lines = menu.innerText.split("\n").map(line => line.trim()).filter(Boolean);
      const slider = menu.querySelector('[role="slider"]');
      const now = slider?.getAttribute("aria-valuenow");
      const max = slider?.getAttribute("aria-valuemax");
      const power = slider?.closest('[role="menuitem"]');
      // innerText includes visually hidden screen-reader text. Only count rendered
      // text, or descriptions explicitly attached to the current control.
      const referenced = new Set();
      for (const control of [slider, power]) {
        for (const attribute of ["aria-describedby", "aria-labelledby"]) {
          for (const id of (control?.getAttribute(attribute) ?? "").split(/\s+/).filter(Boolean)) {
            const target = document.getElementById(id);
            if (target) referenced.add(target);
          }
        }
      }
      const relevant = node => {
        for (let element = node.parentElement; element; element = element.parentElement) {
          if (referenced.has(element)) return true;
          if (element === menu) break;
        }
        for (let element = node.parentElement; element; element = element.parentElement) {
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          if (style.display === "none" || style.visibility !== "visible" || Number(style.opacity) === 0 ||
              (style.display !== "contents" && rect.width <= 1 && rect.height <= 1 &&
                [style.overflow, style.overflowX, style.overflowY].some(value => value === "hidden" || value === "clip")) ||
              /^rect\(0(?:px)?[,\s]+0(?:px)?[,\s]+0(?:px)?[,\s]+0(?:px)?\)$/.test(style.clip) ||
              /^inset\(50%(?:\s+50%){0,3}\)$/.test(style.clipPath)) return false;
          if (element === menu) break;
        }
        return true;
      };
      const stateText = [];
      const collect = element => {
        const block = element !== menu && /^(?:block|flow-root|flex|grid|table|list-item)/.test(getComputedStyle(element).display);
        const boundary = (block || element.tagName === "BR") && relevant({ parentElement: element });
        if (boundary) stateText.push(" ");
        for (const node of element.childNodes) {
          if (node.nodeType === 3 && relevant(node)) stateText.push(node.textContent);
          else if (node.nodeType === 1) collect(node);
        }
        if (block && boundary) stateText.push(" ");
      };
      collect(menu);
      for (const target of referenced) {
        stateText.push(" ", target.textContent, " ");
      }
      const state = stateText.join("");
      return {
        model: lines[0],
        current: now == null ? null : Number(now),
        maximum: max == null ? null : Number(max),
        powerFocused: Boolean(power && document.activeElement === power),
        limited: /利用上限|usage limit|limit reached|reached (?:your|the) limit/i.test(state),
        locked: /ロック中|\blocked\b/i.test(state),
      };
    });
  }

  async function observePro(tab, { select = false, phase = "dispatch" } = {}) {
    const pw = tab.playwright;
    if (!await temporaryChatContext(tab, { phase })) {
      return { reason: "temporary_chat_unconfirmed" };
    }
    const picker = button(pw, pickerName);
    await picker.waitFor({ state: "visible", timeoutMs: uiTimeout });
    if (!await temporaryChatContext(tab, { phase })) return { reason: "temporary_chat_unconfirmed" };
    if (await picker.getAttribute("aria-expanded") !== "true") {
      await (await observedButton(pw, picker)).click({ timeoutMs: uiTimeout });
    }
    await pw.getByRole("menu").waitFor({ state: "visible", timeoutMs: uiTimeout });
    let menu = await menuState(pw);
    const original = menu?.current;
    // The current Chat menu exposes a focused Power control and discrete steps.
    // Advance only through observed values and stop at an actual Pro label.
    if (select && Number.isSafeInteger(original) && Number.isSafeInteger(menu.maximum) &&
        menu.maximum - original <= 8) {
      while (!pro.test(menu.model ?? "") && menu.powerFocused && menu.current < menu.maximum) {
        if (!await temporaryChatContext(tab, { phase })) return { reason: "temporary_chat_unconfirmed" };
        const previous = menu.current;
        await tab.pressKey(null, "Right");
        menu = await menuState(pw);
        if (!menu || menu.current !== previous + 1) break;
      }
    }
    const limited = menu?.limited || await visible(quotaAlert(pw)).count() > 0;
    const reason = !menu || !pro.test(menu.model ?? "") ? "pro_not_observed" :
      limited && phase === "dispatch" ? "quota_exhausted" : menu.locked && !limited ? "pro_locked" : null;
    if (reason && select && Number.isSafeInteger(original)) {
      // Put the picker back if exploring from a different Power reached a lock.
      while (menu?.powerFocused && menu.current > original) {
        if (!await temporaryChatContext(tab, { phase })) return { reason: "temporary_chat_unconfirmed" };
        const previous = menu.current;
        await tab.pressKey(null, "Left");
        menu = await menuState(pw);
        if (!menu || menu.current !== previous - 1) break;
      }
    }
    if (reason) return { reason };
    await tab.pressKey(null, "Escape");
    const model = (await picker.innerText({ timeoutMs: uiTimeout })).trim();
    if (!pro.test(model)) return { reason: "pro_selection_unconfirmed" };
    return {
      live: { surface: "chat-temporary", model, quota_state: limited ? "exhausted" : "no_limit_notice",
        observed_at: new Date().toISOString() },
    };
  }

  async function planFor(tab, listening, phase, live) {
    if (await tab.url() !== listening.url) throw new Error("Transfer form changed.");
    const form = tab.playwright;
    await field(form, "Live observation").fill(JSON.stringify(live), { timeoutMs: uiTimeout });
    const check = button(form, phase === "dispatch" ? "Check dispatch" : "Check receive");
    await form.expectNavigation(() => check.click({ timeoutMs: uiTimeout }), {
      url: listening.url, waitUntil: "domcontentloaded", timeoutMs: uiTimeout,
    });
    if (await tab.url() !== listening.url) throw new Error("Transfer form changed.");
    const result = await readJson(field(form, "Preflight result"));
    if (result?.phase !== phase || !matchesRequest(result, listening) || !result.plan) {
      throw new Error("Preflight result could not be correlated.");
    }
    return result.plan;
  }

  function matchesRequest(value, listening) {
    return value?.request_id === listening.request_id && value.input_sha256 === listening.input_sha256;
  }

  async function disposeTabs(cua, chat, transfer, retainChat) {
    for (const tab of [transfer, retainChat ? null : chat]) {
      try {
        if (tab) await tab.close();
      } catch {
        // The returned metadata distinguishes unconfirmed closure.
      }
    }
    try {
      const open = await cua.listTabs({ browser: "iab", emit: false });
      return {
        chat_closed: chat ? !open.some(tab => tab.id === chat.id) : true,
        transfer_closed: transfer ? !open.some(tab => tab.id === transfer.id) : true,
        handoff_marked: retainChat,
      };
    } catch {
      return { chat_closed: null, transfer_closed: null, handoff_marked: retainChat };
    }
  }

  return async function ({ cua, listening, timeoutMs = 270_000, prepareTools }) {
    if (listening?.event !== "listening" || listening.workflow !== true ||
        typeof listening.request_id !== "string" || typeof listening.needs_tools !== "boolean" ||
        (listening.needs_github !== undefined && typeof listening.needs_github !== "boolean") ||
        (listening.needs_github === true && !listening.needs_tools) ||
        !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 3_600_000) {
      return { event: "blocked", phase: "setup", reason: "invalid_workflow_input" };
    }
    const { request_id, input_sha256 } = listening;
    const result = (event, fields) => ({ event, request_id, input_sha256, ...fields });
    if (listening.needs_tools && typeof prepareTools !== "function") {
      return result("blocked", { phase: "setup", reason: "tool_selection_adapter_required" });
    }
    if (started.has(request_id)) return result("blocked", { phase: "setup", reason: "already_started" });
    started.add(request_id);
    let chat;
    let transfer;
    let attempted = false;
    let phase = "open";
    const stop = reason => result(attempted ? "pending" : "blocked", { phase, reason });

    async function exchange() {
      chat = await cua.createBrowserTab("iab", "about:blank", { visible: false });
      if (!await openTemporary(chat)) return stop("empty_temporary_chat_unconfirmed");
      const temporaryChatUrl = await chat.url();
      if (!isProTemporaryDestination(temporaryChatUrl)) return stop("temporary_chat_destination_unconfirmed");
      phase = "model";
      let observation = await observePro(chat, { select: true });
      if (observation.reason) return stop(observation.reason);
      if (listening.needs_github === true && !await modeInChat(chat, temporaryChatUrl, { select: true })) {
        return stop("personalized_unconfirmed");
      }
      // Scope and connector authority are still checked by the existing plan.
      // An adapter performs only the permitted UI actions inside this call.
      const tools = listening.needs_tools ? await prepareTools(chat) : {};
      if (!tools) return stop("tool_selection_unconfirmed");
      if (listening.needs_github === true && !await modeInChat(chat, temporaryChatUrl)) {
        return stop("personalized_unconfirmed");
      }
      if (listening.needs_tools) {
        observation = await observePro(chat);
        if (observation.reason) return stop(observation.reason);
      }
      const pw = chat.playwright;
      const composer = pw.getByRole("textbox", { name: composerName });
      await composer.waitFor({ state: "visible", timeoutMs: uiTimeout });
      phase = "transfer";
      transfer = await cua.createBrowserTab("iab", "about:blank", { visible: false });
      const handoff = proHandoff({ chatTab: chat, transferTab: transfer, listening });
      await transfer.goto(listening.url);
      phase = "dispatch";
      const dispatch = await planFor(transfer, listening, "dispatch", {
        ...observation.live,
        temporary_chat_mode: listening.needs_github === true ? "personalized" : tools.temporaryChatMode ?? "unknown",
      });
      if (dispatch.next_action !== "send_once") return stop(dispatch.reason ?? "dispatch_unconfirmed");
      // Older listening events may only identify GitHub in the dispatch plan.
      if (listening.needs_github !== true &&
          dispatch.checks?.some(check => check.tool?.toLowerCase() === "github") &&
          !await modeInChat(chat, temporaryChatUrl)) {
        return stop("personalized_unconfirmed");
      }
      phase = "send";
      const sent = await handoff.send({
        plan: dispatch, composer, temporaryChatUrl,
        sendButton: { click: async () => {
          const send = await observedButton(pw, button(pw, sendName));
          // Keep the original tab even if the outer REPL call is interrupted.
          await chat.markHandoff();
          const current = button(pw, pickerName);
          if (!await temporaryChatContext(chat) ||
              !pro.test((await current.innerText({ timeoutMs: uiTimeout })).trim()) ||
              await chat.url() !== temporaryChatUrl) {
            throw new Error("Pro selection changed before sending.");
          }
          attempted = true;
          await send.click({ timeoutMs: uiTimeout });
        } },
        readComposer: tools.readComposer, fillPrompt: tools.fillPrompt,
        verifyGitHubSelection: tools.verifyGitHubSelection,
      });
      if (sent.event !== "send_clicked") return stop(sent.event);
      phase = "wait";
      const assistant = pw.locator("main article").filter({ has: pw.locator(answerSelector) });
      const legacyFinished = button(assistant, copyName);
      const headingFinished = button(pw.locator(".turn-action-controls"), "コピーする");
      const finished = legacyFinished.or(headingFinished);
      const attention = pw.getByRole("alert").filter({
        hasText: /問題が発生|生成でき|something went wrong|error generating/i,
      });
      const quotaAttention = quotaAlert(pw);
      const generating = visible(button(pw, stopName));
      const deadline = Date.now() + timeoutMs;
      const waitFor = async (locator, { state }) => {
        // The IAB caps each locator wait independently of the REPL deadline.
        for (let remaining = deadline - Date.now(); remaining > 0; remaining = deadline - Date.now()) {
          try {
            await locator.waitFor({ state, timeoutMs: Math.min(2_500, remaining) });
            return;
          } catch (error) {
            if (!selectorTimeout.test(error?.message ?? "")) throw error;
          }
        }
        throw new WaitTimeoutError();
      };
      const waitOptions = { finished, attention, timeoutMs, waitFor };
      let ready = await handoff.wait({ ...waitOptions, quotaAttention });
      if (ready.event === "quota_exhausted" && await generating.count() !== 0) {
        ready = await handoff.wait(waitOptions);
      }
      if (ready.event !== "reply_ready") return stop(ready.event);
      if (await generating.count() !== 0) {
        await waitFor(generating.first(), { state: "hidden" });
      }
      phase = "receive";
      observation = await observePro(chat, { phase });
      if (observation.reason) return stop(observation.reason);
      const receive = await planFor(transfer, listening, "receive", observation.live);
      if (receive.next_action !== "collect_reply") return stop(receive.reason ?? "receive_unconfirmed");
      const message = assistant.locator(answerSelector);
      const jsonBlock = visible(message.locator("pre code"));
      let answer;
      if (await visible(legacyFinished).count() === 1) {
        answer = await jsonBlock.count() === 1 ? jsonBlock : message;
      } else {
        const heading = pw.getByRole("heading", { name: assistantHeading, exact: true });
        const user = pw.getByRole("heading", { name: userHeading, exact: true });
        const readSibling = locator => locator.evaluate(element => {
          const sibling = element.nextElementSibling;
          return sibling?.tagName === "DIV" && sibling.getClientRects().length ? sibling.innerText : null;
        });
        answer = {
          filter() { return this; },
          async count() {
            if (await heading.count() !== 1 || await user.count() !== 1 ||
                await visible(headingFinished).count() !== 1) return 0;
            const userText = await readSibling(user);
            return typeof userText === "string" &&
              userText.includes(`Set request_id to ${request_id} and input_sha256 to ${input_sha256}.`) &&
              await readSibling(heading) !== null ? 1 : 0;
          },
          innerText: () => readSibling(heading),
        };
      }
      phase = "save";
      const submission = await handoff.collect({ plan: receive, finished, answer, includeReply: true });
      if (submission.event !== "save_clicked") return stop(submission.event);
      const savedField = field(transfer.playwright, "Saved result");
      await savedField.waitFor({ state: "visible", timeoutMs: uiTimeout });
      const saved = await readJson(savedField);
      if (await transfer.url() !== listening.url || saved.event !== "saved" ||
          !matchesRequest(saved, listening) || saved.status !== submission.reply.status) {
        return stop("save_unconfirmed");
      }
      return { ...saved, completion: ready.event, quota_state: observation.live.quota_state, reply: submission.reply };
    }

    let outcome;
    try {
      outcome = await exchange();
    } catch (error) {
      outcome = stop(error instanceof WaitTimeoutError ? "wait_timeout" : "browser_operation_unconfirmed");
    }
    const retainChat = outcome.event === "pending";
    const cleanup = await disposeTabs(cua, chat, transfer, retainChat);
    return { ...outcome, ...(cleanup.handoff_marked && cleanup.chat_closed === false && { tab_id: chat.id }), cleanup };
  };
})();
