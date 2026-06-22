#!/usr/bin/env python3
# Run an office scenario on a node via the ready uribrowser pack, and write every artifact
# (host->node trace, real DOM, real screenshot, report) into a per-node session folder:
#   ~/.urirun/<node>/session/<UTC-timestamp>/
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

NODE = os.environ.get("NODE", "laptop")
BASE = os.environ.get("NODE_URL", "http://192.168.188.201:8765")
TS = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
SESS = Path.home() / ".urirun" / NODE / "session" / TS
SESS.mkdir(parents=True, exist_ok=True)


def run(uri, payload):
    body = json.dumps({"uri": uri, "payload": payload}).encode()
    req = urllib.request.Request(f"{BASE}/run", data=body, headers={"Content-Type": "application/json"}, method="POST")
    env = json.loads(urllib.request.urlopen(req, timeout=90).read())
    return env, ((env.get("result") or {}).get("value") or env)


steps = [
    ("browser://laptop/main/page/command/open", {"url": "https://example.com", "driver": "playwright"}, "open page (real chromium)"),
    ("browser://laptop/main/page/query/dom", {}, "read DOM"),
    ("browser://laptop/main/page/command/screenshot", {"driver": "playwright"}, "screenshot"),
    ("browser://laptop/main/form/command/submit", {"form_id": "login", "fields": {"email": "jan@firma.pl"}, "driver": "mock"}, "submit form"),
]

trace = []
for i, (uri, payload, why) in enumerate(steps):
    env, val = run(uri, payload)
    ok = bool(env.get("ok"))
    rec = {"i": i, "uri": uri, "payload": payload, "why": why, "ok": ok}
    # save large artifacts to files, keep the trace small
    if uri.endswith("/query/dom") and isinstance(val, dict) and val.get("html"):
        (SESS / "page.html").write_text(val["html"], encoding="utf-8")
        rec["artifact"] = "page.html"; rec["title"] = val.get("title"); rec["bytes"] = len(val["html"])
    elif uri.endswith("/screenshot") and isinstance(val, dict) and val.get("base64") and val.get("mime") == "image/png":
        png = base64.b64decode(val["base64"])
        (SESS / "screenshot.png").write_bytes(png)
        rec["artifact"] = "screenshot.png"; rec["bytes"] = len(png)
    else:
        rec["value"] = val
    trace.append(rec)
    print(f"  [{i}] {'ok' if ok else 'FAIL'}  {why}  -> {rec.get('artifact') or json.dumps(rec.get('value'), ensure_ascii=False)[:80]}")

(SESS / "trace.json").write_text(json.dumps({"node": NODE, "url": BASE, "at": TS, "steps": trace}, indent=2), encoding="utf-8")

md = [f"# Session — {NODE} @ {TS}", "", f"- node: `{NODE}` ({BASE})", f"- via: ready tellmesh `uribrowser` pack (driver=playwright)", "",
      "## Steps (host → node)", "", "| # | why | uri | ok | artifact/value |", "|---|-----|-----|----|----------------|"]
for r in trace:
    av = r.get("artifact") or (json.dumps(r.get("value"), ensure_ascii=False)[:60] if "value" in r else "")
    md.append(f"| {r['i']} | {r['why']} | `{r['uri']}` | {'✓' if r['ok'] else '✗'} | {av} |")
md += ["", "## Artifacts", ""] + [f"- `{p.name}` ({p.stat().st_size} B)" for p in sorted(SESS.iterdir()) if p.is_file()]
(SESS / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

print(f"\nsession folder: {SESS}")
for p in sorted(SESS.iterdir()):
    print(f"  {p.name}  ({p.stat().st_size} B)")
