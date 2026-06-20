#!/usr/bin/env bash
# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

set -euo pipefail

mkdir -p .docker-smoke

export BROWSER_CONTROL_ENDPOINT="${BROWSER_CONTROL_ENDPOINT:-http://fake-browser:8765}"

echo "==> direct connector CLI"
urirun-browser-control open https://example.com/ > .docker-smoke/open-cli.json
urirun-browser-control screenshot https://example.com/ --output example.png > .docker-smoke/screenshot-cli.json

echo "==> build bindings and registry"
python3 - <<'PY' > .docker-smoke/bindings.json
import json
from urirun_connector_browser_control import urirun_bindings
print(json.dumps(urirun_bindings(), indent=2))
PY

urirun validate .docker-smoke/bindings.json
urirun compile .docker-smoke/bindings.json --out .docker-smoke/registry.json

echo "==> execute connector URI through urirun"
urirun run 'browser://desktop/page/command/open' .docker-smoke/registry.json \
  --payload '{"url":"https://example.com/","timeout":5}' \
  --execute \
  --allow 'browser://desktop/*' > .docker-smoke/open-urirun.json

urirun run 'browser://desktop/page/command/screenshot' .docker-smoke/registry.json \
  --payload '{"url":"https://example.com/","output":"example.png","timeout":5}' \
  --execute \
  --allow 'browser://desktop/*' > .docker-smoke/screenshot-urirun.json

echo "==> project registry to MCP tools and A2A card"
python3 -m urirun.v2_mcp tools .docker-smoke/registry.json > .docker-smoke/mcp-tools.json
python3 -m urirun.v2_mcp card .docker-smoke/registry.json \
  --name browser-control-docker \
  --url http://tester/ > .docker-smoke/a2a-card.json

python3 - <<'PY'
import json
from pathlib import Path

base = Path(".docker-smoke")
open_cli = json.loads((base / "open-cli.json").read_text())
shot_cli = json.loads((base / "screenshot-cli.json").read_text())
open_run = json.loads((base / "open-urirun.json").read_text())
shot_run = json.loads((base / "screenshot-urirun.json").read_text())
tools = json.loads((base / "mcp-tools.json").read_text())
card = json.loads((base / "a2a-card.json").read_text())

assert open_cli["ok"] is True and open_cli["forwarded"] is True, open_cli
assert shot_cli["ok"] is True and shot_cli["forwarded"] is True, shot_cli
assert open_run["ok"] is True, open_run
assert shot_run["ok"] is True, shot_run
names = {tool["name"] for tool in tools["tools"]}
assert "browser_desktop_page_command" in names, tools
assert "browser_desktop_page_command_screenshot" in names, tools
assert any("browser://desktop/page/command/open" in skill.get("examples", []) for skill in card["skills"]), card
print(json.dumps({
    "ok": True,
    "mcpTools": len(tools["tools"]),
    "a2aSkills": len(card["skills"]),
}, indent=2))
PY
