#!/usr/bin/env python3
"""Run every test, also with pactl's JSON output broken (forces the text fallback)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_parsing.py", "test_dial.py", "test_routing.py", "test_window.py"]
if "--no-gui" in sys.argv:
    SUITES.remove("test_window.py")

failed = []
for broken in (False, True):
    for suite in SUITES:
        if broken and suite in ("test_parsing.py", "test_dial.py"):
            continue  # nothing there reads full pactl listings
        label = suite + (" [broken pactl JSON]" if broken else "")
        print(f"\n=== {label}")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        if broken:
            env["FAKE_BROKEN_JSON"] = "1"
        r = subprocess.run([sys.executable, os.path.join(HERE, suite)], cwd=HERE, env=env, timeout=180)
        if r.returncode:
            failed.append(label)

print("\n" + ("All tests passed." if not failed else "FAILED: " + ", ".join(failed)))
sys.exit(1 if failed else 0)
