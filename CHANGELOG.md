# Changelog

## 1.3.1

- **Fixes the dial going dead** (pressing it no longer switching to ChatMix, turning it doing nothing) after a replug or a DAC reset. ChatMix is now switched on the way SteelSeries GG does it: the DAC is asked for its status and firmware version first, and the ChatMix switch gets a quiet second either side, without which the DAC can accept it and still ignore the dial. The firmware version is shown in the service log.
- `scripts/dac-probe.py`: checks how the GameDAC responds, for bug reports.

## 1.3.0

- **A new window, laid out like Sonar.** Tabs along the top: Mixer, Chat, Media, Game. The Mixer page has the drag-and-drop lanes plus a gauge showing where the dial sits. Each channel page shows its preset, an EQ on/off switch, 9 favourite slots and a large curve.
- **A proper preset browser.** Slides up over the channel page, with search and categories: Your games (presets for games installed on Steam), Games, Pro players, General, Music, Movies and shows, Voice, My presets and Favourites. Every preset shows its curve and a short description of what it does. Click to use a preset, star it to pin it.
- **Suggestions:** when a game with its own Sonar preset is playing, the Game page offers it.
- Switching the EQ off remembers the preset, so switching it back on restores it.
- Imported preset files are kept in My presets.
- New look: a dark theme with each channel in its own colour.
- Reset the GameDAC moved to the ⋯ menu.
- install.sh won't install a second copy over the AUR/pacman package (a copy in your home folder overrides the package).

## 1.2.0

- **Media on the dial.** By default (Auto), Media follows the Chat side of the dial whenever nothing is playing in Chat, so solo you balance your game against YouTube with the dial; as soon as anything plays in Chat (a call) it's Game vs Chat again and Media goes back to full. Switch it to always or never with the new Dial button on the Media column.

## 1.1.0

- **EQ per output, with SteelSeries Sonar presets.** Each of Game, Chat and Media can have its own EQ: pick from Sonar's presets (hundreds of games plus music, movie and chat ones) with a search box and a curve preview, or load a Sonar preset file. Measured through PipeWire, the result lands within a few hundredths of a dB of Sonar's curves (0.3 dB at worst), can't clip (an automatic preamp), and adds no delay: the EQ path skips the plain loopback's 20 ms buffer.
- **Fix: crash recovery could unload another app's audio module.** When the DAC disappeared, PipeWire freed the service's module numbers and something else could reuse them before cleanup. The service now only unloads modules it can confirm are its own, and reads module numbers in a way that works with every pactl version (pactl 16's JSON leaves them out, so cleanup after a crash silently did nothing).
- Tests now also run against real PipeWire (in a throwaway headless session), on GitHub's CI too.

## 1.0.2

- **Fix: ChatMix stopped working after Reset DAC.** A reset makes the GameDAC forget ChatMix mode, and its audio blinks away for a moment, which silently dropped the links from Game/Chat/Media into it. The service now checks every 2 seconds that the outputs are really connected to the DAC, reconnects them if not, and switches the dial back to ChatMix.

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

