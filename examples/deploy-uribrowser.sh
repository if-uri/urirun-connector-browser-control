#!/usr/bin/env bash
# Deploy the READY tellmesh uribrowserdocker pack onto a urirun node (no vendored copy —
# it pushes the pack's own handlers.py from $TELLMESH_DIR) + a thin bridge, then you drive
# it over uribrowser's standard browser://<node>/<session>/... URIs.
#
#   TELLMESH_DIR=/path/to/tellmesh ./deploy-uribrowser.sh <host[:port]> <node> [identity]
set -euo pipefail
HOST="${1:?usage: deploy-uribrowser.sh <host[:port]> <node> [identity]}"
NODE="${2:?node name (URI authority, e.g. laptop)}"
IDENTITY="${3:-$HOME/.ssh/id_ed25519}"
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${TELLMESH_DIR:?set TELLMESH_DIR to a tellmesh checkout (provides uribrowser)}"
PACK="$TELLMESH_DIR/uribrowser/uribrowserdocker/handlers.py"
[ -f "$PACK" ] || { echo "uribrowser pack not found: $PACK" >&2; exit 1; }

# stage with the module names the bindings expect (ubd.py + ubd_handlers.py)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cp "$HERE/uribrowser-bridge.py" "$TMP/ubd.py"
cp "$PACK" "$TMP/ubd_handlers.py"
sed "s|browser://NODE/|browser://${NODE}/|g" "$HERE/uribrowser-bindings.json" > "$TMP/bindings.json"

echo "Deploying ready uribrowserdocker pack to ${HOST} (browser://${NODE}/main/*) ..."
urirun host deploy "$HOST" --identity "$IDENTITY" \
  --bindings "$TMP/bindings.json" --code "$TMP/ubd.py" --code "$TMP/ubd_handlers.py" \
  --env "URISYS_ALLOW_REAL=1" ${TELLMESH_DIR:+--env "TELLMESH_DIR=$TELLMESH_DIR"} \
  --allow "browser://${NODE}/**"
echo "Real DOM/screenshot via this pack needs playwright on the node:"
echo "  node://${NODE}/package/command/install {\"spec\":[\"playwright\"]}  (node --manage), then 'playwright install chromium'"
