#!/usr/bin/env python3
# Fake pactl backed by a JSON state file, for testing nova-chatmix.
import fcntl, json, os, sys
SF = os.environ["FAKE_PACTL_STATE"]
lock = open(SF + ".lock", "w"); fcntl.flock(lock, fcntl.LOCK_EX)  # one reader/writer at a time
st = json.load(open(SF)) if os.path.exists(SF) else {}
def save():
    json.dump(st, open(SF + ".tmp", "w")); os.replace(SF + ".tmp", SF)  # never half-written
st.setdefault("next", 536870913); st.setdefault("modules", {}); st.setdefault("log", [])
st.setdefault("default", "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wired-00.analog-stereo")
st.setdefault("dac_present", True); st.setdefault("fail_dont_move", False)
a = sys.argv[1:]; st["log"].append(" ".join(a)); out = ""; rc = 0
DAC = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wired-00.analog-stereo"
def sinks():
    s = [{"index": 1, "name": "alsa_output.pci-0000_0c_00.4.analog-stereo", "properties": {"device.vendor.id": "0x1022"}}]
    if st["dac_present"]:
        s.append({"index": 2, "name": DAC, "properties": {"device.vendor.id": "0x1038", "device.product.id": "0x12cb"}})
    for mid, m in st["modules"].items():
        if m["name"] == "module-null-sink":
            n = m["args"][0].split("=",1)[1]; s.append({"index": int(mid)+1000, "name": n, "properties": {}})
    return s
if a == ["list", "modules", "short"]:
    out = "\n".join(f"{i}\t{m['name']}\t{' '.join(m['args'])}\t" for i, m in sorted(st["modules"].items()))
elif a[:3] == ["-f", "json", "list"]:
    k = a[3]
    if k == "sinks": out = json.dumps(sinks())
    elif k == "modules":  # like real pactl 16: the JSON has no module index
        out = json.dumps([{"name": m["name"], "argument": " ".join(m["args"]), "usage_counter": "n/a"} for i, m in st["modules"].items()])
    elif k == "sink-inputs":
        # "links_down": the DAC blinked away and PipeWire dropped the loopback
        # streams into it, while the modules themselves are still loaded
        out = json.dumps([] if st.get("links_down") or not st["dac_present"] else
                         [{"index": int(i)+5000, "owner_module": i, "sink": 2}
                          for i, m in st["modules"].items() if m["name"] == "module-loopback"])
elif a[0] == "load-module":
    st["links_down"] = False
    if st["fail_dont_move"] and any("dont_move" in x for x in a): rc = 1
    else:
        mid = st["next"]; st["next"] += 1; st["modules"][str(mid)] = {"name": a[1], "args": a[2:]}; out = str(mid)
elif a[0] == "unload-module":
    st["modules"].pop(a[1], None)
elif a[0] == "set-sink-input-volume":
    st.setdefault("vol", {})[a[1]] = a[2]
elif a[0] == "get-default-sink": out = st["default"]
elif a[0] == "set-default-sink": st["default"] = a[1]
save()
print(out); sys.exit(rc)
