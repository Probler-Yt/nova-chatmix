"""A throwaway, headless PipeWire session for tests.

Starts its own dbus + pipewire + wireplumber + pipewire-pulse in a temp dir
(never touches the desktop's real audio), with a null sink posing as the
GameDAC. Tests that need it call start(); if PipeWire isn't installed they skip.
"""
import atexit
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

DAC = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro-00.analog-stereo"
NEEDED = ("pipewire", "wireplumber", "pipewire-pulse", "pactl", "dbus-daemon", "pw-play", "pw-record")
_procs = []
_root = None


def skip(reason):
    print(f"SKIP {reason}")
    sys.exit(0)


def start():
    global _root
    missing = [p for p in NEEDED if not shutil.which(p)]
    if missing:
        skip("needs PipeWire installed (missing: " + ", ".join(missing) + ")")
    _root = tempfile.mkdtemp(prefix="nova-chatmix-pw-")
    run = os.path.join(_root, "run")
    os.makedirs(run, mode=0o700)
    env = {
        "XDG_RUNTIME_DIR": run, "PIPEWIRE_RUNTIME_DIR": run, "PULSE_RUNTIME_PATH": os.path.join(run, "pulse"),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={run}/bus", "XDG_CONFIG_HOME": os.path.join(_root, "config"),
        "XDG_STATE_HOME": os.path.join(_root, "state"), "XDG_CACHE_HOME": os.path.join(_root, "cache"),
        "PIPEWIRE_LOG_SYSTEMD": "false", "DISABLE_RTKIT": "1",
    }
    os.environ.update(env)
    for k in ("PIPEWIRE_REMOTE", "PULSE_SERVER"):
        os.environ.pop(k, None)
    atexit.register(stop)
    logs = open(os.path.join(_root, "session.log"), "wb")
    _procs.append(subprocess.Popen(["dbus-daemon", "--session", f"--address=unix:path={run}/bus", "--nofork",
                                    "--nopidfile"], stdout=logs, stderr=logs))
    time.sleep(0.3)
    for cmd in (["pipewire"], ["wireplumber"], ["pipewire-pulse"]):
        _procs.append(subprocess.Popen(cmd, stdout=logs, stderr=logs, start_new_session=True))
        time.sleep(0.4)
    for _ in range(100):
        if subprocess.run(["pactl", "info"], capture_output=True).returncode == 0:
            break
        time.sleep(0.1)
    else:
        skip("couldn't start a test PipeWire session here")
    add_dac()
    subprocess.run(["pactl", "set-default-sink", DAC], capture_output=True)
    return _root


def add_dac():
    subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name={DAC}",
                    "sink_properties=device.description=Arctis_Nova_Pro device.vendor.id=0x1038 device.product.id=0x12cb"],
                   capture_output=True, check=True)


def blink_dac():
    """The DAC's audio device disappears and comes back, like after a USB reset."""
    short = subprocess.run(["pactl", "list", "modules", "short"], capture_output=True, text=True).stdout
    mod = next(line.split("\t")[0] for line in short.splitlines() if f"sink_name={DAC}" in line)
    subprocess.run(["pactl", "unload-module", mod], check=True)
    time.sleep(0.5)
    add_dac()


def stop():
    subprocess.run(["pkill", "-f", f"pipewire -c {_root}"], capture_output=True) if _root else None
    for p in reversed(_procs):
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()
    _procs.clear()
    if _root:
        shutil.rmtree(_root, ignore_errors=True)
