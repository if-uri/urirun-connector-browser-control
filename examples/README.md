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
