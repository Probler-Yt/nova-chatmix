#!/usr/bin/env python3
# Fake gdbus: KDE's global shortcut service (kglobalacceld) and plasmashell's OSD,
# with state in $FAKE_GDBUS_STATE. Answers in gdbus's output format.
import json, os, re, sys
SF = os.environ["FAKE_GDBUS_STATE"]
st = json.load(open(SF)) if os.path.exists(SF) else {}
st.setdefault("components", []); st.setdefault("keys", {}); st.setdefault("log", [])
st.setdefault("taken", {})   # key code -> [action, friendly, component, component friendly]
a = sys.argv[1:]
method = a[a.index("--method") + 1]
args = a[a.index("--method") + 2:]
st["log"].append(" ".join([method] + args))
out, rc = "()", 0
def action_id(s): return re.findall(r"'([^']*)'", s)
if method == "org.kde.osdService.showText":
    pass
elif method.endswith(".getComponent"):
    comp = args[0].strip("'")
    if comp in st["components"]:
        out = f"(objectpath '/component/{comp.replace('.', '_')}',)"
    else:
        print(f"GDBus.Error:org.kde.kglobalaccel.NoSuchComponent: The component '{comp}' doesn't exist.", file=sys.stderr); rc = 1
elif method.endswith(".doRegister"):
    aid = action_id(args[0])
    if aid[0] not in st["components"]: st["components"].append(aid[0])
elif method.endswith(".setShortcutKeys"):
    aid = action_id(args[0])
    if aid[0] not in st["components"]:
        out = "(@a(ai) [],)"   # like the real one: unknown action -> nothing set
    else:
        if "uint32 6" not in args[2]: print("flags must make it present", file=sys.stderr); rc = 1
        keys = [int(k) for k in re.findall(r"\(\[(\d+)", args[1])]
        st["keys"]["/".join(aid[:2])] = keys
        out = "([" + ", ".join(f"([{k}, 0, 0, 0],)" for k in keys) + "],)" if keys else "(@a(ai) [],)"
elif method.endswith(".shortcutKeys"):
    aid = action_id(args[0])
    keys = st["keys"].get("/".join(aid[:2]), []) if aid[0] in st["components"] else []
    out = "([" + ", ".join(f"([{k}, 0, 0, 0],)" for k in keys) + "],)" if keys else "(@a(ai) [],)"
elif method.endswith(".globalShortcutsByKey"):
    k = re.search(r"\[(\d+)", args[0]).group(1)
    hits = [st["taken"][k]] if k in st["taken"] else []
    hits += [[aid.split("/")[1], "ours", aid.split("/")[0], "ChatMix"] for aid, ks in st["keys"].items() if int(k) in ks]
    out = "([" + ", ".join(f"('{h[0]}', '{h[1]}', '{h[2]}', '{h[3]}', 'default', 'Default Context', [{k}], @ai [])" for h in hits) + "],)" if hits else "(@a(ssssssaiai) [],)"
elif method.endswith(".blockGlobalShortcuts"):
    st["blocked"] = args[0] == "true"
json.dump(st, open(SF, "w"))
print(out); sys.exit(rc)
