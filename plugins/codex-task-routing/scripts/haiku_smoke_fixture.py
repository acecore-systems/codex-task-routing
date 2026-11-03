#!/usr/bin/env python3
"""Prepare and verify a synthetic Haiku editing task, without calling a model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from routing_core.common import RoutingError, _assert_safe_ancestors

INITIAL = "def add(a, b):\n    return a - b\n"
TESTS = ("import unittest\nfrom calculator import add\n\n"
         "class CalculatorTests(unittest.TestCase):\n"
         "    def test_positive(self):\n        self.assertEqual(add(2, 3), 5)\n"
         "    def test_negative(self):\n        self.assertEqual(add(-4, 1), -3)\n")
PROMPT = ("Fix calculator.py so add returns the sum. Run python -B -m unittest discover -v. "
          "Do not change tests or any other file. Report the result.\n")


def safe_env():
    allowed = ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    return env


def call(args, cwd):
    return subprocess.run(args, cwd=cwd, env=safe_env(), capture_output=True, text=True,
                          encoding="utf-8", timeout=30, check=True).stdout.strip()


def prepare(output_dir):
    base = _assert_safe_ancestors(output_dir)
    base.mkdir(parents=False, exist_ok=False)
    repo = base / "repo"
    repo.mkdir()
    state = base / "state"
    state.mkdir()
    template = base / "empty-git-template"
    template.mkdir()
    call(["git", "init", "-b", "codex/haiku-smoke", "--template", str(template)], repo)
    (repo / "calculator.py").write_text(INITIAL, encoding="utf-8", newline="\n")
    (repo / "test_calculator.py").write_text(TESTS, encoding="utf-8", newline="\n")
    call(["git", "add", "calculator.py", "test_calculator.py"], repo)
    call(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
          "-c", "commit.gpgsign=false", "commit", "-m", "Initial synthetic fixture"], repo)
    head = call(["git", "rev-parse", "HEAD"], repo)
    (base / "task.txt").write_text(PROMPT, encoding="utf-8", newline="\n")
    request = {"schema_version": 1, "enabled": False, "actor": "root", "request_id": "haiku-smoke-1",
        "input_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(), "cwd": str(repo),
        "branch": "codex/haiku-smoke", "head": head,
        "tools": ["Read", "Glob", "Grep", "Edit", "Bash"],
        "bash_rules": ["Bash(python -B -m unittest *)"], "max_turns": 8, "max_budget_usd": 1.0,
        "host_execution_allowed": False, "target_operations_allowed": False,
        "materials_transfer_allowed": False, "official_endpoint_verified": False}
    (base / "request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8", newline="\n")
    (base / "fixture.json").write_text(json.dumps({"schema_version": 1, "initial_head": head}) + "\n",
                                       encoding="utf-8", newline="\n")
    return {"prepared": True, "opt_in": False, "model_called": False}


def verify(output_dir):
    base = _assert_safe_ancestors(output_dir)
    repo = _assert_safe_ancestors(base / "repo")
    meta = json.loads((base / "fixture.json").read_text(encoding="utf-8"))
    if set(meta) != {"schema_version", "initial_head"} or meta["schema_version"] != 1:
        raise ValueError("invalid fixture metadata")
    if call(["git", "rev-parse", "HEAD"], repo) != meta["initial_head"]:
        raise ValueError("fixture revision changed")
    if (repo / "test_calculator.py").read_text(encoding="utf-8") != TESTS:
        raise ValueError("fixture tests changed")
    if call(["git", "status", "--porcelain", "--untracked-files=normal"], repo) != "M calculator.py":
        raise ValueError("fixture must contain only the intended file change")
    subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-v"], cwd=repo, env=safe_env(),
                   capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    return {"fixture_tests_passed": True, "only_expected_file_changed": True,
            "model_or_api_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "verify"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.output_dir) if args.phase == "prepare" else verify(args.output_dir)
        print(json.dumps(result))
        return 0
    except (RoutingError, ValueError, OSError, subprocess.SubprocessError):
        print("Fixture check failed; inspect the synthetic directory without resending.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
