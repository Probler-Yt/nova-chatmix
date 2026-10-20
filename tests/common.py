"""Shared test helpers: load the programs, fake PipeWire (pactl), fake GameDAC."""

import importlib.machinery
import importlib.util
import os
import socket
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
FAKES = os.path.join(ROOT, "tests", "fakes")


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def load_service():
    return load(os.path.join(SRC, "nova-chatmix.py"), "nova_chatmix_service_under_test")


def fake_pactl(flavour="full"):
    """Put a fake `pactl` first on PATH, backed by a JSON state file. Returns the temp dir."""
    tmp = tempfile.mkdtemp(prefix="nova-chatmix-test-")
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir)
    target = os.path.join(FAKES, f"pactl_{flavour}.py")
    os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.symlink(target, os.path.join(bindir, "pactl"))
    os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]
    os.environ["FAKE_PACTL_STATE"] = os.path.join(tmp, "state.json")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(tmp, "run")
    os.makedirs(os.environ["XDG_RUNTIME_DIR"])
    return tmp


def fake_dac(svc, peer):
    """Replace the service's Dac with one talking over a socketpair we hold the other end of."""
    real = svc.Dac

    def make(path):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        a.setblocking(False)
        peer["sock"] = b
        d = object.__new__(real)
        d.path, d.fd, d._last_tx = path, a.detach(), 0.0
        return d
    svc.Dac = make


class Checks:
    def __init__(self):
        self.fails = 0

    def __call__(self, cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        self.fails += 0 if cond else 1

    def done(self):
        print("FAILURES:", self.fails)
        sys.exit(1 if self.fails else 0)
