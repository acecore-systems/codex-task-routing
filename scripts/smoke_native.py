#!/usr/bin/env python3
"""Exercise native plugin management in a new, isolated Codex home.

No model sessions, network calls, hook trust changes, or personal configuration.
Artifacts are retained below the required new --output-dir for inspection.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "codex-task-routing"
PLUGIN_ID = f"{PLUGIN_NAME}@{PLUGIN_NAME}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    base = args.output_dir.absolute()
    base.mkdir(parents=True, exist_ok=False)
    home = base / "home"
    home.mkdir()
    source = base / "source"
    shutil.copytree(ROOT / ".agents", source / ".agents")
    plugin = source / "plugins" / PLUGIN_NAME
    shutil.copytree(ROOT / "plugins" / PLUGIN_NAME, plugin,
                    ignore=shutil.ignore_patterns("__pycache__", ".tmp*", "tests"))
    guidance = home / "AGENTS.md"
    guidance.write_text("Use concise answers.\n", encoding="utf-8")
    config_file = home / "config.toml"
    config_file.write_text('model_reasoning_effort = "high"\n', encoding="utf-8")
    override = home / PLUGIN_NAME / "overrides.json"
    override.parent.mkdir()
    override.write_text(json.dumps({"schema_version": 1, "models": {
        "sol": {"default_effort": "high"}}}), encoding="utf-8")
    chat_config = home / PLUGIN_NAME / "chatgpt.json"
    before = {p: p.read_bytes() for p in (guidance, override)}
    env = {**os.environ, "CODEX_HOME": str(home),
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
    results = []
    hook_context_chars = {}

    def run(command, *, event=None):
        result = subprocess.run(command, cwd=base, env=env,
                                input=json.dumps(event) if event else None,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {result.stderr}")
        return json.loads(result.stdout)

    def cli(*arguments):
        return run([args.codex, "plugin", *arguments, "--json"])

    def installed_runtime(version):
        candidates = []
        for path in (home / "plugins").rglob("plugin.json"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("name") == PLUGIN_NAME and manifest.get("version") == version:
                candidates.append(path.parent.parent / "scripts" / "routing.py")
        assert len(candidates) == 1, f"Expected one installed {version}; found {len(candidates)}"
        return candidates[0]

    cli("marketplace", "add", str(source))
    cli("add", PLUGIN_ID)
    initial_version = json.loads((plugin / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']
    script = installed_runtime(initial_version)
    status = run([sys.executable, str(script), "status", "--json"])
    assert status["ok"] and status["override"]["present"]
    assert status['configured_child_models']['sol'] == {
        'id': 'gpt-6.1-sol', 'default_effort': 'high'}
    installed_defaults = json.loads((script.parent.parent / 'defaults/config.json').read_text(encoding='utf-8'))
    assert installed_defaults['models']['sol']['default_effort'] == 'xhigh'
    assert status["host"]["trust_state"] == "unknown"
    results.append("native install and installed runtime status passed")
    route_script = script.parent / "chatgpt_route.py"
    default_route = run([sys.executable, str(route_script), "status"])
    assert default_route["enabled"] is False and default_route["transport"] == "browser-temporary"
    default_hook = run([sys.executable, str(script), "hook"],
                       event={"hook_event_name": "SessionStart", "source": "startup"})
    assert "Opt-in ChatGPT" not in default_hook["hookSpecificOutput"]["additionalContext"]
    assert "Observation sample" in default_hook["hookSpecificOutput"]["additionalContext"]
    auth_reference = script.parent.parent / 'skills/task-routing/references/local-auth.md'
    assert auth_reference.is_file()
    assert str(auth_reference) in default_hook['hookSpecificOutput']['additionalContext']
    private_facts = base / 'private-facts.json'
    private_facts.write_text(json.dumps(dict(substantial=False, parent_has_independent_work=False,
                                            materials_approved=False, handoff_proportionate=False,
                                            requires_repeated_local_access=False, requires_private_auth=True,
                                            needs=[])), encoding='utf-8')
    private_plan = run([sys.executable, str(script.parent / 'chat_plan.py'), '--facts', str(private_facts),
                        '--inventory', str(base / 'missing-chat-inventory.json'),
                        '--live', str(base / 'missing-chat-live.json')])
    assert private_plan['route'] == 'local_auth' and private_plan['dispatch_authorized'] is False
    results.append('installed private-authentication route and bundled reference passed without dispatch')
    chat_config.write_text(json.dumps({"schema_version": 1, "enabled": True,
                                     "required_model": "6 Pro", "transport": "codex-app-tools"}), encoding="utf-8")
    before[chat_config] = chat_config.read_bytes()
    results.append("fresh native install keeps Chat route disabled and observation available")
    hooks = json.loads((script.parent.parent / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event in ("SessionStart", "SubagentStart"):
        # The command comes from this reviewed local test copy; run the exact
        # handler string to catch shell/environment expansion problems.
        command = hooks[event][0]["hooks"][0]["command"]
        process = subprocess.run(command, shell=True, cwd=base,
                                 env={**env, "PLUGIN_ROOT": str(script.parent.parent)},
                                 input=json.dumps({"hook_event_name": event, "source": "startup"}),
                                 capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert process.returncode == 0, process.stderr
        payload = json.loads(process.stdout)
        context = payload["hookSpecificOutput"]
        hook_context_chars[event] = len(context["additionalContext"])
        assert context["hookEventName"] == event
        assert payload.get("continue", True) and "policy hash" in context["additionalContext"]
        assert 'Private authentication:' in context['additionalContext']
        assert ("Opt-in ChatGPT Chat route is enabled" in context["additionalContext"]) == (event == "SessionStart")
        if event == "SessionStart":
            assert "transport: codex-app-tools (legacy)" in context["additionalContext"]
            assert '| Sol子 | gpt-6.1-sol | high |' in context['additionalContext']
            assert 'Sol/high' in context['additionalContext']
        else:
            assert 'Sol=gpt-6.1-sol (high)' in context['additionalContext']
        assert "systemMessage" not in payload, payload.get("systemMessage")
    results.append("installed hook commands produced the official event-specific output for both events")

    # Check conditional child delivery with the installed runtime as well as
    # legacy root compatibility; no role/depth is inferred from this event.
    chat_config.write_text(json.dumps({"schema_version": 1, "enabled": True,
                                     "required_model": "Pro", "transport": "browser-temporary"}), encoding="utf-8")
    browser_hook_context_chars = {}
    for event in ("SessionStart", "SubagentStart"):
        payload = run([sys.executable, str(script), "hook"],
                      event={"hook_event_name": event, "source": "startup"})
        assert "systemMessage" not in payload, payload.get("systemMessage")
        context = payload["hookSpecificOutput"]["additionalContext"]
        browser_hook_context_chars[event] = len(context)
        assert len(context) <= 8000
        assert "Opt-in ChatGPT Chat route is enabled" in context
        if event == "SubagentStart":
            assert "Conditional only: root-direct Sol child, depth=1" in context
            assert "SubagentStart does not prove role/depth" in context
            assert "all grandchildren must not use Chat" in context
    chat_config.write_bytes(before[chat_config])
    results.append("installed browser route delivered conditional root-direct Sol guidance without dispatch")

    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    next_version = initial_version.split('+')[0] + '+codex.native-test'
    manifest["version"] = next_version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    cli("add", PLUGIN_ID)
    updated = run([sys.executable, str(installed_runtime(next_version)), "status", "--json"])
    assert updated["ok"] and updated["manifest_version"] == next_version
    assert updated["override"]["present"] and updated["policy_hash"] != status["policy_hash"]
    assert updated['configured_child_models'] == status['configured_child_models']
    updated_hook = run([sys.executable, str(installed_runtime(next_version)), 'hook'],
                       event={'hook_event_name': 'SubagentStart', 'source': 'startup'})
    assert 'Sol=gpt-6.1-sol (high)' in updated_hook['hookSpecificOutput']['additionalContext']
    assert chat_config.read_bytes() == before[chat_config]
    updated_route = installed_runtime(next_version).parent / "chatgpt_route.py"
    updated_route_status = run([sys.executable, str(updated_route), "status"])
    assert updated_route_status["enabled"] is True and updated_route_status["transport"] == "codex-app-tools"
    results.append("native reinstall picked up changed version and preserved override")
    cli("remove", PLUGIN_ID)
    for path, content in before.items():
        assert path.read_bytes() == content
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert config["model_reasoning_effort"] == "high"
    assert not config.get("plugins", {}).get(PLUGIN_ID, {}).get("enabled", False)
    results.append("native removal preserved unrelated settings, guidance, and override")
    report = {"ok": True, "checks": results,
              "hook_context_chars": hook_context_chars,
              "browser_hook_context_chars": browser_hook_context_chars,
              "not_verified": ["host hook trust and live event delivery", "model session routing"]}
    (base / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
