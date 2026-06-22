#!/usr/bin/env bash
# Browser-control: read a page's text and capture a screenshot with local
# headless Chrome (browser://chrome/...). These routes drive a real browser, so
# urirun only runs them under `urirun run ... --execute`. The connector's own CLI
# (urirun-browser-control) runs the route directly; when no Chrome is installed it
# returns a safe no-op (executed:false).
set -euo pipefail
cd "$(dirname "$0")"
URL="${1:-https://example.com}"

echo "== 1) read page text (query) =="
urirun-browser-control chrome-text --url "$URL" --max 160 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('   ok:', d['ok'], '| text:', (d.get('text') or '')[:120])"

echo "== 2) screenshot (command) =="
urirun-browser-control chrome-screenshot --url "$URL" --output page.png \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('   ok:', d['ok'], '| saved:', d.get('saved'), '-> page.png')"

echo "== 3) route surface =="
urirun-browser-control bindings > browser.bindings.json
urirun validate browser.bindings.json
