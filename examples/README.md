# browser-control connector — examples

Headless Chrome (local) + noVNC (remote) + **KVM control of any browser**.

## Drive any browser by GUI on a urirun node (KVM)

Deploy the single-file KVM handler onto a node over the mesh (no SSH; node needs
`--admin-token`/`--key-auth`), then drive Firefox/Chrome/… by GUI:

```bash
# deploy browser://laptop/kvm/* onto the node (substitutes the node name, pushes code+bindings+env)
TELLMESH_DIR=/path/to/tellmesh ./deploy-kvm.sh 192.168.188.201 laptop ~/.ssh/id_ed25519

N=http://192.168.188.201:8765 ; run(){ curl -s -X POST $N/run -H 'Content-Type: application/json' -d "$1"; }
run '{"uri":"browser://laptop/kvm/session/query/probe","payload":{}}'                        # what GUI tools are available
run '{"uri":"browser://laptop/kvm/session/command/launch","payload":{"browser":"firefox","url":"https://example.com"}}'
run '{"uri":"browser://laptop/kvm/page/command/navigate","payload":{"url":"https://example.com/login"}}'
run '{"uri":"browser://laptop/kvm/input/command/type","payload":{"text":"jan@firma.pl"}}'
run '{"uri":"browser://laptop/kvm/screen/query/capture","payload":{"monitor":0}}'
```

Files: [`kvm-flat-handler.py`](kvm-flat-handler.py) (the deployable handler),
[`kvm-bindings.json`](kvm-bindings.json) (route templates), [`deploy-kvm.sh`](deploy-kvm.sh).
The node needs a desktop session + `ydotool`+`grim` (or the tellmesh packs) for real
input/screenshot — see the repo README's Wayland note.

## Real Chrome control with no input tools (CDP) — works under Wayland

When the node has no `ydotool`/`xdotool` (e.g. GNOME/Wayland), use the DevTools-Protocol
handler [`cdp-flat-handler.py`](cdp-flat-handler.py): launch Chrome with a debug port and
control it by running JS in the page. Verified live driving Firefox/Chrome on a remote
GNOME/Wayland laptop:

```bash
B=http://192.168.188.201:8765 ; run(){ curl -s -X POST $B/run -H 'Content-Type: application/json' -d "$1"; }
run '{"uri":"browser://laptop/cdp/session/command/launch","payload":{"browser":"chrome","url":"https://example.com"}}'
run '{"uri":"browser://laptop/cdp/page/query/eval","payload":{"expr":"document.title"}}'                  # "Example Domain"
run '{"uri":"browser://laptop/cdp/page/query/eval","payload":{"expr":"document.querySelector(\"a\").click()"}}'
run '{"uri":"browser://laptop/cdp/page/query/eval","payload":{"expr":"document.title+\" @ \"+location.host"}}'  # navigated
run '{"uri":"browser://laptop/cdp/page/query/screenshot","payload":{}}'                                   # real PNG
```

Deploy it the same way as the KVM handler (`urirun host deploy --code cdp-flat-handler.py`
with bindings on `browser://<node>/cdp/*`).

## Use the ready tellmesh `uribrowser` pack (standard surface)

Instead of a bespoke handler you can deploy the **ready `uribrowserdocker` pack** from
tellmesh and drive it over its standard `browser://<node>/<session>/...` URIs
(`query/status`, `page/command/open`, `page/query/dom`, `page/command/screenshot`,
`form/command/submit`, `social/command/publish-post`). `deploy-uribrowser.sh` pushes the
pack's own `handlers.py` (from `$TELLMESH_DIR`, not a vendored copy) plus a thin
`(payload,context)`→`fn(**payload)` bridge:

```bash
TELLMESH_DIR=/path/to/tellmesh ./deploy-uribrowser.sh 192.168.188.201 laptop ~/.ssh/id_ed25519

B=http://192.168.188.201:8765 ; run(){ curl -s -X POST $B/run -H 'Content-Type: application/json' -d "$1"; }
run '{"uri":"browser://laptop/main/query/status","payload":{}}'                       # driver caps
run '{"uri":"browser://laptop/main/page/command/open","payload":{"url":"https://example.com","driver":"system-open"}}'
run '{"uri":"browser://laptop/main/form/command/submit","payload":{"form_id":"login","fields":{"email":"jan@firma.pl"},"driver":"system-open"}}'
```

Drivers: `mock`, `system-open` (xdg-open — real, no deps), `playwright`/`cdp` (real DOM +
screenshot; need `pip install playwright` on the node). Verified live on the lenovo node:
`system-open` opened the page in the desktop browser and `form.submit` recorded fields;
`playwright` returns the pack's own "pip install playwright" hint until installed.
Files: [`uribrowser-bridge.py`](uribrowser-bridge.py), [`uribrowser-bindings.json`](uribrowser-bindings.json),
[`deploy-uribrowser.sh`](deploy-uribrowser.sh).

## Install
```bash
urirun install urirun-connector-browser-control
```
`urirun install` resolves catalog ids via connect.ifuri.com; `--catalog <url>` points at a
local/on-prem registry; a full package name / git URL / path falls back to `pip install`.

## Run
```bash
# Headless Chrome (local) + noVNC (remote) (read)
urirun run 'browser://chrome/page/query/text' --payload '{"url": "https://example.com", "max": 120}' --execute --allow 'browser://*'

# preview without running (dry-run): drop --execute
urirun run 'browser://chrome/page/query/text' --payload '{"url": "https://example.com", "max": 120}' --allow 'browser://*'
```

## Inspect the runtime (no path — like error:// / log://)
```bash
urirun list | grep 'browser://'                                   # this connector's routes
urirun run 'registry://local/routes/query/list' --payload '{"scheme":"browser"}' --allow 'registry://*'
urirun run 'registry://local/bindings/query/show' --payload '{"uri":"browser://chrome/page/query/text"}' --allow 'registry://*'   # full typed contract
urirun errors                                                      # recent runtime errors (error://)
```

## Generate a client / API surface from the binding
```bash
urirun discover | urirun gen openapi - --out openapi.json   # OpenAPI 3 (one path per route)
urirun discover | urirun gen proto   - --out service.proto  # protobuf + gRPC (typed rpc per route)
urirun discover | urirun gen client  - --out client.py      # typed Python client
```

## Capture a node session to `~/.urirun/<node>/session/<ts>/`

[`office-session.py`](office-session.py) runs an office scenario over the ready uribrowser
pack (driver=playwright) and writes the host→node trace, the real DOM, the real
screenshot, and a Markdown report into a per-node **session folder** you can browse:

It captures BOTH directions (host→node dispatch trace + node→host SSE events) and renders
one self-contained `report.md` (full DOM + the screenshot embedded inline for preview).
The flow is parameterizable via `FLOW`/`TARGET_URL`:

```bash
NODE=laptop NODE_URL=http://192.168.188.201:8765 python3 office-session.py            # default login-form flow
FLOW=example-com TARGET_URL=https://example.com python3 office-session.py             # any page
# -> ~/.urirun/laptop/session/<flow>-<UTC-ts>/  { page.html, screenshot.png, events.json, trace.json, report.md }
```

(Note: urirun's node state lives in `~/.urirun-node/` on the node — one node per machine,
no per-name subfolder; host-side artifacts live under `~/.urirun/`. This script adds the
`~/.urirun/<node>/session/<ts>/` convention for browsable per-run captures.)
