# Browser local transfer

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
after the parent has confirmed the content is authorized for that destination,
the selected UI model is `6 Pro`, and the temporary-Chat conditions hold. The
helper does not make those checks and does not send anything itself.

Choose supported Browser operations that do not automatically emit the full
page on navigation. For example, once the documented Browser runtime is
initialized, create a tab with its `tabs.new()` method and navigate with
`goto()`, then read only the relevant DOM fields. A wrapper that automatically
prints its initial AX tree can echo the entire prompt and defeat this saving.
Keep the prompt and final answer in Browser-runtime variables; report only
length, equality, selected model, and the save result to the parent. Never
read browser storage, session state or private application APIs to move data.

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
completed status is not semantic acceptance: the parent must still assess the
answer against the task's acceptance criteria, evidence, scope, and UI model
confirmation.
