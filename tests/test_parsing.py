"""pactl output parsing, unit names, Steam ids, DAC detection in sysfs."""
import json
import os
import tempfile

from common import Checks, load_service

svc = load_service()
check = Checks()

TEXT = """Sink Input #187
\tDriver: PipeWire
\tOwner Module: n/a
\tClient: 112
\tSink: 1043
\tCorked: no
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\t        balance 0.00
\tProperties:
\t\tapplication.name = "Firefox"
\t\tapplication.process.binary = "firefox"
\t\tmedia.name = "Tom\\"s \\"weird\\" title\ttab"

Sink Input #201
\tOwner Module: 536870914
\tSink: 52
\tCorked: yes
\tVolume: front-left: 47000 /  72% / -8.65 dB,   front-right: 47000 /  72% / -8.65 dB
\tProperties:
\t\tmedia.name = "loopback-1234-18 output"

Module #536870913
\tName: module-null-sink
\tArgument: sink_name=ChatMix_Game sink_properties=node.description=Game
\tProperties:
\t\tmodule.author = "Wim"
"""
items = svc.parse_pactl_text(TEXT)
check(items[0]["sink"] == 1043 and items[0]["client"] == "112" and items[0]["corked"] is False
      and items[0]["properties"]["application.process.binary"] == "firefox", "text fallback: stream fields")
check(items[1]["owner_module"] == "536870914" and items[1]["volume"]["front-left"]["value_percent"] == "72%",
      "text fallback: loopback owner + volume")
check(items[2]["name"] == "module-null-sink" and "ChatMix_Game" in items[2]["argument"], "text fallback: modules")
check(json.loads('[{"m":"tab\there"}]', strict=False)[0]["m"] == "tab\there", "lenient JSON accepts control chars")
check(svc.stream_key({"application.process.binary": "steamwebhelper"}) == "steam", "Steam's web helper counts as Steam")

units = {
    "app-org.kde.dolphin@0f1b2c3d.service": "org.kde.dolphin",
    "app-discord@autostart.service": "discord",
    r"app-brave\x2dbrowser@4d5e6f.service": "brave-browser",
    "app-flatpak-org.chromium.Chromium-123456.scope": "org.chromium.Chromium",
    "app-org.kde.konsole-98765.scope": "org.kde.konsole",
    "app-gnome-firefox-4321.scope": "firefox",
    r"app-dbus\x2d:1.2\x2dorg.freedesktop.impl.portal.desktop.kwallet@0.service": None,
    "pipewire.service": None,
    "session-2.scope": None,
}
bad = [(u, svc.unit_app_id(u), w) for u, w in units.items() if svc.unit_app_id(u) != w]
check(not bad, f"systemd unit -> app id for KDE, GNOME, Flatpak, autostart, D-Bus {bad or ''}")

# DAC detection: pick interface 4 of the GameDAC, not interface 3 or other SteelSeries gear
root = tempfile.mkdtemp()
for name, pid, intf in (("hidraw3", "12CB", 3), ("hidraw4", "12CB", 4), ("hidraw5", "1836", 2)):
    usb = os.path.join(root, "devices", f"1-4:1.{intf}")
    dev = os.path.join(usb, f"0003:1038:{pid}.000{intf}")
    os.makedirs(dev, exist_ok=True)
    open(os.path.join(usb, "bInterfaceNumber"), "w").write(f"{intf:02x}\n")
    open(os.path.join(dev, "uevent"), "w").write(f"HID_ID=0003:00001038:0000{pid}\n")
    os.makedirs(os.path.join(root, "class", name))
    os.symlink(dev, os.path.join(root, "class", name, "device"))
svc.HIDRAW_SYSFS = os.path.join(root, "class")
check(svc.find_hidraw() == ("/dev/hidraw4", 0x12CB), "finds the GameDAC's control interface")
check(svc.parse_chatmix(bytes([7, 0x45, 64, 100])) == (64, 100) and svc.parse_chatmix(bytes([7, 0x25, 1, 2])) is None,
      "dial reports parsed, others ignored")
check.done()
