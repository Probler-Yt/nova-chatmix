# Changelog

## 1.0.1

- **Reset DAC button** in the window, and `nova-chatmix --reset-dac`: a software unplug-and-replug for when the GameDAC shows levels on its screen but the headphones stay silent (some units do this on the first boot, on Windows too).
- The GameDAC is kept out of USB power saving. Unplug and replug it once after updating so the new rule applies.
- README: install from source with `makepkg -si` while AUR sign-ups are paused.

## 1.0.0

First release.

- Hardware ChatMix dial for the Arctis Nova Pro Wired GameDAC (Gen 2) on Linux - no SteelSeries GG / Sonar needed.
- Game, Chat and Media outputs on the DAC; the dial balances Game and Chat, Media is never turned down.
- ChatMix window: your open apps (grouped like KDE System Monitor, Steam games under their real names) - drag them into Chat, Media or Game and they stay there.
- Runs as a systemd user service: survives unplugging the DAC and PipeWire restarts, and gives the dial back to plain volume when stopped.

