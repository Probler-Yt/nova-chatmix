#!/usr/bin/env python3
# Fake pactl backed by a JSON state file, for testing nova-chatmix.
import json, os, sys
SF = os.environ["FAKE_PACTL_STATE"]
st = json.load(open(SF)) if os.path.exists(SF) else {}
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
if a[:3] == ["-f", "json", "list"]:
    k = a[3]
    if k == "sinks": out = json.dumps(sinks())
    elif k == "modules": out = json.dumps([{"index": int(i), "name": m["name"], "argument": " ".join(m["args"])} for i, m in st["modules"].items()])
    elif k == "sink-inputs": out = json.dumps([{"index": int(i)+5000, "owner_module": i} for i, m in st["modules"].items() if m["name"] == "module-loopback"])
elif a[0] == "load-module":
    if st["fail_dont_move"] and any("dont_move" in x for x in a): rc = 1
    else:
        mid = st["next"]; st["next"] += 1; st["modules"][str(mid)] = {"name": a[1], "args": a[2:]}; out = str(mid)
elif a[0] == "unload-module":
    st["modules"].pop(a[1], None)
elif a[0] == "set-sink-input-volume":
    st.setdefault("vol", {})[a[1]] = a[2]
elif a[0] == "get-default-sink": out = st["default"]
elif a[0] == "set-default-sink": st["default"] = a[1]
json.dump(st, open(SF, "w"))
print(out); sys.exit(rc)
