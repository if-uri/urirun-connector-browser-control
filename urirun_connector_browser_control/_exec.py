# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

"""Out-of-process executor for browser-control routes.

The compiled v2 registry runs each route as an ``argv`` template that invokes
``python3 -m urirun_connector_browser_control._exec <subcommand> ...``. urirun
only spawns this template under ``--execute``, so this module always runs the
route logic and prints the connector's JSON result to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import core


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="urirun_connector_browser_control._exec")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open")
    p.add_argument("--url", required=True)
    p.add_argument("--target", default="desktop")

    p = sub.add_parser("screenshot")
    p.add_argument("--url", required=True)
    p.add_argument("--target", default="desktop")
    p.add_argument("--output", default="browser-screenshot.png")

    p = sub.add_parser("chrome-dom")
    p.add_argument("--url", default="")
    p.add_argument("--max", type=int, default=4000)

    p = sub.add_parser("chrome-text")
    p.add_argument("--url", default="")
    p.add_argument("--max", type=int, default=2000)

    p = sub.add_parser("chrome-screenshot")
    p.add_argument("--url", default="")
    p.add_argument("--output", default="chrome-screenshot.png")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    kwargs = {k: v for k, v in vars(args).items() if k != "command"}
    result = core.run_route(args.command, **kwargs)
    print(json.dumps(result))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
