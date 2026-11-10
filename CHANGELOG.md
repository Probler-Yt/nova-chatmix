# Changelog

## 2.1.0

- **The dial works on your speakers too.** Switch to other speakers and your apps now stay in Game, Chat and Media, played out of the speakers instead of the GameDAC, so turning the dial still turns the game up and the video or Discord down. Apps that open while you're on the speakers go into Game (or wherever your rules put them). The EQs are made for the headset, so they pause on the speakers and come back when you switch back.
- Prefer the old way, where ChatMix lets go of everything on the speakers? Untick output menu > *The dial works on speakers too* (setting `speaker_dial`). It switches over straight away.

## 2.0.0

- **Liquid Glass look, now the default.** Frosted glass panels over your desktop wallpaper, with rims that bend what's behind them, a glass tab bar, and lanes that brighten under the pointer and glow in the channel's colour while you drag an app over them. Nothing is blurred live: the backdrop and its frosted copy are painted once per window size and each panel's bent rim once per size and place, so it costs about the same as the old look once it's on screen.
- **Your wallpaper behind the glass.** ChatMix reads the wallpaper from KDE Plasma (the main screen's; wallpaper packages pick their largest picture) or GNOME, softens and dims it so text stays readable, and updates when you change it. ⋯ > *Look* > *ChatMix colours* swaps it for ChatMix's own ribbons in the Chat, Media and Game colours, which is also what you get if no wallpaper can be found.
- **The original look is still there:** ⋯ > *Look* > *Classic (lightest)*. ChatMix remembers which one you picked.

## 1.5.0

- **A global shortcut for headset / speakers, set from ChatMix.** Output menu > *Set a shortcut…*, press the keys (or a macro pad key) and save. ChatMix registers it with KDE's own shortcut service, so it works everywhere, even with the window closed, survives reboots, and shows up in System Settings > Shortcuts > ChatMix. While you're recording, KDE's other shortcuts are paused so pressing one records it, and a key that's already taken is refused with the name of what uses it.
- **KDE's on-screen popup** shows "Headset" or your speakers' name when the shortcut switches.
- **Hide outputs you never use:** output menu > *Show in this list*. Hidden outputs are left out of the menu and skipped by the shortcut.
- ChatMix's app entry has *Switch headset / speakers*, *Play through the headset* and *Play through the speakers* actions (right-click it in the app launcher).

## 1.4.2

- Fixes the Update button failing with "No such file or directory: …/packaging/aur/PKGBUILD": release downloads left out the package recipe the updater builds from. They include it again, and the updater fetches it from the release's tag if it's ever missing.

## 1.4.1

- **Headset / speakers switching, like Sonar's hotkey.** `nova-chatmix --output toggle` switches between the headset and your speakers; put it on a keyboard shortcut or a macro pad key. `--output headset` and `--output speakers` go one way. "Speakers" is whichever other output you last used. ChatMix knows what counts as the headset, so it never fights you: a home-made switch script that checks for the GameDAC's own name sees "Game" instead, switches to the GameDAC, and ChatMix moves it straight back to Game.
- The window's top bar shows where your sound is playing; click it to switch.

## 1.4.0

- **Update button.** The window checks GitHub for a new version (twice a day, or from ⋯ > Check for updates) and shows an *Update to x.y* button. One click downloads the release and installs it the way ChatMix was installed: rebuilt as a pacman package (you're asked for your password once) or copied into `~/.local/bin` for an install.sh copy. Then it restarts the service and reopens the window.
- New versions are released automatically when their tests pass, so the button sees them straight away. `scripts/publish.sh` is now only needed for the AUR.
- The ⋯ menu shows which version you're running.

## 1.3.2

- **Switching to your speakers works properly.** Pick any other output in your system's sound settings (or with a macro pad, `pactl set-default-sink`...) and ChatMix lets go: every app it was holding in Game, Chat or Media, YouTube in Media included, moves to that output, and anything that starts meanwhile goes there too. Pick the GameDAC again and they all go back to their channels, with Game as the default. If you're already on speakers when ChatMix starts, it leaves them alone. The window shows a note while you're on another output.

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

