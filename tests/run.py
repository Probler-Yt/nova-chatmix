#!/usr/bin/env python3
"""Run every test, also with pactl's JSON output broken (forces the text fallback).

test_real_pipewire.py starts its own throwaway PipeWire session; it skips itself
when PipeWire isn't installed. --no-gui / --no-pipewire leave suites out."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_parsing.py", "test_eq.py", "test_dial.py", "test_routing.py", "test_window.py",
          "test_real_pipewire.py"]
if "--no-gui" in sys.argv:
    SUITES.remove("test_window.py")
if "--no-pipewire" in sys.argv:
    SUITES.remove("test_real_pipewire.py")

failed = []
for broken in (False, True):
    for suite in SUITES:
        if broken and suite in ("test_parsing.py", "test_eq.py", "test_dial.py", "test_real_pipewire.py"):
            continue  # nothing there reads full fake pactl listings
        label = suite + (" [broken pactl JSON]" if broken else "")
        print(f"\n=== {label}")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        if broken:
            env["FAKE_BROKEN_JSON"] = "1"
        r = subprocess.run([sys.executable, os.path.join(HERE, suite)], cwd=HERE, env=env, timeout=600,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(r.stdout, end="", flush=True)
        if r.returncode:
            failed.append(label)
            if os.environ.get("GITHUB_ACTIONS"):  # readable from the Actions page and its API
                lines = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")] or r.stdout.splitlines()[-25:]
                body = "%0A".join(l.replace("%", "%25").replace("\r", "") for l in lines)
                print(f"::error title={label} failed::{body}", flush=True)

print("\n" + ("All tests passed." if not failed else "FAILED: " + ", ".join(failed)))
sys.exit(1 if failed else 0)
