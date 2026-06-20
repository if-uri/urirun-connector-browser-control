# urirun-connector-browser-control

Browser Control connector for [ifURI](https://ifuri.com) / [urirun](https://github.com/tellmesh/urirun).

It declares browser actions as URI routes:

- `browser://desktop/page/command/open`
- `browser://desktop/page/command/screenshot`

The connector is safe by default: it does not open the physical host browser unless
`BROWSER_CONTROL_ALLOW_LOCAL=1` is set. In normal ifURI/noVNC demos, point it at a
browser-capable urirun node:

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

## Registry

```bash
urirun-browser-control bindings > browser.bindings.json
urirun validate browser.bindings.json
urirun compile browser.bindings.json --out browser.registry.json
urirun run browser://desktop/page/command/open browser.registry.json \
  --payload '{"url":"https://example.com/"}' \
  --execute \
  --allow 'browser://desktop/*'
```
