# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

from __future__ import annotations

import sys

import urirun

from .core import capture_screenshot, connector_manifest, open_page, urirun_bindings


def register(sub) -> None:
    open_parser = sub.add_parser("open", help="Open a URL through a browser target")
    open_parser.add_argument("url")
    open_parser.add_argument("--target", default="desktop")
    open_parser.add_argument("--timeout", type=float, default=10.0)

    screenshot = sub.add_parser("screenshot", help="Capture a URL screenshot through a browser target")
    screenshot.add_argument("url")
    screenshot.add_argument("--target", default="desktop")
    screenshot.add_argument("--output", default="browser-screenshot.png")
    screenshot.add_argument("--timeout", type=float, default=10.0)


def dispatch(args) -> int:
    if args.command == "open":
        result = open_page(args.url, target=args.target, timeout=args.timeout)
    elif args.command == "screenshot":
        result = capture_screenshot(args.url, target=args.target, output=args.output, timeout=args.timeout)
    else:
        return 1
    urirun.connector_emit(result)
    return 0 if result.get("ok") else 2


def main(argv: list[str] | None = None) -> int:
    return urirun.connector_cli(
        "urirun-browser-control",
        manifest=connector_manifest,
        bindings=urirun_bindings,
        register=register,
        dispatch=dispatch,
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
