# Changelog

## 1.0.1

- **Headphones going silent until the DAC is replugged:** the GameDAC is now kept out of USB power saving, which can leave it showing levels on its screen while sending nothing to the headphones. (Unplug and replug it once after updating so the new rule applies.)
- **Reset DAC button** in the window, and `nova-chatmix --reset-dac`: a software unplug-and-replug for when the headphones do go quiet.
- README: install from source with `makepkg -si` while AUR sign-ups are paused.

## 1.0.0

First release.

- Hardware ChatMix dial for the Arctis Nova Pro Wired GameDAC (Gen 2) on Linux - no SteelSeries GG / Sonar needed.
- Game, Chat and Media outputs on the DAC; the dial balances Game and Chat, Media is never turned down.
- ChatMix window: your open apps (grouped like KDE System Monitor, Steam games under their real names) - drag them into Chat, Media or Game and they stay there.
- Runs as a systemd user service: survives unplugging the DAC and PipeWire restarts, and gives the dial back to plain volume when stopped.

