#!/usr/bin/env bash
# browser-control: install once, then run — auto-discovered, no registry path.
set -euo pipefail
urirun install urirun-connector-browser-control            # local dev: pip install -e .
urirun run 'browser://chrome/page/query/text' --payload '{"url": "https://example.com", "max": 120}' --execute --allow 'browser://*'
