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
    for m in safe_list("modules", errors):
        for k in LABELS:
            if f"source={PREFIX}{k.capitalize()}.monitor" in (m.get("argument") or ""):
                loop_modules[str(m.get("index"))] = k
    clients = {str(c.get("index")): c for c in safe_list("clients", errors)}

    levels, streams = {}, []
    for si in safe_list("sink-inputs", errors):
        owner = str(si.get("owner_module"))
        if owner in loop_modules:
            levels[loop_modules[owner]] = volume_percent(si)
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
    if state:  # the service publishes the dial position; more reliable than guessing
        try:
            levels.update({k: v for k, v in json.loads(state).get("levels", {}).items()
                           if v is not None})
        except ValueError:
            pass
    return {"present": present, "levels": levels, "streams": streams, "errors": errors}


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


def save_rules(rules):
    os.makedirs(os.path.dirname(svc.RULES_PATH), exist_ok=True)
    tmp = svc.RULES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"apps": dict(sorted(rules.items()))}, f, indent=2)
    os.replace(tmp, svc.RULES_PATH)  # atomic, so the service never reads half a file


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

from PySide6.QtCore import (QMimeData, QProcess, QProcessEnvironment, QSize, Qt,  # noqa: E402
                            QTimer)
from PySide6.QtGui import QAction, QColor, QDrag, QIcon, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMenu,  # noqa: E402
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
        self.bar = LevelBar(accent)
        self.bar.setVisible(key in ("game", "chat"))
        outer.addWidget(self.bar)
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

        self.status = QLabel()
        self.status.setObjectName("hint")
        root.addWidget(self.status)

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
