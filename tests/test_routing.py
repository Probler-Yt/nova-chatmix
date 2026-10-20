"""The service sends each app's audio where it's assigned, by app identity."""
import json
import logging
import os
import threading
import time
import types

import fixtures
from common import Checks, fake_dac, fake_pactl, load_service

tmp = fake_pactl("full")
svc = load_service()
fx = fixtures.build(os.path.join(tmp, "session"))
svc.PROC_ROOT = fx["proc"]
logging.basicConfig(level=logging.WARNING)
SF = os.environ["FAKE_PACTL_STATE"]
RULES = svc.RULES_PATH
os.makedirs(os.path.dirname(RULES))
DEV = os.path.join(tmp, "hidraw")
open(DEV, "w").close()
svc.find_hidraw = lambda: (DEV, 0x12CB) if os.path.exists(DEV) else (None, None)
fake_dac(svc, {})
check = Checks()
G = "ChatMix_Game"


def P(name, binary):
    return {"application.name": name, "application.process.binary": binary}


def where():
    return {s["index"]: s["sink"].replace("ChatMix_", "") for s in json.load(open(SF))["streams"]}


def new_stream(index, client_pid, props, sink=G):
    st = json.load(open(SF))
    cid = 100 + index
    st["clients"].append({"index": cid, "properties": {"pipewire.sec.pid": str(client_pid)}})
    st["streams"].append({"index": index, "client": cid, "sink": sink, "props": props})
    json.dump(st, open(SF, "w"))
    with open(SF + ".events", "a") as f:
        f.write(f"Event 'new' on sink-input #{index}\n")


json.dump({"apps": {"steam:2073850": "game", "discord": "chat", "brave-browser": "media"}}, open(RULES, "w"))
json.dump({"clients": [{"index": 70, "properties": {"pipewire.sec.pid": "1013"}},
                       {"index": 71, "properties": {"pipewire.sec.pid": "1030"}},
                       {"index": 72, "properties": {"pipewire.sec.pid": "1020"}},
                       {"index": 73, "properties": {"pipewire.sec.pid": "900"}}],
           "streams": [{"index": 21, "client": 70, "sink": G, "props": P("Discovery.exe", "wine64-preloader")},
                       {"index": 22, "client": 71, "sink": G, "props": P("Discord", "Discord")},
                       {"index": 24, "client": 72, "sink": G, "props": P("Brave", "brave")},
                       {"index": 25, "client": 73, "sink": G, "props": P("plasmashell", "plasmashell")}]},
          open(SF, "w"))

d = svc.Daemon(types.SimpleNamespace(sink=None, no_media=False, no_default=False, boot_wait=0.0, verbose=False))
t = threading.Thread(target=d.run, daemon=True)
t.start()
time.sleep(2)
w = where()
check(w[22] == "Chat" and w[24] == "Media", f"already-playing apps moved on startup: {w}")
check(w[21] == "Game" and w[25] == "Game", "THE FINALS and system sounds left where they are")

json.dump({"apps": {"steam:2073850": "game", "discord": "chat", "brave-browser": "media", "notion-app": "chat"}},
          open(RULES, "w"))
time.sleep(1.2)
new_stream(30, 1040, P("Notion", "notion-app"))
time.sleep(1.5)
check(where()[30] == "Chat", "app assigned while silent goes to Chat the moment it plays")
new_stream(31, 1014, P("denuvo", "wine64-preloader"), sink="ChatMix_Chat")
time.sleep(1.5)
check(where()[31] == "Game", "a Proton helper with the game's Steam app id follows the game's rule")

st = json.load(open(SF))
st["streams"].append({"index": 32, "client": 999, "sink": G, "props": P("Spotify", "spotify")})
json.dump(st, open(SF, "w"))
json.dump({"apps": {"spotify": "media"}}, open(RULES, "w"))  # a rule from an older version
time.sleep(1.5)
check(where()[32] == "Media", "older binary-name rules still work")

state = json.load(open(svc.STATE_PATH))
check(state["levels"] == {"game": 100, "chat": 100, "media": 100}, "dial position published for the window")
moves = [c for c in json.load(open(SF))["calls"] if c.startswith("move-sink-input")]
check(all(not c.startswith("move-sink-input 5") for c in moves), "never moves its own loopbacks")
check(len(moves) == len(set(moves)), "no repeated moves")
d.stop()
t.join(5)
check(not os.path.exists(svc.STATE_PATH), "state file cleaned up on stop")
check.done()
