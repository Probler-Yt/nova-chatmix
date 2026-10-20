"""A fake KDE Plasma session on disk: /proc (with cgroups and environments),
installed apps, autostart entries and a Steam library - modelled on a real
CachyOS desktop running THE FINALS through Proton."""

import os

U = "/user.slice/user-1000.slice/user@1000.service"
STEAM_UNIT = f"{U}/app.slice/app-steam@autostart.service"
FINALS = "2073850"
DAC = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wired-00.analog-stereo"

APPS = {  # desktop id: lines
    "steam": ["Name=Steam", "Exec=/usr/bin/steam %U", "Icon=steam"],
    # Steam's per-game shortcuts all launch `steam` - they must not hijack Steam itself
    "Assetto Corsa EVO": ["Name=Assetto Corsa EVO", "Exec=steam steam://rungameid/3058630",
                          "Icon=steam_icon_3058630"],
    "The Finals": ["Name=The Finals", "Exec=steam steam://rungameid/2073850",
                   "Icon=steam_icon_2073850"],
    "brave-browser": ["Name=Brave", "Exec=brave %U", "Icon=brave-browser"],
    "discord": ["Name=Discord", "Exec=/usr/bin/discord", "Icon=discord"],
    "notion-app": ["Name=Notion", "Exec=notion-app %U", "Icon=notion-app"],
    "org.kde.plasma-systemmonitor": ["Name=System Monitor", "Exec=plasma-systemmonitor",
                                     "Icon=utilities-system-monitor"],
    "org.kde.dolphin": ["Name=Dolphin", "Exec=dolphin %u", "Icon=system-file-manager"],
    "org.kde.discover": ["Name=Discover", "Exec=plasma-discover %F", "Icon=plasmadiscover"],
    "org.kde.kdeconnect.daemon": ["Name=KDE Connect", "Exec=/usr/lib/kdeconnectd", "NoDisplay=true"],
    "nova-chatmix": ["Name=ChatMix", "Exec=/usr/bin/nova-chatmix-gui"],
}
AUTOSTART = {
    "macropad-actions": ["Name=MacroPad actions", "Exec=/home/p/.local/bin/macropad-actions"],
    "limine-snapper-notify": ["Name=Limine-snapper-notify", "Exec=limine-snapper-notify"],
    "shelly": ["Name=Shelly", "Exec=shelly-tray", "Icon=shelly"],
    "cachy-update-tray": ["Name=Cachy-Update Systray Applet", "Exec=cachy-update --tray"],
}
PROTON = "/x/steamapps/common/Proton - Experimental/files/bin"
PROCS = [  # pid, cgroup, comm, exe, steam app id, argv
    (900, f"{U}/session.slice/plasma-plasmashell.service", "plasmashell", "/usr/bin/plasmashell", "", ["plasmashell"]),
    (901, f"{U}/session.slice/plasma-kwin_wayland.service", "kwin_wayland", "/usr/bin/kwin_wayland", "", ["kwin_wayland"]),
    (1000, STEAM_UNIT, "steam", "/home/p/.local/share/Steam/ubuntu12_32/steam", "", ["steam"]),
    (1001, STEAM_UNIT, "steamwebhelper", "/home/p/.local/share/Steam/ubuntu12_64/steamwebhelper", "", ["./steamwebhelper"]),
    (1010, STEAM_UNIT, "reaper", "/home/p/.local/share/Steam/ubuntu12_32/reaper", FINALS, ["reaper", "SteamLaunch"]),
    (1011, STEAM_UNIT, "pressure-vessel", "/x/steamapps/common/SteamLinuxRuntime_4/pv-adverb", FINALS, ["pv-adverb"]),
    (1012, STEAM_UNIT, "wineserver", f"{PROTON}/wineserver", FINALS, ["wineserver"]),
    (1013, STEAM_UNIT, "Discovery.exe", f"{PROTON}/wine64-preloader", FINALS,
     ["Z:\\home\\p\\.local\\share\\Steam\\steamapps\\common\\THE FINALS\\Discovery.exe"]),
    (1014, STEAM_UNIT, "denuvo-anti-ch", f"{PROTON}/wine64-preloader", FINALS,
     ["C:\\Program Files\\Denuvo\\denuvo-anti-cheat-update-service.exe"]),
    (1015, STEAM_UNIT, "explorer.exe", f"{PROTON}/wine64-preloader", FINALS, ["C:\\windows\\system32\\explorer.exe"]),
    (1020, f"{U}/app.slice/app-brave\\x2dbrowser@1a2b3c.service", "brave", "/opt/brave.com/brave/brave", "", ["brave"]),
    (1021, f"{U}/app.slice/app-brave\\x2dbrowser@1a2b3c.service", "brave", "/opt/brave.com/brave/brave", "", ["brave", "--type=renderer"]),
    (1030, f"{U}/app.slice/app-discord@autostart.service", "Discord", "/opt/discord/Discord", "", ["/opt/discord/Discord"]),
    (1031, f"{U}/app.slice/app-discord@autostart.service", "chrome_crashpad", "/opt/discord/chrome_crashpad_handler", "", ["crashpad"]),
    (1040, f"{U}/app.slice/app-notion\\x2dapp@5d6e.service", "notion-app", "/usr/lib/notion-app/notion-app", "", ["notion-app"]),
    (1050, f"{U}/app.slice/app-flatpak-org.chromium.Chromium-445566.scope", "chrome", "/app/chromium/chrome", "", ["chrome"]),
    (1060, f"{U}/app.slice/app-org.kde.plasma\\x2dsystemmonitor@77aa.service", "plasma-systemmo", "/usr/bin/plasma-systemmonitor", "", ["plasma-systemmonitor"]),
    (1070, f"{U}/app.slice/app-macropad\\x2dactions@autostart.service", "python3", "/usr/bin/python3.13", "", ["python3", "macropad-actions"]),
    (1080, f"{U}/app.slice/app-limine\\x2dsnapper\\x2dnotify@autostart.service", "limine-snapper", "/usr/bin/limine-snapper-notify", "", ["limine-snapper-notify"]),
    (1090, f"{U}/app.slice/app-dbus\\x2d:1.2\\x2dorg.freedesktop.impl.portal.desktop.kwallet@0.service", "ksecretd", "/usr/bin/ksecretd", "", ["ksecretd"]),
    (1100, f"{U}/app.slice/app-shelly@autostart.service", "shelly-tray", "/usr/bin/shelly-tray", "", ["shelly-tray"]),
    (1110, f"{U}/app.slice/app-cachy\\x2dupdate\\x2dtray@autostart.service", "cachy-update", "/usr/bin/python3.13", "", ["python3", "cachy-update"]),
    (1120, f"{U}/app.slice/app-org.kde.dolphin@99bb.service", "dolphin", "/usr/bin/dolphin", "", ["dolphin"]),
    (1130, f"{U}/app.slice/app-org.kde.discover@12cd.service", "plasma-discover", "/usr/bin/plasma-discover", "", ["plasma-discover"]),
    (1140, f"{U}/app.slice/app-org.kde.kdeconnect.daemon@autostart.service", "kdeconnectd", "/usr/lib/kdeconnectd", "", ["kdeconnectd"]),
    (1150, f"{U}/app.slice/app-nova\\x2dchatmix@ab12.service", "python3", "/usr/bin/python3.13", "", ["python3", "nova-chatmix-gui"]),
    (1160, f"{U}/app.slice/nova-chatmix.service", "python3", "/usr/bin/python3.13", "", ["python3", "nova-chatmix"]),
]
OTHER_USER_PID = 1200


def _desktop(path, lines):
    with open(path, "w") as f:
        f.write("[Desktop Entry]\nType=Application\n" + "\n".join(lines) + "\n")


def build(root):
    """Create the session under `root`; returns dict of paths."""
    p = {k: os.path.join(root, k) for k in ("proc", "apps", "autostart", "steam", "lib2")}
    for d in p.values():
        os.makedirs(d, exist_ok=True)
    for i, lines in APPS.items():
        _desktop(os.path.join(p["apps"], i + ".desktop"), lines)
    for i, lines in AUTOSTART.items():
        _desktop(os.path.join(p["autostart"], i + ".desktop"), lines)
    os.makedirs(os.path.join(p["steam"], "steamapps"), exist_ok=True)
    os.makedirs(os.path.join(p["lib2"], "steamapps"), exist_ok=True)
    with open(os.path.join(p["steam"], "steamapps", "libraryfolders.vdf"), "w") as f:
        f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                % (p["steam"], p["lib2"]))
    with open(os.path.join(p["steam"], "steamapps", f"appmanifest_{FINALS}.acf"), "w") as f:
        f.write(f'"AppState"\n{{\n\t"appid"\t\t"{FINALS}"\n\t"name"\t\t"THE FINALS"\n}}\n')
    with open(os.path.join(p["lib2"], "steamapps", "appmanifest_3058630.acf"), "w") as f:
        f.write('"AppState"\n{\n\t"appid"\t\t"3058630"\n\t"name"\t\t"Assetto Corsa EVO"\n}\n')
    procs = PROCS + [(OTHER_USER_PID, "/user.slice/user-1001.slice/session-3.scope", "brave",
                      "/opt/brave.com/brave/brave", "", ["brave"])]
    for pid, cg, comm, exe, appid, argv in procs:
        d = os.path.join(p["proc"], str(pid))
        os.makedirs(d)
        with open(os.path.join(d, "cgroup"), "w") as f:
            f.write(f"0::{cg}\n")
        with open(os.path.join(d, "comm"), "w") as f:
            f.write(comm + "\n")
        os.symlink(exe, os.path.join(d, "exe"))
        env = b"HOME=/home/p\0"
        if appid:
            env += f"SteamAppId={appid}\0STEAM_COMPAT_APP_ID={appid}\0".encode()
        with open(os.path.join(d, "environ"), "wb") as f:
            f.write(env)
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"".join(a.encode() + b"\0" for a in argv))
    p["other_user_ok"] = False
    if os.geteuid() == 0:  # only root can fake another user's process
        for dirpath, _, files in os.walk(os.path.join(p["proc"], str(OTHER_USER_PID))):
            os.chown(dirpath, 1001, 1001)
        p["other_user_ok"] = True
    return p
