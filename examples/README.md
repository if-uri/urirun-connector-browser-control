# browser-control connector — examples

Headless Chrome (local) + noVNC (remote).

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
