#!/usr/bin/env python3
# Run an office scenario on a node via the ready uribrowser pack (driver=playwright),
# capture BOTH directions (host->node dispatch trace + node->host SSE events), and render a
# single self-contained Markdown report (full DOM + screenshot embedded inline) into:
#   ~/.urirun/<node>/session/<UTC-timestamp>/
import base64
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

NODE = os.environ.get("NODE", "laptop")
BASE = os.environ.get("NODE_URL", "http://192.168.188.201:8765")
TS = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
FLOW = os.environ.get("FLOW", "login-form")
SESS = Path.home() / ".urirun" / NODE / "session" / f"{FLOW}-{TS}"
SESS.mkdir(parents=True, exist_ok=True)

# a concrete login form (data: URL → deterministic, offline-safe, real DOM + screenshot)
FORM = ("data:text/html," + urllib.parse.quote(
    "<html><head><title>Logowanie — Biuro</title></head><body style='font-family:sans-serif;padding:2rem'>"
    "<h1>Logowanie do panelu</h1>"
    "<form><p><label>E-mail <input name=email value='jan@firma.pl'></label></p>"
    "<p><label>Has&#322;o <input name=pass type=password value='secret'></label></p>"
    "<button>Zaloguj</button></form></body></html>"))
# the flow's target page is configurable so the same tool captures ANY flow
URL = os.environ.get("TARGET_URL", FORM)


def run(uri, payload):
    body = json.dumps({"uri": uri, "payload": payload}).encode()
    req = urllib.request.Request(f"{BASE}/run", data=body, headers={"Content-Type": "application/json"}, method="POST")
    env = json.loads(urllib.request.urlopen(req, timeout=90).read())
    return env, ((env.get("result") or {}).get("value") or env)


# --- node -> host: subscribe to the SSE stream BEFORE dispatching ---
_events, _resp = [], {}


def watch():
    try:
        r = urllib.request.urlopen(urllib.request.Request(BASE + "/events", headers={"Accept": "text/event-stream"}), timeout=60)
        _resp["r"] = r
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data:"):
                try:
                    _events.append(json.loads(line[5:].strip()))
                except Exception:
                    pass
    except Exception:
        pass


has_events = False
try:
    urllib.request.urlopen(BASE + "/health", timeout=5)
    threading.Thread(target=watch, daemon=True).start()
    has_events = True
    time.sleep(0.5)
except Exception:
    pass

steps = [
    ("browser://laptop/main/page/command/open", {"url": URL, "driver": "playwright"}, f"open page [{FLOW}] (real chromium)"),
    ("browser://laptop/main/page/query/dom", {}, "read DOM"),
    ("browser://laptop/main/page/command/screenshot", {"driver": "playwright"}, "screenshot"),
    ("browser://laptop/main/form/command/submit", {"form_id": "login", "fields": {"email": "jan@firma.pl", "pass": "secret"}, "driver": "mock"}, "submit form"),
]

trace, dom_html, shot_b64 = [], None, None
for i, (uri, payload, why) in enumerate(steps):
    try:
        env, val = run(uri, payload)
        ok = bool(env.get("ok"))
    except Exception as exc:  # noqa: BLE001
        env, val, ok = {}, {"error": str(exc)}, False
    rec = {"i": i, "uri": uri, "payload": payload, "why": why, "ok": ok}
    if uri.endswith("/query/dom") and isinstance(val, dict) and val.get("html"):
        dom_html = val["html"]
        (SESS / "page.html").write_text(dom_html, encoding="utf-8")
        rec["artifact"], rec["title"], rec["bytes"] = "page.html", val.get("title"), len(dom_html)
    elif uri.endswith("/screenshot") and isinstance(val, dict) and val.get("base64") and val.get("mime") == "image/png":
        shot_b64 = val["base64"]
        (SESS / "screenshot.png").write_bytes(base64.b64decode(shot_b64))
        rec["artifact"], rec["bytes"] = "screenshot.png", len(base64.b64decode(shot_b64))
    else:
        rec["value"] = val
    trace.append(rec)
    print(f"  [{i}] {'ok' if ok else 'FAIL'}  {why}  -> {rec.get('artifact') or json.dumps(rec.get('value'), ensure_ascii=False)[:70]}")

time.sleep(0.8)  # let the last node->host events arrive
if _resp.get("r"):
    try:
        _resp["r"].close()
    except Exception:
        pass

(SESS / "trace.json").write_text(json.dumps({"node": NODE, "url": BASE, "at": TS, "steps": trace}, indent=2, ensure_ascii=False), encoding="utf-8")
(SESS / "events.json").write_text(json.dumps(_events, indent=2, ensure_ascii=False), encoding="utf-8")

# --- one self-contained Markdown report: every content inline + screenshot embedded ---
L = [f"# Session report — `{NODE}` @ {TS}", "",
     f"- **node**: `{NODE}` — {BASE}",
     f"- **via**: ready tellmesh `uribrowser` pack, driver `playwright`",
     f"- **steps ok**: {sum(1 for r in trace if r['ok'])}/{len(trace)}  ·  **node→host events**: {len(_events)}", "",
     "## Host → Node (dispatched steps)", "",
     "| # | why | uri | ok | result |", "|---|-----|-----|----|--------|"]
for r in trace:
    res = r.get("artifact") or (json.dumps(r.get("value"), ensure_ascii=False)[:70] if "value" in r else "")
    L.append(f"| {r['i']} | {r['why']} | `{r['uri']}` | {'✅' if r['ok'] else '❌'} | {res} |")

L += ["", "## Node → Host (live SSE events)", ""]
if _events:
    L += ["| event | uri | ok | service |", "|-------|-----|----|---------|"]
    for e in _events:
        okv = "" if e.get("ok") is None else ("✅" if e.get("ok") else "❌")
        L.append(f"| {e.get('event')} | `{e.get('uri')}` | {okv} | {e.get('service','')} |")
else:
    L.append("_(no SSE events captured)_")

if shot_b64:
    L += ["", "## Screenshot (rendered on the node)", "",
          f"![screenshot](data:image/png;base64,{shot_b64})", "",
          "_(also saved as `screenshot.png`)_"]
if dom_html:
    L += ["", "## Page DOM (read back from the node)", "", "```html", dom_html.strip(), "```"]

L += ["", "## Raw host→node trace (`trace.json`)", "", "```json", json.dumps(trace, indent=2, ensure_ascii=False), "```",
      "", "## Raw node→host events (`events.json`)", "", "```json", json.dumps(_events, indent=2, ensure_ascii=False), "```",
      "", "## Artifacts in this session folder", ""]
L += [f"- `{p.name}` ({p.stat().st_size} B)" for p in sorted(SESS.iterdir()) if p.is_file()]
(SESS / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")

print(f"\nsession folder: {SESS}")
for p in sorted(SESS.iterdir()):
    print(f"  {p.name}  ({p.stat().st_size} B)")
print(f"\nreport: {SESS / 'report.md'}")
