# Browser local transfer

The selected model label may be `Pro`, `6 Pro` or `GPT-6 Pro`; this is not a subscription badge or backend attestation. Use [operation-specific preflight](chat-authorization.md): inspect before dispatch, reuse existing scoped approvals, then send once. An additional transport_evidence object is not required. Actual host denials and unresolved provider conditions still stop the affected operation.


`chat_transfer.py` is a local loopback helper for an already approved
`bundle.json`. It only renders that bundle's prepared `prompt` in a readonly
Browser form and accepts one JSON response for one explicitly named
`reply.json`. It does not open or control Chat, use an API, access an account,
authentication, conversations, memories, MCP, or any external network.

```text
python <plugin-root>/scripts/chat_transfer.py --bundle bundle.json --reply reply.json --timeout 600
```

Its compact stdout metadata contains the random loopback URL, request ID,
input hash, and lifecycle event. It never prints the prompt or response. The
server binds only `127.0.0.1` on a random port; its unguessable URL token,
strict Host and Origin checks, no-store headers, CSP, form-size limit, and
link/reparse-safe input/output checks constrain the transfer. It stops after a
valid submission, when its timeout expires, or with Ctrl+C.

Open the printed URL using the supported Browser. The stable `Request prompt`
readonly field and `Response JSON` field are plain HTML DOM controls, so a
Browser automation surface can read and fill them without page-script
evaluation. Move the prompt to a new, approved temporary normal Chat only
after the request owner (root or an eligible root-direct Sol child) has confirmed the content is authorized for that destination,
the selected UI model is `Pro`, and the temporary-Chat conditions hold. The
helper does not make those checks and does not send anything itself.

Choose supported Browser operations that do not automatically emit the full
page on navigation. Use only tab/navigation and field-reading operations actually exposed by
the current Browser runtime; do not assume a method name or add another driver. A wrapper that automatically
prints its initial AX tree can echo the entire prompt and defeat this saving.
Keep the prompt and final answer in Browser-runtime variables; report only
length, equality, selected model, and the save result to the parent. Never
read browser storage, session state or private application APIs to move data.

If a long pasted prompt appears as a text attachment, use the observed
"Show in text field" (「テキスト欄に表示」) control to expand it back into the
composer before checking the exact body. Do not send based only on an
attachment filename or thumbnail. If no supported expansion is available,
stop and return the limitation.

If the Chat UI adds a connector chip while selecting an approved source, keep
the prompt-text equality check separate from the chip's UI verification. UI
sentinels or spacing added by that chip are not part of the prepared prompt
and must not be copied into it.

Some Chat composer versions convert source URLs to chips and add display-only
newlines to `innerText`. Do not blindly trim or remove newlines to force a
match. Inspect the current editable DOM once; when it uses observed paragraph,
text and `BR` nodes, serialize text nodes, preserve each `BR` as a newline,
and preserve paragraph boundaries. Compare the exact prepared prompt plus only
the separately observed initial connector-chip prefix. Unknown structure or
an actual mismatch blocks sending. No element IDs or DOM schema are guaranteed
across Chat UI updates; reuse the supported controls observed in that session.

Read the completed assistant response from its observed DOM container into a
variable. If it has exactly one Markdown JSON fence, remove only that fence
mechanically; do not rewrite fields. Fill `Response JSON` from the variable
and submit through the normal form. Do not print the full response as an
intermediate step, send it to another model for formatting, or use a network
request to Chat as a shortcut.

Paste the final Chat answer as the required response JSON and choose `Save
verified response`. The helper calls `chatgpt_route.validate_exchange` before
writing. An identical retry is idempotent; a different value can never replace
an existing reply. `Transfer status` distinguishes a saved server submission,
the response format/correlation check, and `status: completed`. Even a
completed status is not semantic acceptance: the request owner must still assess the
answer against the task's acceptance criteria, evidence, scope, and UI model
confirmation.

If the local form shows a connection error after submission, check the
explicit reply target and the helper lifecycle event before deciding whether
a local save is missing. Revalidate an existing reply against the same bundle;
a matching file and `saved` event establish local receipt, even when the
Browser failed to show the success document. They do not establish semantic
acceptance. If collection must be retried, restore only this local form with
the same bundle and reply target; never resend the model task for a local
display failure.

## One Browser call

For an approved request, use the normal `prepare` command to create
`bundle.json`. Give the transfer helper a `workflow.json` with the existing
dispatch `facts`, the optional capability `inventory`, and `live_base`.
Include `request_id` and `input_sha256` from the approved bundle when recording
the workflow. The helper rejects missing or mismatched identifiers before
opening the local form.
`live_base` contains the owner's confirmed actor, route, transport, action
authorization, and operation checks. The Browser supplies the current
`surface`, `model`, `quota_state`, `observed_at`, and optional
`temporary_chat_mode` for each phase. Record approvals only after verifying
their actual scope. The helper checks inspect at startup and evaluates dispatch
and receive separately when the Browser submits current observations.

```text
python <plugin-root>/scripts/chat_transfer.py --bundle bundle.json --reply reply.json --workflow workflow.json --timeout 720
```

Place `bundle.json`, `workflow.json`, and the `reply.json` target in the same
private job directory. The workflow requires this layout. The startup
`listening` event supplies the loopback URL and the request identifiers.
Once the host has initialized the in-app Browser REPL, send the trusted
`<plugin-root>/scripts/chat_browser.js` definition and the invocation below
together as one script. If the definition is already loaded, send only the
invocation. Set the tool timeout to cover setup, generation, and saving, and
keep the local helper alive for the whole exchange.

```js
const handoffResult = await runProHandoff({ cua, listening, timeoutMs: 600_000 });
// Include the reply only if it may appear in the caller's tool output.
nodeRepl.write({
  event: handoffResult.event,
  completion: handoffResult.completion,
  status: handoffResult.status,
  quota_state: handoffResult.quota_state,
  phase: handoffResult.phase,
  reason: handoffResult.reason,
  reply: handoffResult.reply,
});
```

The runner uses the documented Browser actions with hidden tabs. It opens a
new empty Temporary Chat, confirms the destination and the displayed Pro
selection, dispatches once, waits for the current response, checks receive,
and saves the correlated reply. It waits in short Browser intervals within the
same call and returns `pending` with `reason: "wait_timeout"` if the response
wait expires. It returns `completion: "reply_ready"`,
`event: "saved"`, and `reply` in the same result after the helper acknowledges
the write. `reply` includes the validated result and evidence. Consume that
value in the script or use the verified `reply.json` locally. For material that
must remain local, omit `reply` from tool output and use the saved file.
The reply status and the owner's assessment of the result remain separate.
On a saved reply, `quota_state` reports the observed limit notice during
collection. A notice that new generation is exhausted can coexist with a
finished reply from the last Pro message. Collection still requires a matching
Pro display, receive permission, and response identifiers. A quota alert with
no finished reply or active generation leaves the original request pending.

For requests that need an approved tool, pass a `prepareTools(chatTab)`
callback that performs and verifies the supported selection in the same call.
For GitHub, current workflow metadata tells the runner to observe the mode and
select Personalized when necessary before `prepareTools`. The runner checks
the UI after the callback and uses that observation for dispatch. The callback
supplies a read-only `verifyGitHubSelection` and any editor callbacks the
observed chip requires. Editor callbacks preserve the selected mode and chip.
With older metadata, a GitHub dispatch requires an already selected
Personalized mode that the runner can observe. The preflight checks the
inventory and scope.
The default invocation handles requests without tool selection. If a required control is
unknown, Pro is unavailable, or a host confirmation is required, resolve that
condition through the supported host.

The helper refuses a dispatch if the reply path already exists and reserves
the request ID before returning `send_once`. Continue in the original job
directory and retain its reservation. A `pending` result after an attempted
send can include the original tab for recovery. Use the existing receive
procedure on that tab with the same bundle and output path.
A timeout or an unknown click outcome leaves the request unresolved.

## Browser script and completion signal

With a Browser REPL that supports Playwright locators, load the definition in
`<plugin-root>/scripts/chat_browser.js` once into that REPL. Run it explicitly
as the route owner after observing the new temporary Chat, selected Pro, and
the supported controls. Use the actual dispatch and receive planner outputs.
The script uses the open Chat and loopback tabs and keeps both bodies in
Browser variables.

```js
const handoff = proHandoff({ chatTab, transferTab, listening });
nodeRepl.write(await handoff.send({ plan: dispatchPlan, composer, sendButton, temporaryChatUrl }));
```

Set `temporaryChatUrl` to the URL observed in that new empty temporary Chat.
The observed UI used `https://chatgpt.com/?temporary-chat=true`. This version
requires HTTPS ChatGPT, the root path and exactly one `temporary-chat=true`
parameter, allowing other observed query parameters. It compares the complete
observed tab URL at entry, immediately before filling and before clicking.
If the observed URL differs or changes, stop and inspect the original tab.
After a send attempt, use wait and collect for the existing request in the
same tab. The post-send conversation path may differ from the preflight URL.

For a GitHub-required dispatch, `dispatchPlan` must include the freshly observed
`temporary_chat_mode: "personalized"`. Supply `verifyGitHubSelection`, an async
callback that returns exactly `true` only when the GitHub selection is visible
in this same Chat. It is checked before and after filling; a missing or lost
selection stops the click. Where the editor's accessible value includes the
GitHub chip, supply observed UI-specific `readComposer` and `fillPrompt`
callbacks that preserve and separately verify the chip while comparing the
exact prompt body. Do not remove or trim text to make a comparison pass. These
callbacks and the mode observation do not establish GitHub operation or scope
permissions; those remain separate preflight checks.

`listening` is the CLI's startup event for the approved bundle. Pass the
observed empty editor and the send button from `chatTab`. If the default
multiline fill or text reading differs from the observed editor behavior,
use supported `fillPrompt(prompt)` and `readComposer(composer)` callbacks for
the exact body comparison. `send_clicked` records the click. Confirm that the
same tab contains the sent turn. If the click result is uncertain, recover
that turn in the same tab. The script guards against a second attempt in the
same REPL. Across REPL restarts carry forward the original submission state
and use only wait and receive for an already attempted request.

Bind `finished` to a control for the current assistant turn that becomes
visible only when generation ends. Optionally bind `attention` to an observed
terminal notice in the same Chat, such as a generation error. Wait in one
tool call, with the REPL call timeout longer than the locator wait and within
the host's supported duration.

```js
nodeRepl.write(await handoff.wait({ finished, attention, timeoutMs: 600000 }));
```

`reply_ready` signals that the scoped completion control is visible.
`attention_required` and `completion_unconfirmed` require inspection of the
same tab. A timeout leaves the request unresolved. Keep the tab and resume
waiting for the original turn. The wait returns metadata only, without
repeated model turns, and lasts only while the host runs the tool call.

After `reply_ready`, confirm the fresh UI observations for receive and bind
`answer` to the visible final JSON of that same turn. Pass both locators from
the same Chat tab.

```js
nodeRepl.write(await handoff.collect({ plan: receivePlan, finished, answer }));
```

`save_clicked` signals a form submission attempt. Wait for the local helper
process to exit and consume its `saved` event, which carries `request_id`,
`input_sha256`, and `status`. The helper emits it after validation and saving,
including for `blocked` and `missing_input`. Only `completed` proceeds to the
owner's content review. If a click or local save is unconfirmed, inspect the
existing reply target before recovering the form for the same request.

## Dispatch permission and longer work

Before a new Chat send, follow [usage and authorization gates](chat-limits.md)
and pass fresh observations to `chat_plan.py --live`. For collection of the
existing request, use `chat_plan.py --phase receive --live receive-live.json
--request bundle.json`. Collection requires its own confirmed scope, but not
new-generation quota or permission for another send. `--phase inspect` plans
only permitted contract/UI inspection and never sends or collects a reply. Local transfer validation
is not permission for an otherwise unapproved automated extraction method.

The helper deadline is a local transport lifetime, not proof that Chat failed
or permission to resend its task. Choose `--timeout` within the supported
1–3600 seconds for the expected work. If the helper expires while Chat is
still running, keep the existing Chat and do not resend. Once a reply is
available, a new local helper can use the same validated bundle and reply
target to save that reply; request ID/hash and no-overwrite checks still apply.
Never restart the model task merely to restore a local form.
