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

  EQ     the Chat / Media / Game pages: each channel has its own EQ, chosen
         from the SteelSeries Sonar presets (downloaded as you browse them) or
         a Sonar preset file you import. Pin up to 9 favourites per channel.

  nova-chatmix-gui --debug   print what the window can see, then exit
"""

import glob
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

# Colours. Each channel keeps its own colour everywhere: its tab, its lane,
# its EQ curve and the preset curves you browse for it.
T = {
    "ink": "#151922",     # window
    "deep": "#0E1118",    # sunken panels: the EQ display, the preset drawer
    "raise": "#1E2331",   # hover, inputs
    "hover": "#262C3C",
    "line": "#2A3142",    # hairlines
    "text": "#E8EBF2",
    "muted": "#8D95A9",
    "faint": "#5C647A",
    "warn": "#FF8F70",
}
ACCENT = {"chat": "#3FE0C0", "media": "#C38BFF", "game": "#FFB547", None: "#8D95A9"}
COLUMNS = [  # key, label, accent  (key None = unassigned)
    (None, "Apps", ACCENT[None]),
    ("chat", "Chat", ACCENT["chat"]),
    ("media", "Media", ACCENT["media"]),
    ("game", "Game", ACCENT["game"]),
]
LABELS = {"chat": "Chat", "media": "Media", "game": "Game"}
DIAL_MODES = {  # how the dial treats Media (setting "media_dial")
    "auto": "Auto - on the dial while nobody's in Chat",
    "always": "On - always turned down with Chat",
    "never": "Off - always full volume",
}
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
    eq_state, dial_state = {}, {}
    if state:  # the service publishes the dial position; more reliable than guessing
        try:
            st = json.loads(state)
            levels.update({k: v for k, v in st.get("levels", {}).items() if v is not None})
            eq_state = st.get("eq", {}) if isinstance(st.get("eq"), dict) else {}
            dial_state = {"chat_busy": bool(st.get("chat_busy")), "media_on_dial": st.get("media_on_dial")}
        except ValueError:
            pass
    return {"present": present, "levels": levels, "streams": streams, "errors": errors,
            "eq_state": eq_state, "dial": dial_state}


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
    """spec = {"name", "source", "filters"}, or None to switch that output's EQ off.

    The last preset chosen is remembered (under "eq_last", which the service
    ignores) so switching the EQ back on brings it back. A flat preset is
    remembered too, but there's nothing for the service to run."""
    data = _read_settings()
    eq = dict(data["eq"]) if isinstance(data.get("eq"), dict) else {}
    last = dict(data["eq_last"]) if isinstance(data.get("eq_last"), dict) else {}
    if spec is None and output in eq and output not in last:
        last[output] = eq[output]
    if spec and spec.get("filters"):
        eq[output] = spec
    else:
        eq.pop(output, None)
    if spec:
        last[output] = spec
    data["eq"], data["eq_last"] = eq, last
    _write_settings(data)


def last_eq(output):
    last = _read_settings().get("eq_last")
    spec = last.get(output) if isinstance(last, dict) else None
    return spec if isinstance(spec, dict) else None


def shown_eq(output):
    """(spec, on): the preset to show for an output and whether its EQ is running."""
    on = current_eq().get(output)
    return (on, True) if isinstance(on, dict) else (last_eq(output), False)


def media_dial_setting():
    mode = _read_settings().get("media_dial", "auto")
    return mode if mode in DIAL_MODES else "auto"


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
    title = base.replace("_ ", ": ").replace("_", " ").strip()
    if title.startswith("Music - "):
        title = "Music: " + title[8:]
    return title, kind


# ----------------------------------------------------- preset catalogue ---
# Sonar files every preset under Game, Chat or Mic. Sorted properly, they're
# games, pro players' presets, music, movies, voice and a few general ones.

CATEGORIES = {
    "yours": "Your games", "games": "Games", "pro": "Pro players", "general": "General",
    "music": "Music", "movie": "Movies and shows", "voice": "Voice", "mine": "My presets",
    "fav": "Favourites",
}
CATEGORY_ORDER = {
    "game": ["yours", "games", "pro", "general", "music", "movie", "voice", "mine", "fav"],
    "chat": ["voice", "general", "music", "movie", "yours", "games", "pro", "mine", "fav"],
    "media": ["music", "movie", "general", "voice", "yours", "games", "pro", "mine", "fav"],
}
GENERAL = {"flat", "custom", "podcast", "fps footsteps"}
PRO = re.compile(r"(?i)pro preset|by (atl )?faze|by scroll|by sky\b")
DEFAULT_FAVOURITES = {
    "game": ["FPS Footsteps [Game].json", "Flat [Game].json"],
    "chat": ["Clarity [Chat].json", "Balanced [Chat].json", "Podcast [Chat].json", "Flat [Chat].json"],
    "media": ["Music - Deep Bass [Game].json", "Music - Bright [Game].json", "Movie_ Immersion [Game].json",
              "Movie_ Clear Dialogs [Game].json", "Podcast [Game].json"],
}
MAX_FAVOURITES = 9
# Steam names that don't match the preset's name (keys as title_key() makes them)
TITLE_ALIASES = {"counterstrike2": "cs2", "counterstrikeglobaloffensive": "csgo", "grandtheftautov": "gtav",
                 "grandtheftautovenhanced": "gtav", "playerunknownsbattlegrounds": "pubgbattlegrounds"}


def preset_title(source):
    if source.startswith(("mine:", "file:")):
        return preset_label(os.path.basename(source.split(":", 1)[1]))[0]
    return preset_label(source)[0]


def preset_category(source):
    if source.startswith(("mine:", "file:")):
        return "mine"
    title, kind = preset_label(source)
    t = title.lower()
    if kind == "Mic":
        return "mic"
    if kind == "Chat":
        return "voice"
    if t.startswith("music"):
        return "music"
    if t.startswith("movie"):
        return "movie"
    if t in GENERAL:
        return "general"
    if PRO.search(t):
        return "pro"
    return "games"


def title_key(name):
    """'THE FINALS', 'The finals' -> 'finals'; 'CS2 by FaZe' -> 'cs2'."""
    s = re.sub(r"(?i)\s+(by\s.+|pro preset)$", "", name.strip()).lower().replace("&", "and")
    s = re.sub(r"[\u2122\u00ae\u00a9]", "", s)
    s = re.sub(r"^(the|tom clancy'?s)\s+", "", s)
    key = re.sub(r"[^a-z0-9]+", "", s)
    return TITLE_ALIASES.get(key, key)


_installed = [None, 0.0]


def installed_games():
    """Names of the Steam games installed here."""
    if _installed[0] is not None and time.monotonic() - _installed[1] < 60:
        return _installed[0]
    names = set()
    for lib in steam_libraries():
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            m = re.search(r'"name"\s+"([^"]*)"', svc._read(acf) or "")
            if m and not re.search(r"(?i)proton|steamworks|steam ?linux ?runtime", m.group(1)):
                names.add(m.group(1))
    _installed[:] = [names, time.monotonic()]
    return names


def my_presets_dir():
    return os.path.join(os.path.dirname(svc.RULES_PATH), "presets")


def my_presets():
    try:
        return sorted(f for f in os.listdir(my_presets_dir()) if f.endswith(".json"))
    except OSError:
        return []


def match_games(entries, titles):
    """Sources of the game presets that belong to any of these app / game names."""
    by_key = {}
    for e in entries:
        if e["cat"] in ("games", "pro"):
            by_key.setdefault(title_key(e["title"]), []).append(e["source"])
    found = []
    for t in titles:
        k = title_key(t)
        hit = by_key.get(k)
        if not hit:  # 'The Witcher 3: Wild Hunt' -> 'The Witcher 3'
            stems = [s for s in by_key if len(s) >= 5 and k.startswith(s)]
            hit = by_key[max(stems, key=len)] if stems else []
        found += [s for s in hit if s not in found]
    return found


def build_catalogue(names, your_titles=()):
    """Every usable preset: {"source", "title", "cat", "yours"}, sorted by name."""
    out = []
    for n in names:
        cat = preset_category(n)
        if cat != "mic":  # microphone presets don't apply to an output
            out.append({"source": n, "title": preset_title(n), "cat": cat, "yours": False})
    for fn in my_presets():
        out.append({"source": "mine:" + fn, "title": preset_title("mine:" + fn), "cat": "mine", "yours": False})
    yours = set(match_games(out, your_titles))
    for e in out:
        e["yours"] = e["source"] in yours
    out.sort(key=lambda e: e["title"].lower())
    return out


def filter_catalogue(entries, category, query="", favs=()):
    words = query.lower().split()
    if words:
        return [e for e in entries
                if all(w in (e["title"] + " " + CATEGORIES[e["cat"]]).lower() for w in words)]
    if category == "fav":
        by = {e["source"]: e for e in entries}
        return [by[s] for s in favs if s in by]
    if category == "yours":
        return [e for e in entries if e["yours"]]
    return [e for e in entries if e["cat"] == category]


SHAPE_FREQS = svc.log_freqs(160)


def shape_words(curve, freqs):
    """'More bass, sharp 1-5 kHz peaks': what an EQ curve does, in a few words.

    Judged against the curve's own average level, since most Sonar presets lift
    everything a little (the anti-clip preamp takes that back anyway)."""
    def band(lo, hi):
        vals = [d for f, d in zip(freqs, curve) if lo <= f < hi]
        return sum(vals) / len(vals) if vals else 0.0
    bands = [((20, 150), "More bass", "Less bass"), ((150, 500), "Warmer", "Less muddy"),
             ((500, 2000), "Forward mids", "Scooped mids"), ((2000, 6000), "More presence", "Softer"),
             ((6000, 20001), "More air", "Darker")]
    levels = [band(*b[0]) for b in bands]
    mean = sum(levels) / len(levels)
    names = [b[1:] for b in bands]
    found = sorted(((abs(v - mean), i, names[i][0] if v > mean else names[i][1])
                    for i, v in enumerate(levels) if abs(v - mean) >= 1.5), reverse=True)
    # narrow boosts between 700 Hz and 6 kHz: the footstep / reload detail game presets add
    peaks = 0
    for k in range(1, len(curve) - 1):
        if 700 <= freqs[k] <= 6000 and curve[k] > curve[k - 1] and curve[k] >= curve[k + 1]:
            lo = k
            while lo > 0 and curve[lo - 1] < curve[lo] and freqs[k] / freqs[lo - 1] < 1.6:
                lo -= 1
            hi = k
            while hi < len(curve) - 1 and curve[hi + 1] < curve[hi] and freqs[hi + 1] / freqs[k] < 1.6:
                hi += 1
            if curve[k] - max(curve[lo], curve[hi]) >= 2.5:
                peaks += 1
    words = [w for _, _, w in found[:1]]
    if peaks >= 2:
        words.append("Sharp 1-5 kHz peaks")
    words += [w for _, _, w in found[1:2]]
    words = words[:2]
    if not words:
        return "Even lift" if mean >= 2 else "Even cut" if mean <= -2 else "Close to flat"
    return ", ".join([words[0]] + [w[0].lower() + w[1:] for w in words[1:]])


def short_title(entry):
    """In the Music and Movies lists the prefix just repeats the category."""
    t = entry["title"]
    for prefix in ("Music: ", "Movie: "):
        if t.startswith(prefix) and entry["cat"] in ("music", "movie"):
            return t[len(prefix):]
    return t


def favourites(output):
    fav = _read_settings().get("favourites")
    if isinstance(fav, dict) and isinstance(fav.get(output), list):
        return [s for s in fav[output] if isinstance(s, str)][:MAX_FAVOURITES]
    return list(DEFAULT_FAVOURITES.get(output, []))


def set_favourites(output, sources):
    data = _read_settings()
    fav = dict(data["favourites"]) if isinstance(data.get("favourites"), dict) else {}
    fav[output] = list(dict.fromkeys(sources))[:MAX_FAVOURITES]
    data["favourites"] = fav
    _write_settings(data)


def _sonar_dict(obj):
    if isinstance(obj, dict) and "parametricEQ" not in obj and isinstance(obj.get("data"), (dict, str)):
        obj = json.loads(obj["data"]) if isinstance(obj["data"], str) else obj["data"]
    if not isinstance(obj, dict) or "parametricEQ" not in obj:
        raise ValueError("not a Sonar preset (no parametric EQ in it)")
    return obj


def import_preset(path):
    """Copy a Sonar preset file into My presets. Returns (source, filters)."""
    with open(path, "rb") as f:
        raw = f.read()
    filters = svc.eq_from_sonar(_sonar_dict(json.loads(raw)))
    name = os.path.basename(path)
    name = name if name.endswith(".json") else name + ".json"
    os.makedirs(my_presets_dir(), exist_ok=True)
    dest = os.path.join(my_presets_dir(), name)
    with open(dest + ".tmp", "wb") as f:
        f.write(raw)
    os.replace(dest + ".tmp", dest)
    return "mine:" + name, filters


def load_preset(source):
    """A preset's filters, whatever kind of source it is."""
    if source.startswith("mine:"):
        with open(os.path.join(my_presets_dir(), os.path.basename(source[5:]))) as f:
            return svc.eq_from_sonar(_sonar_dict(json.load(f)))
    if source.startswith("file:"):  # loaded from a file by an older version: kept in the settings
        for spec in list(current_eq().values()) + [last_eq(k) for k in LABELS]:
            if spec and spec.get("source") == source:
                return svc.clean_filters(spec.get("filters"))
        raise ValueError("that preset file isn't saved anywhere any more")
    return sonar_preset(source)


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

from PySide6.QtCore import (QEasingCurve, QMimeData, QObject, QPoint, QPointF, QProcess,  # noqa: E402
                            QProcessEnvironment, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QAction, QColor, QDrag, QFont, QFontMetrics, QIcon,  # noqa: E402
                           QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout,  # noqa: E402
                               QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

AVATAR_COLOURS = ["#5B8DEF", "#E0697B", "#3FA796", "#C98A2E", "#8E6FD8", "#4AA3C7", "#D46FB0"]
HERO_FREQS = svc.log_freqs(240)
MINI_FREQS = svc.log_freqs(56)
SPAN_DB = 15.0


def qc(hex_colour, alpha=255):
    c = QColor(hex_colour)
    c.setAlpha(alpha)
    return c


def rgba(hex_colour, alpha):
    c = QColor(hex_colour)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def dark_palette():
    pal = QPalette()
    for role, c in [(QPalette.Window, T["ink"]), (QPalette.Base, T["raise"]), (QPalette.AlternateBase, T["hover"]),
                    (QPalette.WindowText, T["text"]), (QPalette.Text, T["text"]), (QPalette.Button, T["raise"]),
                    (QPalette.ButtonText, T["text"]), (QPalette.Highlight, "#46557A"),
                    (QPalette.HighlightedText, "#FFFFFF"), (QPalette.ToolTipBase, T["raise"]),
                    (QPalette.ToolTipText, T["text"]), (QPalette.PlaceholderText, T["faint"]),
                    (QPalette.Link, ACCENT["chat"]), (QPalette.Mid, T["line"]), (QPalette.Dark, T["deep"])]:
        pal.setColor(role, QColor(c))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(T["faint"]))
    return pal


def stylesheet():
    return f"""
    QWidget {{ color: {T['text']}; }}
    #brand {{ font-size: 13pt; font-weight: 700; }}
    QPushButton#tab {{ background: transparent; border: none; border-bottom: 2px solid transparent;
        color: {T['muted']}; padding: 12px 2px 9px 2px; margin: 0 9px; font-size: 10.5pt; font-weight: 600; }}
    QPushButton#tab:hover {{ color: {T['text']}; }}
    QToolButton#more {{ background: transparent; border: none; color: {T['muted']}; font-size: 14pt;
        padding: 0 8px 4px 8px; border-radius: 8px; }}
    QToolButton#more:hover {{ background: {T['raise']}; color: {T['text']}; }}
    QToolButton#more::menu-indicator {{ image: none; width: 0; }}
    #caption, #detail {{ color: {T['muted']}; }}
    #caption {{ font-size: 8.5pt; }}
    #hint {{ color: {T['faint']}; font-size: 8.5pt; }}
    #laneTitle {{ font-size: 11.5pt; font-weight: 700; }}
    #laneLevel {{ color: {T['muted']}; }}
    QFrame#row {{ border-radius: 9px; background: transparent; }}
    QFrame#row:hover {{ background: {T['raise']}; }}
    #rowName {{ font-weight: 600; }}
    #rowSub {{ color: {T['muted']}; font-size: 8.5pt; }}
    QFrame#row[idle="true"] #rowName {{ color: {T['faint']}; }}
    #empty {{ color: {T['faint']}; padding: 26px 6px; }}
    QPushButton#link {{ background: transparent; border: none; color: {T['muted']}; text-align: left;
        padding: 3px 0; }}
    QPushButton#link:hover {{ color: {T['text']}; }}
    QPushButton#link::menu-indicator {{ image: none; width: 0; }}
    QPushButton#ghost {{ background: {T['raise']}; border: 1px solid {T['line']}; border-radius: 15px;
        padding: 5px 14px; }}
    QPushButton#ghost:hover {{ background: {T['hover']}; }}
    QPushButton#ghost:disabled {{ color: {T['faint']}; }}
    #heroName {{ font-size: 21pt; font-weight: 700; }}
    QLineEdit {{ background: {T['raise']}; border: 1px solid {T['line']}; border-radius: 15px;
        padding: 5px 12px; selection-background-color: #46557A; }}
    QLineEdit:focus {{ border-color: #4C5A7E; }}
    QFrame#drawer {{ background: {T['deep']}; border: 1px solid {T['line']}; border-bottom: none;
        border-top-left-radius: 20px; border-top-right-radius: 20px; }}
    #drawerTitle {{ font-size: 12.5pt; font-weight: 700; }}
    QFrame#banner {{ background: {rgba(T['warn'], 26)}; border: 1px solid {rgba(T['warn'], 90)};
        border-radius: 10px; }}
    QFrame#suggest {{ background: {rgba(ACCENT['game'], 22)}; border-radius: 12px; }}
    QFrame#sep {{ background: {T['line']}; }}
    QMenu {{ background: {T['raise']}; border: 1px solid {T['line']}; padding: 6px; }}
    QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {T['hover']}; }}
    QMenu::item:disabled {{ color: {T['faint']}; }}
    QMenu::separator {{ height: 1px; background: {T['line']}; margin: 5px 8px; }}
    QToolTip {{ background: {T['raise']}; color: {T['text']}; border: 1px solid {T['line']}; padding: 6px; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {T['line']}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {T['faint']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    """


def eq_curve(filters, freqs):
    """The shape of an EQ in dB (like Sonar draws it: before the anti-clip preamp)."""
    return svc.eq_response_db(filters, freqs) if filters else [0.0] * len(freqs)


def curve_path(values, freqs, rect, span=SPAN_DB):
    import math
    path = QPainterPath()
    lo = math.log10(freqs[0])
    width = math.log10(freqs[-1]) - lo
    for i, (hz, db) in enumerate(zip(freqs, values)):
        x = rect.left() + rect.width() * (math.log10(hz) - lo) / width
        y = rect.center().y() - max(-span - 2, min(span + 2, db)) / span * rect.height() / 2
        path.lineTo(x, y) if i else path.moveTo(x, y)
    return path


def paint_curve(p, rect, values, freqs, colour, glow=True, span=SPAN_DB, fill=70):
    """A curve with a soft glow and a fade towards 0 dB."""
    path = curve_path(values, freqs, rect, span)
    zero = rect.center().y()
    area = QPainterPath(path)
    area.lineTo(rect.right(), zero)
    area.lineTo(rect.left(), zero)
    area.closeSubpath()
    grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
    grad.setColorAt(0.0, qc(colour, fill))
    grad.setColorAt(0.5, qc(colour, 0))
    grad.setColorAt(1.0, qc(colour, fill // 2))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawPath(area)
    p.setBrush(Qt.NoBrush)
    if glow:
        for width, alpha in ((11, 18), (6, 38)):
            p.setPen(QPen(qc(colour, alpha), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
    p.setPen(QPen(QColor(colour), 2.4 if glow else 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)


def app_pixmap(card, size=28):
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


# ---------------------------------------------------------- presets ---

class PresetStore(QObject):
    """Downloads presets in the background and remembers their curves."""
    manifest = Signal(object)          # list of names ([] = couldn't get it)
    ready = Signal(str)                # a preset's filters arrived (or failed)
    _loaded = Signal(str, object, str)
    _listed = Signal(int, object)

    def __init__(self):
        super().__init__()
        from concurrent.futures import ThreadPoolExecutor
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.filters, self.errors, self.pending, self.minis, self.shapes = {}, {}, set(), {}, {}
        self.generation = 0
        self._loaded.connect(self._store)
        self._listed.connect(self._got_list)

    def load_manifest(self, refresh=False):
        self.generation += 1
        gen = self.generation

        def job():
            try:
                names = sonar_manifest(refresh)
            except Exception:
                names = []
            self._listed.emit(gen, names)
        self.pool.submit(job)

    def _got_list(self, gen, names):
        if gen == self.generation:  # an older, slower request finishing late doesn't win
            self.manifest.emit(names)

    def want(self, sources):
        for s in sources:
            if s and s not in self.filters and s not in self.pending and s not in self.errors:
                self.pending.add(s)
                self.pool.submit(self._fetch, s)

    def retry_failed(self):
        self.errors.clear()

    def put(self, source, filters):
        self.filters[source] = filters
        self.minis.pop(source, None)
        self.shapes.pop(source, None)

    def _fetch(self, source):
        try:
            self._loaded.emit(source, load_preset(source), "")
        except Exception as e:  # network, missing file, not a preset
            self._loaded.emit(source, None, str(e) or e.__class__.__name__)

    def _store(self, source, filters, err):
        self.pending.discard(source)
        if err:
            self.errors[source] = err
        else:
            self.filters[source] = filters
        self.ready.emit(source)

    def mini(self, source):
        """Curve for a thumbnail, or None while it's still downloading."""
        if source in self.minis:
            return self.minis[source]
        if source not in self.filters:
            self.want([source])
            return None
        self.minis[source] = eq_curve(self.filters[source], MINI_FREQS)
        return self.minis[source]

    def shape(self, source):
        if source not in self.filters:
            self.want([source])
            return None
        if source not in self.shapes:
            self.shapes[source] = shape_words(eq_curve(self.filters[source], SHAPE_FREQS), SHAPE_FREQS)
        return self.shapes[source]

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


def paint_thumb(p, rect, curve, colour, active=False):
    p.setPen(Qt.NoPen)
    p.setBrush(qc(T["raise"]) if not active else qc(colour, 40))
    p.drawRoundedRect(rect, 7, 7)
    inner = rect.adjusted(5, 6, -5, -6)
    if curve is None:
        p.setPen(QPen(qc(T["faint"], 120), 1, Qt.DotLine))
        p.drawLine(QPointF(inner.left(), inner.center().y()), QPointF(inner.right(), inner.center().y()))
        return
    p.save()
    p.setClipRect(rect)
    paint_curve(p, inner, curve, MINI_FREQS, colour, glow=False, span=11, fill=55)
    p.restore()


# ----------------------------------------------------------- mixer ---

class PlayingGlyph(QWidget):
    """Three little level bars: this app is making sound right now."""

    def __init__(self, colour):
        super().__init__()
        self.colour = colour
        self.setFixedSize(14, 14)
        self.setToolTip("Making sound right now")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.colour))
        for i, h in enumerate((7, 12, 9)):
            p.drawRoundedRect(QRectF(1 + i * 4.5, 13 - h, 3, h), 1.5, 1.5)


class AppCard(QFrame):
    def __init__(self, win, card, colour):
        super().__init__()
        self.win, self.card = win, card
        self.setObjectName("row")
        self.setAttribute(Qt.WA_Hover)
        self.setProperty("idle", not card["open"])
        self.setCursor(Qt.OpenHandCursor)
        self._press = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 10, 6)
        lay.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(app_pixmap(card))
        icon.setFixedSize(28, 28)
        icon.setEnabled(card["open"])
        lay.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(card["name"])
        name.setObjectName("rowName")
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sub = QLabel(card["titles"][0] if card["titles"] else
                     "Playing" if card["playing"] else "Open" if card["open"] else "Not running")
        sub.setObjectName("rowSub")
        sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text.addWidget(name)
        text.addWidget(sub)
        lay.addLayout(text, 1)
        if card["playing"]:
            lay.addWidget(PlayingGlyph(colour))
        self.setToolTip(f"{card['name']}\nDrag it into Chat, Media or Game, or right-click.")

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
        mime.setData(MIME, json.dumps({"key": self.card["key"], "indices": self.card["indices"]}).encode())
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


class DialMeter(QWidget):
    """Where the GameDAC's dial sits: Chat at one end, Game at the other."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(104)
        self.game = self.chat = 100
        self.connected = False
        self.media_follows = False

    def set_state(self, connected, game, chat, media_follows):
        self.connected, self.media_follows = connected, media_follows
        self.game = 100 if game is None else game
        self.chat = 100 if chat is None else chat
        self.update()

    def position(self):
        """-1 = all the way to Chat (Game turned down) ... +1 = all the way to Game."""
        if self.chat < self.game:
            return (100 - self.chat) / 100
        if self.game < self.chat:
            return -(100 - self.game) / 100
        return 0.0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        side = 150
        dim = 255 if self.connected else 90
        big = QFont(self.font())
        big.setPointSizeF(26)
        big.setWeight(QFont.Light)
        small = QFont(self.font())
        small.setPointSizeF(9.5)
        small.setWeight(QFont.DemiBold)
        tiny = QFont(self.font())
        tiny.setPointSizeF(8.5)

        def end(x, align, label, value, colour, note):
            p.setFont(small)
            p.setPen(qc(colour, dim))
            p.drawText(QRectF(x, 10, side, 18), align | Qt.AlignVCenter, label)
            p.setFont(big)
            p.setPen(qc(T["text"], dim))
            p.drawText(QRectF(x, 28, side, 44), align | Qt.AlignVCenter, f"{value}%" if self.connected else "–")
            if note:
                p.setFont(tiny)
                p.setPen(qc(T["muted"], dim))
                p.drawText(QRectF(x, 72, side, 18), align | Qt.AlignVCenter, note)

        end(0, Qt.AlignLeft, "Chat", self.chat, ACCENT["chat"], "Media follows" if self.media_follows else "")
        end(w - side, Qt.AlignRight, "Game", self.game, ACCENT["game"], "")
        x0, x1, y = side + 24, w - side - 24, 50
        if x1 - x0 < 60:
            return
        grad = QLinearGradient(x0, 0, x1, 0)
        grad.setColorAt(0, qc(ACCENT["chat"], 150 if self.connected else 50))
        grad.setColorAt(1, qc(ACCENT["game"], 150 if self.connected else 50))
        p.setPen(QPen(grad, 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))
        p.setPen(QPen(qc(T["line"]), 1))
        for i in range(11):
            x = x0 + (x1 - x0) * i / 10
            tick = 7 if i in (0, 5, 10) else 4
            p.drawLine(QPointF(x, y + 9), QPointF(x, y + 9 + tick))
        if not self.connected:
            p.setFont(tiny)
            p.setPen(qc(T["muted"]))
            p.drawText(QRectF(x0, y + 18, x1 - x0, 20), Qt.AlignCenter, "The dial isn't connected")
            return
        pos = self.position()
        kx = (x0 + x1) / 2 + pos * (x1 - x0) / 2
        toward = ACCENT["game"] if pos > 0 else ACCENT["chat"] if pos < 0 else T["text"]
        for r, a in ((18, 25), (13, 45)):
            p.setPen(Qt.NoPen)
            p.setBrush(qc(toward, a))
            p.drawEllipse(QPointF(kx, y), r, r)
        p.setBrush(QColor(T["text"]))
        p.drawEllipse(QPointF(kx, y), 8, 8)


class Column(QFrame):
    """One lane on the Mixer page: Apps, Chat, Media or Game."""

    def __init__(self, win, key, label, accent):
        super().__init__()
        self.win, self.key, self.accent = win, key, accent
        self.setObjectName("lane")
        self.setAcceptDrops(True)
        self.setProperty("hover", False)
        self.setStyleSheet(f'QFrame#lane[hover="true"] {{ background: {rgba(accent, 24)}; border-radius: 14px; }}')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(2)
        head = QHBoxLayout()
        title = QLabel(label)
        title.setObjectName("laneTitle")
        title.setStyleSheet(f"color: {accent if key else T['text']};")
        self.level = QLabel("")
        self.level.setObjectName("laneLevel")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.level)
        outer.addLayout(head)
        self.caption = QLabel({None: "Not assigned, so they play through Game",
                               "chat": "Turned down as you dial toward Game",
                               "media": "Follows the dial while Chat is quiet",
                               "game": "Turned down as you dial toward Chat"}[key])
        self.caption.setObjectName("caption")
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        # two lines' room in every lane, so the EQ links line up across lanes
        self.caption.setFixedHeight(QFontMetrics(self.caption.font()).lineSpacing() * 2 + 4)
        outer.addWidget(self.caption)
        self.dial_mode = None
        self.eq_btn = self.dial_btn = None
        if key is not None:
            row = QHBoxLayout()
            row.setSpacing(14)
            self.eq_btn = QPushButton("EQ off")
            self.eq_btn.setObjectName("link")
            self.eq_btn.setCursor(Qt.PointingHandCursor)
            self.eq_btn.setToolTip(f"Open the {label} page to choose its EQ")
            self.eq_btn.clicked.connect(lambda: self.win.show_page(self.key))
            row.addWidget(self.eq_btn)
            if key == "media":
                self.dial_btn = QPushButton("Dial: Auto")
                self.dial_btn.setObjectName("link")
                self.dial_btn.setCursor(Qt.PointingHandCursor)
                menu = QMenu(self.dial_btn)
                for mode, text in DIAL_MODES.items():
                    act = QAction(text, menu)
                    act.setCheckable(True)
                    act.setData(mode)
                    act.triggered.connect(lambda _=False, m=mode: self.win.set_media_dial(m))
                    menu.addAction(act)
                self.dial_btn.setMenu(menu)
                row.addWidget(self.dial_btn)
            row.addStretch(1)
            outer.addLayout(row)
        outer.addSpacing(6)

        self.list = QVBoxLayout()
        self.list.setSpacing(2)
        self.list.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(self.list)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

    def set_eq(self, spec, on, active):
        if self.eq_btn is None:
            return
        if spec and on:
            warn = "" if active is not False else "  (not running)"
            self.eq_btn.setText(f"EQ: {spec.get('name', 'Custom')}{warn}")
            self.eq_btn.setStyleSheet(f"color: {self.accent if active is not False else T['warn']};")
        else:
            self.eq_btn.setText("EQ off")
            self.eq_btn.setStyleSheet("")

    def set_dial(self, mode, on_dial, chat_busy):
        """Media only: how the dial treats it, and what it's doing right now."""
        if self.dial_btn is None:
            return
        self.dial_mode = mode
        self.dial_btn.setText({"auto": "Dial: Auto", "always": "Dial: On", "never": "Dial: Off"}[mode])
        for act in self.dial_btn.menu().actions():
            act.setChecked(act.data() == mode)
        if mode == "never":
            text = "The dial never touches this"
        elif mode == "always":
            text = "Turned down with Chat as you dial toward Game"
        elif chat_busy:
            text = "Chat's in use, so the dial is Game vs Chat"
        else:
            text = "Nobody in Chat, so the dial is Game vs Media"
        self.caption.setText(text)
        self.on_dial = on_dial is not False
        self.dial_btn.setToolTip("How the GameDAC's dial treats Media:\n" + "\n".join(DIAL_MODES.values()))

    def set_level(self, pct):
        dial = self.key in ("game", "chat") or (self.key == "media" and self.dial_mode != "never")
        self.level.setText(f"{pct}%" if dial and pct is not None else "")

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


class MixerPage(QWidget):
    def __init__(self, win):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 10, 22, 12)
        lay.setSpacing(10)
        self.meter = DialMeter()
        lay.addWidget(self.meter)
        lanes = QHBoxLayout()
        lanes.setSpacing(6)
        self.seps = []
        for i, (key, label, accent) in enumerate(COLUMNS):
            if i:
                sep = QFrame()
                sep.setObjectName("sep")
                sep.setFixedWidth(1)
                self.seps.append(sep)
                lanes.addWidget(sep)
            win.columns[key] = Column(win, key, label, accent)
            lanes.addWidget(win.columns[key], 1)
        lay.addLayout(lanes, 1)
        self.status = QLabel()
        self.status.setObjectName("hint")
        lay.addWidget(self.status)


# -------------------------------------------------------- channels ---

class EqToggle(QPushButton):
    def __init__(self, colour):
        super().__init__()
        self.colour = colour
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)
        self.setToolTip("Switch this channel's EQ on or off")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QRectF(2, 3, 42, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.colour) if on else QColor(T["line"]))
        p.drawRoundedRect(track, 10, 10)
        p.setBrush(QColor(T["ink"]) if on else QColor(T["muted"]))
        p.drawEllipse(QPointF(track.right() - 10 if on else track.left() + 10, track.center().y()), 7, 7)


class EqDisplay(QWidget):
    """The big curve: the channel's EQ, 20 Hz to 20 kHz."""

    def __init__(self, colour):
        super().__init__()
        self.colour = colour
        self.values = None  # None = EQ off
        self._from = self._to = [0.0] * len(HERO_FREQS)
        self._t = 1.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(220)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._step)
        self.setMinimumHeight(170)

    def set_curve(self, values):
        """values: dB per HERO_FREQS, or None for EQ off. Morphs from the old curve."""
        if values == self.values:
            return
        shown = self._current()
        self.values = values
        self._from, self._to = shown, (values or [0.0] * len(HERO_FREQS))
        self.anim.stop()
        self._t = 0.0
        self.anim.start()

    def _step(self, t):
        self._t = t
        self.update()

    def _current(self):
        t = self._t
        return [a + (b - a) * t for a, b in zip(self._from, self._to)]

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["deep"]))
        p.drawRoundedRect(box, 18, 18)
        r = box.adjusted(44, 18, -20, -30)
        small = QFont(self.font())
        small.setPointSizeF(8)
        p.setFont(small)

        def y_of(db):
            return r.center().y() - db / SPAN_DB * r.height() / 2

        def x_of(hz):
            return r.left() + r.width() * math.log10(hz / 20) / 3

        for db in (-12, -6, 0, 6, 12):
            p.setPen(QPen(qc(T["line"], 255 if db == 0 else 140), 1))
            p.drawLine(QPointF(r.left(), y_of(db)), QPointF(r.right(), y_of(db)))
            p.setPen(qc(T["faint"]))
            p.drawText(QRectF(box.left() + 6, y_of(db) - 8, 32, 16), Qt.AlignRight | Qt.AlignVCenter,
                       f"{db:+d}" if db else "0")
        for hz, name in ((20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1k"),
                         (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k")):
            x = x_of(hz)
            p.setPen(QPen(qc(T["line"], 110), 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(qc(T["faint"]))
            p.drawText(QRectF(x - 20, r.bottom() + 6, 40, 16), Qt.AlignCenter, name)
        p.save()
        p.setClipRect(r.adjusted(-4, -12, 4, 12))
        if self.values is None and self._t >= 1.0:
            p.setPen(QPen(qc(T["faint"]), 1.6, Qt.DashLine))
            p.drawLine(QPointF(r.left(), y_of(0)), QPointF(r.right(), y_of(0)))
            p.restore()
            big = QFont(self.font())
            big.setPointSizeF(11)
            p.setFont(big)
            p.setPen(qc(T["muted"]))
            p.drawText(QRectF(r.left(), y_of(0) - 40, r.width(), 24), Qt.AlignCenter, "EQ is off")
            return
        paint_curve(p, r, self._current(), HERO_FREQS, self.colour)
        p.restore()


class FavStrip(QWidget):
    """Up to 9 pinned presets for this channel; empty slots open the browser."""
    SLOTS = MAX_FAVOURITES

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.items = []
        self.hover = -1
        self.setMouseTracking(True)
        self.setFixedHeight(76)

    def set_items(self, sources):
        self.items = list(sources)
        self.update()

    def slot_rects(self):
        gap = 8
        w = (self.width() - gap * (self.SLOTS - 1)) / self.SLOTS
        return [QRectF(i * (w + gap), 0, w, self.height()) for i in range(self.SLOTS)]

    def slot_at(self, pos):
        return next((i for i, r in enumerate(self.slot_rects()) if r.contains(QPointF(pos))), -1)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        colour = self.page.colour
        current = self.page.current_source()
        small = QFont(self.font())
        small.setPointSizeF(8.5)
        fm = QFontMetrics(small)
        for i, r in enumerate(self.slot_rects()):
            r = r.adjusted(0.5, 0.5, -0.5, -0.5)
            if i >= len(self.items):
                p.setPen(QPen(qc(T["line"] if i != self.hover else T["faint"]), 1.2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r, 12, 12)
                p.setPen(qc(T["faint"] if i != self.hover else T["muted"]))
                f = QFont(self.font())
                f.setPointSizeF(15)
                p.setFont(f)
                p.drawText(r, Qt.AlignCenter, "+")
                continue
            src = self.items[i]
            active = src == current
            p.setPen(QPen(QColor(colour), 1.5) if active else Qt.NoPen)
            p.setBrush(qc(T["hover"]) if i == self.hover else qc(T["raise"]))
            p.drawRoundedRect(r, 12, 12)
            thumb = QRectF(r.left() + 8, r.top() + 8, r.width() - 16, r.height() - 34)
            curve = self.page.win.store.mini(src)
            p.save()
            p.setClipRect(thumb)
            if curve is not None:
                paint_curve(p, thumb, curve, MINI_FREQS, colour if active else T["muted"],
                            glow=False, span=12, fill=60 if active else 30)
            p.restore()
            p.setFont(small)
            p.setPen(QColor(colour) if active else qc(T["text"]))
            name = fm.elidedText(preset_title(src), Qt.ElideRight, int(r.width() - 14))
            p.drawText(QRectF(r.left() + 7, r.bottom() - 24, r.width() - 14, 18), Qt.AlignCenter, name)

    def mouseMoveEvent(self, e):
        i = self.slot_at(e.position().toPoint())
        if i != self.hover:
            self.hover = i
            self.setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)
            if 0 <= i < len(self.items):
                self.setToolTip(f"{preset_title(self.items[i])}\nRight-click to unpin")
            else:
                self.setToolTip("Pin a preset here" if i >= 0 else "")
            self.update()

    def leaveEvent(self, _):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        i = self.slot_at(e.position().toPoint())
        if e.button() != Qt.LeftButton or i < 0:
            return
        if i < len(self.items):
            self.page.win.choose_preset(self.page.key, self.items[i])
        else:
            self.page.open_drawer()

    def contextMenuEvent(self, e):
        i = self.slot_at(e.pos())
        if not 0 <= i < len(self.items):
            return
        menu = QMenu(self)
        act = QAction(f"Unpin {preset_title(self.items[i])}", menu)
        act.triggered.connect(lambda: self.page.win.toggle_favourite(self.page.key, self.items[i]))
        menu.addAction(act)
        menu.exec(e.globalPos())


class CategoryBar(QWidget):
    changed = Signal(str)

    def __init__(self, colour):
        super().__init__()
        self.colour = colour
        self.cats = []      # [(key, count)]
        self.current = None
        self.hover = -1
        self.setFixedHeight(34)
        self.setMouseTracking(True)

    def set_cats(self, cats, current):
        self.cats, self.current = cats, current
        self._fit()
        self.update()

    ROW = 34

    def _rects(self):
        """Tab rectangles, wrapping onto more rows when the window is narrow."""
        f = QFont(self.font())
        f.setPointSizeF(9.5)
        f.setWeight(QFont.DemiBold)
        fm, fs = QFontMetrics(f), QFontMetrics(self._small())
        x, y, out = 0.0, 0.0, []
        for key, n in self.cats:
            w = fm.horizontalAdvance(CATEGORIES[key]) + fs.horizontalAdvance(str(n)) + 8
            if x and x + w > self.width():
                x, y = 0.0, y + self.ROW
            out.append(QRectF(x, y, w, self.ROW))
            x += w + 24
        return out, f

    def _fit(self):
        rects, _ = self._rects()
        h = int(max((r.bottom() for r in rects), default=self.ROW))
        if h != self.height():
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _small(self):
        s = QFont(self.font())
        s.setPointSizeF(8)
        return s

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rects, f = self._rects()
        for i, ((key, n), r) in enumerate(zip(self.cats, rects)):
            on = key == self.current
            p.setFont(f)
            p.setPen(QColor(self.colour) if on else qc(T["text"] if i == self.hover else T["muted"]))
            label = CATEGORIES[key]
            lw = QFontMetrics(f).horizontalAdvance(label)
            p.drawText(QRectF(r.left(), r.top(), lw + 2, r.height() - 6), Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setFont(self._small())
            p.setPen(qc(T["faint"]))
            p.drawText(QRectF(r.left() + lw + 6, r.top(), 40, r.height() - 8), Qt.AlignLeft | Qt.AlignVCenter, str(n))
            if on:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(self.colour))
                p.drawRoundedRect(QRectF(r.left(), r.bottom() - 4, lw, 3), 1.5, 1.5)

    def mouseMoveEvent(self, e):
        rects, _ = self._rects()
        i = next((i for i, r in enumerate(rects) if r.contains(e.position())), -1)
        if i != self.hover:
            self.hover = i
            self.setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        rects, _ = self._rects()
        i = next((i for i, r in enumerate(rects) if r.contains(e.position())), -1)
        if i >= 0:
            self.changed.emit(self.cats[i][0])


class PresetGrid(QWidget):
    """Every preset in the chosen category, with its curve. Click to use it, star to pin it."""
    activated = Signal(str)
    starred = Signal(str)
    TILE_W, TILE_H, GAP = 210, 62, 8

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.entries = []
        self.message = ""
        self.searching = False
        self.hover = -1
        self.hover_star = False
        self.setMouseTracking(True)

    def set_entries(self, entries, message="", searching=False):
        self.entries, self.message, self.searching = entries, message, searching
        self.hover = -1
        self._relayout()
        self.update()

    def _cols(self):
        return max(1, int((self.width() + self.GAP) // (self.TILE_W + self.GAP)))

    def _relayout(self):
        rows = (len(self.entries) + self._cols() - 1) // self._cols()
        self.setMinimumHeight(max(120, rows * (self.TILE_H + self.GAP)))

    def resizeEvent(self, e):
        self._relayout()
        super().resizeEvent(e)

    def tile_rect(self, i):
        cols = self._cols()
        w = (self.width() - self.GAP * (cols - 1)) / cols
        return QRectF((i % cols) * (w + self.GAP), (i // cols) * (self.TILE_H + self.GAP), w, self.TILE_H)

    def star_rect(self, i):
        r = self.tile_rect(i)
        return QRectF(r.right() - 32, r.top() + (r.height() - 26) / 2, 26, 26)

    def index_at(self, pos):
        cols = self._cols()
        w = (self.width() - self.GAP * (cols - 1)) / cols
        col = int(pos.x() // (w + self.GAP))
        row = int(pos.y() // (self.TILE_H + self.GAP))
        i = row * cols + col
        if 0 <= col < cols and 0 <= i < len(self.entries) and self.tile_rect(i).contains(QPointF(pos)):
            return i
        return -1

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.entries:
            p.setPen(qc(T["muted"]))
            p.drawText(QRectF(self.rect()).adjusted(20, 10, -20, -10), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
                       self.message)
            return
        colour = self.page.colour
        current = self.page.current_source()
        favs = set(self.page.favs.items)
        name_f = QFont(self.font())
        name_f.setPointSizeF(9.5)
        name_f.setWeight(QFont.DemiBold)
        sub_f = QFont(self.font())
        sub_f.setPointSizeF(8)
        star_f = QFont(self.font())
        star_f.setPointSizeF(12)
        fm = QFontMetrics(name_f)
        exposed = QRectF(e.rect())
        for i, ent in enumerate(self.entries):
            r = self.tile_rect(i)
            if not r.intersects(exposed):
                continue
            src = ent["source"]
            active = src == current
            if i == self.hover or active:
                p.setPen(Qt.NoPen)
                p.setBrush(qc(colour, 26) if active else qc(T["raise"]))
                p.drawRoundedRect(r, 12, 12)
            thumb = QRectF(r.left() + 8, r.top() + 9, 66, r.height() - 18)
            paint_thumb(p, thumb, self.page.win.store.mini(src), colour, active)
            tx = thumb.right() + 12
            room = r.right() - 38 - tx
            p.setFont(name_f)
            p.setPen(QColor(colour) if active else qc(T["text"]))
            p.drawText(QRectF(tx, r.top() + 11, room, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(short_title(ent) if not self.searching else ent["title"],
                                     Qt.ElideRight, int(room)))
            p.setFont(sub_f)
            p.setPen(qc(T["muted"]))
            store = self.page.win.store
            shape = store.shape(src)
            if store.errors.get(src):
                sub = "Couldn't download"
            elif self.searching:
                sub = CATEGORIES[ent["cat"]] + (f", {shape.lower()}" if shape else "")
            else:
                sub = shape or ""
            sub = QFontMetrics(sub_f).elidedText(sub, Qt.ElideRight, int(room))
            p.drawText(QRectF(tx, r.top() + 31, room, 18), Qt.AlignLeft | Qt.AlignVCenter, sub)
            fav = src in favs
            if fav or i == self.hover:
                p.setFont(star_f)
                hot = i == self.hover and self.hover_star
                p.setPen(QColor(colour) if fav else qc(T["text"] if hot else T["faint"]))
                p.drawText(self.star_rect(i), Qt.AlignCenter, "★" if fav else "☆")

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        i = self.index_at(pos)
        star = i >= 0 and self.star_rect(i).contains(QPointF(pos))
        if (i, star) != (self.hover, self.hover_star):
            self.hover, self.hover_star = i, star
            self.setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)
            if i >= 0:
                ent = self.entries[i]
                self.setToolTip(("Unpin from favourites" if ent["source"] in self.page.favs.items
                                 else "Pin to favourites") if star else ent["title"])
            self.update()

    def leaveEvent(self, _):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position().toPoint()
        i = self.index_at(pos)
        if i < 0:
            return
        if self.star_rect(i).contains(QPointF(pos)):
            self.starred.emit(self.entries[i]["source"])
        else:
            self.activated.emit(self.entries[i]["source"])


class BrowseDrawer(QFrame):
    """Slides up over the channel page: search, categories and every preset."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setObjectName("drawer")
        self.category = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 10)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        title = QLabel(f"Presets for {LABELS[page.key]}")
        title.setObjectName("drawerTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all presets")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(lambda _: self.fill())
        self.import_btn = QPushButton("Import a preset file…")
        self.import_btn.setObjectName("ghost")
        self.import_btn.setToolTip("Add a Sonar preset (.json), e.g. one exported from SteelSeries GG, "
                                   "to My presets")
        self.import_btn.clicked.connect(lambda: page.win.import_file(page.key))
        done = QPushButton("Done")
        done.setObjectName("ghost")
        done.clicked.connect(page.close_drawer)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.search)
        top.addWidget(self.import_btn)
        top.addWidget(done)
        lay.addLayout(top)
        self.cats = CategoryBar(page.colour)
        self.cats.changed.connect(self.set_category)
        lay.addWidget(self.cats)
        self.grid = PresetGrid(page)
        self.grid.activated.connect(lambda s: page.win.choose_preset(page.key, s))
        self.grid.starred.connect(lambda s: page.win.toggle_favourite(page.key, s))
        scroll = QScrollArea()
        scroll.setWidget(self.grid)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll = scroll
        lay.addWidget(scroll, 1)
        foot = QHBoxLayout()
        self.note = QLabel("")
        self.note.setObjectName("hint")
        credit = QLabel('Presets from SteelSeries Sonar, collected by <a style="color:'
                        f'{T["muted"]}" href="https://github.com/loteran/Arctis-Sound-Manager">'
                        'Arctis Sound Manager</a>')
        credit.setObjectName("hint")
        credit.setOpenExternalLinks(True)
        foot.addWidget(self.note, 1)
        foot.addWidget(credit)
        lay.addLayout(foot)

    def set_category(self, key):
        self.category = key
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self.fill()
        self.scroll.verticalScrollBar().setValue(0)

    def fill(self):
        win, key = self.page.win, self.page.key
        entries = win.catalogue
        favs = self.page.favs.items
        counts = {c: len(filter_catalogue(entries, c, "", favs)) for c in CATEGORY_ORDER[key]}
        cats = [(c, n) for c, n in counts.items() if n or c in ("mine", "fav") or c == self.category]
        if self.category is None or (counts.get(self.category, 0) == 0 and self.category not in ("mine", "fav")):
            self.category = next((c for c, n in cats if n), CATEGORY_ORDER[key][0])
        query = self.search.text().strip()
        self.cats.set_cats(cats, None if query else self.category)
        shown = filter_catalogue(entries, self.category, query, favs)
        if win.manifest_names is None and not shown:
            msg = "Getting the Sonar presets…"
        elif not win.manifest_names and not shown and self.category not in ("mine",):
            msg = ("Couldn't download the Sonar presets. Check your internet connection. "
                   "Presets you've used before and your own preset files still work.")
        elif query:
            msg = f'No presets match "{query}".'
        elif self.category == "mine":
            msg = "Presets you import show up here. Use “Import a preset file…” to add a Sonar .json."
        elif self.category == "fav":
            msg = "Nothing pinned yet. Hover a preset and click its star."
        elif self.category == "yours":
            msg = "None of the games installed here have a Sonar preset."
        else:
            msg = "Nothing here."
        self.grid.set_entries(shown, msg, searching=bool(query))


class ChannelPage(QWidget):
    """Chat, Media or Game: the preset in use, favourites, the curve, and the browser."""

    def __init__(self, win, key):
        super().__init__()
        self.win, self.key = win, key
        self.colour = ACCENT[key]
        self.spec, self.on, self.active = None, False, None
        self.pending_title = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 18, 28, 18)
        lay.setSpacing(14)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.name = QPushButton("No EQ")
        self.name.setObjectName("link")
        self.name.setCursor(Qt.PointingHandCursor)
        self.name.setToolTip("Browse presets")
        self.name.setStyleSheet("font-size: 21pt; font-weight: 700; color: " + T["text"] + ";")
        self.name.clicked.connect(self.open_drawer)
        self.detail = QLabel("")
        self.detail.setObjectName("detail")
        self.detail.setWordWrap(True)
        titles.addWidget(self.name, 0, Qt.AlignLeft)
        titles.addWidget(self.detail)
        head.addLayout(titles, 1)
        self.star = QPushButton("☆")
        self.star.setObjectName("link")
        self.star.setCursor(Qt.PointingHandCursor)
        self.star.setStyleSheet("font-size: 16pt;")
        self.star.clicked.connect(lambda: self.current_source() and
                                  win.toggle_favourite(self.key, self.current_source()))
        eq_label = QLabel("EQ")
        eq_label.setObjectName("detail")
        self.toggle = EqToggle(self.colour)
        self.toggle.clicked.connect(lambda on: win.set_eq_enabled(self.key, on))
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.star, 0, Qt.AlignVCenter)
        controls.addSpacing(14)
        controls.addWidget(eq_label, 0, Qt.AlignVCenter)
        controls.addWidget(self.toggle, 0, Qt.AlignVCenter)
        head.addLayout(controls)
        head.setAlignment(controls, Qt.AlignTop)
        lay.addLayout(head)

        self.suggest = QFrame()
        self.suggest.setObjectName("suggest")
        sl = QHBoxLayout(self.suggest)
        sl.setContentsMargins(14, 8, 8, 8)
        self.suggest_text = QLabel("")
        self.suggest_btn = QPushButton("Use it")
        self.suggest_btn.setObjectName("ghost")
        self.suggest_source = None
        self.suggest_btn.clicked.connect(lambda: self.suggest_source and
                                         win.choose_preset(self.key, self.suggest_source))
        sl.addWidget(self.suggest_text, 1)
        sl.addWidget(self.suggest_btn)
        self.suggest.hide()
        lay.addWidget(self.suggest)

        favrow = QHBoxLayout()
        favrow.setSpacing(12)
        self.favs = FavStrip(self)
        self.browse_btn = QPushButton("Browse all\npresets")
        self.browse_btn.setObjectName("ghost")
        self.browse_btn.setStyleSheet("border-radius: 12px; padding: 6px 14px;")
        self.browse_btn.setFixedHeight(76)
        self.browse_btn.clicked.connect(self.open_drawer)
        favrow.addWidget(self.favs, 1)
        favrow.addWidget(self.browse_btn)
        lay.addLayout(favrow)

        self.display = EqDisplay(self.colour)
        lay.addWidget(self.display, 1)
        self.footer = QLabel("")
        self.footer.setObjectName("detail")
        lay.addWidget(self.footer)

        self.drawer = BrowseDrawer(self)
        self.drawer.hide()
        self.slide = QPropertyAnimation(self.drawer, b"pos", self)
        self.slide.setDuration(200)
        self.slide.setEasingCurve(QEasingCurve.OutCubic)
        self.slide.finished.connect(self._slid)
        self._closing = False

    # ---- drawer
    def _drawer_top(self):
        return max(60, self.favs.mapTo(self, QPoint(0, 0)).y() - 14)

    def open_drawer(self):
        self.win.store.retry_failed()
        self.drawer.fill()
        top = self._drawer_top()
        self.drawer.setGeometry(0, self.height(), self.width(), self.height() - top)
        self.drawer.show()
        self.drawer.raise_()
        self._closing = False
        self.slide.stop()
        self.slide.setStartValue(QPoint(0, self.height()))
        self.slide.setEndValue(QPoint(0, top))
        self.slide.start()
        self.drawer.search.setFocus()

    def close_drawer(self):
        if not self.drawer.isVisible():
            return
        self._closing = True
        self.slide.stop()
        self.slide.setStartValue(self.drawer.pos())
        self.slide.setEndValue(QPoint(0, self.height()))
        self.slide.start()

    def _slid(self):
        if self._closing:
            self.drawer.hide()

    def drawer_open(self):
        return self.drawer.isVisible() and not self._closing

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.drawer.isVisible() and not self._closing and self.slide.state() != QPropertyAnimation.Running:
            top = self._drawer_top()
            self.drawer.setGeometry(0, top, self.width(), self.height() - top)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.drawer_open():
            self.close_drawer()
        else:
            super().keyPressEvent(e)

    # ---- state
    def current_source(self):
        return (self.spec or {}).get("source")

    def set_state(self, spec, on, active, favs, apps_here, level, suggestion):
        self.spec, self.on, self.active = spec, on, active
        self.favs.set_items(favs)
        src = self.current_source()
        self.toggle.setChecked(bool(on))
        self.toggle.setEnabled(bool(spec and spec.get("filters")) or bool(on))
        fav = src in favs if src else False
        self.star.setText("★" if fav else "☆")
        self.star.setStyleSheet(f"font-size: 16pt; color: {self.colour if fav else T['muted']};")
        self.star.setVisible(bool(src))
        self.star.setToolTip("Unpin from favourites" if fav else "Pin to favourites")
        # the window refreshes every 2 s: only redo the maths when the preset changes
        key = (json.dumps(spec, sort_keys=True) if spec else None, bool(on), active, self.pending_title)
        if key != getattr(self, "_shown", None):
            self._shown = key
            if self.pending_title:
                self.name.setText(self.pending_title)
                self.detail.setText("Downloading…")
            elif spec:
                self.name.setText(spec.get("name") or "Custom")
                self.detail.setText(self.describe(spec, on, active))
            else:
                self.name.setText("No EQ")
                self.detail.setText("Pick a favourite below, or browse all the presets.")
            self.display.set_curve(eq_curve(svc.clean_filters(spec.get("filters")), HERO_FREQS)
                                   if spec and on and spec.get("filters") else None)
        if suggestion and suggestion[1] != src:
            self.suggest_source = suggestion[1]
            self.suggest_text.setText(f"{suggestion[0]} is playing, and there's a Sonar preset for it.")
            self.suggest.show()
        else:
            self.suggest.hide()
        label = LABELS[self.key]
        names = ", ".join(apps_here[:6]) + (f" and {len(apps_here) - 6} more" if len(apps_here) > 6 else "")
        where = f"In {label}: {names}." if apps_here else f"Nothing assigned to {label} yet. Drag apps here on the Mixer page."
        if self.key == "game":
            where += " Apps you haven't assigned play here too."
        self.footer.setText(where + (f"   Dial: {level}%" if level is not None else ""))
        self.favs.update()
        if self.drawer.isVisible():
            self.drawer.grid.update()

    def describe(self, spec, on, active):
        src = spec.get("source") or ""
        cat = preset_category(src) if src else "mine"
        filters = svc.clean_filters(spec.get("filters"))
        origin = {"mine": "Your preset", "games": "Sonar game preset", "pro": "Sonar pro player preset",
                  "music": "Sonar music preset", "movie": "Sonar movie preset", "voice": "Sonar voice preset",
                  }.get(cat, "Sonar preset")
        if filters:
            words = shape_words(eq_curve(filters, SHAPE_FREQS), SHAPE_FREQS)
            origin += ": " + words[0].lower() + words[1:]
        if not filters:
            return f"{origin}. It's flat, so there's no EQ to run."
        if not on:
            return f"{origin}. The EQ is off: switch it on to use this."
        if active is False:
            return (f"This EQ isn't running: the service couldn't start it, so {LABELS[self.key]} plays "
                    "without it. Details: journalctl --user -u nova-chatmix")
        pre = svc.eq_preamp_db(filters)
        quieter = (f" Played {abs(pre):.1f} dB quieter so its boosts can't clip." if pre < -0.05 else "")
        return f"{origin}.{quieter}"


# ----------------------------------------------------------- window ---

class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChatMix")
        self.setWindowIcon(QIcon.fromTheme("audio-headphones"))
        self.setPalette(dark_palette())
        self.setAutoFillBackground(True)
        self.setStyleSheet(stylesheet())
        self.resize(1120, 700)
        self.setMinimumSize(780, 520)
        self.present = set()
        self.drag_active = False
        self.index = DesktopIndex()
        self.index_time = time.monotonic()
        self.columns, self.channels = {}, {}
        self.catalogue, self.manifest_names, self.your_titles = [], None, None
        self.pending = {}   # channel -> source waiting to download
        self.cards = []
        self.snap = {"levels": {}, "eq_state": {}, "dial": {}}
        self.store = PresetStore()
        self.store.manifest.connect(self._got_manifest)
        self.store.ready.connect(self._preset_ready)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(22, 4, 14, 0)
        bar.setSpacing(0)
        brand = QLabel("ChatMix")
        brand.setObjectName("brand")
        bar.addWidget(brand)
        bar.addSpacing(26)
        self.tabs = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label in [("mixer", "Mixer"), ("chat", "Chat"), ("media", "Media"), ("game", "Game")]:
            b = QPushButton(label)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            colour = ACCENT.get(key, T["text"])
            b.setStyleSheet(f"QPushButton#tab:checked {{ color: {colour}; border-bottom-color: {colour}; }}")
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            group.addButton(b)
            self.tabs[key] = b
            bar.addWidget(b)
        bar.addStretch(1)
        self.more = QToolButton()
        self.more.setObjectName("more")
        self.more.setText("⋯")
        self.more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.more)
        self.reset_action = QAction("Reset the GameDAC", menu)
        self.reset_action.setToolTip("Does the same as unplugging the GameDAC and plugging it back in")
        self.reset_action.triggered.connect(self.reset_dac)
        refresh_presets = QAction("Check for new Sonar presets", menu)
        refresh_presets.triggered.connect(lambda: self.store.load_manifest(refresh=True))
        menu.addAction(self.reset_action)
        menu.addAction(refresh_presets)
        self.more.setMenu(menu)
        bar.addWidget(self.more)
        root.addLayout(bar)
        line = QFrame()
        line.setObjectName("sep")
        line.setFixedHeight(1)
        root.addWidget(line)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 8, 8)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_btn = QPushButton("Turn it on")
        self.banner_btn.setObjectName("ghost")
        self.banner_btn.clicked.connect(self.start_service)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_btn)
        holder = QHBoxLayout()
        holder.setContentsMargins(22, 10, 22, 0)
        holder.addWidget(self.banner)
        root.addLayout(holder)

        self.stack = QStackedWidget()
        self.mixer = MixerPage(self)
        self.status = self.mixer.status
        self.stack.addWidget(self.mixer)
        for key in ("chat", "media", "game"):
            self.channels[key] = ChannelPage(self, key)
            self.stack.addWidget(self.channels[key])
        root.addWidget(self.stack, 1)
        self.show_page("mixer")

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
        self.store.load_manifest()
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
        self.store.shutdown()
        super().closeEvent(e)

    def show_page(self, key):
        self.tabs[key].setChecked(True)
        self.stack.setCurrentWidget(self.mixer if key == "mixer" else self.channels[key])

    # ---- presets
    def _got_manifest(self, names):
        self.manifest_names = names
        self._rebuild_catalogue()
        wanted = set()
        for key in self.channels:
            wanted.update(favourites(key))
        self.store.want(sorted(wanted))
        self.refresh()

    def _rebuild_catalogue(self):
        self.catalogue = build_catalogue(self.manifest_names or [], self.your_titles or ())
        for page in self.channels.values():
            if page.drawer.isVisible():
                page.drawer.fill()

    def _preset_ready(self, source):
        for key, want in list(self.pending.items()):
            if want != source:
                continue
            del self.pending[key]
            self.channels[key].pending_title = None
            if source in self.store.filters:
                self._apply(key, source, self.store.filters[source])
            else:
                self.channels[key].detail.setText(
                    f"Couldn't download {preset_title(source)} ({self.store.errors.get(source)}).")
                self.refresh_channels()
        for page in self.channels.values():
            page.favs.update()
            if page.drawer.isVisible():
                page.drawer.grid.update()

    def choose_preset(self, key, source):
        filters = self.store.filters.get(source)
        if filters is None:
            self.pending[key] = source
            self.channels[key].pending_title = preset_title(source)
            self.store.errors.pop(source, None)
            self.store.want([source])
            self.refresh_channels()
            return
        self.pending.pop(key, None)
        self.channels[key].pending_title = None
        self._apply(key, source, filters)

    def _apply(self, key, source, filters):
        save_eq(key, {"name": preset_title(source), "source": source, "filters": filters})
        self.refresh_channels()
        self.refresh_lanes_eq()

    def set_eq_enabled(self, key, on):
        spec, _ = shown_eq(key)
        if on and spec and spec.get("filters"):
            save_eq(key, spec)
        elif not on:
            save_eq(key, None)
        self.refresh_channels()
        self.refresh_lanes_eq()

    def toggle_favourite(self, key, source):
        favs = favourites(key)
        page = self.channels[key]
        if source in favs:
            favs.remove(source)
        elif len(favs) >= MAX_FAVOURITES:
            page.drawer.note.setText(f"All {MAX_FAVOURITES} favourites are taken. Unpin one first "
                                     "(right-click it).")
            return
        else:
            favs.append(source)
            self.store.want([source])
        page.drawer.note.setText("")
        set_favourites(key, favs)
        self.refresh_channels()
        if page.drawer.isVisible():
            page.drawer.fill()

    def import_file(self, key):
        path, _ = QFileDialog.getOpenFileName(self, "Import a Sonar preset", os.path.expanduser("~"),
                                              "Sonar presets (*.json);;All files (*)")
        if path:
            self.import_path(key, path)

    def import_path(self, key, path):
        page = self.channels[key]
        try:
            source, filters = import_preset(path)
        except (OSError, ValueError, AttributeError, TypeError) as e:
            page.drawer.note.setText(f"That file isn't a Sonar preset ({e}).")
            return None
        page.drawer.note.setText(f"Added {preset_title(source)} to My presets.")
        self.store.put(source, filters)
        self._rebuild_catalogue()
        page.drawer.set_category("mine")
        self.choose_preset(key, source)
        return source

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
        self.reset_action.setEnabled(False)
        self.reset_action.setText("Resetting the GameDAC…" if rc == 0 else "Reset failed: " +
                                  (out.getvalue().strip().splitlines() or ["see the log"])[-1][:80])
        QTimer.singleShot(8000, self._reset_done)

    def _reset_done(self):
        self.reset_action.setEnabled(True)
        self.reset_action.setText("Reset the GameDAC")
        self.refresh()

    def set_media_dial(self, mode):
        data = _read_settings()
        data["media_dial"] = mode
        _write_settings(data)
        QTimer.singleShot(400, self.refresh)  # let the service apply it first
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
        snap = self.snap = audio_snapshot()
        self.present = snap["present"]
        cards = self.cards = build_cards(apps, snap, rules, self.index)

        titles = sorted(installed_games() | {c["name"] for c in cards})
        if titles != self.your_titles:
            self.your_titles = titles
            if self.manifest_names is not None:
                self._rebuild_catalogue()

        if snap["errors"]:
            self.banner.setVisible(True)
            self.banner_btn.setVisible(False)
            self.banner_text.setText("Couldn't read your audio setup: " + "; ".join(snap["errors"]))
        elif not self.present:
            self.banner.setVisible(True)
            self.banner_btn.setVisible(True)
            self.banner_text.setText("The ChatMix service isn't running, or the GameDAC isn't plugged in. "
                                     "You can still assign apps and choose EQs now.")
        else:
            self.banner.setVisible(False)

        dial = snap.get("dial", {})
        mode = media_dial_setting()
        on_dial = dial.get("media_on_dial") if self.present else (mode != "never")
        self.columns["media"].set_dial(mode, on_dial, dial.get("chat_busy", False))
        shown = {k for k in LABELS if k != "media" or "media" in self.present}
        for key, col in self.columns.items():
            col.setVisible(key is None or key in shown)
            col.set_level(snap["levels"].get(key))
            colour = ACCENT[key] if key else ACCENT["game"]
            col.set_cards([AppCard(self, c, colour) for c in cards
                           if c["column"] == key or (key is None and c["column"] not in shown)])
        self.mixer.seps[1].setVisible("media" in shown)
        levels = snap["levels"]
        self.mixer.meter.set_state(bool(self.present), levels.get("game"), levels.get("chat"),
                                   bool(on_dial) and mode != "never" and "media" in shown)
        self.refresh_lanes_eq()
        self.refresh_channels()
        playing = sum(1 for c in cards if c["playing"])
        n_open = sum(1 for c in cards if c["open"])
        self.status.setText(f"{n_open} apps open, {playing} playing. Drag an app into Chat, Media or Game; "
                            "drag it back to Apps to unassign.")
        self.setWindowTitle(f"ChatMix ({playing} playing)" if playing else "ChatMix")

    def refresh_lanes_eq(self):
        eq_state = self.snap.get("eq_state", {})
        for key, col in self.columns.items():
            if key:
                spec, on = shown_eq(key)
                col.set_eq(spec, on, eq_state.get(key, {}).get("active") if self.present and on else None)

    def suggestion(self):
        """A playing game in Game that has a Sonar preset: (name, source)."""
        if not self.catalogue:
            return None
        for c in self.cards:
            if c["playing"] and c["column"] in ("game", None) and c["key"].startswith("steam:"):
                hit = match_games(self.catalogue, [c["name"]])
                hit = [s for s in hit if preset_category(s) == "games"] or hit
                if hit:
                    return c["name"], hit[0]
        return None

    def refresh_channels(self):
        eq_state = self.snap.get("eq_state", {})
        levels = self.snap.get("levels", {})
        shown = {k for k in LABELS if k != "media" or "media" in self.present}
        for key, page in self.channels.items():
            spec, on = shown_eq(key)
            active = eq_state.get(key, {}).get("active") if self.present and on else None
            here = [c["name"] for c in self.cards if c["column"] == key or
                    (key == "game" and c["column"] not in shown and c["column"])]
            level = levels.get(key) if self.present else None
            if key == "media" and self.columns["media"].dial_mode == "never":
                level = None
            page.set_state(spec, on, active, favourites(key), here, level,
                           self.suggestion() if key == "game" else None)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ChatMix")
    app.setDesktopFileName("nova-chatmix")
    app.setPalette(dark_palette())
    w = Window()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
