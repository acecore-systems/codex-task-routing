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
        "terra": {"default_effort": "high"}}}), encoding="utf-8")
    before = {p: p.read_bytes() for p in (guidance, override)}
    env = {**os.environ, "CODEX_HOME": str(home),
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
    results = []

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
    script = installed_runtime("0.1.0")
    status = run([sys.executable, str(script), "status", "--json"])
    assert status["ok"] and status["override"]["present"]
    assert status["host"]["trust_state"] == "unknown"
    results.append("native install and installed runtime status passed")
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
        assert context["hookEventName"] == event
        assert payload.get("continue", True) and "policy hash" in context["additionalContext"]
    results.append("installed hook commands produced the official event-specific output for both events")

    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "0.1.1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    cli("add", PLUGIN_ID)
    updated = run([sys.executable, str(installed_runtime("0.1.1")), "status", "--json"])
    assert updated["ok"] and updated["manifest_version"] == "0.1.1"
    assert updated["override"]["present"] and updated["policy_hash"] != status["policy_hash"]
    results.append("native reinstall picked up changed version and preserved override")
    cli("remove", PLUGIN_ID)
    for path, content in before.items():
        assert path.read_bytes() == content
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert config["model_reasoning_effort"] == "high"
    assert not config.get("plugins", {}).get(PLUGIN_ID, {}).get("enabled", False)
    results.append("native removal preserved unrelated settings, guidance, and override")
    report = {"ok": True, "checks": results,
              "not_verified": ["host hook trust and live event delivery", "model session routing"]}
    (base / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
