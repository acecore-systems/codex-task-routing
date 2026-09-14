---
name: task-routing
description: Apply Codex Task Routing when assigning a substantial independent task to a subagent, or inspect and customize this plugin's delegation policy, model and effort settings. Covers research, documents, images, data, operations, and development.
---

# Task routing

Use the effective policy already supplied by this plugin's start hook. Keep the user's chosen parent model and effort. It authorizes choosing suitable standard subagents only within the user's task, available tools, and higher-priority instructions. A child requires a bounded independent task and useful independent work for its direct parent. Short work stays with the current capable agent.

If no effective policy was supplied, run `python <plugin-root>/scripts/routing.py status --json`, where plugin-root is two directories above this skill folder. Inspect conflicts and errors before calling the policy active. Do not silently replace existing global or project instructions. Read only the relevant section of the resolved policy and catalog; use the handoff reference when needed. Loading this skill alone does not install or trust its hooks.

For a handoff, specify the model and effort explicitly, purpose, scope, approved actions, completion criteria, evidence, return conditions, and the independent work retained by the parent. Reuse a suitable existing child for follow-up, with only the change and new evidence. Recheck model and effort availability from the current host; config values are requests, not proof of execution. Return important judgment and acceptance to the parent. Do not recommend a model for the user's next message.

For configuration requests, consult [configuration](references/configuration.md). Preserve unrelated settings and keep overrides outside the plugin cache. Show the requested differences and validate them. Natural-language principle changes require reviewing related summary and detailed sections; schema validation does not prove semantic consistency. Never edit credentials, user memories, parent model settings, or hook trust records.

For an explicit request to install the optional pre-launch entry point, run `python <plugin-root>/scripts/install_launcher.py` (Windows desktop) or add `--mode cli --no-shortcut` for a CLI entry. This creates only the requested launcher and does not start Codex or update the plugin. Explain that automatic synchronization occurs only when starting through that entry, with all Codex processes closed. The updater preserves the registered Git ref and user overrides. Never run a marketplace upgrade from a live session hook: native upgrades can remove the old plugin files used by active tasks. Launcher diagnostics are in `<CODEX_HOME>/codex-task-routing/launcher/state/last-sync.json`; no raw CLI logs are retained.
