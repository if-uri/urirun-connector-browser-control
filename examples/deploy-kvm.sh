#!/usr/bin/env bash
# Deploy the browser://<node>/kvm/* surface onto a urirun node over the mesh (no SSH).
# The node must run with --admin-token or --key-auth (signed /deploy). For real GUI
# control the node also needs a desktop session + ydotool/grim (or the tellmesh packs).
#
#   ./deploy-kvm.sh <host[:port]> <node-name> [identity]
#   ./deploy-kvm.sh 192.168.188.201 laptop ~/.ssh/id_ed25519
set -euo pipefail

HOST="${1:?usage: deploy-kvm.sh <host[:port]> <node-name> [identity]}"
NODE="${2:?node name (the URI authority, e.g. 'laptop')}"
IDENTITY="${3:-$HOME/.ssh/id_ed25519}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# bindings are templated on NODE — substitute the real node name
BINDINGS="$(mktemp --suffix=.json)"
sed "s|browser://NODE/|browser://${NODE}/|g" "$HERE/kvm-bindings.json" > "$BINDINGS"
trap 'rm -f "$BINDINGS"' EXIT

echo "Deploying browser://${NODE}/kvm/* to ${HOST} ..."
urirun host deploy "$HOST" --identity "$IDENTITY" \
  --bindings "$BINDINGS" --code "$HERE/kvm-flat-handler.py" \
  --env "URISYS_ALLOW_REAL=1" \
  ${TELLMESH_DIR:+--env "TELLMESH_DIR=$TELLMESH_DIR"} \
  --allow "browser://${NODE}/**"

echo
echo "Probe the desktop (what GUI control is available on the node):"
echo "  curl -s -X POST http://${HOST%:*}:${HOST#*:}/run -H 'Content-Type: application/json' \\"
echo "    -d '{\"uri\":\"browser://${NODE}/kvm/session/query/probe\",\"payload\":{}}'"
