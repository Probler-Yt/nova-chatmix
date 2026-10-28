"""The ChatMix window against a fake KDE + Steam session (offscreen Qt)."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fixtures  # noqa: E402
from common import SRC, Checks, fake_pactl, load, read_state  # noqa: E402

try:
    import PySide6  # noqa: F401
except ImportError:
    print("SKIP PySide6 not installed")
    sys.exit(0)

tmp = fake_pactl("full")
fx = fixtures.build(os.path.join(tmp, "session"))
gui = load(os.path.join(SRC, "nova-chatmix-gui.py"), "nova_chatmix_gui_under_test")
svc = gui.svc
svc.PROC_ROOT = fx["proc"]
gui.STEAM_ROOTS = [fx["steam"]]
gui.data_dirs = lambda: [fx["apps"], fx["autostart"]]

from PySide6.QtCore import QMimeData, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QDropEvent, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleFactory  # noqa: E402

# PipeWire as the service leaves it, plus apps playing
os.system("pactl set-default-sink ChatMix_Game >/dev/null")
for k in ("Game", "Chat", "Media"):
    os.system(f"pactl load-module module-null-sink sink_name=ChatMix_{k} >/dev/null")
    os.system(f"pactl load-module module-loopback source=ChatMix_{k}.monitor sink={fixtures.DAC} latency_msec=20 >/dev/null")
st = json.load(open(os.environ["FAKE_PACTL_STATE"]))
st["clients"] = [{"index": 70 + i, "properties": {"pipewire.sec.pid": str(p)}}
                 for i, p in enumerate([1013, 1030, 1020, 900, 1001])]


def P(n, b, title="", role=None):
    d = {"application.name": n, "application.process.binary": b, "media.name": title}
    if role:
        d["media.role"] = role
    return d


st["streams"] = [
    {"index": 21, "client": 70, "sink": "ChatMix_Game", "props": P("Discovery.exe", "wine64-preloader", "audio stream")},
    {"index": 22, "client": 71, "sink": "ChatMix_Game", "props": P("Discord", "Discord", "playStream")},
    {"index": 24, "client": 72, "sink": "ChatMix_Game", "props": P("Brave", "brave", "Ranked tips - YouTube")},
    {"index": 25, "client": 73, "sink": "ChatMix_Game", "props": P("plasmashell", "plasmashell", "Notification", "event")},
    {"index": 26, "client": 74, "sink": "ChatMix_Game", "props": P("Steam", "steamwebhelper"), "corked": True},
]
json.dump(st, open(os.environ["FAKE_PACTL_STATE"], "w"))
os.makedirs(os.path.dirname(svc.RULES_PATH))
# rules as an earlier version saved them (binary names)
json.dump({"apps": {"discord": "chat", "brave": "media", "denuvo-anti-cheat-update-service.exe": "game",
                    "steam:3058630": "game"}}, open(svc.RULES_PATH, "w"))

# Sonar downloads stubbed before the window exists: CI must not depend on the internet
import tempfile  # noqa: E402
import time  # noqa: E402
SAMPLE_JSON = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_preset.json"), "rb").read()
served = []
MANIFEST = ["Alpha Strike [Game].json", "Big Bass Movie [Game].json", "Clear Voices [Chat].json",
            "Boom Mic [Mic].json", "The finals [Game].json", "Music - Deep Bass [Game].json", "Flat [Chat].json"]


def fake_get(url, timeout=10):
    served.append(url)
    if url == gui.SONAR_MANIFEST:
        return json.dumps({"presets": MANIFEST}).encode()
    if url.endswith("Flat%20%5BChat%5D.json"):
        return json.dumps({"parametricEQ": {"enabled": True, "filter1": {"enabled": True, "type": "peakingEQ",
                                                                        "frequency": 1000, "qFactor": 1, "gain": 0}}}).encode()
    if url.startswith(gui.SONAR_FILE):
        return SAMPLE_JSON
    raise OSError("unexpected URL " + url)


def pump(cond, secs=10):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


gui._http_get = fake_get
gui.SONAR_CACHE = tempfile.mkdtemp()

check = Checks()
app = QApplication(sys.argv)
app.setStyle(QStyleFactory.create("Fusion"))
if "--dark" in sys.argv:
    pal = QPalette()
    for role, c in [(QPalette.Window, "#2a2e32"), (QPalette.Base, "#1b1e20"), (QPalette.WindowText, "#fcfcfc"),
                    (QPalette.Text, "#fcfcfc"), (QPalette.Button, "#31363b"), (QPalette.ButtonText, "#fcfcfc")]:
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
w = gui.Window()
w.resize(1040, 600)
w.show()
app.processEvents()


def col(k):
    return [(c.card["name"], "playing" if c.card["playing"] else "open" if c.card["open"] else "closed")
            for c in w.columns[k].cards()]


names = [n for k in (None, "chat", "media", "game") for n, _ in col(k)]
check(w.columns["media"].isVisible(), "Media lane shown")
check(w.stack.currentWidget() is w.mixer and w.tabs["mixer"].isChecked(), "opens on the Mixer page")
check(("THE FINALS", "playing") in col("game"), "THE FINALS: one card, real name, playing, in Game")
check(("Assetto Corsa EVO", "closed") in col("game"), "assigned game that's closed shows its name, not its id")
check(col("chat") == [("Discord", "playing")], "Discord in Chat")
check(col("media") == [("Brave", "playing")], "Brave's old rule carried over")
check("Steam" in names and names.count("Assetto Corsa EVO") == 1, "Steam is Steam (game shortcuts don't hijack it)")
check(not any(n in names for n in ("Proton - Experimental", "SteamLinuxRuntime_4", "Discovery",
                                   "Denuvo-anti-cheat-update-service")), "Proton, runtime and anti-cheat fold into the game")
check(not any(x in " ".join(names).lower() for x in ("kwallet", "plasmashell", "kde connect", "chatmix")),
      "desktop plumbing, hidden apps and ChatMix itself left out")
for want in ("Notion", "Chromium", "System Monitor", "MacroPad actions", "Shelly",
             "Cachy-Update Systray Applet", "Dolphin", "Discover", "Limine-snapper-notify"):
    check(want in [n for n, _ in col(None)], f"{want} listed under Apps")
check(names.count("Brave") == 1, "other users' processes ignored")
check(svc.load_rules() == {"discord": "chat", "brave-browser": "media", "steam:2073850": "game", "steam:3058630": "game"},
      f"old rules migrated to app identities: {svc.load_rules()}")
if len(sys.argv) > 1 and sys.argv[-1].endswith(".png"):
    w.grab().save(sys.argv[-1])


def drop(card, key):
    m = QMimeData()
    m.setData(gui.MIME, json.dumps({"key": card["key"], "indices": card["indices"]}).encode())
    w.columns[key].dropEvent(QDropEvent(QPointF(5, 5), Qt.MoveAction, m, Qt.LeftButton, Qt.NoModifier))
    app.processEvents()


drop(next(c.card for c in w.columns[None].cards() if c.card["name"] == "Notion"), "chat")
check(("Notion", "open") in col("chat") and svc.load_rules().get("notion-app") == "chat", "drag Notion into Chat")
drop(next(c.card for c in w.columns["game"].cards() if c.card["name"] == "THE FINALS"), None)
st = read_state()
check(("THE FINALS", "playing") in col(None) and "steam:2073850" not in svc.load_rules()
      and next(s for s in st["streams"] if s["index"] == 21)["sink"] == st["default"],
      "drag back to Apps: unassigned and its audio goes back to the default output")
calls = []
svc.reset_dac = lambda: calls.append(1) or 0
w.reset_action.trigger()
app.processEvents()
check(calls == [1] and not w.reset_action.isEnabled(), "Reset the GameDAC (in the ⋯ menu) resets it and waits for it")

# --- preset catalogue (plain functions)
names = ["The finals [Game].json", "CS2 by FaZe [Game].json", "CS2 Pro Preset [Game].json",
         "Music - Deep Bass [Game].json", "Movie_ Immersion [Game].json", "Podcast [Game].json",
         "Clarity [Chat].json", "Boom Mic [Mic].json", "Assetto Corsa [Game].json", "Assetto Corsa EVO [Game].json",
         "The Witcher 3 [Game].json", "Anno 117_ Pax Romana [Game].json"]
cats = {n: gui.preset_category(n) for n in names}
check(cats == {"The finals [Game].json": "games", "CS2 by FaZe [Game].json": "pro", "CS2 Pro Preset [Game].json": "pro",
               "Music - Deep Bass [Game].json": "music", "Movie_ Immersion [Game].json": "movie",
               "Podcast [Game].json": "general", "Clarity [Chat].json": "voice", "Boom Mic [Mic].json": "mic",
               "Assetto Corsa [Game].json": "games", "Assetto Corsa EVO [Game].json": "games",
               "The Witcher 3 [Game].json": "games", "Anno 117_ Pax Romana [Game].json": "games"},
      f"Sonar's presets sorted into proper categories: {cats}")
check(gui.preset_title("Music - Deep Bass [Game].json") == "Music: Deep Bass"
      and gui.preset_title("Anno 117_ Pax Romana [Game].json") == "Anno 117: Pax Romana", "preset names tidied")
cat = gui.build_catalogue(names, ["THE FINALS", "Counter-Strike 2", "Assetto Corsa EVO", "The Witcher 3: Wild Hunt",
                                  "Dolphin"])
yours = sorted(e["title"] for e in cat if e["yours"])
check(yours == ["Assetto Corsa EVO", "CS2 Pro Preset", "CS2 by FaZe", "The Witcher 3", "The finals"],
      f"'Your games' finds presets for installed games, by name, alias or prefix: {yours}")
check(not any(e["cat"] == "mic" for e in cat), "microphone presets left out")
check([e["title"] for e in gui.filter_catalogue(cat, "games", "deep")] == ["Music: Deep Bass"],
      "searching looks through every category")
check([e["source"] for e in gui.filter_catalogue(cat, "fav", "", ["Podcast [Game].json", "gone.json"])] ==
      ["Podcast [Game].json"], "Favourites list keeps your order and skips presets that vanished")
freqs = svc.log_freqs(56)
bass = svc.eq_response_db([{"type": "bq_lowshelf", "freq": 120.0, "q": 0.707, "gain": 6.0}], freqs)
check(gui.shape_words(bass, freqs) == "More bass" and gui.shape_words([0.0] * 56, freqs) == "Close to flat",
      "curves described in words (More bass / Close to flat)")

# --- the channel pages
check(pump(lambda: w.manifest_names == MANIFEST), "Sonar preset list downloaded in the background")
check(set(w.tabs) == {"mixer", "chat", "media", "game"}, "tabs: Mixer, Chat, Media, Game")
chat = w.channels["chat"]
w.show_page("chat")
app.processEvents()
check(w.stack.currentWidget() is chat and chat.name.text() == "No EQ" and not chat.toggle.isChecked(),
      "Chat page with no EQ chosen yet")
check(chat.favs.items == gui.DEFAULT_FAVOURITES["chat"], "starts with a few sensible favourites")
chat.open_drawer()
check(pump(lambda: chat.drawer.pos().y() < chat.height() and chat.drawer.isVisible()), "browse drawer slides up")
check(chat.drawer.category == "voice" and [e["title"] for e in chat.drawer.grid.entries] == ["Clear Voices", "Flat"],
      f"Chat opens on Voice presets: {[e['title'] for e in chat.drawer.grid.entries]}")
cat_keys = [k for k, _ in chat.drawer.cats.cats]
check("yours" in cat_keys and "music" in cat_keys and "mine" in cat_keys,
      f"categories with presets in them (plus My presets and Favourites): {cat_keys}")
check(pump(lambda: w.store.mini("Clear Voices [Chat].json") is not None), "each tile's curve is downloaded to draw it")
chat.drawer.search.setText("bass")
app.processEvents()
check([e["title"] for e in chat.drawer.grid.entries] == ["Big Bass Movie", "Music: Deep Bass"] and chat.drawer.grid.searching,
      "search finds presets in every category")
chat.drawer.search.clear()
chat.drawer.grid.activated.emit("Big Bass Movie [Game].json")
check(pump(lambda: gui.current_eq().get("chat", {}).get("source") == "Big Bass Movie [Game].json"),
      "clicking a preset uses it straight away")
saved = gui.current_eq()["chat"]
check(saved["name"] == "Big Bass Movie" and len(saved["filters"]) == 8, "saved with its filters for the service")
check(svc.load_rules().get("discord") == "chat", "app assignments survive saving an EQ")
check(chat.name.text() == "Big Bass Movie" and chat.toggle.isChecked() and chat.display.values is not None,
      "page shows the preset, EQ switched on and its curve")
check(w.columns["chat"].eq_btn.text() == "EQ: Big Bass Movie", f"Mixer lane shows it: {w.columns['chat'].eq_btn.text()!r}")
n_before = len(served)
w.choose_preset("chat", "Big Bass Movie [Game].json")
check(len(served) == n_before, "a preset used before comes from memory (no download)")
chat.toggle.click()
app.processEvents()
check("chat" not in gui.current_eq() and chat.name.text() == "Big Bass Movie" and chat.display.values is None,
      "switching the EQ off stops it but remembers the preset")
check(w.columns["chat"].eq_btn.text() == "EQ off", "...and the Mixer lane says EQ off")
chat.toggle.click()
app.processEvents()
check(gui.current_eq().get("chat", {}).get("source") == "Big Bass Movie [Game].json", "switching it back on restores it")
w.choose_preset("chat", "Flat [Chat].json")
check(pump(lambda: chat.name.text() == "Flat") and "chat" not in gui.current_eq() and not chat.toggle.isEnabled(),
      "a flat preset means no EQ for the service to run")
# favourites
chat.drawer.grid.starred.emit("Big Bass Movie [Game].json")
app.processEvents()
check(gui.favourites("chat")[-1] == "Big Bass Movie [Game].json" and "Big Bass Movie [Game].json" in chat.favs.items,
      "star pins a preset to this channel's favourites")
check(gui.favourites("game") == gui.DEFAULT_FAVOURITES["game"], "favourites are per channel")
chat.drawer.grid.starred.emit("Big Bass Movie [Game].json")
app.processEvents()
check("Big Bass Movie [Game].json" not in gui.favourites("chat"), "star again unpins it")
gui.set_favourites("chat", [f"x{i}.json" for i in range(9)])
w.refresh()
w.toggle_favourite("chat", "Clear Voices [Chat].json")
check(len(gui.favourites("chat")) == 9 and "Unpin one" in chat.drawer.note.text(), "only 9 favourites, says so when full")
gui.set_favourites("chat", ["Clear Voices [Chat].json"])
w.refresh()
slot = chat.favs.slot_rects()[0].center().toPoint()
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
ev = QMouseEvent(QEvent.MouseButtonRelease, QPointF(slot), QPointF(chat.favs.mapToGlobal(slot)), Qt.LeftButton,
                 Qt.NoButton, Qt.NoModifier)
chat.favs.mouseReleaseEvent(ev)
check(pump(lambda: gui.current_eq().get("chat", {}).get("source") == "Clear Voices [Chat].json"),
      "clicking a favourite uses it")
# import your own preset
bad = os.path.join(tmp, "notes.json")
json.dump({"hello": 1}, open(bad, "w"))
check(w.import_path("chat", bad) is None and "isn't a Sonar preset" in chat.drawer.note.text(),
      "importing something that isn't a preset is refused, with a reason")
mine = os.path.join(tmp, "My Tune.json")
open(mine, "wb").write(SAMPLE_JSON)
src = w.import_path("chat", mine)
check(src == "mine:My Tune.json" and os.path.exists(os.path.join(gui.my_presets_dir(), "My Tune.json")),
      "importing a Sonar file copies it into My presets")
check(gui.current_eq().get("chat", {}).get("source") == src and chat.drawer.category == "mine"
      and [e["title"] for e in chat.drawer.grid.entries] == ["My Tune"], "...uses it and shows it under My presets")
os.remove(mine)
check(gui.load_preset(src) and gui.preset_category(src) == "mine", "it keeps working after the original file is gone")
chat.close_drawer()
check(pump(lambda: not chat.drawer.isVisible()), "Done slides the drawer away")
# a game that's playing gets its preset suggested
game = w.channels["game"]
w.refresh()
app.processEvents()
check(game.suggest.isVisibleTo(game) and "THE FINALS" in game.suggest_text.text(),
      f"Game page suggests the playing game's own preset: {game.suggest_text.text()!r}")
game.suggest_btn.click()
check(pump(lambda: gui.current_eq().get("game", {}).get("source") == "The finals [Game].json"), "...one click uses it")
check(pump(lambda: not game.suggest.isVisibleTo(game)), "...and the suggestion goes away")
# older versions' EQ settings still show
data = json.load(open(svc.RULES_PATH))
data["eq"]["media"] = {"name": "Old File", "source": "file:old.json", "filters": json.loads(json.dumps(saved["filters"]))}
json.dump(data, open(svc.RULES_PATH, "w"))
w.refresh()
check(w.channels["media"].name.text() == "Old File" and w.channels["media"].toggle.isChecked(),
      "an EQ loaded from a file by an older version still shows and stays on")


# --- Media on the dial (state comes from the service's state file)
def service_state(**kw):
    os.makedirs(os.path.dirname(svc.STATE_PATH), exist_ok=True)
    json.dump(dict({"levels": {"game": 100, "chat": 40, "media": 40}}, **kw), open(svc.STATE_PATH, "w"))
    w.refresh()
    app.processEvents()


media = w.columns["media"]
service_state(media_dial="auto", chat_busy=False, media_on_dial=True)
check(media.dial_btn.text() == "Dial: Auto" and media.level.text() == "40%" and "Game vs Media" in media.caption.text(),
      f"Auto, nobody in Chat: Media on the dial ({media.caption.text()!r}, {media.level.text()})")
check(w.mixer.meter.media_follows and w.mixer.meter.chat == 40 and w.mixer.meter.position() > 0,
      "the dial gauge sits toward Game and says Media follows")
service_state(media_dial="auto", chat_busy=True, media_on_dial=False, levels={"game": 100, "chat": 40, "media": 100})
check("Chat's in use" in media.caption.text() and not media.on_dial and not w.mixer.meter.media_follows,
      "Auto during a call: says Chat's in use, Media off the dial")
eq_before = gui.current_eq()
next(a for a in media.dial_btn.menu().actions() if a.data() == "always").trigger()
app.processEvents()
data = json.load(open(svc.RULES_PATH))
check(data.get("media_dial") == "always" and data.get("eq", {}) == eq_before and data.get("apps", {}).get("discord") == "chat",
      "choosing On saves the mode and keeps EQ and app settings")
check(media.dial_btn.text() == "Dial: On" and [a.data() for a in media.dial_btn.menu().actions() if a.isChecked()] == ["always"],
      "button and menu show the new mode")
next(a for a in media.dial_btn.menu().actions() if a.data() == "never").trigger()
service_state(media_dial="never", chat_busy=False, media_on_dial=False, levels={"game": 100, "chat": 40, "media": 100})
check(media.level.text() == "" and "never" in media.caption.text(), "Off: no dial level shown for Media")
w.close()
check.done()
