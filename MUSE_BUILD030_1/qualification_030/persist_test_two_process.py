import sys, json, os, tempfile, subprocess
from pathlib import Path
stage, root = sys.argv[1], Path(sys.argv[2])
import muse024
st = root.parent / "tokens.json"
if stage == "write":
    admin = muse024.initialize(root)
    ta = muse024.issue(root, {"principal":"alice","scopes":["big:read","big:write","small:read","small:write"],"owners":["L01"]})["token"]
    tb = muse024.issue(root, {"principal":"bob","scopes":["big:read","big:write","small:read","small:write"],"owners":["L02"]})["token"]
    out = {"alice": ta, "bob": tb, "ptr": {}}
    for pool, bucket in (("big","association"),("small","workspace_micro")):
        out["ptr"][pool] = muse024.call(root, ta, dict(operation="memory_write", pool=pool, owner="L01", record_id="rec-1", bucket=bucket, content="alice-secret"))
    st.write_text(json.dumps(out, default=str)); print("wrote", out["ptr"])
elif stage == "read":
    d = json.loads(st.read_text()); r = {"pid": os.getpid()}
    for pool in ("big","small"):
        p = d["ptr"][pool]
        r[pool+"_restart_read"] = muse024.call(root, d["alice"], dict(operation="memory_read", pool=pool, owner="L01", pointer=p))["content"]
        try: muse024.call(root, d["bob"], dict(operation="memory_read", pool=pool, owner="L01", pointer=p)); r[pool+"_cross_principal"] = "ACCEPTED"
        except Exception as e: r[pool+"_cross_principal"] = type(e).__name__
        for label, ch in (("auth1",{"authority":1}),("authfalse",{"authority":False}),("badtoken",None)):
            try:
                if ch is None: muse024.call(root, "invalid", dict(operation="memory_read", pool=pool, owner="L01", pointer=p))
                else: muse024.call(root, d["alice"], {**dict(operation="memory_read", pool=pool, owner="L01", pointer=p), **ch})
                r[f"{pool}_{label}"] = "ACCEPTED"
            except Exception as e: r[f"{pool}_{label}"] = type(e).__name__
    r["occupancy_note"]="occupancy scope not granted to alice (expected PermissionError): "+ (lambda: (muse024.call(root,d["alice"],{"operation":"occupancy"}) and "ACCEPTED"))() if False else "skipped"
    print(json.dumps(r))
