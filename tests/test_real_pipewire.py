"""The real service against a real (headless) PipeWire: outputs, dial, DAC resets, EQ.

The GameDAC's controls are faked over a socket; everything audio is real.
Skips if PipeWire isn't installed. The EQ accuracy check needs numpy + scipy.
"""
import json
import logging
import os
import subprocess
import threading
import time
import types

import realpw

root = realpw.start()
from common import Checks, fake_dac, load_service  # noqa: E402

svc = load_service()
svc.STATE_PATH = os.path.join(root, "run", "nova-chatmix", "state.json")
svc.RULES_PATH = os.path.join(root, "config", "nova-chatmix", "rules.json")
os.makedirs(os.path.dirname(svc.RULES_PATH))
logging.basicConfig(level=logging.WARNING)
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = svc.eq_from_sonar(json.load(open(os.path.join(HERE, "sample_preset.json"))))
BASS = [{"type": "bq_lowshelf", "freq": 120.0, "q": 0.707, "gain": 6.0}]
check = Checks()


def pj(kind):
    return json.loads(subprocess.run(["pactl", "-f", "json", "list", kind], capture_output=True, text=True).stdout)


def wait(cond, secs=10):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def save_eq(eq):
    tmp = svc.RULES_PATH + ".tmp"
    json.dump({"apps": {}, "eq": eq}, open(tmp, "w"))
    os.replace(tmp, svc.RULES_PATH)


def vol(si):
    return list(si["volume"].values())[0]["value_percent"]


peer = {}
fake_dac(svc, peer)
DEV = os.path.join(root, "hidraw")
open(DEV, "w").close()
svc.find_hidraw = lambda: (DEV, 0x12CB) if os.path.exists(DEV) else (None, None)
d = svc.Daemon(types.SimpleNamespace(sink=None, no_media=False, no_default=False, boot_wait=0.3, verbose=False))
t = threading.Thread(target=d.run, daemon=True)
t.start()

# --- outputs and the dial
check(wait(lambda: d.audio and d.audio.healthy() and "sock" in peer and d.dac is not None),
      "Game, Chat and Media built and linked to the DAC")
peer["sock"].send(bytes([7, 0x45, 100, 25]) + bytes(12))
check(wait(lambda: vol(next(s for s in pj("sink-inputs") if str(s["owner_module"]) == str(d.audio.loop_ids["chat"]))) == "25%"),
      "turning the dial turns Chat down")

# --- Media on the dial: follows Chat's side while Chat is quiet (default "auto")
def media_vol():
    return vol(next(s for s in pj("sink-inputs") if str(s["owner_module"]) == str(d.audio.loop_ids["media"])))


def save_settings(**kw):
    tmp = svc.RULES_PATH + ".tmp"
    json.dump(dict({"apps": {}}, **kw), open(tmp, "w"))
    os.replace(tmp, svc.RULES_PATH)


check(wait(lambda: media_vol() == "25%"), "solo (nothing in Chat): Media follows the dial too")
import wave  # noqa: E402
with wave.open(os.path.join(root, "call.wav"), "wb") as w:  # 6 s of silence: stands in for a voice call
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(48000)
    w.writeframes(bytes(48000 * 6 * 4))
call = subprocess.Popen(["pw-play", "--target", "ChatMix_Chat", os.path.join(root, "call.wav")])
check(wait(lambda: media_vol() == "100%", 5), "a call starts in Chat: Media goes back to full by itself")
call.wait()
check(wait(lambda: media_vol() == "25%", 5), "call over: Media is back on the dial")
save_settings(media_dial="never")
check(wait(lambda: media_vol() == "100%"), "'never': Media stays at full")
save_settings(media_dial="always")
call = subprocess.Popen(["pw-play", "--target", "ChatMix_Chat", os.path.join(root, "call.wav")])
time.sleep(1.5)
check(media_vol() == "25%", "'always': Media follows the dial even during a call")
call.kill()
save_settings()
check(wait(lambda: media_vol() == "25%" and json.load(open(svc.STATE_PATH)).get("media_dial") == "auto"),
      "back to auto (the default); the window can see the mode")

# --- the DAC blinks: reconnect + ChatMix back on
peer["sock"].settimeout(0.2)
try:
    while True:
        peer["sock"].recv(64)
except OSError:
    pass
realpw.blink_dac()
check(wait(lambda: d.audio and d.audio.healthy(), 15), "after the DAC blinks, everything reconnects")
peer["sock"].settimeout(5)
got = []
try:
    for _ in range(4):
        got.append(peer["sock"].recv(64))
except OSError:
    pass
check(any(g[:3] == bytes([6, 0x49, 1]) for g in got), "...and ChatMix is switched back on")

# --- EQ on, switch, off, fallback
save_eq({"game": {"name": "Sample", "filters": SAMPLE}})
check(wait(lambda: "game" in d.audio.chains and d.audio.healthy()), "EQ on Game")
eq_si = lambda key: next((s for s in pj("sink-inputs") if s["properties"].get("node.name") == f"nova_chatmix_eq_{key}"), None)  # noqa: E731
peer["sock"].send(bytes([7, 0x45, 40, 100]) + bytes(12))
check(wait(lambda: vol(eq_si("game")) == "40%"), "the dial works on an EQ'd output")
chat_loop = d.audio.loop_ids.get("chat")
save_eq({"game": {"name": "Bass", "filters": BASS}, "media": {"name": "Sample", "filters": SAMPLE}})
game_conf = os.path.join(os.path.dirname(svc.STATE_PATH), "eq-game.conf")
check(wait(lambda: "media" in d.audio.chains and d.audio.healthy()
           and open(game_conf).read().count("type = builtin") == 2), "switching presets live")
check(d.audio.loop_ids.get("chat") == chat_loop, "outputs whose EQ didn't change are left alone")
check(wait(lambda: vol(eq_si("game")) == "40%"), "dial position survives a preset change")
save_eq({})
check(wait(lambda: not d.audio.chains and d.audio.healthy()), "EQ off everywhere: back to plain links")

# --- accuracy: play noise through the sample EQ, record the DAC, compare with the maths
try:
    import numpy as np
    from scipy import signal
    from scipy.io import wavfile
except ImportError:
    np = None
    print("SKIP EQ accuracy measurement (needs numpy and scipy)")
if np is not None:
    save_eq({"game": {"name": "Sample", "filters": SAMPLE}})
    peer["sock"].send(bytes([7, 0x45, 100, 100]) + bytes(12))
    wait(lambda: "game" in d.audio.chains and d.audio.healthy() and vol(eq_si("game")) == "100%")
    rate = 48000
    x = (np.random.default_rng(7).standard_normal((rate * 6, 2)) * 0.25).clip(-1, 1).astype(np.float32)
    wavfile.write(os.path.join(root, "in.wav"), rate, x)
    import warnings

    def measure():
        """One recording -> (worst error dB, min coherence, peak). Coherence < 1 means the
        recording itself glitched (dropped buffers on a busy machine), not the EQ."""
        out = os.path.join(root, "out.wav")
        rec = subprocess.Popen(["pw-record", "--target", realpw.DAC, "-P", "stream.capture.sink=true", "--rate",
                                str(rate), "--channels", "2", "--format", "f32", out], stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        subprocess.run(["pw-play", "--target", "ChatMix_Game", os.path.join(root, "in.wav")], check=True)
        time.sleep(0.8)
        rec.terminate()
        rec.wait()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _, y = wavfile.read(out)
        y = y.astype(np.float64)
        lag = int(np.argmax(np.abs(signal.correlate(y[:, 0], x[:, 0].astype(np.float64), mode="valid", method="fft"))))
        seg = y[lag:lag + len(x)]
        worst, coh_min = 0.0, 1.0
        for ch in (0, 1):
            f, pxy = signal.csd(x[:, ch], seg[:, ch], fs=rate, nperseg=16384)
            _, pxx = signal.welch(x[:, ch], fs=rate, nperseg=16384)
            _, pyy = signal.welch(seg[:, ch], fs=rate, nperseg=16384)
            band = (f >= 30) & (f <= 18000)
            meas = 20 * np.log10(np.abs(pxy / pxx))[band]
            target = np.array(svc.eq_response_db(SAMPLE, f[band])) + svc.eq_preamp_db(SAMPLE)
            audible = target > -30  # inside the deliberate notch there's nothing left to measure
            worst = max(worst, float(np.max(np.abs(meas - target)[audible])))
            coh_min = min(coh_min, float(np.min((np.abs(pxy) ** 2 / (pxx * pyy))[band][audible])))
        return worst, coh_min, float(np.max(np.abs(y)))

    clean = None
    for attempt in range(5):
        worst, coh, peak = measure()
        if coh > 0.95:
            clean = (worst, peak)
            break
        print(f"     (recording glitched, coherence {coh:.2f} - measuring again)")
    notch = svc.eq_response_db(SAMPLE, [6000.0])[0]
    check(notch < -40, f"the notch filter really notches ({notch:.0f} dB at 6 kHz)")
    if clean is None:
        print("SKIP EQ accuracy: this machine kept dropping audio while recording")
    else:
        worst, peak = clean
        check(worst < 0.5, f"measured EQ matches the preset curve within {worst:.2f} dB (30 Hz-18 kHz, both channels)")
        check(peak < 1.0, f"no clipping (peak {20 * np.log10(peak):+.1f} dBFS from full-scale noise)")

# --- EQ can't start: audio still plays, the window is told
real_which = svc.shutil.which
svc.shutil.which = lambda name: None if name == "pipewire" else real_which(name)
save_eq({"chat": {"name": "Sample", "filters": SAMPLE}})
check(wait(lambda: "chat" in d.audio.loop_ids and not d.audio.chains and d.audio.healthy()),
      "if an EQ can't start, that output plays without it (never silence)")
check(wait(lambda: json.load(open(svc.STATE_PATH))["eq"]["chat"]["active"] is False), "...and reports it isn't active")
svc.shutil.which = real_which

d.stop()
t.join(10)
check(not [x for x in subprocess.run(["pactl", "list", "modules", "short"], capture_output=True, text=True).stdout.splitlines()
           if "ChatMix_" in x], "stopping leaves nothing behind")
check(subprocess.run(["pgrep", "-f", f"pipewire -c {os.path.dirname(svc.STATE_PATH)}"], capture_output=True).returncode == 1,
      "no EQ processes left")
check.done()
