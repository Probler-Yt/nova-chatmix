#!/usr/bin/env python3
"""
ChatMix window - drag apps between Apps, Chat, Media and Game for nova-chatmix.

  Apps   your open applications (grouped like KDE System Monitor's
         Applications page, with Steam games pulled out under their own name)
         that aren't assigned yet - they play through Game by default.
  Chat / Media / Game
         drag an app here and the nova-chatmix service sends its sound there,
         now and every time it plays, even with this window closed.
         Drag it back to Apps to unassign.

  EQ     each of Chat / Media / Game can have its own EQ: pick one of the
         SteelSeries Sonar presets (downloaded when you open the picker) or
         load a Sonar preset file.

  nova-chatmix-gui --debug   print what the window can see, then exit
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import sys
import time


def _load_service():
    here = os.path.dirname(os.path.realpath(__file__))
    for name in ("nova-chatmix", "nova-chatmix.py"):
        path = os.path.join(here, name)
        if os.path.exists(path):
            loader = importlib.machinery.SourceFileLoader("nova_chatmix_service", path)
            spec = importlib.util.spec_from_loader(loader.name, loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    sys.exit("nova-chatmix-gui: can't find the nova-chatmix service next to this file")


svc = _load_service()
PREFIX = svc.PREFIX

COLUMNS = [  # key, label, accent  (key None = unassigned)
    (None, "Apps", "#8A8F98"),
    ("chat", "Chat", "#22B59A"),
    ("media", "Media", "#9B7FE6"),
    ("game", "Game", "#E8A33D"),
]
LABELS = {"chat": "Chat", "media": "Media", "game": "Game"}
MIME = "application/x-nova-chatmix-app"

# Desktop plumbing that runs as its own "app" but isn't one you'd route.
NOT_APPS = ("org.freedesktop.impl.portal", "xdg-desktop-portal", "polkit", "kwalletd", "kwallet",
            "xwaylandvideobridge", "kdeconnect", "org.kde.kded", "kded", "kaccess", "geoclue",
            "baloo", "akonadi", "discover.notifier", "kalendarac", "nova-chatmix", "plasmashell",
            "at-spi", "gnome-keyring", "ssh-agent", "kglobalaccel", "kwin",
            "org.kde.kscreen", "org.kde.ksplash", "pam_kwallet", "gmenudbusmenuproxy",
            "xembedsniproxy", "org.kde.drkonqi", "powerdevil", "ksmserver", "krunner")
# Streams that are system blips / always-open background noise.
NOISE_KEYS = {"speech-dispatcher", "sd_dummy", "sd_espeak-ng", "sd_openjtalk", "kded6", "kded5",
              "pipewire", "wireplumber", "plasmashell", "kwin_wayland"}
SYSTEM_ROLES = {"event", "notification", "alert", "a11y"}
BORING_TITLES = {"", "playback", "audio stream", "playstream", "output", "audio",
                 "webrtc voiceengine", "simple-audio", "sdl audio", "fna audio", "openal"}

STEAM_ROOTS = ["~/.local/share/Steam", "~/.steam/steam", "~/.steam/root",
               "~/.var/app/com.valvesoftware.Steam/.local/share/Steam"]


# ------------------------------------------------------------ app menu ---

def data_dirs():
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = [home] + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += [os.path.join(home, "flatpak/exports/share"), "/var/lib/flatpak/exports/share"]
    out = [os.path.join(d, "applications") for d in dirs if d]
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    out += [os.path.join(cfg, "autostart"), "/etc/xdg/autostart"]  # tray apps started at login
    seen = set()
    return [d for d in out if not (d in seen or seen.add(d))]


class DesktopIndex:
    """Desktop entries by id ('org.kde.dolphin'), which is what app units are named after."""

    def __init__(self, dirs=None):
        self.by_id = {}
        self.by_lower = {}
        for d in dirs or data_dirs():
            try:
                files = sorted(os.listdir(d))
            except OSError:
                continue
            for fn in files:
                if fn.endswith(".desktop") and fn[:-8] not in self.by_id:
                    entry = self._parse(os.path.join(d, fn))
                    if entry:
                        self.by_id[fn[:-8]] = entry
                        self.by_lower.setdefault(fn[:-8].lower(), entry)

    @staticmethod
    def _parse(path):
        d, section = {}, None
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("["):
                        section = line
                    elif section == "[Desktop Entry]" and "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        d.setdefault(k.strip(), v.strip())
        except OSError:
            return None
        return {"name": d.get("Name", ""), "icon": d.get("Icon", ""),
                "hidden": d.get("NoDisplay", "").lower() == "true"}

    def get(self, app_id):
        return self.by_id.get(app_id) or self.by_lower.get(app_id.lower())


# --------------------------------------------------------------- steam ---

_steam_cache = {}


def steam_libraries():
    libs = []
    for root in STEAM_ROOTS:
        root = os.path.expanduser(root)
        if os.path.isdir(os.path.join(root, "steamapps")):
            libs.append(root)
        vdf = svc._read(os.path.join(root, "steamapps", "libraryfolders.vdf"))
        if vdf:
            libs += re.findall(r'"path"\s+"([^"]+)"', vdf)
    seen, out = set(), []
    for lib in libs:
        real = os.path.realpath(lib)
        if real not in seen:
            seen.add(real)
            out.append(lib)
    return out


def steam_game_name(gid):
    hit = _steam_cache.get(gid)
    if hit and (hit[0] or time.monotonic() - hit[1] < 60):
        return hit[0]
    name = None
    for lib in steam_libraries():
        acf = svc._read(os.path.join(lib, "steamapps", f"appmanifest_{gid}.acf"))
        m = re.search(r'"name"\s+"([^"]*)"', acf or "")
        if m:
            name = m.group(1)
            break
    _steam_cache[gid] = (name, time.monotonic())
    return name


# ---------------------------------------------------------- open apps ---

def is_plumbing(app_id):
    low = app_id.lower()
    return any(p in low for p in NOT_APPS)


def scan_apps(index):
    """{identity: info} for every open app, KDE-style, with Steam games split out."""
    me = os.getuid()
    apps = {}
    try:
        pids = [p for p in os.listdir(svc.PROC_ROOT) if p.isdigit()]
    except OSError:
        return apps
    for pid in pids:
        base = os.path.join(svc.PROC_ROOT, pid)
        try:
            if os.stat(base).st_uid != me:
                continue
        except OSError:
            continue
        unit = svc.proc_unit(pid)
        ident = svc.process_identity(pid, unit)
        if not ident:
            continue
        a = apps.get(ident)
        if a is None:
            a = apps[ident] = describe(ident, index)
        if a is None:
            continue
        # names an older ChatMix version may have saved rules under
        exe = os.path.basename((svc._read(os.path.join(base, "comm")) or "").strip()).lower()
        a["aliases"].add(exe)
        try:
            a["aliases"].add(os.path.basename(os.readlink(os.path.join(base, "exe"))).lower())
        except OSError:
            pass
        argv = (svc._read(os.path.join(base, "cmdline"), "rb") or b"").split(b"\0")[:2]
        for arg in argv:
            arg = arg.decode(errors="replace").replace("\\", "/")
            if arg.lower().endswith(".exe"):
                a["aliases"].add(os.path.basename(arg).lower())
    return {k: v for k, v in apps.items() if v is not None}


def describe(ident, index):
    if ident.startswith("steam:"):
        gid = ident.split(":", 1)[1]
        return {"key": ident, "name": steam_game_name(gid) or f"Steam game {gid}",
                "icon": f"steam_icon_{gid}", "aliases": set()}
    if is_plumbing(ident):
        return None
    entry = index.get(ident)
    if entry and entry["hidden"]:
        return None
    name = (entry or {}).get("name") or ident.split(".")[-1]
    return {"key": ident, "name": name[:1].upper() + name[1:],
            "icon": (entry or {}).get("icon") or ident, "aliases": set()}


# --------------------------------------------------------------- audio ---

def safe_list(kind, errors):
    try:
        return svc.pactl_json(kind)
    except Exception as e:
        errors.append(f"couldn't read {kind}: {e}")
        return []


def safe_pactl(*args):
    try:
        return svc.pactl(*args)
    except Exception:
        return None


def volume_percent(si):
    for ch in (si.get("volume") or {}).values():
        try:
            return int(str(ch.get("value_percent", "")).strip().rstrip("%"))
        except (ValueError, AttributeError):
            pass
    return None


def audio_snapshot():
    errors = []
    sinks = safe_list("sinks", errors)
    present = {k for k in LABELS if any(s.get("name") == PREFIX + k.capitalize() for s in sinks)}
    loop_modules = {}
    try:
        modules = svc.list_modules()
    except Exception as e:
        errors.append(f"couldn't read modules: {e}")
        modules = []
    for idx, _, arg in modules:
        for k in LABELS:
            if f"source={PREFIX}{k.capitalize()}.monitor" in arg:
                loop_modules[str(idx)] = k
    clients = {str(c.get("index")): c for c in safe_list("clients", errors)}

    levels, streams = {}, []
    for si in safe_list("sink-inputs", errors):
        owner = str(si.get("owner_module"))
        if owner in loop_modules:
            levels[loop_modules[owner]] = volume_percent(si)
            continue
        node = (si.get("properties") or {}).get("node.name", "")
        if node.startswith("nova_chatmix_eq_"):
            levels[node[len("nova_chatmix_eq_"):]] = volume_percent(si)
            continue
        props = si.get("properties") or {}
        legacy = svc.stream_key(props)
        role = (props.get("media.role") or "").lower()
        streams.append({
            "index": si.get("index"),
            "ident": svc.stream_identity(si, clients),
            "legacy": legacy,
            "name": (props.get("application.name") or legacy or "Unknown").strip(),
            "icon": props.get("application.icon_name") or "",
            "playing": not si.get("corked"),
            "noise": legacy in NOISE_KEYS or role in SYSTEM_ROLES or
                     (props.get("application.process.binary") or "").lower() in NOISE_KEYS,
            "title": (props.get("media.name") or "").strip(),
        })
    state = svc._read(svc.STATE_PATH)
    eq_state = {}
    if state:  # the service publishes the dial position; more reliable than guessing
        try:
            st = json.loads(state)
            levels.update({k: v for k, v in st.get("levels", {}).items() if v is not None})
            eq_state = st.get("eq", {}) if isinstance(st.get("eq"), dict) else {}
        except ValueError:
            pass
    return {"present": present, "levels": levels, "streams": streams, "errors": errors,
            "eq_state": eq_state}


def build_cards(apps, snap, rules, index=None):
    """One card per app: open apps + anything playing + assigned apps that are closed."""
    cards = {k: dict(v, playing=False, open=True, indices=[], titles=[]) for k, v in apps.items()}
    for st in snap["streams"]:
        key = st["ident"] if st["ident"] in cards else None
        if key is None:
            # a stream from something not in the list (or an older-style rule)
            key = st["ident"] or st["legacy"]
            if not key or (st["noise"] and key not in rules):
                continue
            if key not in cards:
                cards[key] = {"key": key, "name": st["name"].removesuffix(".exe"),
                              "icon": st["icon"], "aliases": {st["legacy"] or ""},
                              "playing": False, "open": True, "indices": [], "titles": []}
        c = cards[key]
        c["indices"].append(st["index"])
        c["playing"] |= st["playing"]
        t = st["title"]
        if st["playing"] and t.lower() not in BORING_TITLES and t.lower() != c["name"].lower():
            c["titles"].append(t)
    for key, target in rules.items():
        if key not in cards:  # assigned but closed: still show it, under its real name
            info = describe(key, index) if index is not None else None
            info = info or {"key": key, "name": key.split(":")[-1].removesuffix(".exe"),
                            "icon": key, "aliases": set()}
            cards[key] = dict(info, playing=False, open=False, indices=[], titles=[])
    for c in cards.values():
        c["column"] = rules.get(c["key"])
    return sorted(cards.values(), key=lambda c: (not c["playing"], not c["open"], c["name"].lower()))


def migrate_rules(rules, apps):
    """Rules from the previous version were keyed by binary name; re-key them to apps."""
    changed = False
    for key in [k for k in rules if k not in apps]:
        owner = next((a for a in apps.values() if key in a["aliases"]), None)
        if owner and owner["key"] not in rules:
            rules[owner["key"]] = rules.pop(key)
            changed = True
    return changed


def _read_settings():
    try:
        with open(svc.RULES_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_settings(data):
    os.makedirs(os.path.dirname(svc.RULES_PATH), exist_ok=True)
    tmp = svc.RULES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, svc.RULES_PATH)  # atomic, so the service never reads half a file


def save_rules(rules):
    data = _read_settings()
    data["apps"] = dict(sorted(rules.items()))
    _write_settings(data)


def save_eq(output, spec):
    """spec = {"name", "source", "filters"} or None to turn that output's EQ off."""
    data = _read_settings()
    eq = data.get("eq") if isinstance(data.get("eq"), dict) else {}
    if spec:
        eq[output] = spec
    else:
        eq.pop(output, None)
    data["eq"] = eq
    _write_settings(data)


def current_eq():
    data = _read_settings().get("eq")
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------- Sonar presets ---
# SteelSeries' Sonar presets, as collected by the Arctis Sound Manager project
# (github.com/loteran/Arctis-Sound-Manager). They're fetched on demand and kept
# in ~/.cache, rather than shipped with nova-chatmix.

SONAR_REPO = "https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main"
SONAR_MANIFEST = SONAR_REPO + "/presets_manifest.json"
SONAR_FILE = SONAR_REPO + "/src/arctis_sound_manager/gui/presets/"
SONAR_CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                           "nova-chatmix", "sonar")
SONAR_MAX_AGE_S = 24 * 3600


def _http_get(url, timeout=10):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "nova-chatmix"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def sonar_manifest(refresh=False):
    """List of preset file names; from cache when fresh or offline."""
    path = os.path.join(SONAR_CACHE, "manifest.json")
    try:
        fresh = time.time() - os.stat(path).st_mtime < SONAR_MAX_AGE_S
    except OSError:
        fresh = False
    if refresh or not fresh:
        try:
            data = json.loads(_http_get(SONAR_MANIFEST))
            names = [n for n in data.get("presets", []) if isinstance(n, str) and n.endswith(".json")]
            if names:
                os.makedirs(SONAR_CACHE, exist_ok=True)
                with open(path + ".tmp", "w") as f:
                    json.dump({"presets": names}, f)
                os.replace(path + ".tmp", path)
        except Exception:
            pass  # offline: fall back to whatever we had
    try:
        with open(path) as f:
            return json.load(f)["presets"]
    except (OSError, ValueError, KeyError):
        return []


def sonar_preset(filename):
    """The preset's filters (downloaded once, then cached)."""
    from urllib.parse import quote
    safe = os.path.basename(filename)
    path = os.path.join(SONAR_CACHE, "presets", safe)
    if not os.path.exists(path):
        raw = _http_get(SONAR_FILE + quote(safe))
        json.loads(raw)  # refuse to cache anything that isn't JSON
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(raw)
        os.replace(path + ".tmp", path)
    with open(path) as f:
        return svc.eq_from_sonar(json.load(f))


def preset_label(filename):
    """'Anno 117_ Pax Romana [Game].json' -> ('Anno 117: Pax Romana', 'Game')."""
    base = filename[:-5] if filename.endswith(".json") else filename
    kind = ""
    if base.endswith("]") and " [" in base:
        base, kind = base[:-1].rsplit(" [", 1)
    return base.replace("_ ", ": ").replace("_", " ").strip(), kind


def debug_dump():
    index = DesktopIndex()
    apps = scan_apps(index)
    snap = audio_snapshot()
    raw = {}
    for kind in ("sink-inputs", "clients", "modules"):
        rj = safe_pactl("-f", "json", "list", kind)
        rt = safe_pactl("list", kind)
        try:
            n_json = len(json.loads(rj.stdout or "[]", strict=False)) if rj and rj.returncode == 0 \
                else f"exit {rj.returncode if rj else '?'}"
        except ValueError as e:
            n_json = f"bad JSON: {e}"
        raw[kind] = {"json_items": n_json,
                     "text_items": len(svc.parse_pactl_text(rt.stdout)) if rt else None}
    v = safe_pactl("--version")
    print(json.dumps({
        "pactl": (v.stdout.splitlines() or ["?"])[0] if v else "missing",
        "pactl_raw_counts": raw,
        "errors": snap["errors"],
        "service_outputs": sorted(snap["present"]),
        "dial_levels": snap["levels"],
        "open_apps": {k: a["name"] for k, a in sorted(apps.items())},
        "audio_streams": [{k: s[k] for k in ("index", "ident", "legacy", "name", "playing", "noise")}
                          for s in snap["streams"]],
        "rules": svc.load_rules(),
        "desktop_entries": len(index.by_id),
    }, indent=2, default=list))


# ------------------------------------------------------------------- ui ---

if "--debug" in sys.argv:
    debug_dump()
    sys.exit(0)

from PySide6.QtCore import (QMimeData, QPointF, QProcess, QProcessEnvironment,  # noqa: E402
                            QSize, Qt, QThread, QTimer, Signal)
from PySide6.QtGui import (QAction, QColor, QDrag, QFont, QIcon, QPainter, QPainterPath,  # noqa: E402
                           QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout,  # noqa: E402
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

AVATAR_COLOURS = ["#5B8DEF", "#E0697B", "#3FA796", "#C98A2E", "#8E6FD8", "#4AA3C7", "#D46FB0"]


def app_pixmap(card, size=30):
    names = [card["icon"]]
    if not card["key"].startswith("steam:"):
        names += [card["key"], card["key"].split(".")[-1].lower()]
    for name in names:
        if not name:
            continue
        icon = QIcon(name) if name.startswith("/") else QIcon.fromTheme(name)
        if not icon.isNull():
            return icon.pixmap(QSize(size, size))
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(AVATAR_COLOURS[sum(map(ord, card["key"])) % len(AVATAR_COLOURS)]))
    p.drawRoundedRect(0, 0, size, size, 8, 8)
    f = p.font()
    f.setBold(True)
    f.setPixelSize(int(size * 0.5))
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(0, 0, size, size, Qt.AlignCenter, (card["name"][:1] or "?").upper())
    p.end()
    return pm


class AppCard(QFrame):
    def __init__(self, win, card):
        super().__init__()
        self.win, self.card = win, card
        self.setObjectName("card")
        self.setProperty("idle", not card["open"])
        self.setCursor(Qt.OpenHandCursor)
        self._press = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(app_pixmap(card))
        icon.setFixedSize(30, 30)
        icon.setEnabled(card["open"])
        lay.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(card["name"])
        name.setObjectName("cardName")
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sub = QLabel(card["titles"][0] if card["titles"] else
                     "Playing" if card["playing"] else "Open" if card["open"] else "Not running")
        sub.setObjectName("cardSub")
        sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text.addWidget(name)
        text.addWidget(sub)
        lay.addLayout(text, 1)
        if card["playing"]:
            dot = QLabel("●")
            dot.setObjectName("liveDot")
            dot.setToolTip("Making sound right now")
            lay.addWidget(dot)
        self.setToolTip(f"{card['name']}\nDrag into Chat, Media or Game. Right-click for options.")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, e):
        self._press = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseMoveEvent(self, e):
        if self._press is None or (e.position().toPoint() - self._press).manhattanLength() \
                < QApplication.startDragDistance():
            return
        mime = QMimeData()
        mime.setData(MIME, json.dumps({"key": self.card["key"],
                                       "indices": self.card["indices"]}).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = self.grab()
        faded = QPixmap(pm.size())
        faded.fill(Qt.transparent)
        p = QPainter(faded)
        p.setOpacity(0.85)
        p.drawPixmap(0, 0, pm)
        p.end()
        drag.setPixmap(faded)
        drag.setHotSpot(self._press)
        self._press = None
        self.win.drag_active = True
        drag.exec(Qt.MoveAction)
        self.win.drag_active = False
        self.setCursor(Qt.OpenHandCursor)
        self.win.schedule_refresh()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        for key, label in LABELS.items():
            if key in self.win.present and key != self.card["column"]:
                act = QAction(f"Move to {label}", menu)
                act.triggered.connect(lambda _=False, k=key: self.win.assign(self.card, k))
                menu.addAction(act)
        if self.card["column"]:
            menu.addSeparator()
            act = QAction("Back to Apps (unassign)", menu)
            act.triggered.connect(lambda: self.win.assign(self.card, None))
            menu.addAction(act)
        if not menu.isEmpty():
            menu.exec(e.globalPos())


class LevelBar(QWidget):
    def __init__(self, accent):
        super().__init__()
        self.accent = QColor(accent)
        self.value = 100
        self.setFixedHeight(4)

    def set_value(self, v):
        self.value = max(0, min(100, v))
        self.update()

    def paintEvent(self, _):
        if self.accent.alpha() == 0:
            return  # placeholder that only keeps the layout aligned
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        track = QColor(self.accent)
        track.setAlpha(45)
        p.setBrush(track)
        p.drawRoundedRect(self.rect(), 2, 2)
        fill = self.rect()
        fill.setWidth(int(fill.width() * self.value / 100))
        p.setBrush(self.accent)
        p.drawRoundedRect(fill, 2, 2)


class Worker(QThread):
    """Run fn() off the UI thread; emits (result, error message)."""
    done = Signal(object, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), "")
        except Exception as e:  # network, bad file, ...
            self.done.emit(None, str(e) or e.__class__.__name__)


class EqCurve(QWidget):
    """Frequency response of an EQ, 20 Hz - 20 kHz, preamp included."""
    FREQS = svc.log_freqs(300)

    def __init__(self, accent, parent=None):
        super().__init__(parent)
        self.accent = QColor(accent)
        self.filters = []
        self.setMinimumHeight(150)

    def set_filters(self, filters):
        self.filters = filters or []
        self.update()

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(34, 8, -8, -20)
        text = self.palette().text().color()
        grid = QColor(text)
        grid.setAlpha(28)
        label = QColor(text)
        label.setAlpha(140)
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 2))
        p.setFont(f)
        span = 15.0  # dB above and below 0

        def x_of(hz):
            return r.left() + r.width() * math.log10(hz / 20) / 3

        def y_of(db):
            return r.center().y() - (db / span) * (r.height() / 2)

        for db in (-12, -6, 0, 6, 12):
            y = y_of(db)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(label)
            p.drawText(QPointF(r.left() - 30, y + 4), f"{db:+d}" if db else " 0")
        for hz, name in ((20, "20"), (100, "100"), (1000, "1k"), (10000, "10k"), (20000, "20k")):
            x = x_of(hz)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(label)
            p.drawText(QPointF(x - 8, r.bottom() + 15), name)
        if not self.filters:
            p.setPen(label)
            p.drawText(r, Qt.AlignCenter, "Flat - no EQ")
            return
        pre = svc.eq_preamp_db(self.filters)
        resp = svc.eq_response_db(self.filters, self.FREQS)
        path = QPainterPath()
        for i, (hz, db) in enumerate(zip(self.FREQS, resp)):
            pt = QPointF(x_of(hz), y_of(max(-span, min(span, db + pre))))
            path.lineTo(pt) if i else path.moveTo(pt)
        p.setPen(QPen(self.accent, 2.2))
        p.drawPath(path)


class EqDialog(QDialog):
    """Pick a Sonar preset (or a preset file) for one output."""

    def __init__(self, win, output, accent):
        super().__init__(win)
        self.win, self.output = win, output
        self.setWindowTitle(f"{LABELS[output]} EQ")
        self.resize(560, 600)
        self.chosen = None  # {"name", "source", "filters"} or None = off
        self.workers = []
        cur = current_eq().get(output)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search presets - games, music, movies, chat…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._picked)
        self.list.itemDoubleClicked.connect(lambda *_: self.apply())
        lay.addWidget(self.list, 1)

        self.curve = EqCurve(accent)
        lay.addWidget(self.curve)
        self.info = QLabel("")
        self.info.setObjectName("caption")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        link = self.palette().highlight().color().name()
        credit = QLabel('Presets: SteelSeries Sonar, as collected by '
                        f'<a style="color:{link}" href="https://github.com/loteran/Arctis-Sound-Manager">'
                        'Arctis Sound Manager</a>.')
        credit.setOpenExternalLinks(True)
        credit.setObjectName("caption")
        lay.addWidget(credit)

        buttons = QHBoxLayout()
        load = QPushButton("Load file…")
        load.setToolTip("Use a Sonar preset file (.json), e.g. one exported from SteelSeries GG")
        load.clicked.connect(self.load_file)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setDefault(True)
        self.apply_btn.clicked.connect(self.apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(load)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.apply_btn)
        lay.addLayout(buttons)

        off = QListWidgetItem("Off (no EQ)")
        off.setData(Qt.UserRole, None)
        self.list.addItem(off)
        if cur:
            self.chosen = {k: cur[k] for k in ("name", "source", "filters") if k in cur}
            self.curve.set_filters(svc.clean_filters(cur.get("filters")))
            self.info.setText(f"Now: {cur.get('name', 'Custom')}")
        else:
            self.list.setCurrentRow(0)
        self.loading = QListWidgetItem("Getting the Sonar presets…")
        self.loading.setFlags(Qt.NoItemFlags)
        self.list.addItem(self.loading)
        self._run(sonar_manifest, self._got_manifest)

    def _run(self, fn, cb):
        w = Worker(fn, self)
        w.done.connect(cb)
        w.finished.connect(lambda: self.workers.remove(w) if w in self.workers else None)
        self.workers.append(w)
        w.start()

    def _got_manifest(self, names, err):
        self.list.takeItem(self.list.row(self.loading))
        if not names:
            item = QListWidgetItem("Couldn't download the Sonar presets - check your internet "
                                   "connection, or use Load file…")
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
            return
        order = {"chat": ("Chat", "Game"), "media": ("Game", "Chat"), "game": ("Game", "Chat")}[self.output]
        rows = []
        for n in names:
            title, kind = preset_label(n)
            if kind == "Mic":
                continue  # microphone presets don't apply to an output
            rank = order.index(kind) if kind in order else len(order)
            rows.append((rank, title.lower(), title, kind, n))
        cur = (self.chosen or {}).get("source")
        for _, _, title, kind, n in sorted(rows):
            item = QListWidgetItem(title if kind in ("Game", "") else f"{title}  ·  {kind}")
            item.setData(Qt.UserRole, n)
            self.list.addItem(item)
            if n == cur:
                self.list.setCurrentItem(item)
        self._filter(self.search.text())

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and it.data(Qt.UserRole) is not None and t not in it.text().lower())

    def _picked(self, item, _prev=None):
        if item is None or not (item.flags() & Qt.ItemIsEnabled):
            return
        name = item.data(Qt.UserRole)
        if name is None:
            self.chosen = None
            self.curve.set_filters([])
            self.info.setText("No EQ on this output.")
            return
        self.info.setText("Loading…")
        self.apply_btn.setEnabled(False)

        def done(filters, err, name=name, title=item.text()):
            cur = self.list.currentItem()
            if cur is None or cur.data(Qt.UserRole) != name:
                return  # user moved on
            self.apply_btn.setEnabled(True)
            if err:
                self.chosen = False
                self.info.setText(f"Couldn't download this preset ({err}).")
                return
            self.chosen = {"name": title.split("  ·  ")[0], "source": name, "filters": filters}
            self.curve.set_filters(filters)
            self._describe(filters)

        self._run(lambda: sonar_preset(name), done)

    def _describe(self, filters):
        if not filters:
            self.info.setText("This preset is flat - choosing it turns the EQ off.")
            return
        pre = svc.eq_preamp_db(filters)
        self.info.setText(f"{len(filters)} filters. Turned down {abs(pre):.1f} dB overall so its "
                          "boosts can't clip - turn your volume up a touch to match." if pre < 0
                          else f"{len(filters)} filters.")

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load a Sonar preset", os.path.expanduser("~"),
                                              "Sonar presets (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path) as f:
                filters = svc.eq_from_sonar(json.load(f))
        except (OSError, ValueError, AttributeError) as e:
            self.info.setText(f"That file isn't a Sonar preset ({e}).")
            return
        title = preset_label(os.path.basename(path))[0]
        self.chosen = {"name": title, "source": "file:" + os.path.basename(path), "filters": filters}
        self.list.clearSelection()
        self.curve.set_filters(filters)
        self._describe(filters)

    def apply(self):
        if self.chosen is False:
            return
        if self.chosen and self.chosen.get("filters"):
            save_eq(self.output, self.chosen)
        else:
            save_eq(self.output, None)
        self.accept()

    def done(self, r):
        for w in list(self.workers):
            w.wait(2000)
        super().done(r)


class Column(QFrame):
    def __init__(self, win, key, label, accent):
        super().__init__()
        self.win, self.key = win, key
        self.setObjectName("column")
        self.setAcceptDrops(True)
        self.setProperty("hover", False)
        self.setStyleSheet(f"#column {{ border-top: 3px solid {accent}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel(label)
        title.setObjectName("colTitle")
        self.level = QLabel("")
        self.level.setObjectName("colLevel")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.level)
        outer.addLayout(head)
        self.caption = QLabel({None: "Not assigned · plays through Game",
                               "chat": "Turned down as you dial toward Game",
                               "media": "The dial never touches this",
                               "game": "Turned down as you dial toward Chat"}[key])
        self.caption.setObjectName("caption")
        self.caption.setWordWrap(True)
        outer.addWidget(self.caption)
        # Media has no dial level, but keeps the (empty) bar's space so the
        # EQ buttons line up across columns.
        self.bar = LevelBar(accent if key in ("game", "chat") else "transparent")
        self.bar.setVisible(key is not None)
        outer.addWidget(self.bar)
        self.accent = accent
        self.eq_btn = None
        if key is not None:
            self.eq_btn = QPushButton("EQ: Off")
            self.eq_btn.setObjectName("eqButton")
            self.eq_btn.setCursor(Qt.PointingHandCursor)
            self.eq_btn.setToolTip(f"Choose an EQ preset for everything in {label}")
            self.eq_btn.clicked.connect(lambda: self.win.edit_eq(self.key, self.accent))
            outer.addWidget(self.eq_btn)
        outer.addSpacing(2)

        self.list = QVBoxLayout()
        self.list.setSpacing(6)
        self.list.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(self.list)
        holder.setAutoFillBackground(False)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget { background: transparent; }")
        outer.addWidget(scroll, 1)

    def set_eq(self, spec, active):
        if self.eq_btn is None:
            return
        if not spec:
            self.eq_btn.setText("EQ: Off")
            self.eq_btn.setProperty("on", False)
            self.eq_btn.setToolTip(f"Choose an EQ preset for this output")
        else:
            name = spec.get("name", "Custom")
            warn = "" if active is not False else "  ⚠"
            self.eq_btn.setText(f"EQ: {name}{warn}")
            self.eq_btn.setProperty("on", True)
            self.eq_btn.setToolTip(f"EQ: {name}" + (
                "\nIt isn't running - the service couldn't start it, so this output plays "
                "without EQ. Check: journalctl --user -u nova-chatmix" if active is False else ""))
        self.eq_btn.style().unpolish(self.eq_btn)
        self.eq_btn.style().polish(self.eq_btn)

    def set_level(self, pct):
        if self.key in ("game", "chat") and pct is not None:
            self.level.setText(f"{pct}%")
            self.bar.set_value(pct)
        else:
            self.level.setText("")

    def set_cards(self, cards):
        while self.list.count():
            old = self.list.takeAt(0).widget()
            if old:
                old.hide()
                old.setParent(None)
                old.deleteLater()
        for c in cards:
            self.list.addWidget(c)
        if not cards:
            hint = QLabel("Drag apps here" if self.key else "Everything's assigned")
            hint.setObjectName("empty")
            hint.setAlignment(Qt.AlignCenter)
            self.list.addWidget(hint)
        self.list.addStretch(1)

    def cards(self):
        return [self.list.itemAt(i).widget() for i in range(self.list.count())
                if isinstance(self.list.itemAt(i).widget(), AppCard)]

    def _hover(self, on):
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        data = json.loads(bytes(e.mimeData().data(MIME)).decode())
        e.acceptProposedAction()
        self.win.assign(data, self.key)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChatMix")
        self.setWindowIcon(QIcon.fromTheme("audio-headphones"))
        self.resize(1000, 560)
        self.setMinimumSize(620, 340)
        self.present = set()
        self.drag_active = False
        self.index = DesktopIndex()
        self.index_time = time.monotonic()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(12, 8, 8, 8)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_btn = QPushButton("Turn it on")
        self.banner_btn.clicked.connect(self.start_service)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_btn)
        root.addWidget(self.banner)

        cols = QHBoxLayout()
        cols.setSpacing(10)
        self.columns = {}
        for key, label, accent in COLUMNS:
            self.columns[key] = Column(self, key, label, accent)
            cols.addWidget(self.columns[key], 1)
        root.addLayout(cols, 1)

        foot = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.reset_btn = QPushButton("Reset DAC")
        self.reset_btn.setIcon(QIcon.fromTheme("view-refresh"))
        self.reset_btn.setToolTip("Headphones gone quiet? Does the same as unplugging the "
                                  "GameDAC and plugging it back in.")
        self.reset_btn.clicked.connect(self.reset_dac)
        foot.addWidget(self.status, 1)
        foot.addWidget(self.reset_btn)
        root.addLayout(foot)

        self.apply_style()

        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(150)
        self.debounce.timeout.connect(self.refresh)
        self.watch = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("LC_ALL", "C.UTF-8")  # English event names regardless of locale
        self.watch.setProcessEnvironment(env)
        self.watch.readyReadStandardOutput.connect(self._on_events)
        self.watch.finished.connect(lambda *_: QTimer.singleShot(2000, self._start_watch))
        self._start_watch()
        self.poll = QTimer(self)
        self.poll.setInterval(2000)  # also catches apps opening and closing
        self.poll.timeout.connect(self.refresh)
        self.poll.start()
        self.refresh()

    def _start_watch(self):
        if self.watch.state() == QProcess.NotRunning:
            self.watch.start("pactl", ["subscribe"])

    def _on_events(self):
        data = bytes(self.watch.readAllStandardOutput()).decode(errors="replace")
        if any(w in data for w in ("sink-input", "'sink'", "module", "server")):
            self.schedule_refresh()

    def schedule_refresh(self):
        self.debounce.start()

    def closeEvent(self, e):
        self.watch.kill()
        self.watch.waitForFinished(500)
        super().closeEvent(e)

    # ---- actions
    def assign(self, card, target):
        rules = svc.load_rules()
        if target:
            rules[card["key"]] = target
            sink = PREFIX + target.capitalize()
        else:
            rules.pop(card["key"], None)
            r = safe_pactl("get-default-sink")
            sink = r.stdout.strip() if r and r.returncode == 0 else ""
        save_rules(rules)
        for idx in card.get("indices") or []:
            if sink:
                safe_pactl("move-sink-input", str(idx), sink)
        self.refresh()

    def reset_dac(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            rc = svc.reset_dac()
        self.reset_btn.setEnabled(False)
        self.reset_btn.setText("Resetting…" if rc == 0 else "Reset failed")
        self.reset_btn.setToolTip(out.getvalue().strip())
        QTimer.singleShot(8000, self._reset_done)

    def _reset_done(self):
        self.reset_btn.setEnabled(True)
        self.reset_btn.setText("Reset DAC")
        self.refresh()

    def edit_eq(self, output, accent):
        dlg = EqDialog(self, output, accent)
        dlg.exec()
        self.refresh()

    def start_service(self):
        import subprocess
        subprocess.Popen(["systemctl", "--user", "enable", "--now", "nova-chatmix.service"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.banner_text.setText("Starting… (the GameDAC needs a few seconds)")
        QTimer.singleShot(7000, self.refresh)

    # ---- render
    def refresh(self):
        if self.drag_active:  # don't rebuild cards out from under a drag
            self.schedule_refresh()
            return
        if time.monotonic() - self.index_time > 60:  # pick up newly installed apps
            self.index = DesktopIndex()
            self.index_time = time.monotonic()
        apps = scan_apps(self.index)
        rules = svc.load_rules()
        if migrate_rules(rules, apps):
            save_rules(rules)
        snap = audio_snapshot()
        self.present = snap["present"]
        eq_state = snap.get("eq_state", {})
        for key, spec in current_eq().items():
            if key in self.columns:
                self.columns[key].set_eq(spec, eq_state.get(key, {}).get("active") if self.present else None)
        for key, col in self.columns.items():
            if key and key not in current_eq():
                col.set_eq(None, None)
        cards = build_cards(apps, snap, rules, self.index)

        if snap["errors"]:
            self.banner.setVisible(True)
            self.banner_btn.setVisible(False)
            self.banner_text.setText("Couldn't read your audio setup: " + "; ".join(snap["errors"]))
        elif not self.present:
            self.banner.setVisible(True)
            self.banner_btn.setVisible(True)
            self.banner_text.setText("The ChatMix service isn't running (or the GameDAC isn't "
                                     "plugged in). You can still assign apps now.")
        else:
            self.banner.setVisible(False)

        shown = {k for k in LABELS if k != "media" or "media" in self.present}
        for key, col in self.columns.items():
            col.setVisible(key is None or key in shown)
            col.set_level(snap["levels"].get(key))
            col.set_cards([AppCard(self, c) for c in cards
                           if c["column"] == key or (key is None and c["column"] not in shown)])
        playing = sum(1 for c in cards if c["playing"])
        self.status.setText(f"{sum(1 for c in cards if c['open'])} apps open  ·  {playing} playing"
                            "   —   drag an app into Chat, Media or Game; drag it back to Apps "
                            "to unassign")
        self.setWindowTitle(f"ChatMix · {playing} playing")

    def apply_style(self):
        pal = self.palette()
        dark = pal.window().color().lightness() < 128
        win = pal.window().color()
        col_bg = win.lighter(112) if dark else win.darker(104)
        card_bg = win.lighter(135) if dark else QColor("#ffffff")
        card_hover = win.lighter(155) if dark else QColor("#f3f5f8")
        border = QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 28)
        muted = pal.text().color()
        muted.setAlpha(150)

        def c(q):
            return f"rgba({q.red()},{q.green()},{q.blue()},{q.alpha()})"

        self.setStyleSheet(f"""
            #column {{ background: {c(col_bg)}; border-radius: 10px; }}
            #column[hover="true"] {{ background: {c(card_hover)}; }}
            #colTitle {{ font-weight: 600; font-size: 11pt; }}
            #colLevel, #caption {{ color: {c(muted)}; }}
            #caption {{ font-size: 8.5pt; }}
            #card {{ background: {c(card_bg)}; border: 1px solid {c(border)}; border-radius: 9px; }}
            #card:hover {{ background: {c(card_hover)}; }}
            #card[idle="true"] #cardName {{ color: {c(muted)}; }}
            #cardName {{ font-weight: 600; }}
            #cardSub, #hint, #empty {{ color: {c(muted)}; }}
            #empty {{ padding: 24px 6px; border: 1px dashed {c(border)}; border-radius: 9px; }}
            #liveDot {{ color: #3DCB6C; font-size: 8pt; }}
            #eqButton {{ text-align: left; padding: 4px 8px; border-radius: 6px;
                         border: 1px solid {c(border)}; background: {c(card_bg)}; }}
            #eqButton:hover {{ background: {c(card_hover)}; }}
            #eqButton[on="true"] {{ font-weight: 600; }}
            #banner {{ background: rgba(232,163,61,40); border: 1px solid rgba(232,163,61,120);
                       border-radius: 8px; }}
        """)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ChatMix")
    app.setDesktopFileName("nova-chatmix")
    w = Window()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
