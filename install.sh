#!/usr/bin/env bash
# nova-chatmix: install for your user from this folder (any distro).
# On Arch, prefer the AUR package:  yay -S nova-chatmix
#
#   ./install.sh               install / update
#   ./install.sh --uninstall   remove it (keeps your app assignments)
#   ./install.sh --purge       remove it and your app assignments
#
# Puts the programs in ~/.local/bin, the service in ~/.config/systemd/user and
# the app-menu entry in ~/.local/share/applications. Only the udev rule (so the
# service can talk to the DAC without root) needs sudo.
set -euo pipefail
cd "$(dirname "$0")"

BINDIR="$HOME/.local/bin"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
RULES_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/nova-chatmix"
UDEV_RULE="/etc/udev/rules.d/70-nova-chatmix.rules"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
ask() {  # prompt on the terminal even if stdin is a pipe; fall back to the default
  local a=""
  if { exec 3</dev/tty; } 2>/dev/null; then read -r -p "$1 " a <&3 || a=""; exec 3<&-; fi
  echo "${a:-$2}"
}
reload_udev() {
  sudo udevadm control --reload-rules || true
  sudo udevadm trigger --subsystem-match=hidraw --action=add || true
  sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=1038 --action=change || true
}

if [[ "${1:-}" == "--uninstall" || "${1:-}" == "--purge" ]]; then
  say "Removing nova-chatmix"
  systemctl --user disable --now nova-chatmix.service 2>/dev/null || true
  pkill -f nova-chatmix-gui 2>/dev/null || true
  rm -f "$BINDIR/nova-chatmix" "$BINDIR/nova-chatmix-gui" "$UNIT_DIR/nova-chatmix.service" \
        "$APP_DIR/nova-chatmix.desktop"
  systemctl --user daemon-reload
  if [[ -f "$UDEV_RULE" ]]; then sudo rm -f "$UDEV_RULE"; reload_udev; fi
  [[ "$1" == "--purge" ]] && rm -rf "$RULES_DIR"
  say "Done. Your DAC dial is back to plain volume."
  exit 0
fi

for cmd in python3 pactl systemctl; do
  command -v "$cmd" >/dev/null || { warn "missing '$cmd' - install it first"; exit 1; }
done
python3 -c "import PySide6" 2>/dev/null || warn "PySide6 isn't installed: the service will work, but the ChatMix window won't open until you install it (python3-pyside6 / pyside6)."

# Other Arctis tools grab the DAC too; only one can drive the dial.
mapfile -t OTHERS < <( { systemctl --user list-unit-files --no-legend --state=enabled 'arctis*'
                         systemctl --user list-units --no-legend --state=active 'arctis*'; } 2>/dev/null \
                       | awk '{print $1}' | sort -u)
if (( ${#OTHERS[@]} )); then
  warn "Another Arctis app is running (${OTHERS[*]}). It fights nova-chatmix for the DAC."
  if [[ "$(ask 'Stop and disable it? [Y/n]' y)" =~ ^[Yy] ]]; then
    for u in "${OTHERS[@]}"; do systemctl --user disable --now "$u" 2>/dev/null || true; done
  fi
fi

say "Installing to $BINDIR"
install -Dm755 src/nova-chatmix.py "$BINDIR/nova-chatmix"
install -Dm755 src/nova-chatmix-gui.py "$BINDIR/nova-chatmix-gui"
mkdir -p "$UNIT_DIR" "$APP_DIR"
sed "s|@BINDIR@|$BINDIR|g" data/nova-chatmix.service > "$UNIT_DIR/nova-chatmix.service"
sed "s|@BINDIR@|$BINDIR|g" data/nova-chatmix.desktop > "$APP_DIR/nova-chatmix.desktop"

say "Installing the udev rule (needs sudo once)"
sudo install -Dm644 data/70-nova-chatmix.rules "$UDEV_RULE"
reload_udev

systemctl --user daemon-reload
systemctl --user enable nova-chatmix.service >/dev/null
systemctl --user restart nova-chatmix.service

cat <<'EOF'

  Done. Unplug the GameDAC's USB cable and plug it back in once.

  Then open "ChatMix" from your app menu and drag apps into Chat, Media or Game.
  Turn the DAC dial to balance Game and Chat; press it to flip back to volume.

  Logs:  journalctl --user -u nova-chatmix -f
EOF
