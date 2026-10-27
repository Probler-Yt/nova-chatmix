"""The ChatMix window against a fake KDE + Steam session (offscreen Qt)."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fixtures  # noqa: E402
from common import SRC, Checks, fake_pactl, load  # noqa: E402

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
check(w.columns["media"].isVisible(), "Media column shown")
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
st = json.load(open(os.environ["FAKE_PACTL_STATE"]))
check(("THE FINALS", "playing") in col(None) and "steam:2073850" not in svc.load_rules()
      and next(s for s in st["streams"] if s["index"] == 21)["sink"] == st["default"],
      "drag back to Apps: unassigned and its audio goes back to the default output")
calls = []
svc.reset_dac = lambda: calls.append(1) or 0
w.reset_btn.click()
app.processEvents()
check(calls == [1] and not w.reset_btn.isEnabled(), "Reset DAC button resets the DAC and waits for it to come back")
# --- EQ picker (downloads stubbed: CI must not depend on the internet)
import tempfile  # noqa: E402
SAMPLE_JSON = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_preset.json"), "rb").read()
served = []


def fake_get(url, timeout=10):
    served.append(url)
    if url == gui.SONAR_MANIFEST:
        return json.dumps({"presets": ["Alpha Strike [Game].json", "Big Bass Movie [Game].json",
                                       "Clear Voices [Chat].json", "Boom Mic [Mic].json"]}).encode()
    if url.startswith(gui.SONAR_FILE):
        return SAMPLE_JSON
    raise OSError("unexpected URL " + url)


gui._http_get = fake_get
gui.SONAR_CACHE = tempfile.mkdtemp()


def pump(cond, secs=10):
    import time
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


check(all(w.columns[k].eq_btn is not None for k in ("chat", "media", "game")) and w.columns[None].eq_btn is None,
      "Chat, Media and Game each have an EQ button (Apps doesn't)")
dlg = gui.EqDialog(w, "chat", "#22B59A")
dlg.show()
check(pump(lambda: dlg.list.count() == 4), "preset list loads (microphone presets left out)")
check(dlg.list.item(1).text().startswith("Clear Voices"), "Chat presets listed first in the Chat column")
dlg.search.setText("bass")
app.processEvents()
visible = [dlg.list.item(i).text() for i in range(dlg.list.count()) if not dlg.list.item(i).isHidden()]
check(visible == ["Off (no EQ)", "Big Bass Movie"], f"search filters the list: {visible}")
dlg.list.setCurrentItem(next(dlg.list.item(i) for i in range(dlg.list.count())
                             if dlg.list.item(i).text() == "Big Bass Movie"))
check(pump(lambda: bool(dlg.chosen and dlg.chosen.get("filters"))), f"choosing one downloads and previews it: {dlg.info.text()}")
dlg.apply()
saved = gui.current_eq().get("chat", {})
check(saved.get("source") == "Big Bass Movie [Game].json" and len(saved.get("filters", [])) == 8, "Apply saves it for Chat")
check(svc.load_rules().get("discord") == "chat", "app assignments survive saving an EQ")
w.refresh()
app.processEvents()
check(w.columns["chat"].eq_btn.text() == "EQ: Big Bass Movie", f"column shows the EQ: {w.columns['chat'].eq_btn.text()!r}")
n_before = len(served)
dlg2 = gui.EqDialog(w, "chat", "#22B59A")
dlg2.show()
pump(lambda: dlg2.list.count() == 4)
dlg2.list.setCurrentItem(next(dlg2.list.item(i) for i in range(dlg2.list.count())
                              if dlg2.list.item(i).text() == "Big Bass Movie"))
pump(lambda: bool(dlg2.chosen and dlg2.chosen.get("filters")))
check(len(served) == n_before, "manifest and preset come from the cache the second time (no downloads)")
dlg2.list.setCurrentRow(0)
app.processEvents()
dlg2.apply()
check("chat" not in gui.current_eq(), "Off removes the EQ")
w.close()
check.done()
