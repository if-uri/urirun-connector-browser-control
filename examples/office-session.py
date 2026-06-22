#!/usr/bin/env python3
# Run an office flow on a node via the ready uribrowser pack (driver=playwright), capture
# BOTH directions (host->node trace + node->host SSE events), and render a single
# self-contained Markdown report (each page's DOM + screenshot embedded inline). A flow may
# visit several pages. Writes to ~/.urirun/<node>/session/<flow>-<UTC-ts>/ and refreshes a
# per-node INDEX.md of all sessions.
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
ROOT = Path.home() / ".urirun" / NODE / "session"
SESS = ROOT / f"{FLOW}-{TS}"
SESS.mkdir(parents=True, exist_ok=True)

FORM = ("data:text/html," + urllib.parse.quote(
    "<html><head><title>Logowanie — Biuro</title></head><body style='font-family:sans-serif;padding:2rem'>"
    "<h1>Logowanie do panelu</h1>"
    "<form><p><label>E-mail <input name=email value='jan@firma.pl'></label></p>"
    "<p><label>Has&#322;o <input name=pass type=password value='secret'></label></p>"
    "<button>Zaloguj</button></form></body></html>"))
IANA = "https://www.iana.org/help/example-domains"
FLOWS = {
    "login-form": [("login form", FORM)],
    "example-com": [("example.com", "https://example.com")],
    "iana": [("iana", IANA)],
    "multi": [("login form", FORM), ("example.com", "https://example.com"), ("iana", IANA)],
}
pages = FLOWS.get(FLOW) or [(FLOW, os.environ.get("TARGET_URL", FORM))]


def run(uri, payload):
    body = json.dumps({"uri": uri, "payload": payload}).encode()
    req = urllib.request.Request(f"{BASE}/run", data=body, headers={"Content-Type": "application/json"}, method="POST")
    env = json.loads(urllib.request.urlopen(req, timeout=90).read())
    return env, ((env.get("result") or {}).get("value") or env)


# node -> host: subscribe to SSE before dispatching
_events, _resp = [], {}


def watch():
    try:
        r = urllib.request.urlopen(urllib.request.Request(BASE + "/events", headers={"Accept": "text/event-stream"}), timeout=120)
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


try:
    urllib.request.urlopen(BASE + "/health", timeout=5)
    threading.Thread(target=watch, daemon=True).start()
    time.sleep(0.5)
except Exception:
    pass

trace, captures = [], []  # captures: per-page {label, title, dom, shot}


def step(uri, payload, why):
    try:
        env, val = run(uri, payload)
        ok = bool(env.get("ok"))
    except Exception as exc:  # noqa: BLE001
        env, val, ok = {}, {"error": str(exc)}, False
    trace.append({"i": len(trace), "uri": uri, "payload": {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v) for k, v in payload.items()}, "why": why, "ok": ok})
    print(f"  [{len(trace)-1}] {'ok' if ok else 'FAIL'}  {why}")
    return val


for n, (label, url) in enumerate(pages, 1):
    step("browser://laptop/main/page/command/open", {"url": url, "driver": "playwright"}, f"open [{label}]")
    dom = step("browser://laptop/main/page/query/dom", {}, f"read DOM [{label}]")
    shot = step("browser://laptop/main/page/command/screenshot", {"driver": "playwright"}, f"screenshot [{label}]")
    html = dom.get("html") if isinstance(dom, dict) else None
    b64 = shot.get("base64") if isinstance(shot, dict) and shot.get("mime") == "image/png" else None
    if html:
        (SESS / f"page-{n}.html").write_text(html, encoding="utf-8")
    if b64:
        (SESS / f"screenshot-{n}.png").write_bytes(base64.b64decode(b64))
    captures.append({"label": label, "title": (dom.get("title") if isinstance(dom, dict) else None), "dom": html, "shot": b64})

step("browser://laptop/main/form/command/submit", {"form_id": "login", "fields": {"email": "jan@firma.pl", "pass": "secret"}, "driver": "mock"}, "submit login form")

time.sleep(0.8)
if _resp.get("r"):
    try:
        _resp["r"].close()
    except Exception:
        pass

(SESS / "trace.json").write_text(json.dumps({"node": NODE, "flow": FLOW, "at": TS, "steps": trace}, indent=2, ensure_ascii=False), encoding="utf-8")
(SESS / "events.json").write_text(json.dumps(_events, indent=2, ensure_ascii=False), encoding="utf-8")

ok_n = sum(1 for r in trace if r["ok"])
L = [f"# Session report — `{NODE}` · flow `{FLOW}` @ {TS}", "",
     f"- **node**: `{NODE}` — {BASE}",
     f"- **via**: ready tellmesh `uribrowser` pack, driver `playwright`",
     f"- **pages**: {len(pages)}  ·  **steps ok**: {ok_n}/{len(trace)}  ·  **node→host events**: {len(_events)}", "",
     "## Host → Node (dispatched steps)", "", "| # | why | uri | ok |", "|---|-----|-----|----|"]
L += [f"| {r['i']} | {r['why']} | `{r['uri']}` | {'✅' if r['ok'] else '❌'} |" for r in trace]
L += ["", "## Node → Host (live SSE events)", ""]
if _events:
    L += ["| event | uri | ok |", "|-------|-----|----|"]
    L += [f"| {e.get('event')} | `{e.get('uri')}` | {'' if e.get('ok') is None else ('✅' if e.get('ok') else '❌')} |" for e in _events]
else:
    L.append("_(no SSE events captured)_")
for n, c in enumerate(captures, 1):
    L += ["", f"## Page {n}: {c['label']} — {c.get('title') or ''}", ""]
    if c["shot"]:
        L += [f"![screenshot {n}](data:image/png;base64,{c['shot']})", "", f"_(saved as `screenshot-{n}.png`)_", ""]
    if c["dom"]:
        L += ["<details><summary>DOM</summary>", "", "```html", c["dom"].strip()[:4000], "```", "", "</details>"]
L += ["", "## Artifacts", ""] + [f"- `{p.name}` ({p.stat().st_size} B)" for p in sorted(SESS.iterdir()) if p.is_file()]
(SESS / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")

# refresh the per-node INDEX.md across all sessions
rows = []
for d in sorted(ROOT.iterdir(), reverse=True):
    tj = d / "trace.json"
    if not (d.is_dir() and tj.exists()):
        continue
    try:
        t = json.loads(tj.read_text())
    except Exception:
        continue
    okc = sum(1 for s in t.get("steps", []) if s.get("ok"))
    shots = sorted(d.glob("screenshot*.png"))
    thumb = f"[shot]({d.name}/{shots[0].name})" if shots else "—"
    rows.append(f"| `{d.name}` | {t.get('flow','?')} | {okc}/{len(t.get('steps',[]))} | {thumb} | [report]({d.name}/report.md) |")
idx = [f"# Sessions — node `{NODE}`", "", f"_{len(rows)} runs · {BASE}_", "",
       "| session | flow | steps ok | screenshot | report |", "|---------|------|----------|-----------|--------|", *rows, ""]
(ROOT / "INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")

print(f"\nsession: {SESS}")
print(f"index:   {ROOT / 'INDEX.md'}  ({len(rows)} runs)")
