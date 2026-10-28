#!/usr/bin/env python3
"""Find out how the GameDAC wants ChatMix switched on.

Stop the service first (systemctl --user stop nova-chatmix), then run this and
follow the prompts. It only sends commands SteelSeries GG itself sends to this
DAC, and prints everything the DAC says back.

  python3 dac-probe.py [path to the nova-chatmix service, default /usr/bin/nova-chatmix]
"""
import importlib.machinery
import importlib.util
import os
import select
import subprocess
import sys
import time

svc_path = sys.argv[1] if len(sys.argv) > 1 else "/usr/bin/nova-chatmix"
loader = importlib.machinery.SourceFileLoader("nova_chatmix_service", svc_path)
spec = importlib.util.spec_from_loader(loader.name, loader)
svc = importlib.util.module_from_spec(spec)
loader.exec_module(svc)

me = os.getpid()
others = [l for l in subprocess.run(["pgrep", "-af", "nova-chatmix"], capture_output=True, text=True).stdout.splitlines()
          if not l.startswith(f"{me} ") and "dac-probe" not in l and "nova-chatmix-gui" not in l]
if others:
    sys.exit("The nova-chatmix service is still running. Stop it first:\n"
             "  systemctl --user stop nova-chatmix\n" + "\n".join(others))

path, pid = svc.find_hidraw()
if not path:
    sys.exit("No Nova Pro Wired GameDAC found.")
print(f"GameDAC at {path} (product {pid:04x})\n")
dac = svc.Dac(path)


def show(report, label=""):
    r = report[:16]
    note = ""
    if r[:2] == bytes([0x07, 0x45]):
        note = f"   <- ChatMix dial: game {r[2]}% chat {r[3]}%"
    elif r[:2] == bytes([0x07, 0x25]):
        note = f"   <- volume dial: {r[2]}"
    elif r[:2] == bytes([0x06, 0x20]):
        note = "   <- status reply"
    print(f"  {label}{r.hex(' ')}{note}", flush=True)


SEEN = []  # every report the DAC has sent


def drain(secs):
    got = []
    end = time.monotonic() + secs
    while time.monotonic() < end:
        ready, _, _ = select.select([dac.fd], [], [], max(0.0, end - time.monotonic()))
        if ready:
            for r in dac.read_reports():
                show(r)
                got.append(bytes(r[:16]))
                SEEN.append(bytes(r[:16]))
    return got


def send(*payload, settle=0.15):
    print(f"> {' '.join(f'{b:02x}' for b in (svc.TX, *payload))}", flush=True)
    dac.send(*payload)
    return drain(settle)


def watch(since, secs, prompt):
    print(f"\n{prompt}\n(watching for {secs} s)", flush=True)
    drain(secs)
    got = SEEN[since:]
    dial = [g for g in got if g[:2] == bytes([0x07, 0x45])]
    vol = [g for g in got if g[:2] == bytes([0x07, 0x25])]
    return dial, vol


try:
    print("Step 1: SteelSeries GG's start-up sequence, spaced out")
    start = len(SEEN)
    for op in (0x20, 0x20, 0x10, 0x10):
        send(op, settle=0.3)
    time.sleep(1.0)
    send(0x49, 0x01, settle=1.0)  # ChatMix on, then leave it alone for a second
    send(0x20, settle=0.5)
    dial, vol = watch(start, 20, "Now PRESS the dial once, then TURN it both ways.")
    if dial:
        print("\nRESULT: ChatMix works with GG's sequence. Send this output to Claude.")
        sys.exit(0)
    print("\nNo ChatMix reports." + (" Volume reports came through, so the DAC is talking." if vol else
                                       " No volume reports either."))

    print("\nStep 2: ChatMix on again, on its own, after a quiet pause")
    time.sleep(2.0)
    start = len(SEEN)
    send(0x49, 0x01, settle=1.5)
    dial, vol = watch(start, 20, "PRESS the dial once more, then TURN it both ways.")
    if dial:
        print("\nRESULT: ChatMix works when switched on with a pause around it. Send this output to Claude.")
        sys.exit(0)
    print("\nRESULT: still nothing" + (" (but volume reports came through)." if vol else
                                       ", not even volume reports.") + " Send this output to Claude.")
finally:
    dac.close()  # leaves the DAC as it is; start the service again afterwards
