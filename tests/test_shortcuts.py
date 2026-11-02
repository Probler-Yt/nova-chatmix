"""Headset / speakers switching: the global shortcut (through KDE's shortcut
service, faked here with gdbus), hiding outputs, and the on-screen popup."""
import configparser
import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from common import FAKES, ROOT, SRC, Checks, fake_pactl, load, read_state, write_state  # noqa: E402

try:
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QApplication
except ImportError:
    print("SKIP PySide6 not installed")
    sys.exit(0)

tmp = fake_pactl("full")
write_state({})
os.symlink(os.path.join(FAKES, "gdbus.py"), os.path.join(os.path.dirname(os.environ["FAKE_PACTL_STATE"]), "bin", "gdbus"))
os.environ["FAKE_GDBUS_STATE"] = os.path.join(tmp, "gdbus.json")
gui = load(os.path.join(SRC, "nova-chatmix-gui.py"), "nova_chatmix_gui_shortcut_test")
svc = gui.svc
os.makedirs(os.path.dirname(svc.RULES_PATH))
check = Checks()
SPK = "alsa_output.pci-0000_0c_00.4.analog-stereo"


def kde():
    return json.load(open(os.environ["FAKE_GDBUS_STATE"]))


# --- the desktop file: what KDE runs when the shortcut is pressed
desk = configparser.ConfigParser(interpolation=None, strict=False)
desk.optionxform = str
desk.read(os.path.join(ROOT, "data", "nova-chatmix.desktop"))
check(desk["Desktop Entry"]["Actions"].split(";")[:3] == ["switch-output", "headset", "speakers"] and
      desk["Desktop Action switch-output"]["Exec"] == "@BINDIR@/nova-chatmix --output toggle",
      "ChatMix's app entry has a 'Switch headset / speakers' action for KDE's shortcuts")

# --- setting the shortcut
key = QKeySequence("Ctrl+Alt+F12")[0].toCombined()
check(gui.get_shortcut() == 0, "no shortcut to start with")
gui.set_shortcut(key)
log = kde()["log"]
check(any(entry.startswith("org.kde.KGlobalAccel.doRegister") for entry in log),
      "first time: ChatMix's actions are registered with KDE")
check(gui.get_shortcut() == key and kde()["keys"]["nova-chatmix.desktop/switch-output"] == [key],
      "shortcut saved in KDE, working straight away (not after the next login)")
n = len(kde()["log"])
gui.set_shortcut(QKeySequence("Meta+F9")[0].toCombined())
check(not any(e.startswith("org.kde.KGlobalAccel.doRegister") for e in kde()["log"][n:]) and
      gui.get_shortcut() == QKeySequence("Meta+F9")[0].toCombined(), "changing it later")
st = kde()
taken = QKeySequence("Meta+W")[0].toCombined()
st["taken"][str(taken)] = ["Overview", "Toggle Overview", "kwin", "KWin"]
json.dump(st, open(os.environ["FAKE_GDBUS_STATE"], "w"))
try:
    gui.set_shortcut(taken)
    check(False, "a key that's already used elsewhere is refused")
except gui.ShortcutError as e:
    check("Toggle Overview" in str(e), f"a key that's already used elsewhere is refused: {e}")
gui.set_shortcut(0)
check(gui.get_shortcut() == 0, "removing it")

# --- the dialog
app = QApplication(sys.argv)
w = gui.Window()
dlg = gui.ShortcutDialog(w)
check(kde().get("blocked") is True, "while recording, KDE's shortcuts are paused (so pressing one records it)")
dlg.edit.setKeySequence(QKeySequence("Ctrl+Alt+F12"))
dlg.save_btn.click()
check(dlg.result() == 1 and gui.get_shortcut() == key, "dialog: press the keys, Save")
check(kde().get("blocked") is False, "...and KDE's shortcuts are back on")
w._fill_outputs()
labels = [a.text() for a in w.output_menu.actions()]
check(any(t.startswith("Shortcut: ") and "F12" in t for t in labels), f"output menu shows the shortcut: {labels}")

# --- hiding outputs
show_menu = next(a.menu() for a in w.output_menu.actions() if a.menu())
check([a.text() for a in show_menu.actions()] == ["Starship/Matisse HD Audio Analog Stereo"],
      "'Show in this list' lists every output except the headset")
next(a for a in show_menu.actions()).trigger()
check(svc.hidden_outputs() == {SPK} and SPK not in [o[0] for o in svc.list_outputs()],
      "unticking hides it from the list")
w._fill_outputs()
check("Starship/Matisse HD Audio Analog Stereo" not in [a.text() for a in w.output_menu.actions()],
      "...and from the output menu")
try:
    svc.choose_output("speakers")
    check(False, "the toggle skips hidden outputs")
except RuntimeError as e:
    check("no other output" in str(e), f"the toggle skips hidden outputs: {e}")
w.set_output_shown(SPK, True)
check(svc.hidden_outputs() == set(), "ticking it brings it back")

# --- what the shortcut runs: switch + KDE's on-screen popup
svc_bin = os.path.join(SRC, "nova-chatmix.py")
r = subprocess.run([sys.executable, svc_bin, "--output", "toggle"], capture_output=True, text=True)
osd = [e for e in kde()["log"] if e.startswith("org.kde.osdService.showText")]
check(r.returncode == 0 and read_state()["default"] == SPK and osd and "Starship" in osd[-1],
      f"pressing it: speakers, with KDE's popup saying so ({osd[-1] if osd else None})")
r = subprocess.run([sys.executable, svc_bin, "--output", "toggle"], capture_output=True, text=True)
osd = [e for e in kde()["log"] if e.startswith("org.kde.osdService.showText")]
check(read_state()["default"] in ("ChatMix_Game", "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wired-00.analog-stereo")
      and osd[-1].endswith("'Headset'"), f"pressing it again: headset ({osd[-1]})")
w.close()
check.done()
