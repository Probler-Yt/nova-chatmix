"""Dial -> Game/Chat volumes, hotplug, PipeWire restarts, clean shutdown."""
import logging
import os
import threading
import time
import types

from common import Checks, fake_dac, fake_pactl, load_service, read_state, update_state, wait_for, write_state

tmp = fake_pactl("basic")
svc = load_service()
logging.basicConfig(level=logging.WARNING)
SF = os.environ["FAKE_PACTL_STATE"]
DEV = os.path.join(tmp, "hidraw")
peer = {"sock": None}
svc.find_hidraw = lambda: (DEV, 0x12CB) if os.path.exists(DEV) else (None, None)
fake_dac(svc, peer)
check = Checks()


def state():
    return read_state()


def setstate(**kw):
    update_state(**kw)


def recv(n, timeout=10):
    peer["sock"].settimeout(timeout)
    return [peer["sock"].recv(64) for _ in range(n)]


def vols():
    st, res = state(), {}
    for mid, m in st["modules"].items():
        if m["name"] == "module-loopback":
            res[m["args"][0].split("=")[1].replace(".monitor", "")] = st.get("vol", {}).get(str(int(mid) + 5000))
    return res


def dial(game, chat):
    peer["sock"].send(bytes([0x07, 0x45, game, chat]) + bytes(12))


open(DEV, "w").close()
d = svc.Daemon(types.SimpleNamespace(sink=None, no_media=False, no_default=False, boot_wait=0.2,
                                     verbose=False, listen=False))
t = threading.Thread(target=d.run, daemon=True)
t.start()
while peer["sock"] is None:
    time.sleep(0.05)
cmds = recv(2)
check(cmds[0][:2] == bytes([6, 0x20]) and len(cmds[0]) == 16, "status request sent as a 16-byte report")
check(cmds[1][:3] == bytes([6, 0x49, 1]), "ChatMix enabled on the dial")
st = state()
sinks = sorted(m["args"][0] for m in st["modules"].values() if m["name"] == "module-null-sink")
check(sinks == ["sink_name=ChatMix_Chat", "sink_name=ChatMix_Game", "sink_name=ChatMix_Media"],
      "Game, Chat and Media outputs created (Media on by default)")
check(st["default"] == "ChatMix_Game", "Game is the default output")
check(vols() == {"ChatMix_Game": "100%", "ChatMix_Chat": "100%", "ChatMix_Media": "100%"}, "start at 100/100")

dial(100, 30)
wait_for(lambda: vols() == {"ChatMix_Game": "100%", "ChatMix_Chat": "30%", "ChatMix_Media": "30%"})
check(vols() == {"ChatMix_Game": "100%", "ChatMix_Chat": "30%", "ChatMix_Media": "30%"},
      f"dial toward game: Chat down, and Media too while nothing plays in Chat: {vols()}")
for g in (90, 70, 50, 40):
    dial(g, 100)
wait_for(lambda: vols()["ChatMix_Game"] == "40%" and vols()["ChatMix_Media"] == "100%")
check(vols()["ChatMix_Game"] == "40%" and vols()["ChatMix_Media"] == "100%", "burst of turns: last position wins")
peer["sock"].send(bytes([7, 0x25, 30]) + bytes(13))
time.sleep(1.2)
check(vols()["ChatMix_Game"] == "40%", "unrelated reports ignored")

setstate(modules={})  # PipeWire restarted, all our modules vanished
wait_for(lambda: len(state()["modules"]) == 6 and vols()["ChatMix_Game"] == "40%", 20)
check(len(state()["modules"]) == 6 and vols()["ChatMix_Game"] == "40%", "rebuilt after a PipeWire restart, mix kept")

# Reset DAC: the DAC's audio blinks away and back, PipeWire drops our links into
# it, and the DAC forgets ChatMix mode. Everything must come back on its own.
peer["sock"].settimeout(0.2)
try:
    while True:
        peer["sock"].recv(64)  # drain
except OSError:
    pass
setstate(links_down=True)
wait_for(lambda: len(state()["modules"]) == 6 and not state().get("links_down") and
         vols()["ChatMix_Game"] == "40%", 20)
check(len(state()["modules"]) == 6 and not state().get("links_down"), "dropped links to the DAC noticed and reconnected")
check(vols()["ChatMix_Game"] == "40%", "dial position kept across the reconnect")
check(recv(2)[1][:3] == bytes([6, 0x49, 1]), "ChatMix switched back on after the DAC reset")

setstate(dac_present=False)  # unplug
os.remove(DEV)
peer["sock"].close()
wait_for(lambda: state()["modules"] == {}, 15)
check(state()["modules"] == {}, "outputs removed on unplug")

setstate(dac_present=True)  # replug
peer["sock"] = None
open(DEV, "w").close()
while peer["sock"] is None:
    time.sleep(0.05)
got = recv(2)[1][:3]
wait_for(lambda: len(state()["modules"]) == 6)
check(got == bytes([6, 0x49, 1]) and len(state()["modules"]) == 6, "back after replug")

d.stop()
t.join(5)
check(recv(1)[0][:3] == bytes([6, 0x49, 0]), "dial handed back to volume on stop")
check(state()["modules"] == {} and state()["default"].startswith("alsa_output.usb-SteelSeries"),
      "nothing left behind, default output restored")

write_state({"fail_dont_move": True})
a = svc.Audio("alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wired-00.analog-stereo", True, True)
a.build()
check(len(state()["modules"]) == 6, "works on PipeWire versions without *_dont_move")
svc.Audio(a.dac_sink, True, True).build()
check(len(state()["modules"]) == 6, "leftovers from a crashed run cleaned up")
check.done()
