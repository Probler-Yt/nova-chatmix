#!/usr/bin/env python3
# Fake pactl v2: sinks, modules, app streams with properties, moves, subscribe.
import json, os, sys, time, fcntl
SF = os.environ["FAKE_PACTL_STATE"]; EV = SF + ".events"
DAC = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wired-00.analog-stereo"
SPK = "alsa_output.pci-0000_0c_00.4.analog-stereo"
a = sys.argv[1:]
if a == ["subscribe"]:
    open(EV, "a").close(); f = open(EV); f.seek(0, 2)
    while True:
        l = f.readline()
        if l: print(l.rstrip(), flush=True)
        else: time.sleep(0.05)
lock = open(SF + ".lock", "w"); fcntl.flock(lock, fcntl.LOCK_EX)
st = json.load(open(SF)) if os.path.exists(SF) else {}
def save():
    json.dump(st, open(SF + ".tmp", "w")); os.replace(SF + ".tmp", SF)  # never half-written
st.setdefault("next", 100); st.setdefault("modules", {}); st.setdefault("streams", []); st.setdefault("vol", {})
st.setdefault("default", DAC); st.setdefault("calls", [])
st["calls"].append(" ".join(a))
def event(s): open(EV, "a").write(s + "\n")
def sinks():
    s = [{"index": 1, "name": SPK, "description": "Starship/Matisse HD Audio Analog Stereo", "properties": {"device.vendor.id": "0x1022"}},
         {"index": 2, "name": DAC, "description": "Arctis Nova Pro Wired", "properties": {"device.vendor.id": "0x1038", "device.product.id": "0x12cb"}}]
    for mid, m in sorted(st["modules"].items()):
        if m["name"] == "module-null-sink":
            n = m["args"][0].split("=",1)[1]; s.append({"index": int(mid)+1000, "name": n, "description": n.split("_")[1], "properties": {}})
    return s
def idx_of(name): return next((x["index"] for x in sinks() if x["name"] == name), None)
out = ""; rc = 0
def render_text(kind, items):
    hdr = {"sinks": "Sink", "sink-inputs": "Sink Input", "modules": "Module", "clients": "Client"}[kind]
    L = []
    for it in items:
        L.append(f"{hdr} #{it['index']}")
        if "name" in it: L.append(f"	Name: {it['name']}")
        if "description" in it: L.append(f"	Description: {it['description']}")
        if "argument" in it: L.append(f"	Argument: {it['argument']}")
        if kind == "sink-inputs":
            L.append(f"	Owner Module: {it['owner_module'] or 'n/a'}")
            L.append(f"	Client: {it.get('client', '')}")
            L.append(f"	Sink: {it['sink']}")
            L.append(f"	Corked: {'yes' if it['corked'] else 'no'}")
            v = it['volume']['front-left']['value_percent']
            L.append(f"	Volume: front-left: 65536 / {v} / 0.00 dB,   front-right: 65536 / {v} / 0.00 dB")
            L.append("	        balance 0.00")
        L.append("	Properties:")
        for k, val in it.get("properties", {}).items():
            L.append(f'		{k} = "{val}"')
        L.append("")
    return "\n".join(L)
text_mode = a[:1] == ["list"] and len(a) == 2
if text_mode:
    a = ["-f", "json", "list", a[1]]
elif a[:3] == ["-f", "json", "list"] and os.environ.get("FAKE_BROKEN_JSON"):
    print('[{"index": 12, "properties": {"media.name": "bro'); save(); sys.exit(0)
if a == ["list", "modules", "short"]:
    out = "\n".join(f"{i}\t{m['name']}\t{' '.join(m['args'])}\t" for i, m in sorted(st["modules"].items()))
elif a[:3] == ["-f", "json", "list"]:
    k = a[3]
    if k == "sinks": out = json.dumps(sinks())
    elif k == "clients": out = json.dumps(st.get("clients", []))
    elif k == "modules":  # like real pactl 16: the JSON has no module index
        out = json.dumps([{"name": m["name"], "argument": " ".join(m["args"]), "usage_counter": "n/a"} for i, m in st["modules"].items()])
    elif k == "sink-inputs":
        res = []
        for i, m in st["modules"].items():
            if m["name"] == "module-loopback":
                v = st["vol"].get(str(int(i)+5000), "100%")
                res.append({"index": int(i)+5000, "owner_module": i, "sink": 2, "corked": False,
                            "volume": {"front-left": {"value": 0, "value_percent": v}}, "properties": {"application.name": "pipewire"}})
        for s in st["streams"]:
            res.append({"index": s["index"], "owner_module": "", "client": str(s.get("client", "")), "sink": idx_of(s["sink"]), "corked": s.get("corked", False),
                        "volume": {"front-left": {"value_percent": "100%"}}, "properties": s["props"]})
        out = json.dumps(res)
elif a[0] == "load-module":
    mid = st["next"]; st["next"] += 1; st["modules"][str(mid)] = {"name": a[1], "args": a[2:]}; out = str(mid)
    if a[1] == "module-null-sink": st["default"] = st["default"]
    event(f"Event 'new' on module #{mid}")
elif a[0] == "unload-module":
    st["modules"].pop(a[1], None)
elif a[0] == "set-sink-input-volume": st["vol"][a[1]] = a[2]
elif a[0] == "move-sink-input":
    for s in st["streams"]:
        if str(s["index"]) == a[1] and idx_of(a[2]) is not None: s["sink"] = a[2]; event(f"Event 'change' on sink-input #{a[1]}")
elif a[0] == "get-default-sink": out = st["default"]
elif a[0] == "set-default-sink": st["default"] = a[1]
save()
if text_mode and out:
    out = render_text(a[3], json.loads(out))
print(out); sys.exit(rc)
