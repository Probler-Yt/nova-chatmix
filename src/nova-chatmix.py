#!/usr/bin/env python3
"""
nova-chatmix - hardware ChatMix for the SteelSeries Arctis Nova Pro Wired
GameDAC (Gen 2) on Linux, with no GUI.

What it does:
  * Creates PipeWire outputs "Game", "Chat" and "Media" that all play
    through the GameDAC (Media is never turned down by the dial).
  * Tells the DAC to put its dial into ChatMix mode.
  * Listens for dial movements and turns Game / Chat up and down to match.
  * Runs as a systemd user service, survives unplug/replug and PipeWire
    restarts, and hands the dial back to plain volume when it stops.

Protocol (GameDAC Gen 2, USB 1038:12cb / 12cd / 12fa, HID interface 4):
  host -> DAC   : 16-byte output report, report id 0x06, sent as SET_REPORT
                  [0x06, 0x49, 0x01]  enable ChatMix   (0x00 = disable)
                  [0x06, 0x20]        status request
  DAC  -> host  : [0x07, 0x45, game, chat]  dial position, each 0..100
Commands need >= 50 ms between them, and the DAC wants ~5 s after it
enumerates before it is sent anything. Only opcodes the DAC defines are
used: unknown ones get a USB STALL and can wedge it until a replug.

Only needs Python 3 (stdlib) and pactl (pipewire-pulse).
"""

__version__ = "1.0.1"

import argparse
import errno
import fcntl
import glob
import json
import logging
import os
import re
import select
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------- device ---

VENDOR_ID = 0x1038
PRODUCT_IDS = {0x12CB, 0x12CD, 0x12FA}  # Nova Pro Wired, Wired (Xbox), Wired V2
HID_INTERFACE = 4
HIDRAW_SYSFS = "/sys/class/hidraw"
USB_SYSFS = "/sys/bus/usb/devices"
USBFS = "/dev/bus/usb"
USBDEVFS_RESET = 0x5514  # _IO('U', 20)
REPORT_LEN = 16
TX = 0x06
RX = 0x07
OP_STATUS = 0x20
OP_CHATMIX_ENABLE = 0x49
OP_CHATMIX = 0x45
COMMAND_GAP_S = 0.05
DAC_BOOT_WAIT_S = 5.0

# ----------------------------------------------------------------- audio ---

PREFIX = "ChatMix_"
SINKS = {  # key -> (sink name, label shown in KDE / Discord)
    "game": (PREFIX + "Game", "Game"),
    "chat": (PREFIX + "Chat", "Chat"),
    "media": (PREFIX + "Media", "Media"),
}

STATE_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "nova-chatmix", "state.json")
RULES_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                          "nova-chatmix", "rules.json")
# Binaries that say nothing about which app it is (Proton games, Electron, ...):
# for these the stream's application.name identifies the app instead.
# Helper processes that play audio on behalf of an app the user knows by another name.
STREAM_ALIASES = {"steamwebhelper": "steam"}
GENERIC_BINARIES = {"wine64-preloader", "wine-preloader", "wine", "wine64", "python", "python3",
                    "java", "electron", "node", "pw-play", "paplay", "aplay", "bwrap"}

log = logging.getLogger("nova-chatmix")


class DeviceGone(Exception):
    pass


# =============================================================== HID side ===

def find_hidraw():
    """Return (/dev/hidrawN, product_id) for the DAC's control interface, or (None, None)."""
    for node in sorted(glob.glob(os.path.join(HIDRAW_SYSFS, "hidraw*"))):
        try:
            with open(os.path.join(node, "device", "uevent")) as f:
                uevent = f.read()
        except OSError:
            continue
        hid_id = next((l.split("=", 1)[1] for l in uevent.splitlines()
                       if l.startswith("HID_ID=")), None)
        if not hid_id:
            continue
        try:
            _bus, vid, pid = (int(x, 16) for x in hid_id.split(":"))
        except ValueError:
            continue
        if vid != VENDOR_ID or pid not in PRODUCT_IDS:
            continue
        if interface_number(node) == HID_INTERFACE:
            return "/dev/" + os.path.basename(node), pid
    return None, None


def find_usb_devices():
    """sysfs dirs of every connected GameDAC (the USB device, not an interface)."""
    found = []
    for dev in sorted(glob.glob(os.path.join(USB_SYSFS, "*"))):
        try:
            with open(os.path.join(dev, "idVendor")) as f:
                vid = int(f.read().strip(), 16)
            with open(os.path.join(dev, "idProduct")) as f:
                pid = int(f.read().strip(), 16)
        except (OSError, ValueError):
            continue  # interfaces and hubs have no idVendor of ours
        if vid == VENDOR_ID and pid in PRODUCT_IDS:
            found.append(dev)
    return found


def reset_dac():
    """Software 'unplug and replug': a USB port reset of the GameDAC.

    Same as pulling the cable, without touching it. The service notices the DAC
    going away and coming back and sets everything up again by itself."""
    devs = find_usb_devices()
    if not devs:
        print("No Arctis Nova Pro Wired GameDAC found.", file=sys.stderr)
        return 1
    rc = 0
    for dev in devs:
        try:
            with open(os.path.join(dev, "busnum")) as f:
                bus = int(f.read())
            with open(os.path.join(dev, "devnum")) as f:
                num = int(f.read())
        except (OSError, ValueError) as e:
            print(f"{dev}: can't read its USB address ({e})", file=sys.stderr)
            rc = 1
            continue
        node = os.path.join(USBFS, f"{bus:03d}", f"{num:03d}")
        try:
            fd = os.open(node, os.O_WRONLY)
        except PermissionError:
            print(f"No permission to reset {node}. Unplug and replug the DAC once so the "
                  "udev rule from nova-chatmix applies, then try again.", file=sys.stderr)
            rc = 1
            continue
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
            print(f"Reset the GameDAC ({node}). Audio comes back in a few seconds.")
        except OSError as e:
            print(f"Resetting {node} failed: {e}", file=sys.stderr)
            rc = 1
        finally:
            os.close(fd)
    return rc


def interface_number(hidraw_sysfs):
    # .../1-4:1.4/0003:1038:12CB.0007  -> the USB interface dir is the parent
    usb_intf = os.path.dirname(os.path.realpath(os.path.join(hidraw_sysfs, "device")))
    try:
        with open(os.path.join(usb_intf, "bInterfaceNumber")) as f:
            return int(f.read().strip(), 16)
    except (OSError, ValueError):
        pass
    try:
        return int(os.path.basename(usb_intf).rsplit(".", 1)[1])
    except (IndexError, ValueError):
        return None


class Dac:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self._last_tx = 0.0

    def send(self, *payload):
        msg = bytes([TX, *payload]).ljust(REPORT_LEN, b"\x00")
        wait = self._last_tx + COMMAND_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            os.write(self.fd, msg)
        except OSError as e:
            if e.errno in (errno.ENODEV, errno.EIO, errno.ENOENT, errno.EBADF):
                raise DeviceGone() from e
            raise
        finally:
            self._last_tx = time.monotonic()

    def read_reports(self):
        reports = []
        while True:
            try:
                data = os.read(self.fd, 64)
            except BlockingIOError:
                return reports
            except OSError as e:
                if e.errno in (errno.ENODEV, errno.EIO, errno.EBADF):
                    raise DeviceGone() from e
                raise
            if not data:
                raise DeviceGone()
            reports.append(data)

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def parse_chatmix(report):
    """[0x07, 0x45, game, chat, ...] -> (game, chat) or None."""
    if len(report) >= 4 and report[0] == RX and report[1] == OP_CHATMIX:
        return min(report[2], 100), min(report[3], 100)
    return None


# ============================================================ PipeWire side ===

PACTL_ENV = dict(os.environ, LC_ALL="C.UTF-8")  # English labels for the text fallback


def pactl(*args, timeout=5):
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout,
                          env=PACTL_ENV, errors="replace")


def pactl_json(kind):
    """`pactl list <kind>` as a list of dicts.

    Uses pactl's JSON output, read leniently (control characters in stream
    titles break strict JSON). If that still fails - older pactl, or the
    JSON writer choking on an odd property - falls back to parsing the
    plain-text listing into the same shape."""
    r = pactl("-f", "json", "list", kind)
    if r.returncode == 0:
        try:
            return json.loads(r.stdout or "[]", strict=False)
        except ValueError as e:
            log.debug("pactl JSON for %s unreadable (%s), using text output", kind, e)
    r = pactl("list", kind)
    if r.returncode != 0:
        raise RuntimeError(f"pactl list {kind} failed: {r.stderr.strip()}")
    return parse_pactl_text(r.stdout)


_HEADER = re.compile(r"^\S.*#(\d+)\s*$")
_PROP = re.compile(r'^\t\t([^=]+?) = "(.*)"\s*$')


def parse_pactl_text(text):
    """Turn `pactl list sinks|sink-inputs|modules` text into JSON-like dicts."""
    items, cur, in_props = [], None, False
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = _HEADER.match(raw)
        if m:
            cur = {"index": int(m.group(1)), "properties": {}}
            items.append(cur)
            in_props = False
            continue
        if cur is None:
            continue
        if raw.startswith("\t\t"):
            pm = _PROP.match(raw) if in_props else None
            if pm:
                cur["properties"][pm.group(1)] = pm.group(2).replace('\\"', '"')
            continue
        key, _, val = raw.strip().partition(":")
        val = val.strip()
        in_props = key == "Properties"
        if key == "Name":
            cur["name"] = val
        elif key == "Description":
            cur["description"] = val
        elif key == "Argument":
            cur["argument"] = val
        elif key == "Owner Module":
            cur["owner_module"] = "" if val == "n/a" else val
        elif key == "Sink":
            cur["sink"] = int(val) if val.isdigit() else val
        elif key == "Client":
            cur["client"] = val
        elif key == "Corked":
            cur["corked"] = val == "yes"
        elif key == "Volume":
            pct = re.search(r"(\d+)%", val)
            cur["volume"] = {"front-left": {"value_percent": pct.group(1) + "%"}} if pct else {}
    return items


def as_int(value):
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        try:
            return int(str(value), 16)
        except (TypeError, ValueError):
            return None


def find_dac_sink(override=None):
    sinks = pactl_json("sinks")
    names = [s.get("name", "") for s in sinks]
    if override:
        return override if override in names else None
    candidates = []
    for s in sinks:
        name = s.get("name", "")
        if name.startswith(PREFIX):
            continue
        props = s.get("properties", {}) or {}
        vid = as_int(props.get("device.vendor.id"))
        pid = as_int(props.get("device.product.id"))
        by_id = vid == VENDOR_ID and pid in PRODUCT_IDS
        by_name = "steelseries" in name.lower() and "nova_pro" in name.lower()
        if by_id or by_name:
            candidates.append(name)
    if not candidates:
        return None
    # If the DAC ever exposes a separate chat endpoint, prefer the main one.
    candidates.sort(key=lambda n: ("chat" in n.lower(), n))
    return candidates[0]


def load_module(*args):
    r = pactl("load-module", *args)
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def unload_module(module_id):
    pactl("unload-module", str(module_id))


def unload_stale_modules():
    """Remove anything a previous (crashed / killed) run left behind."""
    try:
        modules = pactl_json("modules")
    except Exception:
        return
    for m in modules:
        arg = m.get("argument") or ""
        if f"sink_name={PREFIX}" in arg or f"source={PREFIX}" in arg:
            log.info("removing leftover module %s (%s)", m.get("index"), m.get("name"))
            unload_module(m.get("index"))


class Audio:
    def __init__(self, dac_sink, with_media, set_default):
        self.dac_sink = dac_sink
        self.keys = ["game", "chat"] + (["media"] if with_media else [])
        self.set_default = set_default
        self.null_ids = {}
        self.loop_ids = {}
        self.stream_idx = {}
        self.applied = {}

    def build(self):
        unload_stale_modules()
        for key in self.keys:
            name, label = SINKS[key]
            nid = load_module(
                "module-null-sink",
                f"sink_name={name}",
                f"sink_properties=node.description={label} "
                f"device.description={label} node.nick={label}",
            )
            if nid is None:
                raise RuntimeError(f"could not create sink {name}")
            self.null_ids[key] = nid
            base = [f"source={name}.monitor", f"sink={self.dac_sink}", "latency_msec=20"]
            # *_dont_move stops WirePlumber re-routing the loopback if the DAC
            # vanishes (worst case: into our own Game sink -> feedback loop).
            lid = load_module("module-loopback", *base,
                              "source_dont_move=true", "sink_dont_move=true")
            if lid is None:
                lid = load_module("module-loopback", *base)
            if lid is None:
                raise RuntimeError(f"could not route {name} to {self.dac_sink}")
            self.loop_ids[key] = lid
        if self.set_default:
            pactl("set-default-sink", SINKS["game"][0])
        log.info("outputs ready: %s -> %s",
                 ", ".join(SINKS[k][1] for k in self.keys), self.dac_sink)

    def teardown(self, restore_default=True):
        for key in list(self.loop_ids):
            unload_module(self.loop_ids.pop(key))
        for key in list(self.null_ids):
            unload_module(self.null_ids.pop(key))
        self.stream_idx.clear()
        self.applied.clear()
        try:
            os.remove(STATE_PATH)
        except OSError:
            pass
        if restore_default and self.set_default and self.dac_sink:
            r = pactl("get-default-sink")
            current = r.stdout.strip()
            if not current or current.startswith(PREFIX):
                pactl("set-default-sink", self.dac_sink)

    def healthy(self):
        try:
            names = {s.get("name") for s in pactl_json("sinks")}
        except Exception:
            return False
        return self.dac_sink in names and all(SINKS[k][0] in names for k in self.keys)

    def _resolve_streams(self):
        """Map each loopback module to the sink-input that plays into the DAC."""
        wanted = {str(v): k for k, v in self.loop_ids.items()}
        for si in pactl_json("sink-inputs"):
            key = wanted.get(str(si.get("owner_module")))
            if key:
                self.stream_idx[key] = si.get("index")

    def set_mix(self, game, chat):
        # Volume goes on the loopback stream (Game/Chat -> DAC), so the
        # Game/Chat sinks' own sliders stay free for you to use as normal.
        levels = {"game": game, "chat": chat, "media": 100}
        for key in self.keys:
            pct = levels[key]
            if self.applied.get(key) == pct:
                continue
            for attempt in range(2):
                if key not in self.stream_idx:
                    self._resolve_streams()
                idx = self.stream_idx.get(key)
                if idx is None:
                    break
                r = pactl("set-sink-input-volume", str(idx), f"{pct}%")
                if r.returncode == 0:
                    self.applied[key] = pct
                    break
                self.stream_idx.pop(key, None)  # stale index, look it up again
        write_state({"outputs": self.keys, "levels": {k: self.applied.get(k) for k in self.keys}})


def write_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


# ========================================================= app identity ===
#
# Apps are identified the way KDE's System Monitor groups them: by the systemd
# unit they were launched in (app-<desktop id>@....service / -....scope). Games
# run inside Steam's unit, so anything carrying a Steam app id in its
# environment is split out as "steam:<appid>" - that folds Proton, the Steam
# runtime and anti-cheat helpers into the game they belong to.

PROC_ROOT = "/proc"
_UNIT_ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})")


def _read(path, mode="r"):
    try:
        with open(path, mode) as f:
            return f.read()
    except OSError:
        return None


def proc_unit(pid):
    """systemd unit a process lives in, e.g. 'app-org.kde.dolphin@a1b2.service'."""
    text = _read(f"{PROC_ROOT}/{pid}/cgroup", "rb")
    if not text:
        return None
    for line in text.decode(errors="replace").splitlines():
        if line.startswith("0::"):
            for part in reversed(line[3:].split("/")):
                if part.startswith("app-"):
                    return part
            return None
    return None


def unit_app_id(unit):
    """app-[launcher-]<AppID>@x.service | app-[launcher-]<AppID>-x.scope -> AppID."""
    if not unit or not unit.startswith("app-"):
        return None
    body = unit[4:]
    if body.endswith(".service"):
        body = body[:-8].split("@", 1)[0]
    elif body.endswith(".scope"):
        body = body[:-6]
        if "-" in body:
            body = body.rsplit("-", 1)[0]
    else:
        return None
    app = _UNIT_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), body.split("-")[-1])
    if not app or ":" in app or app.startswith("dbus"):
        return None  # D-Bus activated background services
    return app


def steam_game_id(pid):
    env = _read(f"{PROC_ROOT}/{pid}/environ", "rb")
    if not env:
        return None
    vals = {}
    for item in env.split(b"\0"):
        k, _, v = item.partition(b"=")
        if k in (b"SteamAppId", b"STEAM_COMPAT_APP_ID", b"SteamGameId"):
            vals[k] = v.decode(errors="replace")
    for k in (b"SteamAppId", b"STEAM_COMPAT_APP_ID", b"SteamGameId"):
        v = vals.get(k, "")
        if v.isdigit() and int(v) > 0:
            return v
    return None


def process_identity(pid, unit=None):
    """'steam:2073850', 'discord', 'org.kde.dolphin', ... or None."""
    if unit is None:
        unit = proc_unit(pid)
    app = unit_app_id(unit)
    if app is None or "steam" in app.lower():
        gid = steam_game_id(pid)
        if gid:
            return "steam:" + gid
    return app


def stream_pid(si, clients):
    """Host PID behind an audio stream (socket credentials first: right for Flatpaks)."""
    cprops = (clients.get(str(si.get("client"))) or {}).get("properties") or {}
    for v in (cprops.get("pipewire.sec.pid"),
              (si.get("properties") or {}).get("application.process.id"),
              cprops.get("application.process.id")):
        if v is not None and str(v).isdigit():
            return int(v)
    return None


def stream_identity(si, clients):
    pid = stream_pid(si, clients)
    if pid and os.path.isdir(f"{PROC_ROOT}/{pid}"):
        return process_identity(pid)
    return None


# ===================================================== remembered apps ===

def stream_key(props):
    """Stable name for 'which app is this' - shared with the ChatMix window."""
    binary = (props.get("application.process.binary") or "").strip()
    name = (props.get("application.name") or "").strip()
    if binary and binary.lower() not in GENERIC_BINARIES:
        key = binary.lower()
    else:
        key = (name or binary).lower() or None
    return STREAM_ALIASES.get(key, key)


def load_rules():
    try:
        with open(RULES_PATH) as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.get("apps", {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


class RuleEnforcer:
    """Moves apps you've assigned in the ChatMix window to Game/Chat/Media,
    including streams they open later - so this works with the window closed."""

    NEW_STREAM = re.compile(r"Event 'new' on sink-input #(\d+)")

    def __init__(self, audio):
        self.audio = audio
        self.proc = None
        self.buf = b""
        self.rules = {}
        self.mtime = None

    def start(self):
        self.stop()
        self.proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=PACTL_ENV)
        os.set_blocking(self.proc.stdout.fileno(), False)
        self.buf = b""
        self.reload(force=True)

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def fileno(self):
        return self.proc.stdout.fileno()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def reload(self, force=False):
        try:
            mtime = os.stat(RULES_PATH).st_mtime
        except OSError:
            mtime = None
        if force or mtime != self.mtime:
            self.mtime = mtime
            self.rules = load_rules()
            self.apply()

    def on_readable(self):
        try:
            chunk = os.read(self.fileno(), 65536)
        except BlockingIOError:
            return
        if not chunk:
            return
        self.buf += chunk
        *lines, self.buf = self.buf.split(b"\n")
        new = {int(m.group(1)) for l in lines
               if (m := self.NEW_STREAM.search(l.decode(errors="replace")))}
        if new:
            self.apply(new)

    def apply(self, only=None):
        if not self.rules:
            return
        try:
            sinks = {s.get("index"): s.get("name") for s in pactl_json("sinks")}
            inputs = pactl_json("sink-inputs")
        except Exception as e:
            log.debug("rules: pactl unavailable: %s", e)
            return
        try:
            clients = {str(c.get("index")): c for c in pactl_json("clients")}
        except Exception:
            clients = {}
        ours = {str(v) for v in self.audio.loop_ids.values()}
        for si in inputs:
            idx = si.get("index")
            if (only is not None and idx not in only) or str(si.get("owner_module")) in ours:
                continue
            key = stream_identity(si, clients)
            target = self.rules.get(key) if key else None
            if target is None:  # rules saved by older versions used the binary name
                key = stream_key(si.get("properties") or {})
                target = self.rules.get(key) if key else None
            if target not in self.audio.keys:
                continue
            sink = SINKS[target][0]
            if sinks.get(si.get("sink")) != sink:
                log.info("moving %s to %s", key, SINKS[target][1])
                pactl("move-sink-input", str(idx), sink)


# ================================================================== main ===

class Daemon:
    def __init__(self, args):
        self.args = args
        self.running = True
        self.dac = None
        self.audio = None
        self.rules = None

    def stop(self, *_):
        self.running = False

    def wait_for_dac_sink(self, timeout=20.0):
        end = time.monotonic() + timeout
        while self.running and time.monotonic() < end:
            try:
                sink = find_dac_sink(self.args.sink)
            except Exception as e:
                log.debug("pactl not ready: %s", e)
                sink = None
            if sink:
                return sink
            time.sleep(1)
        return None

    def session(self, path):
        """One connected-DAC session. Returns when the DAC goes away or we stop."""
        log.info("GameDAC found at %s, giving it %.0fs to settle", path, self.args.boot_wait)
        self.sleep(self.args.boot_wait)
        if not self.running:
            return
        sink = self.wait_for_dac_sink()
        if not sink:
            log.warning("GameDAC audio output not found in PipeWire; retrying")
            self.sleep(3)
            return
        self.audio = Audio(sink, not self.args.no_media, not self.args.no_default)
        self.audio.build()
        self.audio.set_mix(100, 100)
        self.rules = RuleEnforcer(self.audio)
        self.rules.start()

        self.dac = Dac(path)
        self.dac.send(OP_STATUS)
        self.dac.send(OP_CHATMIX_ENABLE, 0x01)
        log.info("ChatMix enabled on the dial (press the dial to switch volume/ChatMix)")

        last_check = time.monotonic()
        while self.running:
            watch = [self.dac.fd] + ([self.rules.fileno()] if self.rules.alive() else [])
            ready, _, _ = select.select(watch, [], [], 1.0)
            if self.rules.alive() and self.rules.fileno() in ready:
                self.rules.on_readable()
            self.rules.reload()  # picks up changes made in the ChatMix window
            if self.dac.fd in ready:
                latest = None
                for report in self.dac.read_reports():
                    if self.args.verbose:
                        log.debug("rx %s", report.hex(" "))
                    mix = parse_chatmix(report)
                    if mix:
                        latest = mix  # only the newest position matters
                if latest:
                    log.debug("dial: game %d%% chat %d%%", *latest)
                    self.audio.set_mix(*latest)
            if time.monotonic() - last_check > 5:
                last_check = time.monotonic()
                if not os.path.exists(path):
                    raise DeviceGone()
                if not self.audio.healthy():
                    log.warning("audio graph changed (PipeWire restart?), rebuilding")
                    game, chat = self.audio.applied.get("game", 100), self.audio.applied.get("chat", 100)
                    self.audio.teardown(restore_default=False)
                    sink = self.wait_for_dac_sink(10)
                    if not sink:
                        raise DeviceGone()
                    self.audio.dac_sink = sink
                    self.audio.build()
                    self.audio.set_mix(game, chat)
                    self.rules.start()
                elif not self.rules.alive():
                    self.rules.start()

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while self.running and time.monotonic() < end:
            time.sleep(min(0.2, end - time.monotonic()))

    def cleanup(self, device_present):
        if self.rules:
            self.rules.stop()
            self.rules = None
        if self.dac:
            if device_present:
                try:
                    self.dac.send(OP_CHATMIX_ENABLE, 0x00)  # dial back to plain volume
                except Exception:
                    pass
            self.dac.close()
            self.dac = None
        if self.audio:
            self.audio.teardown()
            self.audio = None

    def run(self):
        waiting_logged = False
        while self.running:
            path, pid = find_hidraw()
            if not path:
                if not waiting_logged:
                    log.info("waiting for an Arctis Nova Pro Wired GameDAC...")
                    waiting_logged = True
                self.sleep(2)
                continue
            waiting_logged = False
            present = True
            try:
                self.session(path)
            except DeviceGone:
                present = False
                log.info("GameDAC disconnected")
            except PermissionError:
                log.error("no permission for %s - is the udev rule installed? "
                          "(unplug/replug the DAC after installing it)", path)
                self.cleanup(False)
                self.sleep(10)
                continue
            except Exception:
                log.exception("unexpected error, restarting session")
                self.cleanup(os.path.exists(path))
                self.sleep(3)
                continue
            self.cleanup(present and os.path.exists(path))


def listen(args):
    """Debug mode: enable ChatMix and print every report from the DAC."""
    path, pid = find_hidraw()
    if not path:
        sys.exit("No Nova Pro Wired GameDAC (interface 4) found.")
    print(f"{path}  (product {pid:04x}) - turn the dial, Ctrl+C to stop")
    dac = Dac(path)
    dac.send(OP_STATUS)
    dac.send(OP_CHATMIX_ENABLE, 0x01)
    try:
        while True:
            select.select([dac.fd], [], [], 1.0)
            for r in dac.read_reports():
                mix = parse_chatmix(r)
                extra = f"   <- game {mix[0]}%  chat {mix[1]}%" if mix else ""
                print(r[:16].hex(" ") + extra, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        dac.send(OP_CHATMIX_ENABLE, 0x00)
        dac.close()


def main():
    p = argparse.ArgumentParser(description="ChatMix dial for the Arctis Nova Pro Wired GameDAC")
    p.add_argument("--version", action="version", version=f"nova-chatmix {__version__}")
    p.add_argument("--no-media", action="store_true",
                   help="don't create the 'Media' output (the one the dial never turns down)")
    p.add_argument("--media", action="store_true", help=argparse.SUPPRESS)  # old flag, now default
    p.add_argument("--no-default", action="store_true",
                   help="don't make 'Game' the default output")
    p.add_argument("--sink", help="exact PipeWire sink name of the DAC (auto-detected if omitted)")
    p.add_argument("--boot-wait", type=float, default=DAC_BOOT_WAIT_S, help=argparse.SUPPRESS)
    p.add_argument("--listen", action="store_true",
                   help="debug: print raw DAC reports while you turn the dial")
    p.add_argument("--reset-dac", action="store_true",
                   help="headphones silent? reset the GameDAC (same as unplugging it and back in)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    if args.listen:
        return listen(args)
    if args.reset_dac:
        sys.exit(reset_dac())

    d = Daemon(args)
    signal.signal(signal.SIGTERM, d.stop)
    signal.signal(signal.SIGINT, d.stop)
    d.run()
    log.info("stopped")


if __name__ == "__main__":
    main()
