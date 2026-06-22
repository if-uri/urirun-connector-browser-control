# urirun-connector-browser-control

Browser Control connector for [ifURI](https://ifuri.com) / [urirun](https://github.com/if-uri/urirun).

Public hub page:
[connect.ifuri.com/connectors/browser-control](https://connect.ifuri.com/connectors/browser-control)

It declares browser actions as URI routes across **three targets** — pick by how much
control you need and what's available on the node:

### `browser://kvm/…` — drive ANY browser by GUI (Firefox / Chrome / Brave / Edge / …)

Real screen control (not headless), so it works with every browser, its extensions,
logins and plugins. Implemented by **reusing the `tellmesh` modules** (`urihim` keyboard/
mouse, `urikvm` screenshot + OCR-click) as the URI handlers:

| URI | does | via |
|-----|------|-----|
| `browser://kvm/session/command/launch` `{browser, url?}` | launch a chosen browser window | binary lookup (firefox/chrome/chromium/brave/edge/opera/vivaldi/…) |
| `browser://kvm/page/command/navigate` `{url}` | Ctrl+L → type URL → Enter | `urihim` |
| `browser://kvm/input/command/type` `{text, enter?}` | type into the focused field | `urihim` |
| `browser://kvm/input/command/hotkey` `{keys}` | press a shortcut (e.g. `["ctrl","t"]`) | `urihim` |
| `browser://kvm/input/command/click` `{x, y, button?}` | click at coordinates | `urihim` |
| `browser://kvm/page/command/click-text` `{text}` | OCR-locate a visible label and click it | `urikvm` |
| `browser://kvm/screen/query/capture` `{monitor?}` | capture the screen | `urikvm` |
| `browser://kvm/session/command/close` `{hard?, browser?}` | close the active tab (Ctrl+W) or kill | `urihim` |

KVM routes need a **desktop session** on the node. For input/screenshot they prefer the
**tellmesh** packs (`urihim`/`urikvm`, which abstract X11 *and* Wayland — pip-installed or
`TELLMESH_DIR=<checkout>`), and **fall back to bare OS tools** when tellmesh is absent:

| need | Wayland | X11 |
|------|---------|-----|
| keyboard / mouse | `ydotool` | `xdotool` (or `ydotool`) |
| screenshot | `grim` | `import` (ImageMagick) / `scrot` / `maim` |
| OCR click-text | tellmesh `urikvm` (+`tesseract`) | same |

Set `URISYS_ALLOW_REAL=1` to actually drive the real mouse/keyboard (otherwise the
tellmesh handlers run in mock mode). Any missing tool yields a clean error — never a crash.

> **Wayland note.** On a GNOME/Wayland desktop, run the node **inside the user's graphical
> session** (so it has `WAYLAND_DISPLAY` + `DBUS_SESSION_BUS_ADDRESS` + portal). A node
> running as a detached service can launch browsers but cannot inject input or screenshot
> via the portal (`gnome-screenshot` will block); install `ydotool`+`grim` (or tellmesh)
> for portal-free control.

### `browser://chrome/…` — headless read/screenshot

- `browser://chrome/page/query/dom`, `…/page/query/text`, `…/page/command/screenshot`
  (local headless Chrome/Chromium; safe dry-run when none is installed).

### `browser://cdp/…` — real Chrome control via DevTools Protocol (no input tools)

Drives Chrome/Chromium/Brave/Edge over CDP — launch with a debug port, then navigate,
**run JS in the page** (click/fill/read), list tabs, and screenshot. Needs **no
xdotool/ydotool** and works **headed under Wayland** (where synthetic input is blocked),
so it's the most reliable way to genuinely control a Chrome-family browser on a node.
Chrome-family only — for browser-agnostic GUI control use `browser://kvm`.

- `browser://cdp/session/command/launch` `{browser?, url?, headless?}`
- `browser://cdp/page/command/navigate` `{url}` · `browser://cdp/page/query/tabs`
- `browser://cdp/page/query/eval` `{expr}` — run JS, returns the value
- `browser://cdp/page/query/screenshot` — PNG of the live page

```bash
N=http://NODE:8765 ; run(){ curl -s -X POST $N/run -H 'Content-Type: application/json' -d "$1"; }
run '{"uri":"browser://cdp/session/command/launch","payload":{"browser":"chrome","url":"https://example.com"}}'
run '{"uri":"browser://cdp/page/query/eval","payload":{"expr":"document.title"}}'
run '{"uri":"browser://cdp/page/query/eval","payload":{"expr":"document.querySelector(\"a\").click()"}}'  # click → navigate
run '{"uri":"browser://cdp/page/query/screenshot","payload":{}}'
```

### `browser://desktop/…` — forward to a noVNC/urirun node

- `browser://desktop/page/command/open`, `…/page/command/screenshot`

Safe by default: it does not open the physical host browser unless
`BROWSER_CONTROL_ALLOW_LOCAL=1`. Point it at a browser-capable urirun node:

```bash
export BROWSER_CONTROL_ENDPOINT=http://pc1:8765
# or:
export URI_SERVICE_MAP='{"desktop":"http://pc1:8765"}'
```

## Quick Start

```bash
pip install -e ".[test]"
make test
make smoke
make docker-test
```

## CLI

```bash
urirun-browser-control open https://example.com/ --target desktop
urirun-browser-control screenshot https://example.com/ --target desktop --output example.png
urirun-browser-control bindings
urirun-browser-control manifest
```

### Drive any browser by GUI (KVM) over a urirun node

Adopt this connector into a node's registry, then drive any browser by URI — e.g. log
into a site in Firefox, entirely by GUI control:

```bash
N=http://NODE:8765 ; run(){ curl -s -X POST $N/run -H 'Content-Type: application/json' -d "$1"; }
run '{"uri":"browser://kvm/session/command/launch","payload":{"browser":"firefox","url":"https://example.com/login"}}'
run '{"uri":"browser://kvm/page/command/navigate","payload":{"url":"https://example.com/login"}}'
run '{"uri":"browser://kvm/page/command/click-text","payload":{"text":"Email"}}'      # OCR-click the field
run '{"uri":"browser://kvm/input/command/type","payload":{"text":"jan@firma.pl"}}'
run '{"uri":"browser://kvm/input/command/hotkey","payload":{"keys":["Tab"]}}'
run '{"uri":"browser://kvm/input/command/type","payload":{"text":"secret","enter":true}}'
run '{"uri":"browser://kvm/screen/query/capture","payload":{"monitor":0}}'            # screenshot the result
```

The same calls work against Chrome, Brave, Edge … — only `browser` changes; the GUI
control surface (`urihim`/`urikvm`) is browser-agnostic.

## Run it (install, then run)

On installation the connector registers under the `urirun.bindings` entry-point
group, so urirun auto-discovers it — no compile step and no registry file. Two
declarations: what to install, and what to run.

```bash
urirun install urirun-connector-browser-control   # local dev: pip install -e .
urirun run 'browser://desktop/page/command/open' \
  --payload '{"url":"https://example.com/"}' \
  --execute --allow 'browser://desktop/*'
```

Inspect the live runtime over the same URI contract (routes are discoverable, not
just runnable — `registry://` is built in, alongside `error://`/`log://`):

```bash
urirun list                                              # every installed route + builtins
urirun run 'registry://local/routes/query/list' --execute --allow 'registry://*'
```

> Auto-discovery, the `urirun install` alias and the `registry://` builtins need
> **urirun ≥ 0.4.4**. On older urirun, compile an explicit registry first:
>
> ```bash
> urirun-browser-control bindings > browser.bindings.json
> urirun validate browser.bindings.json
> urirun compile browser.bindings.json --out browser.registry.json
> urirun run browser://desktop/page/command/open browser.registry.json \
>   --payload '{"url":"https://example.com/"}' --execute --allow 'browser://desktop/*'
> ```

## Related projects

- Runtime: [if-uri/urirun](https://github.com/if-uri/urirun)
- Docs: [docs.ifuri.com/connectors.html](https://docs.ifuri.com/connectors.html) · [authoring a connector](https://docs.ifuri.com/connector-authoring.html)
- Hub page: [connect.ifuri.com/connectors/browser-control](https://connect.ifuri.com/connectors/browser-control)
- Connector hub: [connect.ifuri.com](https://connect.ifuri.com)
- noVNC example: [if-uri/examples/11-novnc_lan_flow](https://github.com/if-uri/examples/tree/main/11-novnc_lan_flow)
- Current work summary:
  [work-summary-2026-06-20](https://github.com/if-uri/docs/blob/main/work-summary-2026-06-20.md)

Repository notes: [TODO.md](TODO.md) · [CHANGELOG.md](CHANGELOG.md)

## License

Released under the terms in [LICENSE](LICENSE).

## Examples

Runnable walkthrough: [`examples/`](examples/) — `./examples/read-and-screenshot.sh`.
