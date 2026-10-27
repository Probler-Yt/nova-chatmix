"""EQ maths and preset handling (no audio needed)."""
import json
import math
import os

from common import Checks, load_service

svc = load_service()
check = Checks()
HERE = os.path.dirname(os.path.abspath(__file__))
preset = json.load(open(os.path.join(HERE, "sample_preset.json")))


def at(filters, hz):
    return svc.eq_response_db(filters, [hz])[0]


# --- the filters themselves (RBJ cookbook, as PipeWire computes them)
pk = [{"type": "bq_peaking", "freq": 1000.0, "q": 2.0, "gain": 6.0}]
check(abs(at(pk, 1000) - 6.0) < 1e-6, "a +6 dB bell is exactly +6 dB at its centre")
check(abs(at(pk, 50)) < 0.05 and abs(at(pk, 18000)) < 0.1, "...and flat far away from it")
ls = [{"type": "bq_lowshelf", "freq": 100.0, "q": 0.707, "gain": 4.0}]
check(abs(at(ls, 20) - 4.0) < 0.1 and abs(at(ls, 5000)) < 0.05, "low shelf: +4 dB in the bass, flat in the treble")
hs = [{"type": "bq_highshelf", "freq": 8000.0, "q": 0.707, "gain": -5.0}]
check(abs(at(hs, 20000) + 5.0) < 0.3 and abs(at(hs, 100)) < 0.05, "high shelf: -5 dB in the treble, flat in the bass")
hp = [{"type": "bq_highpass", "freq": 100.0, "q": 0.70710678, "gain": 0}]
check(abs(at(hp, 100) + 3.0103) < 0.01 and at(hp, 10) < -38, "high pass: -3 dB at its corner, steep below")
lp = [{"type": "bq_lowpass", "freq": 5000.0, "q": 0.70710678, "gain": 0}]
check(abs(at(lp, 5000) + 3.0103) < 0.02 and abs(at(lp, 100)) < 0.01, "low pass: -3 dB at its corner, flat below")
nt = [{"type": "bq_notch", "freq": 6000.0, "q": 8, "gain": 0}]
check(at(nt, 6000) < -60 and abs(at(nt, 1000)) < 0.05, "notch: removes its frequency, leaves the rest")
check(svc.biquad_coeffs({"type": "bq_highshelf", "freq": 0.0, "q": 1.0, "gain": -6.0})[0] == 10 ** (-6 / 20),
      "the preamp (high shelf at 0 Hz) is a plain -6 dB gain, like PipeWire's")

# --- Sonar presets
filters = svc.eq_from_sonar(preset)
kinds = [f["type"] for f in filters]
check(kinds == ["bq_highpass", "bq_lowshelf", "bq_peaking", "bq_peaking", "bq_peaking", "bq_highshelf",
                "bq_notch", "bq_lowpass"],
      f"Sonar filter types mapped, disabled and 0 dB filters dropped: {kinds}")
check(svc.eq_from_sonar({"id": "x", "schemaVersion": 5, "data": preset}) == filters,
      "newer Sonar export format (wrapped in 'data') reads the same")
check(svc.eq_from_sonar(dict(preset, globalEnableState=False)) == [], "a switched-off preset means no EQ")
flat = {"parametricEQ": {"enabled": True, **{f"filter{i}": {"enabled": True, "type": "peakingEQ", "frequency": 100 * i,
                                                            "qFactor": 1, "gain": 0} for i in range(1, 11)}}}
check(svc.eq_from_sonar(flat) == [], "a flat preset means no EQ")
check(svc.clean_filters([{"type": "rm -rf", "freq": 1}, {"type": "bq_peaking", "freq": float("nan"), "gain": 3},
                         {"type": "bq_peaking", "freq": 1e9, "q": -4, "gain": 99}]) ==
      [{"type": "bq_peaking", "freq": 23000.0, "q": 0.05, "gain": 30.0}], "junk in the settings file is dropped or clamped")

# --- clipping headroom
pre = svc.eq_preamp_db(filters)
peak = max(svc.eq_response_db(filters, svc.log_freqs(4000, 10, 22000)))
check(pre < 0 and abs(peak + pre) < 0.11, f"preamp {pre} dB cancels the loudest boost ({peak:.2f} dB)")
check(svc.eq_preamp_db([{"type": "bq_peaking", "freq": 1000.0, "q": 1, "gain": -6}]) == 0, "a cut-only EQ needs no preamp")

# --- the generated filter-chain config
conf = svc.eq_conf("game", filters, "ChatMix_Game", 'dac"with quotes')
check('target.object       = "ChatMix_Game"' in conf and 'node.name           = "nova_chatmix_eq_game"' in conf,
      "config captures the Game output and is findable by node name")
check('"dac\\"with quotes"' in conf, "odd characters in device names are escaped")
check(conf.count("label = bq_raw") == len(filters) + 1 and f"b0 = {10 ** (pre / 20):.12g} " in conf,
      "one raw biquad per filter plus the preamp gain")
check(all(conf.count(f"rate = {r} ") == len(filters) for r in svc.EQ_RATES if r != 48000),
      "coefficients for every common sample rate (44.1 to 192 kHz)")
check('outputs = [ "eq7:Out" ]' in conf and 'inputs  = [ "preamp:In" ]' in conf, "filters chained in order")
check(conf.count("{") == conf.count("}") and conf.count("[") == conf.count("]"), "config brackets balance")
check.done()
